import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import re
from time import monotonic
from uuid import uuid5, NAMESPACE_URL

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from logly import logger
from surreal_orm import Q, SurrealDBConnectionManager as Connections

from . import ai
from .ingredients import ingredient_hash
from .jobs import enqueue_enrichment, enrichment_hash
from .models import EnrichmentJob, Photo, Recipe as RecipeModel, User
from .photos import PUBLIC_FIELDS
from surreal_sdk.protocol.cbor import RecordId
from .schemas import (ParseRequest, ParseResult, Recipe, RecipeDraft, RecipeUpdate, MEAL_CLASSIFIERS,
                      tag_key, RecipeListResponse, TagCatalog, Tag, RecipeLookupRequest, RecipeLookupResponse,
                      IngredientLinesRequest, IngredientLinesResult)
from .security import authorize, client_ip, get_context, now, rate_limit, require_user, retry_transaction


router = APIRouter()
CATEGORIES = MEAL_CLASSIFIERS
AUTHORED = ("original_text", "quantity", "quantity_max", "unit", "name", "preparation", "optional")
DERIVED = ("grams", "grams_range", "grams_estimate", "grams_provenance", "grams_input_hash", "grams_error", "grams_confirmed")
SUMMARY_FIELDS = ("id", "title", "string::slice(description, 0, 300) AS description", "tags", "owner_id", "owner_id.display_name AS author_name")


def _id(value, table):
    return str(value).removeprefix(table + ":") if value is not None else None


def _live(recipe):
    return recipe is not None and not recipe.get("deleted_at") and recipe.get("status") == 'published'


def _editable(recipe, context):
    return context.admin or bool(context.user and recipe.get("owner_id") and recipe["owner_id"] == context.user["id"])


def _group(recipe):
    return next((category for category in CATEGORIES if category in {tag_key(tag) for tag in recipe["tags"]}), "Other")


def search_terms(query, known_tags):
    words, tags, unknown, fields = [], [], [], []
    for token in filter(None, query.split(" ")):
        if token.startswith("-"):
            token = token[1:]
            if ":" not in token:
                continue
        if ":" in token:
            field, value = token.split(":")[:2]
            if field != "tag":
                fields.append(field)
            elif value:
                (tags if tag_key(value) in {tag_key(tag) for tag in known_tags} else unknown).append(tag_key(value))
        else:
            words.append(token)
    errors = []
    if fields:
        errors.append("Unknown search fields: " + ", ".join(fields))
    if unknown:
        errors.append("Tags not found: " + ", ".join(unknown))
    return " ".join(words).lower(), tags, errors


async def _project(repo, fields, condition=None):
    await repo.connect()
    rows, offset = [], 0
    async with Connections.using(repo._name):
        while True:
            query = RecipeModel.objects().select(*fields).filter(deleted_at=None, status="published")
            if condition is not None:
                query.filter(condition)
            page = await query.order_by("id").offset(offset).limit(1000).exec()
            for row in page:
                value = row if isinstance(row, dict) else repo._output(row)
                rows.append(value)
            if len(page) < 1000:
                return rows
            offset += len(page)


def invalidate_catalog(repo):
    repo._recipe_catalog = None
    repo._recipe_search = {}


async def _catalog(repo):
    if not hasattr(repo, "_recipe_catalog_lock"):
        repo._recipe_catalog_lock = asyncio.Lock()
    async with repo._recipe_catalog_lock:
        cached = getattr(repo, "_recipe_catalog", None)
        if cached and cached[0] > monotonic():
            return cached[1]
        rows = await _project(repo, SUMMARY_FIELDS)
        summaries = [{"id": _id(row["id"], "recipes"), "title": row.get("title", ""),
                      "description": row.get("description", ""), "tags": row.get("tags", []),
                      "owner_id": _id(row.get("owner_id"), "users"), "author_name": row.get("author_name")}
                     for row in rows]
        order = {name: index for index, name in enumerate((*CATEGORIES, "Other"))}
        summaries.sort(key=lambda item: (order[_group(item)], item["title"].lower(), item["id"]))
        repo._recipe_catalog = (monotonic() + 15, summaries)
        repo._recipe_search = {}
        return summaries


