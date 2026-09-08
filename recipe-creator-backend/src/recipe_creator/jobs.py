"""Persisted enrichment queue with transactional leases and stale-input protection."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from logly import logger

from .ai import consume_ai_quota, estimate_grams
from .ingredients import enrich_ingredient_groups, estimation_eligible, ingredient_hash
from .repository import ConflictError
from .settings import Settings


def _now() -> datetime:
    return datetime.now(UTC)


def _date(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def enrichment_hash(recipe: dict) -> str:
    content = {"source_text": recipe.get("source_text", ""), "ingredient_groups": [
        {"title": group.get("title", ""),
         "ingredients": [ingredient_hash(item) for item in group.get("ingredients", [])]}
        for group in recipe.get("ingredient_groups", [])]}
    return sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


async def enqueue_enrichment(repo, recipe: dict) -> dict:
    """Idempotent by recipe/revision/input; pass tx to atomically save recipe + job."""
    digest = enrichment_hash(recipe)
    recipe_id = str(recipe["id"]).removeprefix("recipes:")
    revision = recipe["revision"]
    key = sha256(f"grams:{recipe_id}:{revision}:{digest}".encode()).hexdigest()
    async def enqueue(tx):
        existing = await tx.get("jobs", key)
        if existing:
            return existing
        return await tx.create("jobs", {
            "recipe_id": recipe_id, "kind": "grams", "input_revision": revision,
            "input_hash": digest, "dedupe_key": key, "state": "pending",
            "attempts": 0, "revision": 1, "available_at": _now(), "lease_until": None,
        }, id=key)

    # Let the caller retry the entire recipe-save transaction on conflict.
    if getattr(repo, "_tx", None) is not None:
        return await enqueue(repo)
    for attempt in range(4):
        try:
            async with repo.transaction() as tx:
                result = await enqueue(tx)
            return result
        except ConflictError:
            if attempt == 3:
                raise
            await asyncio.sleep(0)
    raise AssertionError("Unreachable")


async def enrich_recipe(recipe: dict, settings: Settings, *, repo=None) -> list[dict]:
    """Mass is deterministic; optional ingredient-dependent AI uses durable budgets."""
    groups = enrich_ingredient_groups(recipe.get("ingredient_groups", []))
    if not getattr(settings, "ai_estimation_enabled", True) or not settings.ai_api_key.get_secret_value():
        return groups
    for group in groups:
        for item in group.get("ingredients", []):
            if not estimation_eligible(item):
                continue
            if repo is None:
                raise ValueError("AI estimation requires a durable quota repository")
            await consume_ai_quota(repo, settings, user_id=recipe.get("owner_id"))
            estimate = await estimate_grams(item, settings)
            item.update(grams_input_hash=estimate["input_hash"], grams_estimate=True,
                        grams_provenance={"source": "ai_estimate", **estimate})
            if estimate["refusal_reason"]:
                item["grams_error"] = "estimation_refused"
            else:
                item.update(grams={"low": estimate["grams_low"], "high": estimate["grams_high"],
                                   "source": "ai_estimate", "input_hash": estimate["input_hash"],
                                   "model": estimate["model"], "basis": estimate["basis"],
                                   "regional_assumption": estimate["regional_assumption"],
                                   "assumptions": estimate["assumptions"]},
                            grams_range=[estimate["grams_low"], estimate["grams_high"]])
    return groups


class JobRunner:
    def __init__(self, repo, settings: Settings):
        self.repo = repo
        self.settings = settings
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._tasks:
            return
        self._stop.clear()
        self._tasks = [asyncio.create_task(self.run(), name=f"enrichment-{i}")
                       for i in range(self.settings.ai_concurrency)]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        # Cancelled work remains leased; another process can recover it after expiry.

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                if await self.run_once():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Provider/database messages can contain source text or credentials.
                logger.exception("Enrichment polling failed ({})", type(exc).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(), self.settings.job_poll_seconds)
            except TimeoutError:
                pass

    def _eligible(self, job: dict, now: datetime) -> bool:
        if job.get("kind") != "grams":
            return False
        if job["state"] == "running":
            return (_date(job.get("lease_until")) or now) <= now
        return (job["state"] in {"pending", "retry"}
                and (_date(job.get("available_at")) or now) <= now)

    async def _claim(self) -> dict | None:
        for state in ("running", "pending", "retry"):
            start = 0
            while True:
                rows = await self.repo.list("jobs", {"state": state}, limit=100, start=start)
                for row in rows:
                    if not self._eligible(row, _now()):
                        continue
                    try:
                        async with self.repo.transaction() as tx:
                            job = await tx.get("jobs", row["id"])
                            if not job or not self._eligible(job, _now()):
                                continue
                            if job["attempts"] >= self.settings.job_max_attempts:
                                await tx.compare_and_swap("jobs", job["id"], job["revision"], {
                                    "state": "failed", "lease_until": None, "lease_token": None,
                                    "last_error": "attempts_exhausted",
                                })
                                continue
                            claimed = await tx.compare_and_swap("jobs", job["id"], job["revision"], {
                                "state": "running", "attempts": job["attempts"] + 1,
                                "lease_token": uuid4().hex,
                                "lease_until": _now() + timedelta(seconds=self.settings.job_lease_seconds),
                            })
                        return claimed
                    except ConflictError:
                        continue
                if len(rows) < 100:
                    break
                start += len(rows)
        return None

    @staticmethod
    def _matches(recipe: dict | None, job: dict) -> bool:
        return bool(recipe and not recipe.get("deleted_at")
                    and recipe["revision"] == job["input_revision"]
                    and enrichment_hash(recipe) == job["input_hash"])

    @staticmethod
    def _owns(current: dict | None, claimed: dict) -> bool:
        return bool(current and current["state"] == "running"
                    and current.get("lease_token") == claimed.get("lease_token")
                    and current["revision"] == claimed["revision"]
                    and (_date(current.get("lease_until")) or _now()) > _now())

    async def _finish(self, claimed: dict, groups=None, error: str | None = None) -> None:
        for attempt in range(4):
            try:
                async with self.repo.transaction() as tx:
                    job = await tx.get("jobs", claimed["id"])
                    if not self._owns(job, claimed):
                        return
                    recipe = await tx.get("recipes", job["recipe_id"])
                    changes = {"lease_until": None, "lease_token": None}
                    if not self._matches(recipe, job):
                        changes.update(state="stale", last_error="input_changed")
                    elif error:
                        changes.update(
                            state="failed" if job["attempts"] >= self.settings.job_max_attempts else "retry",
                            last_error=error,
                            available_at=_now() + timedelta(seconds=min(300, 2 ** min(job["attempts"], 8))),
                        )
                    else:
                        if groups != recipe.get("ingredient_groups", []):
                            await tx.compare_and_swap("recipes", recipe["id"], recipe["revision"],
                                                      {"ingredient_groups": groups})
                            history = await tx.list("revisions", {"recipe_id": recipe["id"], "revision": recipe["revision"]}, limit=1)
                            if not history:
                                await tx.create("revisions", {
                                    "recipe_id": recipe["id"], "revision": recipe["revision"],
                                    "reason": "enrichment", "content": recipe,
                                })
                        changes.update(state="succeeded", last_error=None, completed_at=_now())
                    await tx.compare_and_swap("jobs", job["id"], job["revision"], changes)
                return
            except ConflictError:
                if attempt == 3:
                    raise
                await asyncio.sleep(0)

    async def run_once(self) -> bool:
        """Process at most one persisted job; convenient for tests and external schedulers."""
        job = await self._claim()
        if job is None:
            return False
        try:
            recipe = await self.repo.get("recipes", job["recipe_id"])
            if not self._matches(recipe, job):
                await self._finish(job)
                return True
            # Work must end before lease expiry; no expired worker can publish results.
            timeout = min(self.settings.ai_timeout_seconds, self.settings.job_lease_seconds * .8)
            async with asyncio.timeout(timeout):
                groups = await enrich_recipe(deepcopy(recipe), self.settings, repo=self.repo)
            await self._finish(job, groups=groups)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._finish(job, error=type(exc).__name__)
        return True
