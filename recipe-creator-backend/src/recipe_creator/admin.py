from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .identity import invalidate_pairings
from .schemas import AdminUsersResponse, OwnerResult, validate_classifiers
from .recipes import _save_revision, invalidate_catalog
from .jobs import enqueue_enrichment
from .security import (
    all_rows, authorize, digest, now, public_user, record_id, require_admin, retry_transaction,
)
from .views import reconcile_merged_users


router = APIRouter(prefix="/admin")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserInput(Input):
    trusted: bool | None = None
    state: Literal["active", "blocked"] | None = None


class OwnerInput(Input):
    owner_id: str | None
    expected_revision: int = Field(ge=1)


class RestoreInput(Input):
    revision_id: str
    expected_revision: int = Field(ge=1)


class MergePreviewInput(Input):
    source_id: str
    target_id: str


class MergeInput(MergePreviewInput):
    confirm: Literal[True]


class ModerationInput(Input):
    state: Literal["pending", "approved", "rejected"]


async def audit(tx, context, action, target, **data):
    await tx.create("audit", {"actor_id": context.user["id"] if context.user else None,
                              "action": action, "target": target,
                              "device_id": context.device["id"] if context.device else None, **data})



@router.get("/users", response_model=AdminUsersResponse)
async def users(request: Request, q: str = Query(default="", max_length=100), start: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=500), eligible_owner: bool = False, eligible_merge: bool = False):
    await require_admin(request)
    rows = await all_rows(request.app.state.repo, "users")
    if eligible_owner:
        rows = [row for row in rows if row["state"] == "active" and not row.get("merged_into")]
    elif eligible_merge:
        rows = [row for row in rows if row["state"] != "merged" and not row.get("merged_into")]
    rows.sort(key=lambda row: (row["display_name"].casefold(), row["id"]))
    rows = [row for row in rows if q.casefold() in row["display_name"].casefold() or q.casefold() in row["id"].casefold()]
    devices = await all_rows(request.app.state.repo, "devices")
    last_login = {}
    for device in devices:
        user_id, timestamp = device.get("user_id"), device.get("last_used_at")
        if user_id and timestamp and (user_id not in last_login or timestamp > last_login[user_id]):
            last_login[user_id] = timestamp
    items = [{**public_user(row), "last_login_at": last_login.get(row["id"])} for row in rows[start:start + limit]]
    return {"items": items, "users": items, "total": len(rows), "start": start, "limit": limit, "has_more": start + limit < len(rows)}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserInput, request: Request):
    user_id = record_id(user_id, "users")

    async def update(tx):
        context = await authorize(request, tx, admin=True)
        user = await tx.get("users", user_id)
        if not user:
            raise HTTPException(404, "User not found")
        if user.get("merged_into"):
            raise HTTPException(409, "Update the surviving profile")
        changes = {}
        if body.trusted is not None:
            changes["trusted"] = body.trusted
        if body.state is not None:
            changes["state"] = body.state
        result = await tx.update("users", user_id, changes)
        if result["state"] == "blocked":
            await invalidate_pairings(tx, user_ids=(user_id,))
        await audit(tx, context, "user.update", user_id, changes=changes)
        return result

    return {"user": public_user(await retry_transaction(request.app.state.repo, update))}


@router.post("/recipes/{recipe_id}/owner", response_model=OwnerResult)
async def assign_owner(recipe_id: str, body: OwnerInput, request: Request):
    recipe_id = record_id(recipe_id, "recipes")
    owner_id = record_id(body.owner_id, "users") if body.owner_id is not None else None

    async def assign(tx):
        context = await authorize(request, tx, admin=True)
        recipe = await tx.get("recipes", recipe_id)
        if not recipe:
            raise HTTPException(404, "Recipe not found")
        if recipe["revision"] != body.expected_revision:
            raise HTTPException(409, "Revision changed")
        owner = await tx.get("users", owner_id) if owner_id else None
        if owner_id and (not owner or owner["state"] != "active" or owner.get("merged_into")):
            raise HTTPException(422, "Active owner not found")
        if owner:
            await tx.update("users", owner["id"], {})
        result = await _save_revision(tx, recipe_id, body.expected_revision, {"owner_id": owner["id"] if owner else None},
                                               actor_id=context.user["id"] if context.user else None, reason="admin.owner")
        await audit(tx, context, "recipe.owner", recipe_id, previous_owner_id=recipe["owner_id"], owner_id=result["owner_id"])
        return {"id": result["id"], "owner_id": result["owner_id"], "author_name": owner["display_name"] if owner else None, "revision": result["revision"]}

    result = await retry_transaction(request.app.state.repo, assign)
    invalidate_catalog(request.app.state.repo)
    return result


