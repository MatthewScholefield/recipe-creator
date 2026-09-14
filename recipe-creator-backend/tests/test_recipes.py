import asyncio
from datetime import timedelta
from io import StringIO
import os
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
import pytest
import pytest_asyncio
from logly import logger as logly_logger

from recipe_creator import ai, recipes
from recipe_creator.ingredients import ingredient_hash
from recipe_creator.photos import PhotoService
from recipe_creator.repository import ConflictError, Repository
from recipe_creator.schemas import RecipeCatalogProjection, RecipeDraft
from recipe_creator.security import ADMIN_COOKIE, DEVICE_COOKIE, digest, now
from recipe_creator.settings import Settings


def draft(**changes):
    return {"title": "Soup", "source_text": "  Original\n\n prose  ", "mode": "structured",
            "ingredient_groups": [{"id": "group", "name": "Broth", "ingredients": [
                {"id": "ingredient", "original_text": "2 cups stock", "quantity": "2", "unit": "cups", "name": "stock"}]}],
            "directions": " Warm slowly.\n", **changes}


@pytest.mark.parametrize("changes", [
    {"title": " "}, {"title": 3}, {"mode": "source"}, {"title": "x" * 301},
    {"source_url": "javascript:alert(1)"}, {"source_url": "https://user:pass@example.com"},
    {"source_url": "https://example.com\n"}, {"unknown": True},
])
def test_strict_draft(changes):
    with pytest.raises(ValidationError):
        RecipeDraft.model_validate(draft(**changes))


def test_exact_strings_bounds_and_untrusted_estimates():
    value = draft(owner_id="forged", photo_trusted=True, source_url="https://example.com/source", yield_amount="about 6")
    row = value["ingredient_groups"][0]["ingredients"][0]
    row.update(quantity="1 or 2", quantity_max="to taste",
               grams={"amount": 100, "source": "author_confirmed"},
               grams_input_hash="forged", grams_confirmed=True)
    parsed = RecipeDraft.model_validate(value)
    assert parsed.source_text == value["source_text"]
    assert parsed.directions == value["directions"]
    assert parsed.yield_amount == "about 6"
    assert parsed.ingredient_groups[0].ingredients[0].quantity == "1 or 2"
    assert parsed.ingredient_groups[0].ingredients[0].quantity_max == "to taste"
    assert parsed.ingredient_groups[0].ingredients[0].grams is None
    assert "owner_id" not in parsed.model_dump()
    assert recipes._content(parsed)["ingredient_groups"][0]["ingredients"][0].get("grams") is None


def test_search_prototype_semantics():
    assert recipes.search_terms(" Red  soup tag:dinner -tag:lunch -incomplete", {"dinner", "lunch"}) == ("red soup", ["dinner", "lunch"], [])
    assert recipes.search_terms("foo:x tag:missing tag:", set()) == ("", [], ["Unknown search fields: foo", "Tags not found: missing"])

def test_groups_output_repairs_duplicate_ids():
    groups = [
        {"id": "same-group", "name": "", "ingredients": [
            {"id": "same-row", "original_text": "2 eggs"},
            {"id": "same-row", "original_text": "salt"},
        ]},
        {"id": "same-group", "name": "", "ingredients": [
            {"id": "same-row", "original_text": "pepper"},
        ]},
    ]
    output = recipes._groups_output(groups, "seed")
    group_ids = [group["id"] for group in output]
    row_ids = [row["id"] for group in output for row in group["ingredients"]]
    assert len(set(group_ids)) == len(group_ids)
    assert len(set(row_ids)) == len(row_ids)
    assert [row["original_text"] for group in output for row in group["ingredients"]] == ["2 eggs", "salt", "pepper"]


def test_catalog_projection_is_strict_and_normalizes_record_ids():
    row = RecipeCatalogProjection.model_validate({"id": "recipes:soup", "title": "Soup", "description": "",
                                                   "tags": ["dinner"], "owner_id": "users:alice", "author_name": "Alice"})
    assert row.id == "soup" and row.owner_id == "alice"
    with pytest.raises(ValidationError):
        RecipeCatalogProjection.model_validate({**row.model_dump(), "unexpected": True})


