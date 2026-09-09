import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from io import BytesIO
import os
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, PngImagePlugin
import pytest
import pytest_asyncio

from recipe_creator.photos import MAX_BYTES, PhotoService, STORAGE_BUCKET
from recipe_creator.repository import ConflictError, Repository
from recipe_creator.settings import Settings


class MemoryRepo:
    """Transactional unit-test double; integration tests exercise actual MVCC."""
    def __init__(self):
        self.rows = {name: {} for name in ("users", "recipes", "photos", "usage", "audit")}
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            previous = deepcopy(self.rows)
            try:
                yield self
            except BaseException:
                self.rows = previous
                raise

    async def get(self, table, id):
        return deepcopy(self.rows[table].get(id))

    async def list(self, table, filters=None, limit=100, start=0):
        rows = [deepcopy(value) for _, value in sorted(self.rows[table].items())
                if all(value.get(key) == val for key, val in (filters or {}).items())]
        return rows[start:start + limit]

    async def create(self, table, data, id=None):
        id = id or uuid4().hex
        if id in self.rows[table]:
            raise ConflictError("Duplicate")
        self.rows[table][id] = {"id": id, "revision": 1, **deepcopy(data)}
        return await self.get(table, id)

    async def update(self, table, id, data):
        self.rows[table][id].update(deepcopy(data))
        return await self.get(table, id)

    async def compare_and_swap(self, table, id, expected_revision, data):
        assert self.rows[table][id]["revision"] == expected_revision
        return await self.update(table, id, {**data, "revision": expected_revision + 1})


def upload_file(format="PNG", size=(64, 48), **options):
    output = BytesIO()
    Image.new("RGB", size, "red").save(output, format=format, **options)
    return UploadFile(file=BytesIO(output.getvalue()), filename="../../not-an-image.exe")


@pytest_asyncio.fixture
async def service(tmp_path):
    repo = MemoryRepo()
    await repo.create("users", {"state": "active", "photo_trust": False}, "owner")
    await repo.create("users", {"state": "active", "photo_trust": False}, "uploader")
    await repo.create("recipes", {"owner_id": "owner", "deleted_at": None}, "recipe")
    return PhotoService(repo, Settings(media_root=tmp_path))


async def put(service, **kwargs):
    return await service.upload("recipe", "uploader", kwargs.pop("file", upload_file()),
                                kwargs.pop("caption", "A meal"), kwargs.pop("idempotency_key", "key"),
                                kwargs.pop("ip", "127.0.0.1"), **kwargs)


async def test_pending_is_private_including_recipe_owner_and_metadata(service):
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("GPS", "private location")
    photo = await put(service, file=upload_file(pnginfo=metadata))
    assert photo["status"] == "pending"
    assert "storage_key" not in photo and "upload_token" not in photo
    assert await service.visible_photos(user_id="owner") == []
    for user in (None, "owner"):
        with pytest.raises(HTTPException) as exc:
            await service.image(photo["id"], user_id=user)
        assert exc.value.status_code == 404
    path, public = await service.image(photo["id"], user_id="uploader")
    assert not public and path.is_file()
    with Image.open(path) as image:
        assert image.format == "JPEG" and image.size == (64, 48)
        assert not image.getexif() and "GPS" not in image.info and "icc_profile" not in image.info
    thumb, public = await service.image(photo["id"], admin=True, thumbnail=True)
    assert thumb != path and not public
    assert len(await service.visible_photos(user_id="uploader", mine=True)) == 1
    assert await service.visible_photos(mine=True) == []


async def test_caption_boundaries_preserve_authored_text(service):
    caption = "😀\n" + "a" * 4998
    photo = await put(service, caption=caption, idempotency_key="long-note")
    assert photo["caption"] == caption
    assert (await service.repo.get("photos", photo["id"]))["caption"] == caption

    with pytest.raises(HTTPException) as exc:
        await put(service, caption=caption + "b", idempotency_key="too-long-note")
    assert exc.value.status_code == 422
    assert exc.value.detail == "Caption must be at most 5000 characters"

    empty = await put(service, caption="", idempotency_key="empty-note")
    assert empty["caption"] == ""


