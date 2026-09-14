from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from uuid import uuid4

import pytest
from recipe_creator import ai

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


@pytest.fixture
def parse_calls(monkeypatch):
    calls = []

    async def organize(source, settings):
        calls.append(source)
        return {
            "description": "Organized description",
            "ingredient_groups": [{
                "name": "Ingredients",
                "ingredients": [{
                    "original_text": "0.5 cup milk",
                    "quantity": "0.5",
                    "quantity_max": None,
                    "unit": "cup",
                    "name": "milk",
                    "preparation": "",
                    "optional": False,
                }],
            }],
            "directions": "Organized directions",
            "notes": "Organized notes",
            "yield_amount": "4",
            "yield_unit": "servings",
            "source_url": "https://example.com/recipe",
        }

    monkeypatch.setattr("recipe_creator.ai.parse_recipe", organize)
    return calls


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


async def test_dry_run_duplicate_conflict_and_atomic_validation(legacy, parse_calls):
    repo = MemoryRepository()
    report = await import_recipes(repo, [legacy, legacy], Settings(), dry_run=True)
    assert report.ok and report.would_create == [legacy["uuid"]]
    assert report.duplicates == [legacy["uuid"]]
    assert not repo.rows["recipes"]
    changed = {**legacy, "notes": "different"}
    report = await import_recipes(repo, [legacy, changed], Settings())
    assert not report.ok and report.conflicts == [legacy["uuid"]]
    assert not repo.rows["recipes"]
    report = await import_recipes(repo, [legacy, {**legacy, "uuid": "bad"}], Settings())
    assert not report.ok and report.errors
    assert not repo.rows["recipes"]
    assert parse_calls == []


async def test_organizes_new_recipe_once_and_preserves_edits(legacy, parse_calls):
    repo = MemoryRepository()
    first = await import_recipes(repo, [legacy], Settings())
    assert first.created == [legacy["uuid"]]
    assert parse_calls == [convert_recipe(legacy)["source_text"]]
    imported = repo.rows["recipes"][legacy["uuid"]]
    ingredient = imported["ingredient_groups"][0]["ingredients"][0]
    assert ingredient["original_text"] == "0.5 cup milk"
    assert ingredient["quantity"] == "0.5"
    assert ingredient["unit"] == "cup"
    assert ingredient["name"] == "milk"
    assert imported["original_snapshot"] == legacy
    saved = deepcopy(repo.rows["revisions"])
    repo.rows["recipes"][legacy["uuid"]]["title"] = "Explicit later edit"
    second = await import_recipes(repo, [legacy], Settings())
    assert second.unchanged == [legacy["uuid"]] and not second.created
    assert len(parse_calls) == 1
    assert repo.rows["recipes"][legacy["uuid"]]["title"] == "Explicit later edit"
    assert repo.rows["revisions"] == saved
    conflict = await import_recipes(repo, [{**legacy, "tags": ["changed"]}], Settings())
    assert conflict.conflicts == [legacy["uuid"]]
    assert len(parse_calls) == 1
    assert repo.rows["revisions"] == saved
    repo.rows["revisions"].clear()
    assert not (await import_recipes(repo, [legacy], Settings())).ok


async def test_organization_failure_keeps_the_whole_import_unwritten(legacy, parse_calls, monkeypatch):
    successful = ai.parse_recipe
    attempted = 0

    async def fail_second(source, settings):
        nonlocal attempted
        attempted += 1
        if attempted == 2:
            raise RuntimeError("provider failed")
        return await successful(source, settings)

    monkeypatch.setattr(ai, "parse_recipe", fail_second)
    second = {**legacy, "uuid": "aa742473-9947-4203-91d2-7495f5bd3b37", "title": "Second"}
    repo = MemoryRepository()
    report = await import_recipes(repo, [legacy, second], Settings())
    assert not report.ok
    assert report.errors == [f"Recipe {second['uuid']}: organization failed"]
    assert attempted == 2
    assert not repo.rows["recipes"]
    assert not repo.rows["revisions"]


async def test_category_order_change_is_conflict(legacy, parse_calls):
    repo = MemoryRepository()
    await import_recipes(repo, [legacy], Settings())
    changed = {**legacy, "ingredientCategories": dict(reversed(list(legacy["ingredientCategories"].items())))}
    assert (await import_recipes(repo, [changed], Settings())).conflicts == [legacy["uuid"]]


async def test_history_failure_rolls_back_recipe(legacy, parse_calls):
    repo = MemoryRepository()
    create = repo.create

    async def fail_history(table, data, id=None):
        if table == "revisions":
            raise RuntimeError("history write failed")
        return await create(table, data, id)

    repo.create = fail_history
    with pytest.raises(RuntimeError):
        await import_recipes(repo, [legacy], Settings())
    assert not repo.rows["recipes"]


@pytest.mark.integration
async def test_real_organized_idempotent_import(legacy, parse_calls):
    url = os.getenv("RECIPE_TEST_DB_URL")
    if not url:
        pytest.skip("Set RECIPE_TEST_DB_URL for real SurrealDB import")
    settings = Settings(db_url=url, db_user=os.getenv("RECIPE_TEST_DB_USER", "root"),
                        db_password=os.getenv("RECIPE_TEST_DB_PASSWORD", ""),
                        db_namespace="recipe_tests", db_database="legacy_" + uuid4().hex)
    async with Repository(settings) as repo:
        await repo.migrate()
        try:
            assert (await import_recipes(repo, [legacy], settings, dry_run=True)).would_create == [legacy["uuid"]]
            assert not await repo.list("recipes")
            assert (await import_recipes(repo, [legacy], settings)).created == [legacy["uuid"]]
            found = await repo.get("recipes", legacy["uuid"])
            assert found["id"] == legacy["uuid"]
            assert found["description"] == "Organized description"
            assert found["directions"] == "Organized directions"
            assert found["notes"] == "Organized notes"
            assert found["original_snapshot"] == legacy
            assert [group["name"] for group in found["ingredient_groups"]] == ["Ingredients"]
            await repo.update("recipes", legacy["uuid"], {"title": "Edited", "revision": 2})
            assert (await import_recipes(repo, [legacy], settings)).unchanged == [legacy["uuid"]]
            assert (await repo.get("recipes", legacy["uuid"]))["title"] == "Edited"
            with pytest.raises(ValueError, match="immutable"):
                await repo.update("revisions", "legacy-" + legacy["uuid"], {"content": {}})
        finally:
            await repo._connection.query(f"REMOVE DATABASE `{settings.db_database}`;")