@pytest_asyncio.fixture
async def app(tmp_path):
    url = os.getenv("RECIPE_TEST_DB_URL")
    if not url:
        pytest.skip("Set RECIPE_TEST_DB_URL to run against real SurrealDB")
    settings = Settings(db_url=url, db_user=os.getenv("RECIPE_TEST_DB_USER", "root"),
                        db_password=os.getenv("RECIPE_TEST_DB_PASSWORD", ""),
                        db_namespace="recipes_" + uuid4().hex, db_database="tests", media_root=tmp_path)
    async with Repository(settings) as repo:
        await repo.migrate()
        app = FastAPI()
        app.state.repo, app.state.settings = repo, settings
        app.state.photos = PhotoService(repo, settings)
        app.include_router(recipes.router)

        @app.exception_handler(ConflictError)
        async def conflict(request, exc):
            return JSONResponse({"detail": "Recipe changed"}, status_code=409)

        @app.exception_handler(ai.AIQuotaExceeded)
        async def quota(request, exc):
            return JSONResponse({"detail": "AI quota exceeded"}, status_code=429)

        try:
            yield app
        finally:
            await repo._connection.query(f"REMOVE NAMESPACE `{settings.db_namespace}`;")


def client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def identify(app, browser, name="Alice"):
    user = await app.state.repo.create("users", {"display_name": name})
    secret = uuid4().hex
    await app.state.repo.create("devices", {"user_id": user["id"], "secret_hash": digest(secret), "expires_at": now() + timedelta(days=1)})
    browser.cookies.set(DEVICE_COOKIE, secret)
    return user


async def test_crud_idempotency_original_snapshot_and_history(app):
    async with client(app) as browser:
        user = await identify(app, browser)
        response = await browser.post("/recipes", json=draft(owner_id="forged"), headers={"Idempotency-Key": "stable"})
        assert response.status_code == 201, response.text
        original = response.json()
        rid = original["id"]
        assert original["owner_id"] == user["id"]
        assert original["can_edit"] and original["author_name"] == "Alice"
        assert len(await app.state.repo.list("jobs")) == 1
        response = await browser.get(f"/recipes/{rid}")
        assert response.status_code == 200, response.text
        assert response.json()["source_text"] == draft()["source_text"]
        response = await browser.put(f"/recipes/{rid}", json=draft(title="Changed", expected_revision=1))
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 2
        assert (await browser.put(f"/recipes/{rid}", json=draft(expected_revision=1))).status_code == 409
        revisions = await app.state.repo.list("revisions", {"recipe_id": rid})
        assert len(revisions) == 1 and revisions[0]["content"]["title"] == "Soup"
        assert revisions[0]["reason"] == "publish" and revisions[0]["revision"] == 1
        retry = await browser.post("/recipes", json=draft(title="Not the first"), headers={"Idempotency-Key": "stable"})
        assert retry.json() == original
        assert (await browser.delete(f"/recipes/{rid}?expected_revision=2")).status_code == 204
        assert (await browser.get(f"/recipes/{rid}")).status_code == 404
        assert (await browser.get("/recipes")).json()["items"] == []
        assert (await browser.post("/recipes", json=draft(), headers={"Idempotency-Key": "stable"})).json() == original