async def test_trust_only_affects_future_uploads_moderation_and_recipe_deletion(service):
    pending = await put(service)
    await service.repo.update("users", "uploader", {"photo_trust": True})
    assert await service.visible_photos() == []
    approved = await put(service, idempotency_key="next")
    assert approved["status"] == "approved"
    assert (await service.image(approved["id"]))[1]
    await service.moderate(pending["id"], "approved")
    assert len(await service.visible_photos()) == 2
    await service.moderate(approved["id"], "rejected")
    assert len(await service.visible_photos()) == 1
    assert len(await service.repo.list("audit")) == 2
    await service.repo.update("recipes", "recipe", {"deleted_at": datetime.now(UTC).isoformat()})
    assert await service.visible_photos(admin=True) == []
    with pytest.raises(HTTPException):
        await service.image(pending["id"], admin=True)
    await service.cleanup()
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] > 0
    assert list(service.root.iterdir())
    for row in await service.repo.list("photos"):
        assert row["previous_status"] in {"approved", "rejected"}
        await service.repo.update("photos", row["id"], {"delete_after": datetime.now(UTC) - timedelta(seconds=1)})
    await service.cleanup()
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] == 0
    assert not list(service.root.iterdir())


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
async def test_supported_formats(service, format):
    assert (await put(service, file=upload_file(format)))["status"] == "pending"


@pytest.mark.parametrize("kind,code", [("large", 413), ("svg", 422), ("gif", 422),
                                       ("animated", 422), ("dimensions", 422), ("pixels", 422),
                                       ("truncated", 422)])
async def test_rejections_count_and_release_storage(service, kind, code):
    if kind == "large":
        file = UploadFile(file=BytesIO(b"x" * (MAX_BYTES + 10)))
    elif kind == "svg":
        file = UploadFile(file=BytesIO(b'<svg xmlns="http://www.w3.org/2000/svg"/>'))
    elif kind == "animated":
        file = upload_file(save_all=True, append_images=[Image.new("RGB", (64, 48), "blue")])
    elif kind == "dimensions":
        file = upload_file(size=(8193, 1))
    elif kind == "pixels":
        service.settings.image_max_pixels = 10
        file = upload_file()
    elif kind == "truncated":
        file = UploadFile(file=BytesIO((await upload_file("JPEG").read())[:100]))
    else:
        file = upload_file("GIF")
    with pytest.raises(HTTPException) as exc:
        await put(service, file=file)
    assert exc.value.status_code == code
    counts = await service.repo.list("usage", {"kind": "photo_attempt"})
    assert len(counts) == 3 and all(row["count"] == 1 for row in counts)
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] == 0
    assert (await service.repo.list("photos"))[0]["status"] == "failed"
    assert not service.root.exists() or not list(service.root.iterdir())
    # Failed attempts are retryable with the same key after cleanup.
    service.settings.image_max_pixels = 24_000_000
    assert (await put(service))["status"] == "pending"


async def test_idempotency_remove_and_capacity(service):
    service.settings.upload_daily_limit = 8
    photo = await put(service)
    assert await put(service, file=UploadFile(file=BytesIO(b"ignored retry"))) == photo
    assert len(await service.repo.list("photos")) == 1
    with pytest.raises(HTTPException) as exc:
        await put(service, caption="different")
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        await service.remove(photo["id"], "owner")
    service.settings.storage_max_bytes = 655360
    with pytest.raises(HTTPException) as exc:
        await put(service, idempotency_key="other")
    assert exc.value.status_code == 507
    path, _ = await service.image(photo["id"], user_id="uploader")
    await service.remove(photo["id"], "uploader")
    deleted = await service.repo.get("photos", photo["id"])
    await service.remove(photo["id"], "uploader")
    assert (await service.repo.get("photos", photo["id"]))["delete_after"] == deleted["delete_after"]
    assert deleted["previous_status"] == "pending" and path.is_file()
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] > 0
    assert await service.visible_photos(admin=True) == []
    with pytest.raises(HTTPException):
        await service.image(photo["id"], admin=True)
    await service.repo.update("photos", photo["id"], {"delete_after": datetime.now(UTC) - timedelta(seconds=1)})
    await service.cleanup()
    assert not path.exists()
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] == 0
    with pytest.raises(HTTPException):
        await put(service)
    assert (await put(service, idempotency_key="other"))["status"] == "pending"


