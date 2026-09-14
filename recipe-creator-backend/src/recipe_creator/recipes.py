import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from time import monotonic
from uuid import uuid5, NAMESPACE_URL

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from logly import logger
from surreal_orm import Q, SurrealDBConnectionManager as Connections

from . import ai
from .ingredients import ingredient_hash
from .jobs import enqueue_enrichment, enrichment_hash
from .models import EnrichmentJob, Photo, Recipe as RecipeModel, RecipeViewStats, User
from .photos import PUBLIC_FIELDS
from surreal_sdk.protocol.cbor import RecordId
from .schemas import (ParseRequest, ParseResult, Recipe, RecipeCatalogProjection, RecipeDraft,
                      RecipeUpdate, MEAL_CLASSIFIERS, tag_key, RecipeListResponse, TagCatalog,
                      Tag, RecipeLookupRequest, RecipeLookupResponse, IngredientLinesRequest, IngredientLinesResult,
                      RecipeViewCounts)

from .security import authorize, client_ip, get_context, now, rate_limit, require_user, retry_transaction, set_cookie


router = APIRouter()
CATEGORIES = MEAL_CLASSIFIERS
AUTHORED = ("original_text", "quantity", "quantity_max", "unit", "name", "preparation", "optional")
DERIVED = ("grams", "grams_range", "grams_estimate", "grams_provenance", "grams_input_hash", "grams_error", "grams_confirmed")
SUMMARY_FIELDS = ("id", "title", "string::slice(description, 0, 300) AS description",
                  "description AS search_description", "payload.directions AS search_directions",
                  "ingredient_groups", "tags", "owner_id", "owner_id.display_name AS author_name")
SUMMARY_KEYS = ("id", "title", "description", "tags", "owner_id", "author_name", "total_views", "unique_viewers")


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


def _tokens(value):
    import unicodedata

    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    words, current = [], []
    for character in normalized:
        if unicodedata.category(character) == "Mn":
            continue
        if character.isalnum():
            current.append(character)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return tuple(words)


def _search_document(recipe):
    ingredient_tokens = []
    for group in recipe.get("ingredient_groups") or []:
        for ingredient in group.get("ingredients") or []:
            ingredient_tokens.extend(_tokens(ingredient.get("name", "")))
    tag_tokens = [token for tag in recipe.get("tags") or [] for token in _tokens(tag)]
    return {
        "title": _tokens(recipe.get("title", "")),
        "ingredients": tuple(ingredient_tokens),
        "tags": tuple(tag_tokens),
        "description": _tokens(recipe.get("search_description", recipe.get("description", ""))),
        "directions": _tokens(recipe.get("search_directions", recipe.get("directions", ""))),
    }


def _search_score_tokens(document: dict, ordered: tuple[str, ...]) -> float | None:
    if not ordered:
        return None
    distinct = tuple(dict.fromkeys(ordered))
    final = ordered[-1]
    prefix_allowed = len(final) >= 3
    fields = (("title", 5), ("ingredients", 4), ("tags", 3), ("description", 2), ("directions", 1))
    weights = []
    for query_token in distinct:
        strongest = 0.0
        for field, weight in fields:
            tokens = document.get(field, ())
            if query_token in tokens:
                strongest = max(strongest, float(weight))
            elif prefix_allowed and query_token == final and any(token.startswith(query_token) for token in tokens):
                strongest = max(strongest, weight / 2)
        if not strongest:
            return None
        weights.append(strongest)

    title = document.get("title", ())
    if title == ordered:
        tier = 4
    elif any(title[index:index + len(ordered)] == ordered for index in range(len(title) - len(ordered) + 1)):
        tier = 3
    elif all(token in title for token in distinct):
        tier = 2
    else:
        tier = 1
    return tier + sum(weights) / (5 * len(distinct) + 1)


def search_score(document: dict, text: str) -> float | None:
    return _search_score_tokens(document, _tokens(text))


def _public_summary(item, score=None):
    summary = {key: item[key] for key in SUMMARY_KEYS}
    if score is not None:
        summary["search_score"] = score
    return summary


async def _project(repo, fields, row_model, condition=None):
    await repo.connect()
    rows, offset = [], 0
    async with Connections.using(repo._name):
        while True:
            query = RecipeModel.objects().select(*fields).filter(deleted_at=None, status="published")
            if condition is not None:
                query.filter(condition)
            query = query.order_by("id").offset(offset).limit(1000)
            page = await query._execute_query(query._compile_query())
            rows.extend(row_model.model_validate(row) for row in page)
            if len(page) < 1000:
                return rows
            offset += len(page)


