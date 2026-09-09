"""Private, durable image storage. Routes must authenticate, enforce CSRF and
serve image() results (never mount media_root). Only approved rows are public.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import os
import re
import time
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from logly import logger
from PIL import Image, ImageOps, UnidentifiedImageError

from .repository import ConflictError, Repository
from .settings import Settings


MAX_BYTES = 512 * 1024
THUMB_BYTES = 128 * 1024
MAX_DIMENSION = 8192
LEASE_SECONDS = 3600
STORAGE_BUCKET = "photo_storage"
PUBLIC_FIELDS = ("id", "recipe_id", "uploader_id", "width", "height", "size_bytes",
                 "caption", "status", "created_at", "updated_at", "moderated_at")


def _error(code, detail):
    return HTTPException(status_code=code, detail=detail)


def _id(value, table):
    value = value.get("id") if isinstance(value, dict) else value
    if not isinstance(value, str):
        raise _error(400, "Invalid ID")
    value = value.removeprefix(table + ":")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise _error(400, "Invalid ID")
    return value


def _digest(*parts):
    return sha256("\0".join(parts).encode()).hexdigest()


def _public(photo):
    return {key: photo[key] for key in PUBLIC_FIELDS if key in photo}


def _live(recipe):
    return recipe and not recipe.get("deleted_at") and recipe.get("status") not in {"deleted", "hidden"}


def _date(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


class PhotoService:
    def __init__(self, repo: Repository, settings: Settings):
        self.repo = repo
        self.settings = settings
        self.root = Path(settings.media_root).resolve() / "photos"
        self._slots = asyncio.Semaphore(settings.image_concurrency)
        # Settings predates the strict transport cap; configuration may lower it only.
        self.max_bytes = min(MAX_BYTES, settings.upload_max_bytes)

    async def _retry(self, operation):
        for attempt in range(8):
            try:
                async with self.repo.transaction() as tx:
                    result = await operation(tx)
                return result
            except ConflictError:
                if attempt == 7:
                    raise _error(503, "Photo storage busy; retry later") from None
                await asyncio.sleep(0.005 * (attempt + 1))

    async def _thread(self, function, *args):
        # Cancellation must not release a slot while its native decoder still runs.
        async with self._slots:
            task = asyncio.create_task(asyncio.to_thread(function, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        logger.exception("Photo processing task failed while cancellation was pending")
                        break
                if not task.cancelled():
                    task.exception()
                raise

    async def _all(self, table, filters=None, repo=None):
        rows, start = [], 0
        while True:
            page = await (repo or self.repo).list(table, filters, limit=1000, start=start)
            rows.extend(page)
            if len(page) < 1000:
                return rows
            start += len(page)

    async def _active_user(self, tx, user_id, touch=False):
        seen = set()
        while user_id and user_id not in seen:
            seen.add(user_id)
            user = await tx.get("users", user_id)
            if not user or user.get("state") == "blocked":
                break
            if touch:
                await tx.update("users", user_id, {"photo_access_token": uuid4().hex})
            if not user.get("merged_into"):
                if user.get("state") == "active":
                    return user
                break
            user_id = _id(user["merged_into"], "users")
        raise _error(403, "Active user required")

    async def _recipe(self, tx, recipe_id, touch=False):
        recipe = await tx.get("recipes", recipe_id)
        if not _live(recipe):
            raise _error(404, "Recipe not found")
        if recipe.get("owner_id"):
            await self._active_user(tx, recipe["owner_id"], touch=touch)
        if touch:
            await tx.update("recipes", recipe_id, {"photo_access_token": uuid4().hex})
        return recipe

    def _retention(self):
        days = getattr(self.settings, "photo_retention_days", None)
        if days is None:
            days = int(os.getenv("RECIPE_PHOTO_RETENTION_DAYS", "30"))
        return timedelta(days=max(0, days))

    async def _charge(self, user_id, ip):
        now = datetime.now(UTC)
        day = now.date().isoformat()
        async def charge(tx):
            user = await self._active_user(tx, user_id, touch=True)
            scopes = (("user:" + user["id"], self.settings.upload_daily_limit),
                      ("ip:" + _digest(str(ip)), getattr(self.settings, "upload_ip_daily_limit", 60)),
                      ("global", getattr(self.settings, "upload_global_daily_limit", 200)))
            denied = False
            for scope, limit in scopes:
                key = "photo_attempt_" + _digest(day, scope)
                row = await tx.get("usage", key)
                count = (row["count"] if row else 0) + 1
                data = {"scope": scope, "kind": "photo_attempt", "count": count,
                        "expires_at": (now + timedelta(days=2)).isoformat()}
                if row:
                    await tx.compare_and_swap("usage", key, row["revision"], data)
                else:
                    await tx.create("usage", data, id=key)
                denied |= count > limit
            return denied, user["id"]

        # Deliberately commit counters even when an attempt will be rejected.
        denied, canonical_id = await self._retry(charge)
        if denied:
            raise _error(429, "Daily photo upload limit exceeded")
        return canonical_id

    async def _storage(self, tx, delta):
        row = await tx.get("usage", STORAGE_BUCKET)
        count = (row["count"] if row else 0) + delta
        if count < 0:
            raise RuntimeError("Photo storage ledger is inconsistent")
        if count > self.settings.storage_max_bytes:
            raise _error(507, "Photo storage capacity exceeded")
        if row:
            await tx.compare_and_swap("usage", STORAGE_BUCKET, row["revision"], {"count": count})
        else:
            await tx.create("usage", {"scope": "global", "kind": "photo_storage", "count": count}, id=STORAGE_BUCKET)

    async def upload(self, recipe, user, file: UploadFile, caption="", idempotency_key=None, ip="unknown"):
        user_id = _id(user, "users")
        user_id = await self._charge(user_id, ip)
        recipe_id = _id(recipe, "recipes")
        if not isinstance(caption, str) or len(caption) > 5000:
            raise _error(422, "Caption must be at most 5000 characters")
        if idempotency_key is not None and (not isinstance(idempotency_key, str)
                                            or not 1 <= len(idempotency_key) <= 200):
            raise _error(400, "Invalid idempotency key")
        random_id = uuid4().hex
        token = uuid4().hex
        reserve = MAX_BYTES + THUMB_BYTES

        async def claim(tx):
            await self._recipe(tx, recipe_id, touch=True)
            current_user = await self._active_user(tx, user_id, touch=True)
            canonical_id = current_user["id"]
            photo_id = ("upload_" + _digest(canonical_id, idempotency_key)
                        if idempotency_key is not None else random_id)
            previous = await tx.get("photos", photo_id)
            if not previous and idempotency_key is not None:
                matches = await self._all("photos", {"uploader_id": canonical_id,
                                                    "idempotency_digest": _digest(idempotency_key)}, repo=tx)
                if matches:
                    previous = matches[0]
                    photo_id = previous["id"]
            if previous:
                if previous["recipe_id"] != recipe_id or previous["caption"] != caption:
                    raise _error(409, "Idempotency key already used for a different request")
                if previous["status"] in {"pending", "approved", "rejected"}:
                    return previous, False
                if previous["status"] != "failed" or previous.get("reserved_bytes", 0):
                    raise _error(409, "Upload is in progress or has been deleted")
            pending = await self._all("photos", {"uploader_id": canonical_id}, repo=tx)
            if sum(row["status"] in {"uploading", "pending"} for row in pending) >= self.settings.upload_pending_limit:
                raise _error(429, "Pending photo upload limit exceeded")
            await self._storage(tx, reserve)
            data = {"recipe_id": recipe_id, "uploader_id": canonical_id, "caption": caption,
                    "idempotency_digest": _digest(idempotency_key) if idempotency_key is not None else None,
                    "status": "uploading", "storage_key": token + ".jpg",
                    "thumbnail_key": token + "_thumb.jpg", "reserved_bytes": reserve,
                    "upload_token": token, "lease_until": (datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)).isoformat(),
                    "trusted_upload": bool(current_user.get("photo_trust")), "size_bytes": 0}
            row = (await tx.update("photos", photo_id, data) if previous
                   else await tx.create("photos", data, id=photo_id))
            return row, True

        photo, claimed = await self._retry(claim)
        photo_id = photo["id"]
        if not claimed:
            return _public(photo)
        try:
            async with asyncio.timeout(120):
                data = bytearray()
                while True:
                    chunk = await file.read(min(64 * 1024, self.max_bytes + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > self.max_bytes:
                        raise _error(413, "Photo exceeds 512 KiB upload limit")
                result = await self._thread(self._process, bytes(data), photo)

            async def finish(tx):
                row = await tx.get("photos", photo_id)
                if not row or row["status"] != "uploading" or row.get("upload_token") != token:
                    raise _error(409, "Upload no longer active")
                await self._recipe(tx, row["recipe_id"], touch=True)
                current_user = await self._active_user(tx, row["uploader_id"], touch=True)
                await self._storage(tx, result["stored_bytes"] - reserve)
                return await tx.update("photos", photo_id, {
                    **result, "reserved_bytes": result["stored_bytes"], "lease_until": None,
                    "uploader_id": current_user["id"],
                    "status": "approved" if row["trusted_upload"] and current_user.get("photo_trust") else "pending"})

            return _public(await self._retry(finish))
        except BaseException:
            # A crash or failed cleanup leaves a durable reservation for cleanup().
            # Do not unlink here: a failed commit response may have committed approval.
            try:
                await self._mark_deleting(photo_id, "failed", token=token)
                await self._purge(photo_id)
            except Exception:
                logger.exception("Failed to clean up interrupted photo upload")
            raise

    def _path(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}(?:_thumb)?\.jpg", key):
            raise _error(404, "Image not found")
        path = self.root / key
        if path.is_symlink() or path.resolve().parent != self.root:
            raise _error(404, "Image not found")
        return path

    @staticmethod
    def _validate_container(data):
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            pos = 8
            while pos + 12 <= len(data):
                size = int.from_bytes(data[pos:pos + 4], "big")
                kind = data[pos + 4:pos + 8]
                pos += size + 12
                if pos > len(data):
                    break
                if kind == b"IEND":
                    if size == 0 and pos == len(data):
                        return
                    break
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            if int.from_bytes(data[4:8], "little") + 8 == len(data):
                pos = 12
                while pos + 8 <= len(data):
                    size = int.from_bytes(data[pos + 4:pos + 8], "little")
                    pos += 8 + size + size % 2
                if pos == len(data):
                    return
        elif data.startswith(b"\xff\xd8"):
            pos, scan = 2, False
            while pos < len(data):
                if scan:
                    pos = data.find(b"\xff", pos)
                    if pos < 0:
                        break
                elif data[pos] != 0xff:
                    break
                while pos < len(data) and data[pos] == 0xff:
                    pos += 1
                if pos == len(data):
                    break
                marker = data[pos]
                pos += 1
                if scan and (marker == 0 or 0xd0 <= marker <= 0xd7):
                    continue
                if marker == 0xd9:
                    if pos == len(data):
                        return
                    break
                if marker in {0, 0xd8} or 0xd0 <= marker <= 0xd7:
                    break
                if marker == 1:
                    continue
                if pos + 2 > len(data):
                    break
                size = int.from_bytes(data[pos:pos + 2], "big")
                if size < 2 or pos + size > len(data):
                    break
                pos += size
                scan = marker == 0xda
        raise _error(422, "Invalid or unsupported image container")

    def _process(self, data, photo):
        try:
            self._validate_container(data)
            with Image.open(BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as image:
                width, height = image.size
                if (width <= 0 or height <= 0 or max(width, height) > MAX_DIMENSION
                        or width * height > min(self.settings.image_max_pixels, 24_000_000)):
                    raise _error(422, "Image dimensions exceed limits")
                if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                    raise _error(422, "Animated images are not supported")
                image.verify()
            with Image.open(BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as image:
                image.load()
                oriented = ImageOps.exif_transpose(image)
                # A new pixel-only image cannot carry EXIF, ICC, XMP or text chunks.
                clean = Image.new("RGB", oriented.size, "white")
                if "A" in oriented.getbands() or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    clean.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    clean.paste(oriented.convert("RGB"))
            full = self._encode(clean, MAX_BYTES)
            thumb = clean.copy()
            thumb.thumbnail((320, 320), Image.Resampling.LANCZOS)
            small = self._encode(thumb, THUMB_BYTES)
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            for key, content in ((photo["storage_key"], full), (photo["thumbnail_key"], small)):
                path = self._path(key)
                staged = path.with_suffix(".jpg.partial")
                # Exclusive randomized staging; no client filename is used.
                with staged.open("xb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                staged.replace(path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return {"width": clean.width, "height": clean.height, "size_bytes": len(full),
                    "stored_bytes": len(full) + len(small)}
        except HTTPException:
            raise
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise _error(422, "Invalid or unsupported image") from exc

    @staticmethod
    def _encode(image, cap):
        output = BytesIO()
        image.save(output, format="JPEG", quality=85, exif=b"", icc_profile=None)
        if output.tell() > cap:
            raise _error(422, "Reencoded image exceeds storage limit")
        return output.getvalue()

    @staticmethod
    def _visible(photo, user_id, admin):
        status = photo.get("status")
        return status in {"approved", "pending", "rejected"} and (
            status == "approved" or admin or (user_id is not None and photo["uploader_id"] == user_id))

    async def visible_photos(self, recipe_id=None, user_id=None, admin=False, mine=False):
        recipe_id = _id(recipe_id, "recipes") if recipe_id is not None else None
        user_id = _id(user_id, "users") if user_id is not None else None
        if mine and user_id is None:
            return []
        filters = {"recipe_id": recipe_id} if recipe_id else {}
        if mine:
            filters["uploader_id"] = user_id
        if user_id is not None:
            user_id = (await self._active_user(self.repo, user_id))["id"]
            if mine:
                filters["uploader_id"] = user_id
        rows = await self._all("photos", filters)
        recipes, result = {}, []
        for photo in rows:
            if not self._visible(photo, user_id, admin):
                continue
            rid = photo["recipe_id"]
            try:
                if rid not in recipes:
                    recipes[rid] = await self._recipe(self.repo, rid)
                await self._active_user(self.repo, photo["uploader_id"])
            except HTTPException:
                continue
            result.append(_public(photo))
        return result

    async def image(self, photo_id, user_id=None, admin=False, thumbnail=False):
        user_id = _id(user_id, "users") if user_id is not None else None
        async with self.repo.transaction() as tx:
            if user_id is not None:
                user_id = (await self._active_user(tx, user_id))["id"]
            photo = await tx.get("photos", _id(photo_id, "photos"))
            if (not photo or not self._visible(photo, user_id, admin)
                    or not _live(await tx.get("recipes", photo["recipe_id"]))):
                raise _error(404, "Image not found")
            try:
                await self._recipe(tx, photo["recipe_id"])
                await self._active_user(tx, photo["uploader_id"])
            except HTTPException:
                raise _error(404, "Image not found") from None
            path = self._path(photo["thumbnail_key" if thumbnail else "storage_key"])
            if not await self._thread(path.is_file):
                raise _error(404, "Image not found")
            return path, photo["status"] == "approved"

    async def _mark_deleting(self, photo_id, target, token=None, user_id=None, admin=False, stale=False, authorize_actor=None):
        async def mark(tx):
            if authorize_actor is not None:
                await authorize_actor(tx)
            photo = await tx.get("photos", photo_id)
            if not photo:
                raise _error(404, "Photo not found")
            if user_id is not None and not admin:
                user = await self._active_user(tx, user_id, touch=True)
                if photo["uploader_id"] != user["id"]:
                    raise _error(404, "Photo not found")
            if token is not None and (photo["status"] != "uploading" or photo.get("upload_token") != token):
                return
            if stale and (photo["status"] != "uploading" or not photo.get("lease_until")
                          or _date(photo["lease_until"]) > datetime.now(UTC)):
                return
            if photo["status"] in {"deleted", "deleting"}:
                return
            # A different worker may still be decoding/writing this upload. Keep
            # its reservation until the lease expires before deleting its files.
            delete_after = (photo.get("lease_until")
                            if photo["status"] == "uploading" and token is None else None)
            now = datetime.now(UTC)
            if target == "deleted":
                deadline = now + self._retention()
                if delete_after:
                    deadline = max(deadline, _date(delete_after))
                delete_after = deadline.isoformat()
            await tx.update("photos", photo_id, {"status": "deleting", "delete_target": target,
                                                "previous_status": photo["status"], "deleted_at": now,
                                                "delete_after": delete_after})
        await self._retry(mark)

    def _unlink(self, photo):
        for key in (photo.get("storage_key"), photo.get("thumbnail_key")):
            if key:
                path = self._path(key)
                path.unlink(missing_ok=True)
                path.with_suffix(".jpg.partial").unlink(missing_ok=True)

    async def _purge(self, photo_id):
        photo = await self.repo.get("photos", photo_id)
        if not photo or photo["status"] != "deleting":
            return
        if photo.get("delete_after") and _date(photo["delete_after"]) > datetime.now(UTC):
            return
        await self._thread(self._unlink, photo)

        async def release(tx):
            row = await tx.get("photos", photo_id)
            if (row and row["status"] == "deleting"
                    and row.get("upload_token") == photo.get("upload_token")):
                await self._storage(tx, -row.get("reserved_bytes", 0))
                await tx.update("photos", photo_id, {"status": row.get("delete_target", "deleted"),
                                                    "reserved_bytes": 0, "lease_until": None})
        await self._retry(release)

    async def remove(self, photo_id, user_id, admin=False, *, authorize_actor=None):
        photo_id = _id(photo_id, "photos")
        user_id = _id(user_id, "users") if user_id is not None else None
        if user_id is None and not admin:
            raise _error(401, "Authentication required")
        await self._mark_deleting(photo_id, "deleted", user_id=user_id, admin=admin, authorize_actor=authorize_actor)
        await self._purge(photo_id)

    async def moderate(self, photo_id, state, actor_id=None):
        if state not in {"pending", "approved", "rejected"}:
            raise _error(422, "Invalid moderation state")
        photo_id = _id(photo_id, "photos")
        actor_id = _id(actor_id, "users") if actor_id is not None else None

        async def change(tx):
            row = await tx.get("photos", photo_id)
            if not row:
                raise _error(404, "Photo not found")
            await self._recipe(tx, row["recipe_id"], touch=True)
            if state == "approved":
                await self._active_user(tx, row["uploader_id"], touch=True)
            if row["status"] not in {"pending", "approved", "rejected"}:
                raise _error(409, "Photo is not ready for moderation")
            result = await tx.update("photos", photo_id, {"status": state, "moderated_at": datetime.now(UTC)})
            await tx.create("audit", {"actor_id": actor_id, "action": "photo.moderate", "target": photo_id,
                                      "previous_status": row["status"], "status": state})
            return result
        return _public(await self._retry(change))

    async def _expire_rejected(self, photo_id):
        async def expire(tx):
            photo = await tx.get("photos", photo_id)
            if not photo or photo["status"] != "rejected":
                return
            since = photo.get("moderated_at") or photo.get("updated_at") or photo.get("created_at")
            if since and _date(since) + self._retention() <= datetime.now(UTC):
                await tx.update("photos", photo_id, {"status": "deleting", "delete_target": "deleted",
                                                    "previous_status": "rejected", "delete_after": None,
                                                    "deleted_at": datetime.now(UTC)})
        await self._retry(expire)

    async def cleanup(self):
        """Run periodically/startup. Errors retain reservations for a later retry."""
        rows = await self._all("photos")
        for photo in rows:
            if photo["status"] in {"deleted", "failed"}:
                continue
            recipe = await self.repo.get("recipes", photo["recipe_id"])
            if not recipe or recipe.get("deleted_at") or recipe.get("status") == "deleted":
                await self._mark_deleting(photo["id"], "deleted")
            elif photo["status"] == "rejected":
                await self._expire_rejected(photo["id"])
            elif photo["status"] == "uploading":
                await self._mark_deleting(photo["id"], "failed", stale=True)
            await self._purge(photo["id"])
        # Never remove a referenced file; orphan grace exceeds the upload lease.
        rows = await self._all("photos")
        referenced = {row[key] for row in rows if row["status"] not in {"failed", "deleted"}
                      for key in ("storage_key", "thumbnail_key") if row.get(key)}
        await self._thread(self._orphans, referenced)

    def _orphans(self, referenced):
        if not self.root.is_dir():
            return
        cutoff = time.time() - 86400
        for path in self.root.iterdir():
            key = path.name.removesuffix(".partial")
            if (re.fullmatch(r"[0-9a-f]{32}(?:_thumb)?\.jpg", key)
                    and key not in referenced and not path.is_symlink()
                    and path.is_file() and path.stat().st_mtime < cutoff):
                path.unlink(missing_ok=True)