async def test_quota_persists_including_rejected_attempts(service):
    service.settings.upload_daily_limit = 1
    with pytest.raises(HTTPException):
        await put(service, file=UploadFile(file=BytesIO(b"bad")))
    restarted = PhotoService(service.repo, service.settings)
    with pytest.raises(HTTPException) as exc:
        await put(restarted)
    assert exc.value.status_code == 429
    assert all(row["count"] == 2 for row in await service.repo.list("usage", {"kind": "photo_attempt"}))


async def test_crash_cleanup_and_filesystem_failure(service, monkeypatch):
    original = service._process
    def interrupted(data, photo):
        original(data, photo)
        raise OSError("disk failure after rename")
    monkeypatch.setattr(service, "_process", interrupted)
    with pytest.raises(OSError):
        await put(service)
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] == 0
    assert not list(service.root.iterdir())
    monkeypatch.setattr(service, "_process", original)
    photo = await put(service)
    await service.repo.update("photos", photo["id"], {"status": "uploading", "lease_until":
                              (datetime.now(UTC) - timedelta(seconds=1)).isoformat()})
    await service.cleanup()
    await service.cleanup()
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] == 0
    assert (await service.repo.get("photos", photo["id"]))["status"] == "failed"


async def test_concurrent_quota_and_storage_claims(service):
    service.settings.upload_daily_limit = 2
    results = await asyncio.gather(*(put(service, idempotency_key=str(i)) for i in range(5)),
                                   return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 2
    assert all(isinstance(result, dict) or result.status_code == 429 for result in results)
    photos = await service.repo.list("photos")
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] == sum(row["stored_bytes"] for row in photos)


async def test_path_escape_and_symlink_denied(service, tmp_path):
    photo = await put(service)
    row = await service.repo.get("photos", photo["id"])
    path, _ = await service.image(photo["id"], admin=True)
    path.unlink()
    path.symlink_to(tmp_path / "secret")
    with pytest.raises(HTTPException):
        await service.image(photo["id"], admin=True)
    await service.repo.update("photos", photo["id"], {"storage_key": "../secret"})
    with pytest.raises(HTTPException):
        await service.image(photo["id"], admin=True)
    assert row["storage_key"] != "../secret"


async def test_cancelled_thread_retains_processing_slot(service):
    import threading
    started, release, second = threading.Event(), threading.Event(), threading.Event()
    def slow():
        started.set()
        release.wait(5)
    first = asyncio.create_task(service._thread(slow))
    while not started.is_set():
        await asyncio.sleep(0.001)
    first.cancel()
    next_task = asyncio.create_task(service._thread(second.set))
    await asyncio.sleep(0.02)
    assert not second.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await next_task
    assert second.is_set()


async def test_remove_during_processing_retains_reservation_until_lease(service, monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    original = service._process
    def slow(data, photo):
        started.set()
        release.wait(5)
        return original(data, photo)
    monkeypatch.setattr(service, "_process", slow)
    task = asyncio.create_task(put(service))
    while not started.is_set():
        await asyncio.sleep(0.001)
    row = (await service.repo.list("photos"))[0]
    await service.remove(row["id"], "uploader")
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] > 0
    release.set()
    with pytest.raises(HTTPException) as exc:
        await task
    assert exc.value.status_code == 409
    assert (await service.repo.get("photos", row["id"]))["status"] == "deleting"
    await service.repo.update("photos", row["id"], {"delete_after":
                              (datetime.now(UTC) - timedelta(seconds=1)).isoformat()})
    await service.cleanup()
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] == 0
    assert not list(service.root.iterdir())


async def test_ip_and_global_quota_enforced_for_distinct_users(service):
    service.settings.upload_ip_daily_limit = 1
    service.settings.upload_global_daily_limit = 2
    await put(service)
    with pytest.raises(HTTPException) as exc:
        await service.upload("recipe", "owner", upload_file(), "", "owner-key", "127.0.0.1")
    assert exc.value.status_code == 429
    with pytest.raises(HTTPException) as exc:
        await service.upload("recipe", "owner", upload_file(), "", "owner-key", "127.0.0.2")
    assert exc.value.status_code == 429
    counters = await service.repo.list("usage", {"kind": "photo_attempt"})
    assert next(row for row in counters if row["scope"] == "global")["count"] == 3