async def test_search_projection_pagination_and_cache(app, monkeypatch):
    repo = app.state.repo
    async with client(app) as browser:
        await identify(app, browser)
        for value in [draft(title="Zebra", tags=["breakfast"], description="red soup"),
                      draft(title="Apple", tags=["dinner"], directions="RED SOUP here"),
                      draft(title="Berry", tags=["lunch"], directions="red then soup"),
                      draft(title="Aardvark", tags=["other"], source_text="red soup")]:
            assert (await browser.post("/recipes", json=value)).status_code == 201
        calls = []
        project = recipes._project
        async def spy(repository, fields, row_model, condition=None):
            calls.append((fields, row_model))
            return await project(repository, fields, row_model, condition)
        monkeypatch.setattr(recipes, "_project", spy)
        response = await browser.get("/recipes?limit=2")
        assert response.status_code == 200, response.text
        result = response.json()
        assert [item["title"] for item in result["items"]] == ["Zebra", "Berry"]
        assert result["has_more"] and result["total"] == 4
        assert "source_text" not in result["items"][0]
        assert result["items"][0]["author_name"] == "Alice"
        assert (await browser.get("/recipes?offset=2&limit=2")).json()["items"][0]["title"] == "Apple"
        assert len(calls) == 1
        result = (await browser.get("/recipes", params={"q": "red soup tag:lunch -tag:dinner"})).json()
        assert [item["title"] for item in result["items"]] == ["Apple"]
        assert calls[-1][0] == ("id",)
        result = (await browser.get("/recipes", params={"q": "tag:absent owner:x"})).json()
        assert len(result["errors"]) == 2
        assert (await browser.get("/recipes", params={"q": "$x); DELETE recipes;"})).status_code == 200
        assert (await browser.get("/tags")).json()["tags"] == ["breakfast", "dinner", "lunch", "other"]
        assert (await browser.get("/recipes?limit=101")).status_code == 422


async def test_catalog_projection_does_not_log_orm_validation_fallback(app):
    output = StringIO()
    sink = logly_logger.add(output, format="{message}\n", enqueue=False)
    try:
        async with client(app) as browser:
            await identify(app, browser)
            assert (await browser.post("/recipes", json=draft())).status_code == 201
            assert (await browser.get("/recipes")).status_code == 200
    finally:
        logly_logger.remove(sink)

    assert "Pydantic invalid format for the class" not in output.getvalue()


async def test_owner_admin_blocked_and_scoped_keys(app):
    async with client(app) as owner, client(app) as other, client(app) as anonymous:
        user = await identify(app, owner)
        await identify(app, other, "Bob")
        first = (await owner.post("/recipes", json=draft(), headers={"Idempotency-Key": "same"})).json()
        second = (await other.post("/recipes", json=draft(), headers={"Idempotency-Key": "same"})).json()
        assert first["id"] != second["id"]
        rid = first["id"]
        assert (await other.put(f"/recipes/{rid}", json=draft(expected_revision=1))).status_code == 403
        assert not (await other.get(f"/recipes/{rid}")).json()["can_edit"]
        assert (await anonymous.post("/recipes", json=draft())).status_code == 401
        await app.state.repo.update("users", user["id"], {"state": "blocked"})
        assert (await owner.delete(f"/recipes/{rid}?expected_revision=1")).status_code == 401
        assert (await app.state.repo.get("recipes", rid))["revision"] == 1
        unknown = await app.state.repo.create("recipes", {"title": "Unknown", "status": "published"})
        assert (await other.put(f"/recipes/{unknown['id']}", json=draft(expected_revision=1))).status_code == 403
        secret = uuid4().hex
        await app.state.repo.create("admin_sessions", {"secret_hash": digest(secret), "expires_at": now() + timedelta(days=1)})
        anonymous.cookies.set(ADMIN_COOKIE, secret)
        assert (await anonymous.put(f"/recipes/{unknown['id']}", json=draft(expected_revision=1))).status_code == 401
        operator = await identify(app, anonymous, "Operator")
        await app.state.repo.update("users", operator["id"], {"is_admin": True})
        assert (await anonymous.put(f"/recipes/{unknown['id']}", json=draft(expected_revision=1))).status_code == 200