@router.get("/recipes/{recipe_id}/revisions")
async def revisions(recipe_id: str, request: Request):
    await require_admin(request)
    recipe_id = record_id(recipe_id, "recipes")
    items = await all_rows(request.app.state.repo, "revisions", {"recipe_id": recipe_id})
    return {"items": items, "revisions": items}


@router.post("/recipes/{recipe_id}/restore")
async def restore(recipe_id: str, body: RestoreInput, request: Request):
    recipe_id = record_id(recipe_id, "recipes")
    revision_id = record_id(body.revision_id, "revisions")

    async def apply(tx):
        context = await authorize(request, tx, admin=True)
        recipe = await tx.get("recipes", recipe_id)
        revision = await tx.get("revisions", revision_id)
        if not recipe or not revision or revision["recipe_id"] != recipe_id:
            raise HTTPException(404, "Revision not found")
        if recipe["revision"] != body.expected_revision:
            raise HTTPException(409, "Revision changed")
        excluded = {"id", "created_at", "updated_at", "revision", "owner_id", "deleted_at",
                    "admin", "is_admin", "trusted", "photo_trust", "photo_trusted", "author_name",
                    "can_edit", "total_views", "unique_viewers", "idempotency_user_id", "idempotency_snapshot"}
        content = {key: value for key, value in revision["content"].items() if key not in excluded}
        try:
            validate_classifiers(content.get("tags", []))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        content["deleted_at"] = None
        result = await _save_revision(tx, recipe_id, body.expected_revision, content,
                                               actor_id=context.user["id"] if context.user else None, reason="admin.restore")
        await enqueue_enrichment(tx, result)
        await audit(tx, context, "recipe.restore", recipe_id, revision_id=revision_id)
        return {"id": result["id"], "revision": result["revision"]}

    result = await retry_transaction(request.app.state.repo, apply)
    invalidate_catalog(request.app.state.repo)
    return result


async def merge_users(tx, source_id, target_id):
    source = await tx.get("users", source_id)
    target = await tx.get("users", target_id)
    if not source or not target:
        raise HTTPException(404, "User not found")
    if source_id == target_id:
        raise HTTPException(422, "Choose two distinct profiles")
    if target.get("merged_into"):
        raise HTTPException(409, "Target is already merged")
    if source.get("merged_into") and source["merged_into"] != target_id:
        raise HTTPException(409, "Source is already merged elsewhere")
    return source, target


@router.post("/merge/preview")
async def merge_preview_body(body: MergePreviewInput, request: Request):
    return await merge_preview(request, body.source_id, body.target_id)


@router.get("/merge/preview")
async def merge_preview(request: Request, source_id: str, target_id: str):
    await require_admin(request)
    source_id, target_id = record_id(source_id, "users"), record_id(target_id, "users")
    repo = request.app.state.repo
    source, target = await merge_users(repo, source_id, target_id)
    profiles = []
    for user in (source, target):
        counts = {}
        for table, field in (("recipes", "owner_id"), ("photos", "uploader_id"), ("devices", "user_id")):
            counts[table] = len(await all_rows(repo, table, {field: user["id"]}))
        profiles.append({"user": public_user(user), **counts})
    return {"source": profiles[0], "target": profiles[1],
            "result": {"trusted": source["trusted"] and target["trusted"],
                       "state": "blocked" if "blocked" in {source["state"], target["state"]} else "active"}}