async def test_stream_stops_after_cap_plus_one_byte(service):
    class Stream:
        consumed = 0
        async def read(self, size):
            self.consumed += size
            return b"x" * size
    stream = Stream()
    with pytest.raises(HTTPException) as exc:
        await put(service, file=stream)
    assert exc.value.status_code == 413
    assert stream.consumed == MAX_BYTES + 1


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
@pytest.mark.parametrize("suffix", [b"<script>alert(1)</script>", b"PK\x03\x04payload", b"\xff\xd9"])
async def test_trailing_polyglot_bytes_rejected(service, format, suffix):
    data = await upload_file(format).read()
    with pytest.raises(HTTPException) as exc:
        await put(service, file=UploadFile(file=BytesIO(data + suffix)))
    assert exc.value.status_code == 422
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] == 0


@pytest.mark.parametrize("format", ["PNG", "WEBP"])
async def test_container_payload_size_rejected(service, format):
    data = bytearray(await upload_file(format).read())
    if format == "PNG":
        data[-12:-8] = (1).to_bytes(4, "big")
    else:
        data[16:20] = (len(data) + 20).to_bytes(4, "little")
    with pytest.raises(HTTPException) as exc:
        await put(service, file=UploadFile(file=BytesIO(data)))
    assert exc.value.status_code == 422


async def test_progressive_jpeg_and_marker_bytes_in_metadata(service):
    assert (await put(service, file=upload_file("JPEG", progressive=True,
                                              comment=b"embedded \xff\xd9 marker")))["status"] == "pending"


async def test_pending_limit_includes_inflight_and_is_configurable(service):
    assert service.settings.upload_daily_limit == 5
    assert service.settings.upload_pending_limit == 3
    service.settings.upload_daily_limit = 20
    results = await asyncio.gather(*(put(service, idempotency_key=str(i)) for i in range(6)),
                                   return_exceptions=True)
    photos = [result for result in results if isinstance(result, dict)]
    assert len(photos) == 3
    assert all(isinstance(result, dict) or result.status_code == 429 for result in results)
    assert (await put(service, idempotency_key=next(str(i) for i in range(6)
                                                  if isinstance(results[i], dict))))["id"] in {p["id"] for p in photos}
    await service.moderate(photos[0]["id"], "rejected")
    assert (await put(service, idempotency_key="released"))["status"] == "pending"
    service.settings.upload_pending_limit = 0
    with pytest.raises(HTTPException) as exc:
        await put(service, idempotency_key="disabled")
    assert exc.value.status_code == 429


async def merge_uploader(repo):
    from recipe_creator.admin import merge_usage
    async with repo.transaction() as tx:
        source = await tx.get("users", "uploader")
        target = await tx.get("users", "owner")
        await tx.update("users", "owner", {"photo_trust": bool(source.get("photo_trust") and target.get("photo_trust"))})
        for row in await tx.list("photos", {"uploader_id": "uploader"}):
            await tx.update("photos", row["id"], {"uploader_id": "owner"})
        await merge_usage(tx, "uploader", "owner")
        await tx.update("users", "uploader", {"state": "merged", "merged_into": "owner"})


@pytest.mark.parametrize("stage", [1, 2, 3])
@pytest.mark.parametrize("mutation", ["merge", "block"])
async def test_transaction_retries_revalidate_identity(service, monkeypatch, stage, mutation):
    original = service.repo.transaction
    calls = 0
    touched = False

    @asynccontextmanager
    async def transaction():
        nonlocal calls, touched
        calls += 1
        inject = calls == stage
        try:
            async with original() as tx:
                before = await tx.get("users", "uploader")
                yield tx
                if inject:
                    after = await tx.get("users", "uploader")
                    touched = before.get("photo_access_token") != after.get("photo_access_token")
                    raise ConflictError("Concurrent user change at commit")
        except ConflictError:
            if inject:
                if mutation == "merge":
                    await merge_uploader(service.repo)
                else:
                    await service.repo.update("users", "uploader", {"state": "blocked"})
            raise

    monkeypatch.setattr(service.repo, "transaction", transaction)
    if mutation == "merge":
        photo = await put(service)
        assert photo["uploader_id"] == "owner"
        assert (await put(service))["id"] == photo["id"]
        attempts = await service.repo.list("usage", {"kind": "photo_attempt", "scope": "user:owner"})
        assert sum(row["count"] for row in attempts) == 2
        assert all(row["uploader_id"] == "owner" for row in await service.repo.list("photos"))
        assert not any(row["count"] for row in await service.repo.list("usage", {"scope": "user:uploader"}))
    else:
        with pytest.raises(HTTPException) as exc:
            await put(service)
        assert exc.value.status_code == 403
        assert await service.visible_photos(admin=True) == []
        storage = await service.repo.get("usage", STORAGE_BUCKET)
        assert not storage or storage["count"] == 0
    assert touched