def invalidate_catalog(repo):
    repo._recipe_catalog = None
    repo._recipe_search = {}

async def attach_view_counts(repo, items):
    if not items:
        return items
    await repo.connect()
    counts = {}
    ids = [item["id"] for item in items]
    async with Connections.using(repo._name):
        for start in range(0, len(ids), 1000):
            chunk = ids[start:start + 1000]
            rows = await RecipeViewStats.objects().select(
                "id", "total_views", "unique_viewers"
            ).filter(id__in=[RecordId("recipe_view_stats", recipe_id) for recipe_id in chunk]).limit(1000).exec()
            for row in rows:
                row = row if isinstance(row, dict) else repo._output(row)
                counts[_id(row["id"], "recipe_view_stats")] = row
    for item in items:
        stats = counts.get(item["id"])
        item["total_views"] = stats["total_views"] if stats else 0
        item["unique_viewers"] = stats["unique_viewers"] if stats else 0
    return items



async def _catalog(repo):
    if not hasattr(repo, "_recipe_catalog_lock"):
        repo._recipe_catalog_lock = asyncio.Lock()
    async with repo._recipe_catalog_lock:
        cached = getattr(repo, "_recipe_catalog", None)
        if cached and cached[0] > monotonic():
            return cached[1]
        rows = await _project(repo, SUMMARY_FIELDS, RecipeCatalogProjection)
        summaries = await attach_view_counts(repo, [row.model_dump() for row in rows])
        for summary in summaries:
            summary["_search"] = _search_document(summary)
            for field in ("search_description", "search_directions", "ingredient_groups"):
                summary.pop(field)
        order = {name: index for index, name in enumerate((*CATEGORIES, "Other"))}
        summaries.sort(key=lambda item: (
            order[_group(item)], -item["total_views"], item["title"].lower(), item["id"]
        ))
        repo._recipe_catalog = (monotonic() + 15, summaries)
        repo._recipe_search = {}
        return summaries


async def _text_matches(repo, text, catalog):
    cache = getattr(repo, "_recipe_search", {})
    key = _tokens(text)
    cached = cache.get(key)
    if cached and cached[0] > monotonic():
        return cached[1]
    scores = {}
    for item in catalog:
        score = _search_score_tokens(item["_search"], key)
        if score is not None:
            scores[item["id"]] = score
    if len(cache) >= 128:
        cache.pop(next(iter(cache)))
    cache[key] = (monotonic() + 15, scores)
    repo._recipe_search = cache
    return scores


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


async def filter_summaries(repo, catalog, q, selected_tags, owner_id=None, *, candidates=None, text_scores=None):
    known = {tag_key(tag) for item in catalog for tag in item["tags"]}
    text, legacy_tags, errors = search_terms(q, known)
    selected = {tag_key(tag) for tag in selected_tags}
    unknown = selected - known
    if unknown:
        errors.append("Tags not found: " + ", ".join(sorted(unknown)))
    has_text = bool(_tokens(text))
    scores = (text_scores if text_scores is not None else await _text_matches(repo, text, catalog)) if has_text else None
    filtered = []
    for item in catalog if candidates is None else candidates:
        tags = {tag_key(tag) for tag in item["tags"]}
        if (unknown or (scores is not None and item["id"] not in scores)
                or not selected <= tags or (legacy_tags and not set(legacy_tags) & tags)
                or (owner_id is not None and item["owner_id"] != _id(owner_id, "users"))):
            continue
        filtered.append(_public_summary(item, scores[item["id"]] if scores is not None else None))
    if scores is not None:
        filtered.sort(key=lambda item: (-item["search_score"], item["id"]))
    return filtered, errors


