from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
import re
import secrets

from fastapi import HTTPException, Request

from .security import DEVICE_COOKIE, Context, client_ip, digest, resolve_context, retry_transaction
from surreal_orm import SurrealDBConnectionManager as Connections

from .models import ViewerCredential, ViewIPWindow


VIEWER_COOKIE = "recipe_viewer"
VIEWER_TTL_SECONDS = 365 * 86400
VIEWER_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,160}")
CATEGORIES = ("trusted", "named", "anonymous")

def utcnow() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime | str) -> datetime:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _viewer_id(value: str) -> str:
    if not isinstance(value, str) or not VIEWER_ID_PATTERN.fullmatch(value):
        raise RuntimeError("Corrupt viewer identity reference")
    return value


async def canonical_viewer(tx, viewer_id: str) -> dict:
    seen: set[str] = set()
    current_id = _viewer_id(viewer_id)
    while current_id not in seen:
        seen.add(current_id)
        viewer = await tx.get("viewers", current_id)
        if not viewer:
            raise RuntimeError("Viewer identity is missing")
        target = viewer.get("merged_into")
        if not target:
            return viewer
        current_id = _viewer_id(target)
    raise RuntimeError("Viewer identity alias cycle")


async def _device_credential_is_unusable(tx, request: Request, context: Context) -> bool:
    if context.user:
        return False
    secret = request.cookies.get(DEVICE_COOKIE, "")
    if not secret or len(secret) > 128:
        return False
    return bool(await tx.list("devices", {"secret_hash": digest(secret)}, limit=1))


@dataclass
class ViewerResolution:
    viewer_id: str | None
    viewer: dict | None
    context: Context
    category: str
    cookie_secret: str | None = None
    credential: dict | None = None
    candidate_secret: str | None = None
    candidate_viewer_id: str | None = None
    suppress: bool = False


async def _cookie_viewer(tx, request: Request, timestamp: datetime) -> tuple[dict | None, dict | None, str | None, bool]:
    secret = request.cookies.get(VIEWER_COOKIE, "")
    if not secret or len(secret) > 128:
        return None, None, None, False
    credential = await tx.get("viewer_credentials", digest(secret))
    if not credential:
        return None, None, None, False
    if _timestamp(credential["expires_at"]) <= timestamp:
        return None, credential, secret, True
    return await canonical_viewer(tx, credential["viewer_id"]), credential, secret, False


async def _bind_profile_viewer(tx, user: dict, guest: dict | None) -> dict:
    configured_id = user.get("viewer_id")
    if configured_id:
        profile_viewer = await canonical_viewer(tx, configured_id)
        if profile_viewer["id"] != configured_id:
            user = await tx.update("users", user["id"], {"viewer_id": profile_viewer["id"]})
    elif guest and not guest.get("user_id"):
        profile_viewer = await tx.compare_and_swap(
            "viewers", guest["id"], guest["revision"], {"user_id": user["id"]}
        )
        user = await tx.update("users", user["id"], {"viewer_id": profile_viewer["id"]})
    else:
        viewer_id = digest("user:" + user["id"])
        profile_viewer = await tx.get("viewers", viewer_id)
        if not profile_viewer:
            profile_viewer = await tx.create("viewers", {"user_id": user["id"]}, id=viewer_id)
        elif profile_viewer.get("user_id") != user["id"]:
            raise RuntimeError("Profile viewer identity belongs to another user")
        user = await tx.update("users", user["id"], {"viewer_id": viewer_id})
    if guest and not guest.get("user_id") and guest["id"] != profile_viewer["id"]:
        await merge_viewers(tx, guest["id"], profile_viewer["id"])
        profile_viewer = await canonical_viewer(tx, profile_viewer["id"])
    return profile_viewer


async def resolve_viewer(tx, request: Request, timestamp: datetime) -> ViewerResolution:
    context = await resolve_context(request, tx)
    cookie_viewer, credential, cookie_secret, bad_tracking_credential = await _cookie_viewer(tx, request, timestamp)
    if context.user:
        viewer = await _bind_profile_viewer(tx, context.user, cookie_viewer)
        category = "trusted" if context.user.get("trusted") is True else "named"
        return ViewerResolution(viewer["id"], viewer, context, category, cookie_secret, credential)
    if bad_tracking_credential or await _device_credential_is_unusable(tx, request, context):
        return ViewerResolution(None, None, context, "anonymous", suppress=True)
    if cookie_viewer:
        return ViewerResolution(cookie_viewer["id"], cookie_viewer, context, "anonymous", cookie_secret, credential)
    secret = secrets.token_urlsafe(32)
    viewer_id = secrets.token_urlsafe(24)
    return ViewerResolution(viewer_id, None, context, "anonymous", candidate_secret=secret,
                            candidate_viewer_id=viewer_id)