async def test_merge_preserves_existing_daily_quota(service):
    service.settings.upload_daily_limit = 2
    await service.upload("recipe", "owner", upload_file(), idempotency_key="owner")
    await put(service)
    await merge_uploader(service.repo)
    with pytest.raises(HTTPException) as exc:
        await put(service, idempotency_key="over-limit")
    assert exc.value.status_code == 429
    assert sum(row["count"] for row in await service.repo.list("usage", {"scope": "user:owner"})) == 3


@pytest.mark.parametrize("mutation", ["merge", "uploader_block", "owner_block", "hidden", "deleted", "untrust"])
async def test_processing_revalidates_current_ownership_and_access(service, monkeypatch, mutation):
    await service.repo.update("users", "uploader", {"photo_trust": True})
    original = service._thread

    async def thread(function, *args):
        result = await original(function, *args)
        if function == service._process:
            if mutation == "merge":
                await merge_uploader(service.repo)
            elif mutation.endswith("_block"):
                await service.repo.update("users", mutation.removesuffix("_block"), {"state": "blocked"})
            elif mutation == "untrust":
                await service.repo.update("users", "uploader", {"photo_trust": False})
            else:
                await service.repo.update("recipes", "recipe", {"status": mutation})
        return result

    monkeypatch.setattr(service, "_thread", thread)
    if mutation in {"merge", "untrust"}:
        photo = await put(service)
        assert photo["status"] == "pending"
        assert photo["uploader_id"] == ("owner" if mutation == "merge" else "uploader")
    else:
        with pytest.raises(HTTPException) as exc:
            await put(service)
        assert exc.value.status_code in {403, 404}
        assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] == 0
        assert not list(service.root.iterdir())


@pytest.mark.parametrize("blocked_user", ["owner", "uploader"])
async def test_access_and_approval_revalidate_blocks(service, blocked_user):
    photo = await put(service)
    await service.moderate(photo["id"], "approved")
    await service.repo.update("users", blocked_user, {"state": "blocked"})
    assert await service.visible_photos() == []
    with pytest.raises(HTTPException):
        await service.image(photo["id"], admin=True)
    with pytest.raises(HTTPException):
        await service.moderate(photo["id"], "approved")
    with pytest.raises(HTTPException):
        await put(service, idempotency_key="blocked")
    if blocked_user == "uploader":
        with pytest.raises(HTTPException):
            await service.remove(photo["id"], "uploader")
        await service.moderate(photo["id"], "rejected")


async def test_hidden_recipe_retains_photo_for_recipe_restore(service):
    photo = await put(service)
    path, _ = await service.image(photo["id"], admin=True)
    await service.repo.update("recipes", "recipe", {"status": "hidden"})
    await service.cleanup()
    assert path.is_file()
    assert (await service.repo.get("photos", photo["id"]))["status"] == "pending"
    assert await service.visible_photos(admin=True) == []
    with pytest.raises(HTTPException):
        await service.image(photo["id"], admin=True)
    await service.repo.update("recipes", "recipe", {"status": "draft"})
    assert (await service.image(photo["id"], admin=True))[0] == path


async def test_user_removal_retention_is_configurable(service, monkeypatch):
    monkeypatch.setenv("RECIPE_PHOTO_RETENTION_DAYS", "2")
    photo = await put(service)
    now = datetime.now(UTC)
    await service.remove(photo["id"], "uploader")
    row = await service.repo.get("photos", photo["id"])
    deadline = datetime.fromisoformat(row["delete_after"])
    assert now + timedelta(days=2) <= deadline <= datetime.now(UTC) + timedelta(days=2)
    monkeypatch.setenv("RECIPE_PHOTO_RETENTION_DAYS", "0")
    next_photo = await put(service, idempotency_key="zero-retention")
    path, _ = await service.image(next_photo["id"], admin=True)
    await service.remove(next_photo["id"], "uploader")
    assert not path.exists()
    assert (await service.repo.get("photos", next_photo["id"]))["status"] == "deleted"
    assert (await service.repo.get("photos", photo["id"]))["delete_after"] == row["delete_after"]