@router.post("/recipes/lookup", response_model=RecipeLookupResponse)
async def lookup_recipes(body: RecipeLookupRequest, request: Request):
    repo = request.app.state.repo
    ids = list(dict.fromkeys(_id(value, "recipes") for value in body.ids))
    if not ids:
        return {"items": [], "unavailable_ids": []}
    # Bounded candidate retrieval, not a collection scan or stale summary cache.
    candidates, unavailable, text_scores = [], [], {}
    text, _, _ = search_terms(body.q, set())
    query_tokens = _tokens(text)
    has_text = bool(query_tokens)
    for recipe_id in ids:
        row = await repo.get("recipes", recipe_id)
        if not _live(row):
            unavailable.append(recipe_id)
            continue
        owner = await repo.get("users", row["owner_id"]) if row.get("owner_id") else None
        candidate = {"id": recipe_id, "title": row.get("title", ""),
                     "description": (row.get("description") or "")[:300], "tags": row.get("tags") or [],
                     "owner_id": row.get("owner_id"), "author_name": owner["display_name"] if owner else None,
                     "_search": _search_document(row)}
        candidates.append(candidate)
        if has_text:
            score = _search_score_tokens(candidate["_search"], query_tokens)
            if score is not None:
                text_scores[recipe_id] = score
    await attach_view_counts(repo, candidates)
    catalog = await _catalog(repo) if body.q or body.tags else candidates
    items, _ = await filter_summaries(repo, catalog, body.q, body.tags, candidates=candidates,
                                      text_scores=text_scores if has_text else None)
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
    popular = []
    if not q.strip() and not tag and owner_id is None:
        qualifying = [item for item in catalog if item["total_views"] >= 6 and item["unique_viewers"] >= 2]
        qualifying.sort(key=lambda item: (-item["total_views"], item["title"].lower(), item["id"]))
        if len(qualifying) >= 3:
            popular = await summary_thumbnails(request, qualifying[:3])
    return _revalidate(request, response, {"items": items, "offset": offset, "limit": limit, "total": len(filtered),
            "has_more": offset + limit < len(filtered), "errors": errors, "popular": popular,
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


def _unique_stable_id(candidate, seed, kind, index, used):
    value = candidate or _stable_id(seed, kind, index)
    if value not in used:
        used.add(value)
        return value
    attempt = 1
    while True:
        value = _stable_id(seed, f"{kind}:collision", f"{index}:{attempt}")
        if value not in used:
            used.add(value)
            return value
        attempt += 1


def _groups_output(groups, seed):
    result = []
    group_ids, row_ids = set(), set()
    for gi, group in enumerate(groups):
        gid = _unique_stable_id(group.get("id"), seed, "group", gi, group_ids)
        rows = []
        for ii, ingredient in enumerate(group.get("ingredients", [])):
            item = {key: ingredient.get(key, False if key == "optional" else None if key in {"quantity", "quantity_max"} else "") for key in AUTHORED}
            item["original_text"] = ingredient.get("original_text", ingredient.get("text", ""))
            for key in ("quantity", "quantity_max"):
                if item[key] is not None:
                    item[key] = str(item[key])
            item["unit"] = item["unit"] or ""
            item["id"] = _unique_stable_id(ingredient.get("id"), seed, gid, ii, row_ids)
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
            refusal = provenance.get("refusal_reason") if isinstance(provenance, dict) else None
            if grams is None and ingredient.get("grams_error") == "estimation_refused" and refusal:
                grams = {
                    "estimated": True,
                    "basis": basis,
                    "refusal_reason": refusal,
                }
            item["grams"] = grams
            rows.append(item)
        result.append({"id": gid, "name": group.get("name", group.get("title", "")), "ingredients": rows})
    return result


def _draft_output(recipe):
    output = {key: recipe.get(key, field.get_default(call_default_factory=True)) for key, field in RecipeDraft.model_fields.items()}
    output["mode"] = "structured" if recipe.get("mode") == "structured" else "text"
    output["directions"] = recipe.get("directions", recipe.get("instructions", ""))
    output["ingredient_groups"] = _groups_output(recipe.get("ingredient_groups", []), recipe["id"])
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
    stats = await repo.get("recipe_view_stats", recipe["id"])
    return {**_draft_output(recipe), "id": recipe["id"], "revision": recipe["revision"],
            "owner_id": recipe.get("owner_id"), "author_name": owner["display_name"] if owner else None,
            "can_edit": _editable(recipe, context), "enrichment_status": "complete" if state == "succeeded" else state,
            "total_views": stats["total_views"] if stats else 0,
            "unique_viewers": stats["unique_viewers"] if stats else 0,
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


@router.post("/recipes/{recipe_id}/views", response_model=RecipeViewCounts)
async def create_recipe_view(request: Request, response: Response, recipe_id: str):
    from .views import VIEWER_COOKIE, register_view
    counts, cookie_secret, max_age = await register_view(request, recipe_id)
    if cookie_secret:
        set_cookie(response, request, VIEWER_COOKIE, cookie_secret, max_age)
    return counts


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
        return {
            **result,
            "ingredient_groups": _groups_output(
                result.get("ingredient_groups", []), ai.source_hash(body.source_text)
            ),
        }
    except Exception as exc:
        logger.opt(exception=exc).error("Recipe parsing failed")
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