async def test_provenance_edit_invalidation_and_terminal_retry(app):
    async with client(app) as browser:
        await identify(app, browser)
        original = (await browser.post("/recipes", json=draft())).json()
        rid = original["id"]
        stored = await app.state.repo.get("recipes", rid)
        groups = stored["ingredient_groups"]
        row = groups[0]["ingredients"][0]
        row.update(grams=100, grams_input_hash=ingredient_hash(row), grams_estimate=True, grams_provenance="ai")
        await app.state.repo.update("recipes", rid, {"ingredient_groups": groups})
        response = await browser.put(f"/recipes/{rid}", json=draft(expected_revision=1, notes="new note"))
        assert response.status_code == 200, response.text
        assert response.json()["ingredient_groups"][0]["ingredients"][0]["grams"]["amount"] == 100
        changed = draft(expected_revision=2)
        changed["ingredient_groups"][0]["ingredients"][0].update(preparation="chopped", grams={"amount": 999})
        response = await browser.put(f"/recipes/{rid}", json=changed)
        assert response.status_code == 200, response.text
        assert response.json()["ingredient_groups"][0]["ingredients"][0]["grams"] is None
        jobs = await app.state.repo.list("jobs", {"recipe_id": rid})
        job = max(jobs, key=lambda item: item["input_revision"])
        await app.state.repo.update("jobs", job["id"], {"state": "failed", "attempts": 4})
        response = await browser.post(f"/recipes/{rid}/enrich")
        assert response.status_code == 202 and response.json()["state"] == "pending"
        assert (await app.state.repo.get("jobs", job["id"]))["attempts"] == 0


async def test_parse_quota_recipe_fields_and_sanitized_failure(app, monkeypatch):
    calls = []
    async def parse(source, settings):
        calls.append(source)
        return {
            "description": "",
            "directions": "",
            "notes": "",
            "yield_amount": "4",
            "yield_unit": "servings",
            "source_url": "https://example.com/recipe",
            "ingredient_groups": [
                {"name": "", "ingredients": [{
                    "original_text": "2 eggs",
                    "quantity": "2",
                    "quantity_max": None,
                    "unit": "",
                    "name": "eggs",
                    "preparation": "",
                    "optional": False,
                }]}
            ],
        }
    monkeypatch.setattr(ai, "parse_recipe", parse)
    async with client(app) as browser:
        assert (await browser.post("/parse", json={"source_text": "text"})).status_code == 200
        assert await app.state.repo.list("users") == []
        await identify(app, browser)
        source = "  source\r\n\n"
        result = await browser.post("/parse", json={"source_text": source})
        assert result.status_code == 200, result.text
        assert result.json()["yield_amount"] == "4"
        assert result.json()["yield_unit"] == "servings"
        assert result.json()["source_url"] == "https://example.com/recipe"
        group = result.json()["ingredient_groups"][0]
        assert group["name"] == ""
        assert group["ingredients"][0]["name"] == "eggs"
        assert group["ingredients"][0]["id"]
        assert len(await app.state.repo.list("usage")) == 3
        async def fail(source, settings):
            raise RuntimeError("provider secret token")
        monkeypatch.setattr(ai, "parse_recipe", fail)
        response = await browser.post("/parse", json={"source_text": source})
        assert response.status_code == 503 and "secret" not in response.text
        app.state.settings.ai_daily_limit = 4
        assert (await browser.post("/parse", json={"source_text": source})).status_code == 429
        assert calls == ["text", source]


@pytest.mark.parametrize('tags', [['Breakfast', 'DINNER'], ['lunch', 'dessert']])
def test_classifier_rejection(tags):
    with pytest.raises(ValidationError, match='Choose only one meal type'):
        RecipeDraft(**draft(tags=tags))


def test_tags_normalize_dedupe_before_bounds():
    value = RecipeDraft(**draft(tags=[' Dinner ', 'DINNER', 'Cafe\u0301', 'Café', 'Indian']))
    assert value.tags == ['dinner', 'Café', 'Indian']
    assert RecipeDraft(**draft(tags=['dinner'] * 100)).tags == ['dinner']
    with pytest.raises(ValidationError):
        RecipeDraft(**draft(tags=[' ']))