async def persist_candidate_viewer(tx, resolution: ViewerResolution, timestamp: datetime) -> dict:
    if not resolution.candidate_secret or not resolution.candidate_viewer_id:
        if not resolution.viewer:
            raise RuntimeError("Viewer resolution has no identity")
        return resolution.viewer
    viewer = await tx.create("viewers", {}, id=resolution.candidate_viewer_id)
    credential = await tx.create(
        "viewer_credentials",
        {"viewer_id": viewer["id"], "expires_at": timestamp + timedelta(seconds=VIEWER_TTL_SECONDS)},
        id=digest(resolution.candidate_secret),
    )
    resolution.viewer = viewer
    resolution.credential = credential
    resolution.cookie_secret = resolution.candidate_secret
    return viewer


async def merge_viewers(tx, source_id: str, target_id: str) -> None:
    source = await canonical_viewer(tx, source_id)
    target = await canonical_viewer(tx, target_id)
    if source["id"] == target["id"]:
        return
    if source.get("merged_into"):
        raise RuntimeError("Viewer identity is already merged elsewhere")
    source = await tx.compare_and_swap(
        "viewers", source["id"], source["revision"], {"merged_into": target["id"]}
    )
    await tx.compare_and_swap("viewers", target["id"], target["revision"], {})
    while memberships := await tx.list("recipe_viewers", {"viewer_id": source["id"]}, limit=1000):
        for membership in memberships:
            recipe_id = membership["recipe_id"]
            target_membership_id = digest(recipe_id + "\0" + target["id"])
            target_membership = await tx.get("recipe_viewers", target_membership_id)
            if not target_membership:
                await tx.create("recipe_viewers", {
                    "recipe_id": recipe_id,
                    "viewer_id": target["id"],
                    "first_view_at": membership["first_view_at"],
                    "last_counted_at": membership["last_counted_at"],
                    "first_category": membership["first_category"],
                }, id=target_membership_id)
            else:
                source_first = (_timestamp(membership["first_view_at"]), source["id"])
                target_first = (_timestamp(target_membership["first_view_at"]), target["id"])
                winner, loser = ((membership, target_membership) if source_first < target_first
                                 else (target_membership, membership))
                await tx.compare_and_swap("recipe_viewers", target_membership_id,
                                          target_membership["revision"], {
                    "first_view_at": winner["first_view_at"],
                    "first_category": winner["first_category"],
                    "last_counted_at": max(_timestamp(membership["last_counted_at"]),
                                           _timestamp(target_membership["last_counted_at"])),
                })
                stats = await tx.get("recipe_view_stats", recipe_id)
                if not stats or stats["unique_viewers"] < 1 or stats[loser["first_category"] + "_unique_viewers"] < 1:
                    raise RuntimeError("Recipe view aggregates are inconsistent")
                await tx.compare_and_swap("recipe_view_stats", recipe_id, stats["revision"], {
                    "unique_viewers": stats["unique_viewers"] - 1,
                    loser["first_category"] + "_unique_viewers":
                        stats[loser["first_category"] + "_unique_viewers"] - 1,
                })
            await tx.delete("recipe_viewers", membership["id"])


async def reconcile_merged_users(tx, source: dict, target: dict) -> str | None:
    source_id, target_id = source.get("viewer_id"), target.get("viewer_id")
    source_viewer = await canonical_viewer(tx, source_id) if source_id else None
    target_viewer = await canonical_viewer(tx, target_id) if target_id else None
    if source_viewer and not target_viewer:
        target_viewer = await tx.compare_and_swap(
            "viewers", source_viewer["id"], source_viewer["revision"], {"user_id": target["id"]}
        )
    elif source_viewer and target_viewer and source_viewer["id"] != target_viewer["id"]:
        await merge_viewers(tx, source_viewer["id"], target_viewer["id"])
    survivor = target_viewer or source_viewer
    if survivor:
        await tx.update("users", target["id"], {"viewer_id": survivor["id"]})
        await tx.update("users", source["id"], {"viewer_id": survivor["id"]})
        return survivor["id"]
    return None


def _counts(stats: dict | None) -> dict[str, int]:
    return {
        "total_views": stats["total_views"] if stats else 0,
        "unique_viewers": stats["unique_viewers"] if stats else 0,
    }


def _normalized_ip(request: Request) -> str | None:
    try:
        address = ip_address(client_ip(request))
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.compressed


async def _ip_entries(tx, row: dict | None, timestamp: datetime) -> list[dict]:
    cutoff = timestamp - timedelta(seconds=3600)
    canonical: dict[str, datetime] = {}
    for entry in row.get("entries", []) if row else []:
        counted_at = _timestamp(entry["last_counted_at"])
        if counted_at <= cutoff:
            continue
        viewer = await canonical_viewer(tx, entry["viewer_id"])
        previous = canonical.get(viewer["id"])
        if previous is None or counted_at > previous:
            canonical[viewer["id"]] = counted_at
    return [{"viewer_id": viewer_id, "last_counted_at": counted_at}
            for viewer_id, counted_at in sorted(canonical.items())]


