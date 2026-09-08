"""Opt in: RECIPE_TEST_DB_URL=http://127.0.0.1:18080 RECIPE_TEST_DB_PASSWORD=... uv run pytest tests/test_repository.py.

Each test uses a fresh real SurrealDB database and removes only that database.
No mock or in-memory repository fallback. The server may use memory or disk.
"""
import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from surreal_orm import SurrealDBConnectionManager as Connections
from surreal_orm.migrations import Migration
from surreal_orm.migrations.operations import CreateTable, RawSQL

from recipe_creator.models import MODELS, Recipe, SiteSettings
from recipe_creator.repository import ConflictError, NotFoundError, Repository, _MigrationExecutor
from recipe_creator.settings import Settings


def test_model_registry_and_nested_validation():
    assert set(MODELS) == {"users", "devices", "pairings", "admin_sessions", "recipes", "revisions", "photos", "jobs", "audit", "usage", "site_settings"}
    assert SiteSettings(copy={"site_title": "Recipes"}).model_dump()["copy"] == {"site_title": "Recipes"}
    with pytest.raises(ValidationError):
        Recipe(ingredient_groups=[{"ingredients": [{"grams": -1}]}])


@pytest_asyncio.fixture
async def repo():
    url = os.getenv("RECIPE_TEST_DB_URL")
    if not url:
        pytest.skip("Set RECIPE_TEST_DB_URL to run against real SurrealDB 3.2.4")
    settings = Settings(db_url=url, db_user=os.getenv("RECIPE_TEST_DB_USER", "root"),
                        db_password=os.getenv("RECIPE_TEST_DB_PASSWORD", ""),
                        db_namespace="recipe_tests", db_database="test_" + uuid4().hex)
    async with Repository(settings) as repository:
        await repository.migrate()
        try:
            yield repository
        finally:
            await repository._connection.query(f"REMOVE DATABASE `{settings.db_database}`;")


@pytest.mark.integration
async def test_nested_roundtrip_schema_and_references(repo):
    user = await repo.create("users", {"display_name": "Ada"})
    data = {
        "title": "Crème & bread", "owner_id": user["id"],
        "source_text": "  1½ tbsp sugar\nDo NOT rewrite.\n",
        "ingredient_groups": [{"title": "Sauce", "ingredients": [
            {"text": "1½ tbsp sugar", "name": "sugar", "quantity": "1½", "unit": "tbsp", "grams": 19.5,
             "provenance": {"alternatives": ["brown", "white"], "verified": False}},
        ]}],
        "metadata": {"yield": {"amount": 2, "unit": "loaves"}, "nullable": None, "steps": [{"text": "Fold", "timers": [3, 5]}]},
    }
    recipe = await repo.create("recipes", data)
    found = await repo.get("recipes", recipe["id"])
    for key, value in data.items():
        assert found[key] == value
    assert (await repo.list("recipes", {"owner_id": user["id"]}))[0]["id"] == recipe["id"]
    with pytest.raises(Exception, match="reference|referenced|delet",):
        await repo.delete("users", user["id"])
    assert await repo.get("users", user["id"])
    info = await repo._connection.query("INFO FOR TABLE recipes;")
    assert "SCHEMAFULL" in str((await repo._connection.query("INFO FOR DB;")).results[0].result)
    assert "record<users>" in str(info.results[0].result)
    assert "FLEXIBLE" in str(info.results[0].result)


@pytest.mark.integration
async def test_all_tables_and_dedupe(repo):
    for table in MODELS:
        from recipe_creator.schemas import DEFAULT_SITE_COPY
        data = {'copy': DEFAULT_SITE_COPY} if table == 'site_settings' else {}
        row = await repo.create(table, {**data, "service_nested": {"null": None, "values": [None, {"ok": True}]}})
        assert (await repo.get(table, row["id"]))["service_nested"] == {"null": None, "values": [None, {"ok": True}]}
    with pytest.raises(NotFoundError):
        await repo.create("devices", {"user_id": "missing"})
    await repo.create("jobs", {"dedupe_key": "one"})
    with pytest.raises(ConflictError):
        await repo.create("jobs", {"dedupe_key": "one"})
    # Optional unique keys must permit multiple absent values.
    await repo.create("jobs", {})


@pytest.mark.integration
async def test_transaction_read_your_writes_rollback_and_isolation(repo):
    with pytest.raises(RuntimeError, match="abort"):
        async with repo.transaction() as tx:
            row = await tx.create("users", {"display_name": "Uncommitted"})
            assert await tx.get("users", row["id"])
            assert await repo.get("users", row["id"]) is None
            await tx.update("users", row["id"], {"display_name": "Updated"})
            assert (await tx.list("users", {"display_name": "Updated"}))[0]["id"] == row["id"]
            raise RuntimeError("abort")
    assert await repo.get("users", row["id"]) is None
    with pytest.raises(Exception):
        async with repo.transaction() as tx:
            await tx.create("users", {}, id="before_error")
            await tx._connection.query("THROW 'database error';")
    assert await repo.get("users", "before_error") is None