async def test_selected_tags_lookup_privacy_and_complete_retrieval(app):
    repo = app.state.repo
    async with client(app) as browser:
        owner = await identify(app, browser)
        public_ids = []
        for i in range(105):
            row = await repo.create('recipes', {'title': f'Recipe {i}', 'status': 'published',
                                               'owner_id': owner['id'], 'tags': ['Dinner', 'Vegetarian'],
                                               'directions': 'Simmer delicious soup', 'description': 'not secret'})
            public_ids.append(row['id'])
        hidden = []
        for state in ['hidden', 'deleted', 'draft']:
            hidden.append((await repo.create('recipes', {'title': 'private', 'status': state, 'tags': ['SECRET']}))['id'])
        listing = (await browser.get('/recipes', params=[('q', 'soup'), ('tag', 'dinner'), ('tag', 'vegetarian'), ('limit', '1')])).json()
        assert listing['total'] == 105 and len(listing['items']) == 1
        unknown = (await browser.get('/recipes?tag=missing')).json()
        assert not unknown['items'] and unknown['errors']
        assert (await browser.get('/recipes?tag=dinner&tag=Vegetarian&owner_id=missing')).json()['total'] == 0
        catalog = (await browser.get('/tags')).json()
        assert catalog == {'tags': ['dinner', 'Vegetarian'], 'classifier_tags': ['breakfast', 'lunch', 'dinner', 'dessert']}
        await repo.update('users', owner['id'], {'is_admin': True})
        found = []
        for start in range(0, len(public_ids), 100):
            result = await browser.post('/recipes/lookup', json={'ids': public_ids[start:start + 100], 'q': 'soup', 'tags': ['dinner', 'vegetarian']})
            assert result.status_code == 200, result.text
            found.extend(row['id'] for row in result.json()['items'])
            assert not result.json()['unavailable_ids']
        assert found == public_ids
        pending = await repo.create('photos', {'recipe_id': public_ids[0], 'uploader_id': owner['id'], 'status': 'pending'})
        result = (await browser.post('/recipes/lookup', json={'ids': [public_ids[0]]})).json()
        assert result['items'][0]['thumbnail_photo_id'] is None
        approved = await repo.create('photos', {'recipe_id': public_ids[0], 'uploader_id': owner['id'], 'status': 'approved'})
        result = (await browser.post('/recipes/lookup', json={'ids': [public_ids[0]]})).json()
        assert result['items'][0]['thumbnail_photo_id'] == approved['id']
        assert pending['id'] not in str(result)
        result = (await browser.post('/recipes/lookup', json={'ids': ['recipes:' + public_ids[0], public_ids[0], *hidden, 'missing'], 'q': 'no match'})).json()
        assert not result['items'] and result['unavailable_ids'] == [*hidden, 'missing']
        assert (await browser.post('/recipes/lookup', json={'ids': public_ids})).status_code == 422
        assert (await browser.post('/recipes/lookup', json={'ids': ['invalid:id']})).status_code == 422
        # Newly private rows cannot leak through a warm catalog.
        await repo.update('recipes', public_ids[0], {'status': 'hidden'})
        result = (await browser.post('/recipes/lookup', json={'ids': [public_ids[0]]})).json()
        assert result == {'items': [], 'unavailable_ids': [public_ids[0]]}


