"""Import a prototype JSON export and organize each newly created recipe."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from logly import logger

from . import ai
from .repository import ConflictError, Repository
from .schemas import normalize_import_tags
from .settings import Settings


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def load_snapshot(path: Path) -> list[dict]:
    """Accept a JSON list or {\"recipes\": [...]} without losing object order."""
    def invalid_constant(value):
        raise ValueError(f"Invalid JSON number: {value}")

    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object,
                      parse_constant=invalid_constant)
    if isinstance(data, dict):
        data = data.get("recipes")
    if not isinstance(data, list):
        raise ValueError("Expected a JSON list or an object containing a recipes list")
    return data


def snapshot_hash(record: dict) -> str:
    # Do not sort keys: category order is meaningful in the original object.
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def convert_recipe(record: dict) -> dict:
    """Preserve source prose and metadata while normalizing legacy tags."""
    if not isinstance(record, dict):
        raise ValueError("Each recipe must be an object")
    identifier = record.get("uuid")
    if not isinstance(identifier, str):
        raise ValueError("Recipe uuid must be a UUID string")
    try:
        parsed = UUID(identifier)
    except ValueError as exc:
        raise ValueError("Recipe uuid must be a UUID string") from exc
    if str(parsed) != identifier.lower():
        raise ValueError("Recipe uuid must use the hyphenated UUID format")
    for key in ("title", "description", "directions", "notes"):
        if not isinstance(record.get(key), str):
            raise ValueError(f"Recipe {identifier}: {key} must be a string")
    tags = record.get("tags")
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError(f"Recipe {identifier}: tags must be a list of strings")
    categories = record.get("ingredientCategories")
    if not isinstance(categories, dict):
        raise ValueError(f"Recipe {identifier}: ingredientCategories must be an object")
    groups = []
    for index, (name, lines) in enumerate(categories.items()):
        if not isinstance(name, str) or not isinstance(lines, list) or any(
            not isinstance(line, str) for line in lines
        ):
            raise ValueError(f"Recipe {identifier}: ingredient categories must contain string lists")
        group_id = str(uuid5(NAMESPACE_URL, f"legacy:{identifier}:group:{index}"))
        groups.append({"id": group_id, "name": name, "ingredients": [
            {"id": str(uuid5(NAMESPACE_URL, f"{group_id}:ingredient:{row}")),
             "original_text": line, "quantity": None, "unit": "", "name": line}
            for row, line in enumerate(lines)
        ]})
    body = [record["description"]]
    for group in groups:
        body.append((f"=== {group['name']} ===\n" if group["name"] else "")
                    + "\n".join(row["original_text"] for row in group["ingredients"]))
    body.extend([record["directions"], record["notes"]])
    return {
        "title": record["title"], "description": record["description"],
        "mode": "structured", "status": "published", "source_text": "\n\n".join(body),
        "ingredient_groups": groups, "directions": record["directions"], "notes": record["notes"],
        "tags": normalize_import_tags(tags), "source_url": "", "modifications": "", "revision": 1,
        "owner_id": None, "legacy_uuid": identifier, "legacy_created_at": None,
        "original_snapshot": deepcopy(record), "original_snapshot_hash": snapshot_hash(record),
    }


async def _organize_recipe(data: dict, settings: Settings) -> dict:
    from .recipes import _groups_output

    result = await ai.parse_recipe(data["source_text"], settings)
    organized = deepcopy(data)
    for key in ("description", "directions", "notes", "yield_amount", "yield_unit", "source_url"):
        organized[key] = result[key]
    organized["ingredient_groups"] = _groups_output(
        result["ingredient_groups"], ai.source_hash(data["source_text"])
    )
    return organized


@dataclass
class ImportReport:
    total: int
    created: list[str] = field(default_factory=list)
    would_create: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    organization_failures: list[dict[str, str]] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self):
        return not self.errors and not self.conflicts and not self.organization_failures

    def as_dict(self):
        return {**asdict(self), "ok": self.ok}


async def import_recipes(
    repo: Repository, records: list[dict], settings: Settings, *, dry_run=False
) -> ImportReport:
    """Validate the snapshot, organize new recipes, then atomically insert successes.

    Existing records are compared to immutable import history, not current edited
    content. Repeat runs never overwrite edits, and one organization failure does
    not discard other successfully organized recipes.
    """
    report = ImportReport(total=len(records), dry_run=dry_run)
    candidates = {}
    for index, record in enumerate(records):
        try:
            data = convert_recipe(record)
        except (ValueError, TypeError) as exc:
            report.errors.append(f"Row {index + 1}: {exc}")
            continue
        identifier = record["uuid"]
        if identifier in candidates:
            report.duplicates.append(identifier)
            if candidates[identifier]["original_snapshot_hash"] != data["original_snapshot_hash"]:
                report.conflicts.append(identifier)
        else:
            candidates[identifier] = data
    if report.errors or report.conflicts:
        return report

    async with repo.transaction() as tx:
        for identifier, data in candidates.items():
            current = await tx.get("recipes", identifier)
            original = await tx.get("revisions", f"legacy-{identifier}")
            if current is not None or original is not None:
                if current is not None and original is not None and original.get("content", {}).get(
                    "original_snapshot_hash"
                ) == data["original_snapshot_hash"]:
                    report.unchanged.append(identifier)
                else:
                    report.conflicts.append(identifier)
            else:
                report.would_create.append(identifier)
    if not report.ok or dry_run:
        return report

    async def organize(identifier):
        try:
            return identifier, await _organize_recipe(candidates[identifier], settings), None
        except Exception as exc:
            logger.opt(exception=exc).error(f"Imported recipe organization failed: {identifier}")
            return identifier, None, exc

    organized = {}
    for identifier, data, error in await asyncio.gather(
        *(organize(identifier) for identifier in report.would_create)
    ):
        if error is not None:
            report.organization_failures.append({
                "id": identifier,
                "title": candidates[identifier]["title"],
            })
            report.would_create.remove(identifier)
        else:
            organized[identifier] = data

    try:
        async with repo.transaction() as tx:
            ready = []
            for identifier in list(report.would_create):
                current = await tx.get("recipes", identifier)
                original = await tx.get("revisions", f"legacy-{identifier}")
                if current is None and original is None:
                    ready.append(identifier)
                    continue
                report.would_create.remove(identifier)
                if current is not None and original is not None and original.get("content", {}).get(
                    "original_snapshot_hash"
                ) == candidates[identifier]["original_snapshot_hash"]:
                    report.unchanged.append(identifier)
                else:
                    report.conflicts.append(identifier)
            if report.conflicts:
                return report
            for identifier in ready:
                data = organized[identifier]
                await tx.create("recipes", data, id=identifier)
                await tx.create("revisions", {
                    "recipe_id": identifier, "actor_id": None, "revision": 1,
                    "reason": "legacy_import", "content": deepcopy(data),
                }, id=f"legacy-{identifier}")
            report.created = ready
            report.would_create.clear()
    except ConflictError:
        report.created.clear()
        report.conflicts.append("Concurrent database change; rerun the import")
    return report