async def merge_usage(tx, source_id, target_id):
    source_scope, target_scope = "user:" + source_id, "user:" + target_id
    for row in await all_rows(tx, "usage"):
        if row["scope"] not in {source_scope, digest(source_scope), source_id}:
            continue
        expiry = row.get("expires_at")
        date = datetime.fromisoformat(expiry.replace("Z", "+00:00")) if isinstance(expiry, str) else expiry
        if row["kind"] == "ai" and date:
            scope = digest(target_scope)
            day = (date - timedelta(days=1)).date().isoformat()
            target_key = digest(f"ai:{day}:{scope}")
        elif row["kind"] == "photo_attempt" and date:
            scope = target_scope
            day = (date - timedelta(days=2)).date().isoformat()
            target_key = "photo_attempt_" + digest(day + "\0" + scope)
        else:
            scope = target_scope if row["scope"] == source_scope else target_id
            target_key = digest(f"merged:{row['kind']}:{scope}:{expiry}")
        destination = await tx.get("usage", target_key)
        if destination:
            await tx.compare_and_swap("usage", target_key, destination["revision"], {"count": destination["count"] + row["count"]})
        else:
            await tx.create("usage", {"kind": row["kind"], "scope": scope, "count": row["count"], "expires_at": expiry}, id=target_key)
        await tx.compare_and_swap("usage", row["id"], row["revision"], {"count": 0, "merged_into_bucket": target_key})


@router.post("/merge")
async def merge(body: MergeInput, request: Request):
    source_id, target_id = record_id(body.source_id, "users"), record_id(body.target_id, "users")

    async def apply(tx):
        context = await authorize(request, tx, admin=True)
        source, target = await merge_users(tx, source_id, target_id)
        if source.get("merged_into") == target_id:
            return {"user": public_user(target), "already_merged": True}
        await reconcile_merged_users(tx, source, target)
        restrictions = {"trusted": source["trusted"] and target["trusted"],
                        "state": "blocked" if "blocked" in {source["state"], target["state"]} else "active"}
        target = await tx.update("users", target_id, restrictions)
        for table, field in (("recipes", "owner_id"), ("photos", "uploader_id"), ("devices", "user_id")):
            rows = await all_rows(tx, table, {field: source_id})
            for row in rows:
                await tx.update(table, row["id"], {field: target_id})
        await invalidate_pairings(tx, user_ids=(source_id, target_id))
        await merge_usage(tx, source_id, target_id)
        await tx.update("users", source_id, {"merged_into": target_id, "state": "merged", "trusted": False, "is_admin": False})
        await audit(tx, context, "user.merge", target_id, source_id=source_id, target_id=target_id, restrictions=restrictions)
        return {"user": public_user(target), "already_merged": False}

    result = await retry_transaction(request.app.state.repo, apply)
    invalidate_catalog(request.app.state.repo)
    return result


@router.get("/audit")
async def audit_events(request: Request, start: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500)):
    await require_admin(request)
    items = await request.app.state.repo.list("audit", start=start, limit=limit)
    return {"items": items, "events": items}


@router.get("/photos")
async def photos(request: Request):
    await require_admin(request)
    items = await request.app.state.photos.visible_photos(admin=True)
    return {"items": items, "photos": items}


@router.post("/photos/{photo_id}")
@router.post("/photos/{photo_id}/moderate")
async def moderate_photo(photo_id: str, body: ModerationInput, request: Request):
    from .photos import _public
    service = request.app.state.photos
    photo_id = record_id(photo_id, 'photos')

    async def change(tx):
        context = await authorize(request, tx, admin=True)
        row = await tx.get('photos', photo_id)
        if not row:
            raise HTTPException(404, 'Photo not found')
        await service._recipe(tx, row['recipe_id'], touch=True)
        if body.state == 'approved':
            await service._active_user(tx, row['uploader_id'], touch=True)
        if row['status'] not in {'pending', 'approved', 'rejected'}:
            raise HTTPException(409, 'Photo is not ready for moderation')
        result = await tx.update('photos', photo_id, {'status': body.state, 'moderated_at': now()})
        await audit(tx, context, 'photo.moderate', photo_id, previous_status=row['status'], status=body.state)
        return _public(result)

    return await retry_transaction(request.app.state.repo, change)