async def _text_matches(repo, text):
    cache = getattr(repo, "_recipe_search", {})
    cached = cache.get(text)
    if cached and cached[0] > monotonic():
        return cached[1]
    pattern = "(?i)" + re.escape(text)
    condition = Q(title__regex=pattern) | Q(description__regex=pattern) | Q(**{"payload.directions__regex": pattern})
    rows = await _project(repo, ("id",), condition)
    ids = {_id(row["id"], "recipes") for row in rows}
    if len(cache) >= 128:
        cache.pop(next(iter(cache)))
    cache[text] = (monotonic() + 15, ids)
    repo._recipe_search = cache
    return ids


def _revalidate(request, response, result, *, private=False):
    encoded = jsonable_encoder(result)
    etag = 'W/"' + sha256(json.dumps(encoded, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest() + '"'
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache" if private else "public, no-cache"
    if private:
        response.headers["Vary"] = "Cookie"
    candidates = [value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")]
    if "*" in candidates or etag.removeprefix("W/") in candidates:
        return Response(status_code=304, headers=dict(response.headers))
    return encoded


async def summary_thumbnails(request, items):
    result = []
    for item in items:
        photos = await request.app.state.photos.visible_photos(recipe_id=item['id'])
        approved = sorted(photo['id'] for photo in photos if photo.get('status') == 'approved')
        result.append({**item, 'thumbnail_photo_id': approved[0] if approved else None})
    return result


async def filter_summaries(repo, catalog, q, selected_tags, owner_id=None, *, candidates=None, text_match_ids=None):
    known = {tag_key(tag) for item in catalog for tag in item["tags"]}
    text, legacy_tags, errors = search_terms(q, known)
    selected = {tag_key(tag) for tag in selected_tags}
    unknown = selected - known
    if unknown:
        errors.append("Tags not found: " + ", ".join(sorted(unknown)))
    matches = (text_match_ids if text_match_ids is not None else await _text_matches(repo, text)) if text else None
    filtered = [item for item in (catalog if candidates is None else candidates)
                if not unknown and (matches is None or item["id"] in matches)
                and selected <= {tag_key(tag) for tag in item["tags"]}
                and (not legacy_tags or set(legacy_tags) & {tag_key(tag) for tag in item["tags"]})
                and (owner_id is None or item["owner_id"] == _id(owner_id, "users"))]
    return filtered, errors


@router.post("/recipes/lookup", response_model=RecipeLookupResponse)
async def lookup_recipes(body: RecipeLookupRequest, request: Request):
    repo = request.app.state.repo
    ids = list(dict.fromkeys(_id(value, "recipes") for value in body.ids))
    if not ids:
        return {"items": [], "unavailable_ids": []}
    # Bounded candidate retrieval, not a collection scan or stale summary cache.
    candidates, unavailable, text_matches = [], [], set()
    text, _, _ = search_terms(body.q, set())
    for recipe_id in ids:
        row = await repo.get("recipes", recipe_id)
        if not row or row.get("status") != "published" or row.get("deleted_at"):
            unavailable.append(recipe_id)
            continue
        if text in " ".join(str(row.get(field, "")) for field in ("title", "description", "directions")).lower():
            text_matches.add(recipe_id)
        owner = await repo.get("users", row["owner_id"]) if row.get("owner_id") else None
        candidates.append({"id": recipe_id, "title": row.get("title", ""),
                           "description": row.get("description", "")[:300], "tags": row.get("tags", []),
                           "owner_id": row.get("owner_id"), "author_name": owner["display_name"] if owner else None})
    catalog = await _catalog(repo) if body.q or body.tags else candidates
    items, _ = await filter_summaries(repo, catalog, body.q, body.tags, candidates=candidates, text_match_ids=text_matches)
    return {"items": await summary_thumbnails(request, items), "unavailable_ids": unavailable}


@router.get("/recipes", response_model=RecipeListResponse)
async def list_recipes(request: Request, response: Response, q: str = Query(default="", max_length=2000),
                       owner_id: str | None = Query(default=None, max_length=166, pattern=r"^(users:)?[A-Za-z0-9_-]{1,160}$"),
                       offset: int = Query(default=0, ge=0, le=1_000_000),
                       limit: int = Query(default=60, ge=1, le=100),
                       tag: list[Tag] = Query(default=[], max_length=50)):
    repo = request.app.state.repo
    catalog = await _catalog(repo)
    filtered, errors = await filter_summaries(repo, catalog, q, tag, owner_id)
    items = await summary_thumbnails(request, filtered[offset:offset + limit])
    return _revalidate(request, response, {"items": items, "offset": offset, "limit": limit, "total": len(filtered),
            "has_more": offset + limit < len(filtered), "errors": errors,
            "groups": [{"name": name, "items": [item for item in items if _group(item) == name]}
                       for name in (*CATEGORIES, "Other") if any(_group(item) == name for item in items)]})


@router.get("/tags", response_model=TagCatalog)
async def list_tags(request: Request, response: Response):
    catalog = await _catalog(request.app.state.repo)
    spellings = sorted({tag for item in catalog for tag in item["tags"]}, key=lambda tag: (tag_key(tag), tag))
    representatives = {}
    for tag in spellings:
        representatives.setdefault(tag_key(tag), tag_key(tag) if tag_key(tag) in MEAL_CLASSIFIERS else tag)
    return _revalidate(request, response, {"tags": list(representatives.values()), "classifier_tags": list(MEAL_CLASSIFIERS)})


def _stable_id(seed, kind, index):
    return uuid5(NAMESPACE_URL, f"recipe-creator:{seed}:{kind}:{index}").hex


def _groups_output(groups, seed):
    result = []
    for gi, group in enumerate(groups):
        gid = group.get("id") or _stable_id(seed, "group", gi)
        rows = []
        for ii, ingredient in enumerate(group.get("ingredients", [])):
            item = {key: ingredient.get(key, False if key == "optional" else None if key in {"quantity", "quantity_max"} else "") for key in AUTHORED}
            item["original_text"] = ingredient.get("original_text", ingredient.get("text", ""))
            for key in ("quantity", "quantity_max"):
                if item[key] is not None:
                    item[key] = str(item[key])
            item["unit"] = item["unit"] or ""
            item["id"] = ingredient.get("id") or _stable_id(seed, gid, ii)
            grams = ingredient.get("grams")
            provenance = ingredient.get("grams_provenance", "")
            basis = provenance.get("basis", provenance.get("source", "")) if isinstance(provenance, dict) else provenance
            if isinstance(grams, (int, float)):
                grams = {"amount": grams, "estimated": ingredient.get("grams_estimate", False), "basis": basis}
            elif isinstance(grams, dict):
                grams = deepcopy(grams)
                grams.setdefault("estimated", ingredient.get("grams_estimate", True))
                grams.setdefault("basis", basis)
            bounds = ingredient.get("grams_range")
            if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
                grams = grams or {"estimated": ingredient.get("grams_estimate", True), "basis": basis}
                grams.setdefault("low", bounds[0])
                grams.setdefault("high", bounds[1])
            item["grams"] = grams
            rows.append(item)
        result.append({"id": gid, "name": group.get("name", group.get("title", "")), "ingredients": rows})
    return result


def _draft_output(recipe):
    output = {key: recipe.get(key, field.get_default(call_default_factory=True)) for key, field in RecipeDraft.model_fields.items()}
    output["mode"] = "structured" if recipe.get("mode") == "structured" else "text"
    output["directions"] = recipe.get("directions", recipe.get("instructions", ""))
    output["ingredient_groups"] = _groups_output(recipe.get("ingredient_groups", []), recipe["id"])
    gaps = output["unclassified"]
    if isinstance(gaps, list):
        output["unclassified"] = "".join(piece.get("text", "") for piece in gaps)
    if output["yield_amount"] is None and recipe.get("servings"):
        output["yield_amount"] = str(recipe["servings"])
    return output


async def _detail_photos(repo, recipe, context):
    """Project public photo fields and batch uploader identities, including merges."""
    photos, offset = [], 0
    async with Connections.using(repo._name):
        while True:
            query = Photo.objects().select(*PUBLIC_FIELDS).filter(recipe_id=recipe["id"])
            if not context.admin:
                visible = Q(status="approved")
                if context.user:
                    visible |= Q(uploader_id=RecordId("users", context.user["id"]), status__in=["pending", "rejected"])
                query.filter(visible)
            else:
                query.filter(status__in=["approved", "pending", "rejected"])
            page = await query.order_by("id").offset(offset).limit(1000).exec()
            photos.extend(row if isinstance(row, dict) else repo._output(row) for row in page)
            if len(page) < 1000:
                break
            offset += len(page)
        users = {}
        pending = {_id(photo.get("uploader_id"), "users") for photo in photos} | {recipe.get("owner_id")}
        while pending := pending - users.keys() - {None}:
            ids = list(pending)
            pending = set()
            for start in range(0, len(ids), 1000):
                rows = await User.objects().select("id", "state", "merged_into").filter(
                    id__in=[RecordId("users", uid) for uid in ids[start:start + 1000]]).limit(1000).exec()
                for uid in ids[start:start + 1000]:
                    users[uid] = None
                for row in rows:
                    row = row if isinstance(row, dict) else repo._output(row)
                    users[_id(row["id"], "users")] = row
                    pending.add(_id(row.get("merged_into"), "users"))
    def active(uid):
        seen = set()
        while uid and uid not in seen:
            seen.add(uid)
            user = users.get(uid)
            if not user or user.get("state") == "blocked":
                return False
            target = _id(user.get("merged_into"), "users")
            if not target:
                return user.get("state") == "active"
            uid = target
        return False
    if not active(recipe.get("owner_id")):
        return []
    result = []
    for photo in photos:
        if active(_id(photo.get("uploader_id"), "users")):
            photo = {key: photo[key] for key in PUBLIC_FIELDS if key in photo}
            for field, table in (("id", "photos"), ("recipe_id", "recipes"), ("uploader_id", "users")):
                photo[field] = _id(photo.get(field), table)
            result.append(photo)
    return result


async def recipe_output(request, recipe, context=None):
    context = context or await get_context(request)
    repo = request.app.state.repo
    owner = await repo.get("users", recipe["owner_id"]) if recipe.get("owner_id") else None
    await repo.connect()
    async with Connections.using(repo._name):
        jobs = await EnrichmentJob.objects().select("id", "state", "input_revision").filter(recipe_id=recipe["id"]).order_by("-input_revision").limit(1).exec()
    latest = (jobs[0] if isinstance(jobs[0], dict) else repo._output(jobs[0])) if jobs else None
    state = latest["state"] if latest else "none"
    photos = await _detail_photos(repo, recipe, context)
    return {**_draft_output(recipe), "id": recipe["id"], "revision": recipe["revision"],
            "owner_id": recipe.get("owner_id"), "author_name": owner["display_name"] if owner else None,
            "can_edit": _editable(recipe, context), "enrichment_status": "complete" if state == "succeeded" else state,
            "photos": [{**photo, "state": photo.get("state", photo.get("status", "pending")),
                        "can_delete": context.admin or bool(context.user and photo.get("uploader_id") == context.user["id"])} for photo in photos]}


async def _recipe(repo, recipe_id):
    try:
        recipe = await repo.get("recipes", recipe_id)
    except ValueError:
        raise HTTPException(404, "Recipe not found") from None
    if not _live(recipe):
        raise HTTPException(404, "Recipe not found")
    return recipe


@router.get("/recipes/{recipe_id}", response_model=Recipe)
async def get_recipe(request: Request, response: Response, recipe_id: str):
    recipe = await _recipe(request.app.state.repo, recipe_id)
    result = Recipe.model_validate(await recipe_output(request, recipe)).model_dump(mode="json")
    return _revalidate(request, response, result, private=True)


def _content(draft, previous=None):
    data = draft.model_dump(exclude={"expected_revision"})
    previous_groups = previous.get("ingredient_groups", []) if previous else []
    normalized = _groups_output(previous_groups, previous["id"]) if previous else []
    old = {view["id"]: (view, stored) for group, original in zip(normalized, previous_groups)
           for view, stored in zip(group["ingredients"], original["ingredients"])}
    old_groups = {view['id']: stored for view, stored in zip(normalized, previous_groups)}
    for group in data["ingredient_groups"]:
        previous_group = old_groups.get(group['id'], {})
        for key, value in previous_group.items():
            if key not in {'id', 'name', 'title', 'ingredients'}:
                group.setdefault(key, deepcopy(value))
        group["title"] = group["name"]
        for item in group["ingredients"]:
            item.pop("grams", None)
            item["text"] = item["original_text"]
            pair = old.get(item["id"])
            if pair and data["source_text"] == previous.get("source_text", "") and all(item.get(key) == pair[0].get(key) for key in AUTHORED):
                stored = pair[1]
                for key, value in stored.items():
                    if key not in {'id', 'text', *AUTHORED} and not key.startswith('grams'):
                        item.setdefault(key, deepcopy(value))
                if stored.get("grams_input_hash") == ingredient_hash(stored):
                    item.update({key: deepcopy(stored[key]) for key in DERIVED if key in stored})
    data["instructions"] = data["directions"]
    return data


async def _authorize_edit(request, tx, recipe_id):
    context = await get_context(request)
    context = await authorize(request, tx, admin=context.admin)
    recipe = await _recipe(tx, recipe_id)
    if not _editable(recipe, context):
        raise HTTPException(403, "Recipe owner or administrator required")
    return context, recipe


@router.post("/recipes", status_code=201, response_model=Recipe)
async def create_recipe(request: Request, draft: RecipeDraft,
                        idempotency_key: str | None = Header(default=None, min_length=1, max_length=200, pattern=r"^[\x21-\x7e]+$")):
    await require_user(request)
    await rate_limit(request, "recipe_create", 20, 86400)
    repo = request.app.state.repo
    async def create(tx):
        context = await authorize(request, tx)
        key = sha256(json.dumps([context.user["id"], idempotency_key], separators=(",", ":")).encode()).hexdigest() if idempotency_key else None
        if key:
            existing = await tx.get("recipes", key)
            if existing:
                snapshot = existing.get("idempotency_snapshot")
                if not snapshot or existing.get("idempotency_user_id") != context.user["id"]:
                    raise HTTPException(409, "Idempotency key unavailable")
                return deepcopy(snapshot)
        recipe = await tx.create("recipes", {**_content(draft), "owner_id": context.user["id"], "status": "published"}, id=key)
        await tx.create("revisions", {"recipe_id": recipe["id"], "actor_id": context.user["id"],
                                      "revision": 1, "reason": "publish", "content": deepcopy(recipe)})
        job = await enqueue_enrichment(tx, recipe)
        snapshot = {**_draft_output(recipe), "id": recipe["id"], "revision": recipe["revision"],
                    "owner_id": context.user["id"], "author_name": context.user["display_name"],
                    "can_edit": True, "photos": [], "enrichment_status": job["state"]}
        if key:
            await tx.update("recipes", recipe["id"], {"idempotency_snapshot": snapshot, "idempotency_user_id": context.user["id"]})
        return snapshot
    result = await retry_transaction(repo, create)
    invalidate_catalog(repo)
    return result


def _enrichment_inputs(recipe):
    groups = _groups_output(recipe.get("ingredient_groups", []), recipe["id"])
    return recipe.get("source_text", ""), [
        (group["id"], group["name"], [(row["id"], tuple(row.get(key) for key in AUTHORED))
                                      for row in group["ingredients"]]) for group in groups]


async def _save_revision(tx, recipe_id, expected_revision, content, **metadata):
    # The registry has a unique (recipe_id, revision) index: publish already
    # archives revision 1. Never overwrite that original or insert a duplicate.
    if expected_revision == 1 and await tx.list("revisions", {"recipe_id": recipe_id, "revision": 1}, limit=1):
        return await tx.compare_and_swap("recipes", recipe_id, expected_revision, content)
    return await tx.save_recipe_revision(recipe_id, expected_revision, content, **metadata)


@router.put("/recipes/{recipe_id}", response_model=Recipe)
async def update_recipe(request: Request, recipe_id: str, draft: RecipeUpdate):
    await rate_limit(request, "recipe_update", 100, 86400)
    repo = request.app.state.repo
    async with repo.transaction() as tx:
        context, previous = await _authorize_edit(request, tx, recipe_id)
        recipe = await _save_revision(tx, recipe_id, draft.expected_revision, _content(draft, previous),
                                                actor_id=context.user["id"] if context.user else None)
        if _enrichment_inputs(previous) != _enrichment_inputs(recipe):
            await enqueue_enrichment(tx, recipe)
        else:
            # Retarget queued work across prose-only edits. A running worker holds
            # the old input revision, so it needs replacement work for safe fencing.
            jobs = await tx.list("jobs", {"recipe_id": recipe_id, "input_revision": previous["revision"]}, limit=1000)
            for job in jobs:
                if job["input_hash"] != enrichment_hash(recipe):
                    continue
                if job["state"] in {"pending", "retry"}:
                    await tx.update("jobs", job["id"], {"input_revision": recipe["revision"]})
                elif job["state"] == "running":
                    await enqueue_enrichment(tx, recipe)
    invalidate_catalog(repo)
    return await recipe_output(request, recipe, context)


@router.delete("/recipes/{recipe_id}", status_code=204)
async def delete_recipe(request: Request, recipe_id: str, expected_revision: int = Query(ge=1, le=2_147_483_647)):
    repo = request.app.state.repo
    async with repo.transaction() as tx:
        context, previous = await _authorize_edit(request, tx, recipe_id)
        await _save_revision(tx, recipe_id, expected_revision, {"deleted_at": now(), "status": "deleted"},
                                      actor_id=context.user["id"] if context.user else None, reason="delete")
    invalidate_catalog(repo)
    return Response(status_code=204)


async def organization_context(request, tx):
    from .security import DEVICE_COOKIE, digest, resolve_context
    context = await resolve_context(request, tx)
    secret = request.cookies.get(DEVICE_COOKIE, "")
    if secret and len(secret) <= 128:
        rows = await tx.list("devices", {"secret_hash": digest(secret)}, limit=2)
        for device in rows:
            user = await tx.get("users", device["user_id"])
            if device.get("revoked_at") or not user or user.get("state") == "blocked":
                raise HTTPException(403, "Credential is unavailable")
            # A merged chain may lead to a blocked survivor, even for an expired device.
            seen = set()
            while user and user.get("merged_into") and user["id"] not in seen:
                seen.add(user["id"])
                user = await tx.get("users", user["merged_into"])
                if not user or user.get("state") == "blocked":
                    raise HTTPException(403, "Credential is unavailable")
    if context.user:
        return await authorize(request, tx)
    return context


@router.post("/ingredients/parse", response_model=IngredientLinesResult)
async def parse_lines(request: Request, body: IngredientLinesRequest):
    from .ingredient_lines import parse_ingredient_lines
    await organization_context(request, request.app.state.repo)

    async def reserve():
        async def charge(tx):
            context = await organization_context(request, tx)
            await ai.consume_ai_quota(tx, request.app.state.settings,
                                     user_id=context.user["id"] if context.user else None,
                                     ip=client_ip(request), units=1)
        await retry_transaction(request.app.state.repo, charge)

    return await parse_ingredient_lines(body.lines, request.app.state.settings, reserve_fallback=reserve)


@router.post("/parse", response_model=ParseResult)
async def parse(request: Request, body: ParseRequest):
    async def reserve(tx):
        context = await organization_context(request, tx)
        await ai.consume_ai_quota(tx, request.app.state.settings, user_id=context.user["id"] if context.user else None, ip=client_ip(request))
    await retry_transaction(request.app.state.repo, reserve)
    try:
        result = await ai.parse_recipe(body.source_text, request.app.state.settings)
        result = {**result, "source_text": body.source_text,
                  "ingredient_groups": _groups_output(result.get("ingredient_groups", []), ai.source_hash(body.source_text))}
        gaps = result.get("unclassified", "")
        result["unclassified"] = "".join(piece["text"] for piece in gaps) if isinstance(gaps, list) else gaps
        return result
    except Exception as exc:
        logger.exception("Recipe parsing failed ({})", type(exc).__name__)
        raise HTTPException(503, "Recipe parsing is temporarily unavailable") from None


@router.post("/recipes/{recipe_id}/enrich", status_code=202)
async def enrich_recipe(request: Request, recipe_id: str):
    await rate_limit(request, "recipe_enrich", 40, 86400)
    async def enqueue(tx):
        context, recipe = await _authorize_edit(request, tx, recipe_id)
        job = await enqueue_enrichment(tx, recipe)
        if job["state"] in {"failed", "stale", "succeeded", "cancelled"}:
            job = await tx.compare_and_swap("jobs", job["id"], job["revision"],
                                            {"state": "pending", "attempts": 0, "available_at": now(),
                                             "lease_until": None, "lease_token": None, "last_error": None, "completed_at": None})
        return {"id": job["id"], "state": job["state"], "enrichment_status": job["state"]}
    return await retry_transaction(request.app.state.repo, enqueue)