async def test_rejected_retention_cleanup_and_moderation_reset(service, monkeypatch):
    monkeypatch.setenv("RECIPE_PHOTO_RETENTION_DAYS", "2")
    photo = await put(service)
    path, _ = await service.image(photo["id"], admin=True)
    await service.moderate(photo["id"], "rejected")
    await service.repo.update("photos", photo["id"], {"moderated_at": datetime.now(UTC) - timedelta(days=1)})
    await service.cleanup()
    assert path.is_file()
    await service.repo.update("photos", photo["id"], {"moderated_at": datetime.now(UTC) - timedelta(days=3)})
    await service.moderate(photo["id"], "approved")
    await service.cleanup()
    assert path.is_file()
    await service.moderate(photo["id"], "rejected")
    await service.repo.update("photos", photo["id"], {"moderated_at": datetime.now(UTC) - timedelta(days=3)})
    await service.cleanup()
    await service.cleanup()
    assert not path.exists()
    row = await service.repo.get("photos", photo["id"])
    assert row["previous_status"] == "rejected" and row["status"] == "deleted"
    assert (await service.repo.get("usage", STORAGE_BUCKET))["count"] == 0


@pytest.mark.integration
async def test_real_database_upload_visibility_quota_and_restart(tmp_path):
    url = os.getenv("RECIPE_TEST_DB_URL")
    if not url:
        pytest.skip("Set RECIPE_TEST_DB_URL for real SurrealDB photo tests")
    settings = Settings(db_url=url, db_user=os.getenv("RECIPE_TEST_DB_USER", "root"),
                        db_password=os.getenv("RECIPE_TEST_DB_PASSWORD", ""), db_namespace="recipe_tests",
                        db_database="photos_" + uuid4().hex, media_root=tmp_path, upload_daily_limit=8)
    async with Repository(settings) as repo:
        await repo.migrate()
        try:
            await repo.create("users", {"state": "active"}, "owner")
            await repo.create("users", {"state": "active"}, "uploader")
            await repo.create("recipes", {"owner_id": "owner", "title": "Meal"}, "recipe")
            service = PhotoService(repo, settings)
            requested_at = datetime.now(UTC)
            photo = await put(service)
            assert requested_at <= datetime.fromisoformat(photo["created_at"]) <= datetime.now(UTC)
            assert datetime.fromisoformat(photo["created_at"]).tzinfo == UTC
            assert await service.visible_photos(user_id="owner") == []
            async with Repository(settings) as other:
                restarted = PhotoService(other, settings)
                retried = await put(restarted)
                assert retried["id"] == photo["id"]
                assert retried["created_at"] == photo["created_at"]
                await restarted.moderate(photo["id"], "approved")
                assert (await restarted.image(photo["id"]))[1]
                results = await asyncio.gather(*(put(restarted, idempotency_key=str(i)) for i in range(3)),
                                               return_exceptions=True)
                assert all(isinstance(result, dict) for result in results), results
            rows = await repo.list("photos")
            assert (await repo.get("usage", STORAGE_BUCKET))["count"] == sum(row["stored_bytes"] for row in rows)
            assert all(row["count"] == 5 for row in await repo.list("usage", {"kind": "photo_attempt"}))
            settings.upload_pending_limit = 4
            with pytest.raises(HTTPException) as exc:
                await put(service, idempotency_key="bad", file=UploadFile(file=BytesIO(b"bad")))
            assert exc.value.status_code == 422
            settings.upload_daily_limit = 6
            with pytest.raises(HTTPException) as exc:
                await put(PhotoService(repo, settings), idempotency_key="limited")
            assert exc.value.status_code == 429
            assert all(row["count"] == 7 for row in await repo.list("usage", {"kind": "photo_attempt"}))
            await repo.update("recipes", "recipe", {"deleted_at": datetime.now(UTC)})
            await service.cleanup()
            assert await service.visible_photos(admin=True) == []
            assert (await repo.get("usage", STORAGE_BUCKET))["count"] > 0
            for row in await repo.list("photos"):
                await repo.update("photos", row["id"], {"delete_after": datetime.now(UTC) - timedelta(seconds=1)})
            await service.cleanup()
            assert (await repo.get("usage", STORAGE_BUCKET))["count"] == 0
        finally:
            await repo._connection.query(f"REMOVE DATABASE `{settings.db_database}`;")
