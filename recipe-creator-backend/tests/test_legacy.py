from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from uuid import uuid4

import pytest

from recipe_creator.legacy import convert_recipe, import_recipes, load_snapshot
from recipe_creator.repository import Repository
from recipe_creator.settings import Settings


@pytest.fixture
def legacy():
    return {"uuid": "F44F2768-6F47-43CD-BAC0-E5758C0A5C94", "title": "  Crème & Tea  ",
            "description": "A paragraph.\n\nSecond\r\n", "directions": "Fold 3 times.\n\nDON'T rewrite!  ",
            "notes": "=== unusual ---\n\tNotes.", "tags": ["Dinner", " dinner ", "Dinner", ""],
            "ingredientCategories": {"": ["  ½ cup milk  ", ""], "Empty": [], "--- Sauce ===": ["salt to taste"]},
            "unknown_metadata": {"keep": [None, 1, "verbatim"]}}


class MemoryRepository:
    def __init__(self):
        self.rows = {"recipes": {}, "revisions": {}}

    @asynccontextmanager
    async def transaction(self):
        saved = deepcopy(self.rows)
        try:
            yield self
        except BaseException:
            self.rows = saved
            raise

    async def get(self, table, id):
        return deepcopy(self.rows[table].get(id))

    async def create(self, table, data, id=None):
        assert id not in self.rows[table]
        self.rows[table][id] = {"id": id, **deepcopy(data)}
        return await self.get(table, id)


def test_lossless_groups_fields_ids_and_no_invented_metadata(legacy):
    data = convert_recipe(legacy)
    for key in ("title", "description", "directions", "notes", "tags"):
        assert data[key] == legacy[key]
    assert data["legacy_uuid"] == legacy["uuid"]
    assert data["owner_id"] is None
    assert data["legacy_created_at"] is None
    assert "created_at" not in data
    assert data["original_snapshot"] == legacy
    groups = data["ingredient_groups"]
    assert [group["name"] for group in groups] == list(legacy["ingredientCategories"])
    assert [[row["original_text"] for row in group["ingredients"]] for group in groups] == list(
        legacy["ingredientCategories"].values())
    assert all(row["quantity"] is None and row["unit"] == "" for group in groups for row in group["ingredients"])
    assert convert_recipe(legacy) == data
    data["original_snapshot"]["unknown_metadata"]["keep"].append("changed")
    assert legacy["unknown_metadata"]["keep"] == [None, 1, "verbatim"]


@pytest.mark.parametrize("wrapper", [False, True])
def test_explicit_json_shapes(tmp_path, legacy, wrapper):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"recipes": [legacy]} if wrapper else [legacy]))
    assert load_snapshot(path) == [legacy]


@pytest.mark.parametrize("content", ['{}', '{"recipes":{}}', '[{"a":1,"a":2}]', '[NaN]'])
def test_invalid_json_rejected(tmp_path, content):
    path = tmp_path / "snapshot.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_snapshot(path)


async def test_dry_run_duplicate_conflict_and_atomic_validation(legacy):
    repo = MemoryRepository()
    report = await import_recipes(repo, [legacy, legacy], dry_run=True)
    assert report.ok and report.would_create == [legacy["uuid"]]
    assert report.duplicates == [legacy["uuid"]]
    assert not repo.rows["recipes"]
    changed = {**legacy, "notes": "different"}
    report = await import_recipes(repo, [legacy, changed])
    assert not report.ok and report.conflicts == [legacy["uuid"]]
    assert not repo.rows["recipes"]
    report = await import_recipes(repo, [legacy, {**legacy, "uuid": "bad"}])
    assert not report.ok and report.errors
    assert not repo.rows["recipes"]


async def test_idempotent_after_edits_and_conflicting_existing_record(legacy):
    repo = MemoryRepository()
    first = await import_recipes(repo, [legacy])
    assert first.created == [legacy["uuid"]]
    saved = deepcopy(repo.rows["revisions"])
    repo.rows["recipes"][legacy["uuid"]]["title"] = "Explicit later edit"
    second = await import_recipes(repo, [legacy])
    assert second.unchanged == [legacy["uuid"]] and not second.created
    assert repo.rows["recipes"][legacy["uuid"]]["title"] == "Explicit later edit"
    assert repo.rows["revisions"] == saved
    conflict = await import_recipes(repo, [{**legacy, "tags": ["changed"]}])
    assert conflict.conflicts == [legacy["uuid"]]
    assert repo.rows["revisions"] == saved
    repo.rows["revisions"].clear()
    assert not (await import_recipes(repo, [legacy])).ok


async def test_category_order_change_is_conflict(legacy):
    repo = MemoryRepository()
    await import_recipes(repo, [legacy])
    changed = {**legacy, "ingredientCategories": dict(reversed(list(legacy["ingredientCategories"].items())))}
    assert (await import_recipes(repo, [changed])).conflicts == [legacy["uuid"]]


async def test_history_failure_rolls_back_recipe(legacy):
    repo = MemoryRepository()
    create = repo.create

    async def fail_history(table, data, id=None):
        if table == "revisions":
            raise RuntimeError("history write failed")
        return await create(table, data, id)

    repo.create = fail_history
    with pytest.raises(RuntimeError):
        await import_recipes(repo, [legacy])
    assert not repo.rows["recipes"]


@pytest.mark.integration
async def test_real_lossless_idempotent_import(legacy):
    url = os.getenv("RECIPE_TEST_DB_URL")
    if not url:
        pytest.skip("Set RECIPE_TEST_DB_URL for real SurrealDB import")
    settings = Settings(db_url=url, db_user=os.getenv("RECIPE_TEST_DB_USER", "root"),
                        db_password=os.getenv("RECIPE_TEST_DB_PASSWORD", ""),
                        db_namespace="recipe_tests", db_database="legacy_" + uuid4().hex)
    async with Repository(settings) as repo:
        await repo.migrate()
        try:
            assert (await import_recipes(repo, [legacy], dry_run=True)).would_create == [legacy["uuid"]]
            assert not await repo.list("recipes")
            assert (await import_recipes(repo, [legacy])).created == [legacy["uuid"]]
            found = await repo.get("recipes", legacy["uuid"])
            assert found["id"] == legacy["uuid"]
            for key in ("description", "directions", "notes", "tags"):
                assert found[key] == legacy[key]
            assert found["original_snapshot"] == legacy
            assert [group["name"] for group in found["ingredient_groups"]] == list(legacy["ingredientCategories"])
            await repo.update("recipes", legacy["uuid"], {"title": "Edited", "revision": 2})
            assert (await import_recipes(repo, [legacy])).unchanged == [legacy["uuid"]]
            assert (await repo.get("recipes", legacy["uuid"]))["title"] == "Edited"
            with pytest.raises(ValueError, match="immutable"):
                await repo.update("revisions", "legacy-" + legacy["uuid"], {"content": {}})
        finally:
            await repo._connection.query(f"REMOVE DATABASE `{settings.db_database}`;")