@pytest.mark.integration
async def test_crud_pagination_payload_and_duplicate(repo):
    for number in range(4):
        await repo.create("users", {"display_name": str(number), "service_flag": "yes"}, id=f"u{number}")
    assert [x["id"] for x in await repo.list("users", {"service_flag": "yes"}, limit=2, start=1)] == ["u1", "u2"]
    assert await repo.list("users", limit=0) == []
    with pytest.raises(ConflictError):
        await repo.create("users", {}, id="u1")
    updated = await repo.update("users", "u1", {"service_flag": None})
    assert updated["service_flag"] is None
    await repo.delete("users", "u1")
    assert await repo.get("users", "u1") is None
    with pytest.raises(ValueError):
        await repo.list("users", {"state__raw": "bad"})
    with pytest.raises(ValueError):
        await repo.get("users", "u1;DELETE users")


@pytest.mark.integration
async def test_concurrent_revision_history_and_counter(repo):
    recipe = await repo.create("recipes", {"title": "Initial"})
    barrier = asyncio.Barrier(2)

    async def edit(title):
        try:
            async with repo.transaction() as tx:
                assert (await tx.get("recipes", recipe["id"]))["revision"] == 1
                await barrier.wait()
                await tx.save_recipe_revision(recipe["id"], 1, {"title": title})
            return "ok"
        except ConflictError:
            return "conflict"

    results = await asyncio.gather(edit("A"), edit("B"))
    assert sorted(results) == ["conflict", "ok"]
    assert (await repo.get("recipes", recipe["id"]))["revision"] == 2
    history = await repo.list("revisions", {"recipe_id": recipe["id"]})
    assert len(history) == 1
    assert history[0]["content"]["title"] == "Initial"
    with pytest.raises(ConflictError):
        await repo.compare_and_swap("recipes", recipe["id"], 1, {"title": "stale"})
    with pytest.raises(ValueError):
        await repo.update("revisions", history[0]["id"], {})

    bucket = await repo.create("usage", {"scope": "global", "kind": "ai"})
    async def increment():
        for _ in range(30):
            try:
                async with repo.transaction() as tx:
                    current = await tx.get("usage", bucket["id"])
                    await tx.compare_and_swap("usage", bucket["id"], current["revision"], {"count": current["count"] + 1})
                return
            except ConflictError:
                await asyncio.sleep(0.005)
        pytest.fail("Counter exceeded retry budget")
    await asyncio.gather(*(increment() for _ in range(6)))
    assert (await repo.get("usage", bucket["id"]))["count"] == 6


@pytest.mark.integration
async def test_security_cutover_from_existing_database(repo):
    # A separate empty database starts at 0001, containing pre-cutover records.
    settings = repo.settings.model_copy(update={'db_database': 'cutover_' + uuid4().hex})
    async with Repository(settings) as old:
        await old.connect()
        await old._connection.query(f'DEFINE DATABASE `{settings.db_database}`;')
        executor = _MigrationExecutor(settings.migrations_dir)
        try:
            async with Connections.using(old._name):
                await executor.migrate(target='0001_initial', schema_only=False)
            await old._connection.query("CREATE users:legacy CONTENT {display_name: 'Legacy', state: 'active', photo_trust: true, created_at: time::now(), updated_at: time::now(), payload: {is_admin: true}};")
            await old._connection.query("CREATE admin_sessions:legacy CONTENT {secret_hash: 'historical', expires_at: time::now() + 1d, created_at: time::now(), updated_at: time::now(), payload: {}};")
            recipe = await old.create('recipes', {'title': 'Original', 'source_text': ' Exact\\r\\nprose ', 'tags': ['Dinner', 'Breakfast', 'Cafe\u0301'], 'owner_id': 'legacy'})
            before = await old.get('recipes', recipe['id'])
            assert await old.migrate() == ['0002_user_admin_site_settings']
            user = await old.get('users', 'legacy')
            assert user['is_admin'] is False and user['photo_trust'] is True
            assert (await old.get('admin_sessions', 'legacy'))['revoked_at']
            assert await old.get('recipes', recipe['id']) == before
            assert not await old.list('site_settings')
            await old.update('users', 'legacy', {'is_admin': True})
            assert await old.migrate() == []
            assert (await old.get('users', 'legacy'))['is_admin']
        finally:
            await old._connection.query(f'REMOVE DATABASE `{settings.db_database}`;')


@pytest.mark.integration
async def test_migration_idempotency_and_partial_failure(repo, monkeypatch):
    assert await repo.migrate() == []
    executor = _MigrationExecutor(repo.settings.migrations_dir)
    bad = Migration(name="0002_failure_probe", dependencies=["0001_initial"], operations=[
        CreateTable(name="partial_probe"), RawSQL(sql="THROW 'intentional migration failure';"),
    ])
    monkeypatch.setattr(executor, "get_available_migrations", lambda: ["0001_initial", "0002_failure_probe"])
    original_load = executor.load_migration
    monkeypatch.setattr(executor, "load_migration", lambda name: bad if name == bad.name else original_load(name))
    async with Connections.using(repo._name):
        with pytest.raises(Exception, match="intentional"):
            await executor.migrate(schema_only=False)
        assert "0002_failure_probe" not in await executor.get_applied_migrations()
        # DDL persisted despite the later error. Reconcile explicitly before retry.
        assert "partial_probe" in str((await repo._connection.query("INFO FOR DB;")).results[0].result)
        await repo._connection.query("REMOVE TABLE partial_probe;")
        bad.operations.pop()
        assert await executor.migrate(schema_only=False) == ["0002_failure_probe"]