async def _registration_attempt(tx, request: Request, recipe_id: str):
    timestamp = utcnow()
    try:
        recipe = await tx.get("recipes", recipe_id)
    except ValueError:
        raise HTTPException(404, "Recipe not found") from None
    if not recipe or recipe.get("deleted_at") or recipe.get("status") != "published":
        raise HTTPException(404, "Recipe not found")

    resolution = await resolve_viewer(tx, request, timestamp)
    stats = await tx.get("recipe_view_stats", recipe_id)
    if resolution.suppress:
        return _counts(stats), None, 0
    membership_id = digest(recipe_id + "\0" + resolution.viewer_id)
    membership = await tx.get("recipe_viewers", membership_id)
    if membership and not stats:
        raise RuntimeError("Recipe view membership exists without aggregates")
    if membership and timestamp < _timestamp(membership["last_counted_at"]) + timedelta(seconds=3600):
        remaining = 0
        if resolution.cookie_secret and resolution.credential:
            remaining = max(0, int((_timestamp(resolution.credential["expires_at"]) - timestamp).total_seconds()))
        return _counts(stats), resolution.cookie_secret if remaining else None, remaining

    normalized_ip = _normalized_ip(request)
    if normalized_ip is None:
        return _counts(stats), None, 0
    ip_key = digest(normalized_ip)
    ip_row = await tx.get("view_ip_windows", ip_key)
    entries = await _ip_entries(tx, ip_row, timestamp)
    existing_ip_entry = next((entry for entry in entries if entry["viewer_id"] == resolution.viewer_id), None)
    if existing_ip_entry is None and len(entries) >= 10:
        return _counts(stats), None, 0

    viewer = await persist_candidate_viewer(tx, resolution, timestamp)
    viewer = await tx.compare_and_swap("viewers", viewer["id"], viewer["revision"], {})
    entries = [entry for entry in entries if entry["viewer_id"] != viewer["id"]]
    entries.append({"viewer_id": viewer["id"], "last_counted_at": timestamp})
    expiry = max(entry["last_counted_at"] for entry in entries) + timedelta(seconds=3600)
    if ip_row:
        await tx.compare_and_swap("view_ip_windows", ip_key, ip_row["revision"],
                                  {"entries": entries, "expires_at": expiry})
    else:
        await tx.create("view_ip_windows", {"entries": entries, "expires_at": expiry}, id=ip_key)

    is_unique = membership is None
    if membership:
        await tx.compare_and_swap("recipe_viewers", membership_id, membership["revision"],
                                  {"last_counted_at": timestamp})
    else:
        await tx.create("recipe_viewers", {
            "recipe_id": recipe_id,
            "viewer_id": viewer["id"],
            "first_view_at": timestamp,
            "last_counted_at": timestamp,
            "first_category": resolution.category,
        }, id=membership_id)

    changes = {
        "recipe_id": recipe_id,
        "total_views": (stats["total_views"] if stats else 0) + 1,
        "unique_viewers": (stats["unique_viewers"] if stats else 0) + int(is_unique),
    }
    for category in CATEGORIES:
        changes[category + "_views"] = (stats[category + "_views"] if stats else 0) + int(category == resolution.category)
        changes[category + "_unique_viewers"] = (
            (stats[category + "_unique_viewers"] if stats else 0)
            + int(is_unique and category == resolution.category)
        )
    if stats:
        stats = await tx.compare_and_swap("recipe_view_stats", recipe_id, stats["revision"], changes)
    else:
        stats = await tx.create("recipe_view_stats", changes, id=recipe_id)

    cookie_secret = resolution.cookie_secret
    if resolution.credential and cookie_secret:
        credential = resolution.credential
        await tx.compare_and_swap(
            "viewer_credentials", credential["id"], credential["revision"],
            {"expires_at": timestamp + timedelta(seconds=VIEWER_TTL_SECONDS)},
        )
    return _counts(stats), cookie_secret, VIEWER_TTL_SECONDS if cookie_secret else 0


async def register_view(request: Request, recipe_id: str) -> tuple[dict[str, int], str | None, int]:
    async def attempt(tx):
        return await _registration_attempt(tx, request, recipe_id)

    return await retry_transaction(request.app.state.repo, attempt, attempts=20)


async def _expired_tracking_ids(repo, model, timestamp: datetime) -> list[str]:
    await repo.connect()
    async with Connections.using(repo._name):
        rows = await model.objects().select("id").filter(expires_at__lte=timestamp).order_by("id").limit(1000).exec()
    return [str(row["id"] if isinstance(row, dict) else row.id).partition(":")[2]
            or str(row["id"] if isinstance(row, dict) else row.id) for row in rows]


async def cleanup_view_tracking(repo, timestamp: datetime) -> int:
    deleted = 0
    for table, model in (("viewer_credentials", ViewerCredential), ("view_ip_windows", ViewIPWindow)):
        while ids := await _expired_tracking_ids(repo, model, timestamp):
            async def remove(tx):
                removed = 0
                for record_id in ids:
                    row = await tx.get(table, record_id)
                    if row and _timestamp(row["expires_at"]) <= timestamp:
                        await tx.delete(table, record_id)
                        removed += 1
                return removed
            deleted += await retry_transaction(repo, remove)
    return deleted