async def test_anonymous_ingredient_quota_and_blocked_credentials(app, monkeypatch):
    from unittest.mock import AsyncMock
    fallback = AsyncMock(return_value=ai.IngredientBatchOutput(items=[
        ai.ParsedIngredientLine(id='old', original_text='2 eggs', quantity='2', name='eggs')
    ]))
    monkeypatch.setattr(ai, 'parse_ingredient_lines_batch', fallback)
    async with client(app) as browser:
        response = await browser.post('/ingredients/parse', json={'lines': [{'id': 'old', 'text': '2 eggs'}]})
        assert response.status_code == 200 and response.json()['items'][0]['method'] == 'llm'
        assert not await app.state.repo.list('users')
        app.state.settings.ai_global_daily_limit = 2
        body = {'lines': [{'id': 'old', 'text': '2 cans beans'}]}
        results = await asyncio.gather(*(browser.post('/ingredients/parse', json=body, headers={'X-Forwarded-For': f'forged-{i}'}) for i in range(3)))
        assert sorted(response.status_code for response in results) == [200, 429, 429]
        assert fallback.await_count == 2
        user = await identify(app, browser)
        await app.state.repo.update('users', user['id'], {'state': 'blocked'})
        assert (await browser.post('/ingredients/parse', json=body)).status_code == 403
        assert (await browser.post('/parse', json={'source_text': '2 eggs'})).status_code == 403


def test_stable_legacy_ids_and_estimate_output():
    groups = [{"title": "Sauce", "ingredients": [{"text": "1 cup flour", "grams": {
        "low": 100, "high": 120, "basis": "cup", "assumptions": ["US cup"]}}]}]
    first = recipes._groups_output(groups, "recipe")
    assert first == recipes._groups_output(groups, "recipe")
    assert first[0]["name"] == "Sauce"
    assert "amount" not in first[0]["ingredients"][0]["grams"]
    assert first[0]["ingredients"][0]["grams"]["low"] == 100
    assert first[0]["ingredients"][0]["grams"]["high"] == 120
    assert first[0]["ingredients"][0]["grams"]["estimated"] is True
    value = draft()
    value["ingredient_groups"].append(value["ingredient_groups"][0])
    with pytest.raises(ValidationError):
        RecipeDraft.model_validate(value)


async def test_failed_enqueue_rolls_back_recipe_and_edit(app, monkeypatch):
    async with client(app) as browser:
        await identify(app, browser)
        rid = (await browser.post("/recipes", json=draft())).json()["id"]
        async def fail(tx, recipe):
            raise RuntimeError("enqueue unavailable")
        monkeypatch.setattr(recipes, "enqueue_enrichment", fail)
        with pytest.raises(RuntimeError, match="enqueue unavailable"):
            await browser.post("/recipes", json=draft(), headers={"Idempotency-Key": "failed"})
        with pytest.raises(RuntimeError, match="enqueue unavailable"):
            await browser.put(f"/recipes/{rid}", json=draft(title="Unsaved", source_text="Changed source", expected_revision=1))
        rows = await app.state.repo.list("recipes")
        assert len(rows) == 1 and rows[0]["title"] == "Soup" and rows[0]["revision"] == 1
        assert len(await app.state.repo.list("revisions")) == 1
        assert (await app.state.repo.list("revisions"))[0]["reason"] == "publish"
        assert len(await app.state.repo.list("jobs")) == 1


async def test_block_between_initial_auth_and_transaction(app, monkeypatch):
    async with client(app) as browser:
        user = await identify(app, browser)
        require = recipes.require_user
        async def block(request):
            context = await require(request)
            await app.state.repo.update("users", user["id"], {"state": "blocked"})
            return context
        monkeypatch.setattr(recipes, "require_user", block)
        assert (await browser.post("/recipes", json=draft())).status_code == 401
        assert await app.state.repo.list("recipes") == []
        assert await app.state.repo.list("jobs") == []


async def test_concurrent_create_and_edit_atomicity(app):
    async with client(app) as browser:
        await identify(app, browser)
        responses = await asyncio.gather(*[browser.post("/recipes", json=draft(), headers={"Idempotency-Key": "parallel"}) for _ in range(2)])
        assert all(response.status_code == 201 for response in responses)
        assert responses[0].json() == responses[1].json()
        rid = responses[0].json()["id"]
        responses = await asyncio.gather(*[browser.put(f"/recipes/{rid}", json=draft(title=title, expected_revision=1)) for title in ("A", "B")])
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert len(await app.state.repo.list("revisions")) == 1
        assert len(await app.state.repo.list("jobs")) == 1


async def test_owner_filter_precedes_pagination_and_public_etags(app):
    async with client(app) as alice, client(app) as bob:
        owner = await identify(app, alice)
        await identify(app, bob, "Bob")
        await alice.post("/recipes", json=draft(title="A", tags=["dinner"]))
        await bob.post("/recipes", json=draft(title="B", tags=["dinner"]))
        await alice.post("/recipes", json=draft(title="C", tags=["dinner"]))
        url = f"/recipes?owner_id={owner['id']}&offset=1&limit=1"
        response = await bob.get(url)
        assert [row["title"] for row in response.json()["items"]] == ["C"]
        assert response.json()["total"] == 2 and not response.json()["has_more"]
        assert response.headers["cache-control"] == "public, no-cache"
        assert (await bob.get(url, headers={"If-None-Match": response.headers["etag"]})).status_code == 304
        tags = await bob.get("/tags")
        assert tags.headers["cache-control"] == "public, no-cache"
        assert (await bob.get("/tags", headers={"If-None-Match": tags.headers["etag"]})).status_code == 304
        await alice.post("/recipes", json=draft(tags=["new-tag"]))
        assert (await bob.get("/tags", headers={"If-None-Match": tags.headers["etag"]})).status_code == 200


async def test_private_etag_photos_and_batched_uploaders(app, monkeypatch):
    repo = app.state.repo
    async with client(app) as owner, client(app) as anonymous:
        user = await identify(app, owner)
        rid = (await owner.post("/recipes", json=draft())).json()["id"]
        uploaders = [await repo.create("users", {"display_name": "Uploader"}) for _ in range(4)]
        for uploader in uploaders:
            await repo.create("photos", {"recipe_id": rid, "uploader_id": uploader["id"], "status": "approved"})
        pending = await repo.create("photos", {"recipe_id": rid, "uploader_id": user["id"], "status": "pending", "storage_key": "private-path"})
        calls = []
        get = repo.get
        async def spy(table, key):
            calls.append((table, key))
            return await get(table, key)
        monkeypatch.setattr(repo, "get", spy)
        public = await anonymous.get(f"/recipes/{rid}")
        assert public.status_code == 200, public.text
        assert len(public.json()["photos"]) == 4
        assert not any(table == "users" and key in {row['id'] for row in uploaders} for table, key in calls)
        private = await owner.get(f"/recipes/{rid}", headers={"If-None-Match": public.headers["etag"]})
        assert private.status_code == 200
        assert private.headers["cache-control"] == "private, no-cache" and private.headers["vary"] == "Cookie"
        assert private.json()["can_edit"] and len(private.json()["photos"]) == 5
        assert "private-path" not in private.text
        unchanged = await owner.get(f"/recipes/{rid}", headers={"If-None-Match": private.headers["etag"]})
        assert unchanged.status_code == 304 and not unchanged.content
        assert unchanged.headers["cache-control"] == "private, no-cache"
        await repo.update("photos", pending["id"], {"status": "approved"})
        assert (await anonymous.get(f"/recipes/{rid}", headers={"If-None-Match": public.headers["etag"]})).status_code == 200
        await repo.update("users", uploaders[0]["id"], {"state": "blocked"})
        assert len((await anonymous.get(f"/recipes/{rid}")).json()["photos"]) == 4


async def test_publish_snapshot_without_key_and_prose_edits_keep_job(app):
    from recipe_creator.jobs import JobRunner
    repo = app.state.repo
    async with client(app) as browser:
        await identify(app, browser)
        rid = (await browser.post("/recipes", json=draft())).json()["id"]
        original = (await repo.list("revisions", {"recipe_id": rid}))[0]
        assert original["revision"] == 1 and original["reason"] == "publish"
        assert original["content"]["source_text"] == draft()["source_text"]
        runner = JobRunner(repo, app.state.settings)
        queued = (await repo.list("jobs", {"recipe_id": rid}))[0]
        stored = await repo.get("recipes", rid)
        groups = stored["ingredient_groups"]
        row = groups[0]["ingredients"][0]
        row.update(grams={"low": 90, "high": 110}, grams_range=[90, 110], grams_estimate=True,
                   grams_input_hash=ingredient_hash(row))
        await repo.update("recipes", rid, {"ingredient_groups": groups})
        response = await browser.put(f"/recipes/{rid}", json=draft(expected_revision=1, directions="New directions"))
        assert response.status_code == 200, response.text
        assert response.json()["ingredient_groups"][0]["ingredients"][0]["grams"]["low"] == 90
        jobs = await repo.list("jobs", {"recipe_id": rid})
        assert len(jobs) == 1 and jobs[0]["input_revision"] == 2
        assert jobs[0]["revision"] == queued["revision"]
        claimed = await runner._claim()
        await runner._finish(claimed, groups=groups)
        assert (await repo.get("jobs", claimed["id"]))["state"] == "succeeded"
        response = await browser.put(f"/recipes/{rid}", json=draft(expected_revision=2, source_text="Different source"))
        assert response.status_code == 200, response.text
        assert response.json()["ingredient_groups"][0]["ingredients"][0]["grams"] is None
        assert len(await repo.list("jobs", {"recipe_id": rid})) == 2
        assert await repo.get("revisions", original["id"]) == original


async def test_running_job_is_fenced_after_prose_edit(app):
    from recipe_creator.jobs import JobRunner
    repo = app.state.repo
    async with client(app) as browser:
        await identify(app, browser)
        rid = (await browser.post("/recipes", json=draft())).json()["id"]
        runner = JobRunner(repo, app.state.settings)
        claimed = await runner._claim()
        response = await browser.put(f"/recipes/{rid}", json=draft(expected_revision=1, directions="New prose"))
        assert response.status_code == 200
        await runner._finish(claimed, groups=[])
        assert (await repo.get("jobs", claimed["id"]))["state"] == "stale"
        assert (await repo.get("recipes", rid))["ingredient_groups"]
        jobs = await repo.list("jobs", {"recipe_id": rid})
        assert len(jobs) == 2 and any(job["state"] == "pending" and job["input_revision"] == 2 for job in jobs)


@pytest.mark.parametrize("kind,budget", [("recipe_create", 20), ("recipe_update", 100), ("recipe_enrich", 40)])
@pytest.mark.parametrize("global_scope", [False, True])
async def test_persistent_write_limits_and_free_reads(app, kind, budget, global_scope):
    async with client(app) as browser:
        await identify(app, browser)
        rid = (await browser.post("/recipes", json=draft())).json()["id"]
        scope = "global" if global_scope else "ip:" + digest("127.0.0.1")
        timestamp = now()
        key = digest(f"{kind}:{scope}:{int(timestamp.timestamp()) // 86400}")
        data = {"scope": scope, "kind": kind, "count": budget * (20 if global_scope else 1),
                "expires_at": timestamp + timedelta(days=2)}
        if await app.state.repo.get("usage", key):
            await app.state.repo.update("usage", key, data)
        else:
            await app.state.repo.create("usage", data, id=key)
        recipes.invalidate_catalog(app.state.repo)
        before = await app.state.repo.list("usage")
        assert (await browser.get("/recipes")).status_code == 200
        assert (await browser.get("/tags")).status_code == 200
        assert (await browser.get(f"/recipes/{rid}")).status_code == 200
        assert await app.state.repo.list("usage") == before
        if kind == "recipe_create":
            response = await browser.post("/recipes", json=draft())
        elif kind == "recipe_update":
            response = await browser.put(f"/recipes/{rid}", json=draft(expected_revision=1))
        else:
            response = await browser.post(f"/recipes/{rid}/enrich")
        assert response.status_code == 429 and response.headers["retry-after"] == "86400"
        assert (await app.state.repo.get("usage", key))["count"] == data["count"] + 1
        assert len(await app.state.repo.list("recipes")) == 1
