import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from recipe_creator import jobs
from recipe_creator.jobs import JobRunner, enqueue_enrichment
from recipe_creator.repository import ConflictError
from recipe_creator.settings import Settings


class MemoryRepository:
    """Transactional fake; shared storage survives runner recreation."""
    def __init__(self):
        self.rows = {"recipes": {}, "jobs": {}, "revisions": {}, "usage": {}}
        self._tx = None
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            previous = deepcopy(self.rows)
            try:
                self._tx = True
                yield self
            except BaseException:
                self.rows = previous
                raise
            finally:
                self._tx = None

    async def get(self, table, id):
        return deepcopy(self.rows[table].get(id))

    async def list(self, table, filters=None, limit=100, start=0):
        rows = [row for row in self.rows[table].values()
                if all(row.get(k) == v for k, v in (filters or {}).items())]
        return deepcopy(sorted(rows, key=lambda row: row["id"])[start:start + limit])

    async def create(self, table, data, id=None):
        id = id or str(len(self.rows[table]) + 1)
        if id in self.rows[table]:
            raise ConflictError("duplicate")
        self.rows[table][id] = {"id": id, **deepcopy(data)}
        return await self.get(table, id)

    async def compare_and_swap(self, table, id, expected_revision, data):
        row = self.rows[table][id]
        if row["revision"] != expected_revision:
            raise ConflictError("changed")
        row.update(deepcopy(data), revision=expected_revision + 1)
        return await self.get(table, id)


@pytest.fixture
def repo():
    repo = MemoryRepository()
    repo.rows["recipes"]["r"] = {
        "id": "r", "revision": 1, "source_text": "½ kg flour",
        "ingredient_groups": [{"title": "", "ingredients": [{"text": "½ kg flour"}]}],
    }
    return repo


async def enqueue(repo):
    return await enqueue_enrichment(repo, await repo.get("recipes", "r"))


async def test_dedupes_and_persists_across_runner_restart(repo):
    results = await asyncio.gather(*(enqueue(repo) for _ in range(5)))
    assert len({row["id"] for row in results}) == 1
    assert len(repo.rows["jobs"]) == 1
    runner = JobRunner(repo, Settings())
    assert await runner.run_once()
    recipe = await repo.get("recipes", "r")
    assert recipe["ingredient_groups"][0]["ingredients"][0]["grams"] == 500
    assert recipe["revision"] == 2
    job = await repo.get("jobs", results[0]["id"])
    assert job["state"] == "succeeded"
    assert job["attempts"] == 1
    assert len(repo.rows["revisions"]) == 1
    assert not await JobRunner(repo, Settings()).run_once()
    assert recipe["source_text"] == "½ kg flour"


async def test_two_workers_only_execute_once(repo, monkeypatch):
    await enqueue(repo)
    calls = 0
    original = jobs.enrich_recipe

    async def enrich(recipe, settings, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(.01)
        return await original(recipe, settings)

    monkeypatch.setattr(jobs, "enrich_recipe", enrich)
    runners = [JobRunner(repo, Settings()) for _ in range(2)]
    assert sorted(await asyncio.gather(*(r.run_once() for r in runners))) == [False, True]
    assert calls == 1


@pytest.mark.parametrize("change", [
    {"revision": 2}, {"source_text": "edited"}, {"deleted_at": "2026-01-01T00:00:00Z"},
    {"ingredient_groups": [{"ingredients": [{"text": "1 cup sugar"}]}]},
])
async def test_stale_result_never_overwrites_edit(repo, monkeypatch, change):
    job = await enqueue(repo)
    original = jobs.enrich_recipe

    async def edit_during_work(recipe, settings, **kwargs):
        repo.rows["recipes"]["r"].update(deepcopy(change))
        return await original(recipe, settings)

    monkeypatch.setattr(jobs, "enrich_recipe", edit_during_work)
    await JobRunner(repo, Settings()).run_once()
    assert (await repo.get("jobs", job["id"]))["state"] == "stale"
    current = await repo.get("recipes", "r")
    assert all(current[k] == v for k, v in change.items())
    assert "grams" not in current["ingredient_groups"][0]["ingredients"][0]


async def test_retries_are_persisted_bounded_and_sanitized(repo, monkeypatch):
    job = await enqueue(repo)

    async def fail(*args, **kwargs):
        raise RuntimeError("SECRET provider response")

    monkeypatch.setattr(jobs, "enrich_recipe", fail)
    settings = Settings(job_max_attempts=2)
    await JobRunner(repo, settings).run_once()
    row = repo.rows["jobs"][job["id"]]
    assert row["state"] == "retry"
    assert row["last_error"] == "RuntimeError"
    assert row["available_at"] > datetime.now(UTC)
    assert not await JobRunner(repo, settings).run_once()
    row["available_at"] = datetime.now(UTC) - timedelta(seconds=1)
    await JobRunner(repo, settings).run_once()
    assert row["state"] == "failed"
    assert row["attempts"] == 2
    assert not await JobRunner(repo, settings).run_once()


async def test_expired_lease_recovers_and_fences_previous_worker(repo):
    job = await enqueue(repo)
    first = JobRunner(repo, Settings())
    old_claim = await first._claim()
    repo.rows["jobs"][job["id"]]["lease_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    second = JobRunner(repo, Settings())
    new_claim = await second._claim()
    assert new_claim["attempts"] == 2
    await first._finish(old_claim, groups=[])
    assert repo.rows["jobs"][job["id"]]["state"] == "running"
    await second._finish(new_claim, groups=await jobs.enrich_recipe(await repo.get("recipes", "r"), Settings()))
    assert repo.rows["jobs"][job["id"]]["state"] == "succeeded"


async def test_cancel_leaves_recoverable_lease(repo, monkeypatch):
    job = await enqueue(repo)
    started = asyncio.Event()

    async def wait_forever(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(jobs, "enrich_recipe", wait_forever)
    runner = JobRunner(repo, Settings())
    await runner.start()
    await started.wait()
    await runner.stop()
    assert repo.rows["jobs"][job["id"]]["state"] == "running"
    assert not runner._tasks


async def test_timeout_retries_and_exhausted_crashes_fail(repo, monkeypatch):
    job = await enqueue(repo)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(jobs, "enrich_recipe", slow)
    runner = JobRunner(repo, Settings(ai_timeout_seconds=.001, job_max_attempts=1))
    await runner.run_once()
    assert repo.rows["jobs"][job["id"]]["last_error"] == "TimeoutError"
    row = repo.rows["jobs"][job["id"]]
    row.update(state="running", lease_until=datetime.now(UTC) - timedelta(seconds=1))
    assert not await runner.run_once()
    assert row["state"] == "failed"


async def test_success_and_recipe_write_are_one_transaction(repo, monkeypatch):
    job = await enqueue(repo)
    runner = JobRunner(repo, Settings())
    claim = await runner._claim()
    original = repo.compare_and_swap
    failures = 0

    async def conflict_once(table, id, revision, data):
        nonlocal failures
        if table == "jobs" and data.get("state") == "succeeded" and failures == 0:
            failures += 1
            raise ConflictError("race at commit")
        return await original(table, id, revision, data)

    monkeypatch.setattr(repo, "compare_and_swap", conflict_once)
    groups = await jobs.enrich_recipe(await repo.get("recipes", "r"), Settings())
    await runner._finish(claim, groups=groups)
    assert (await repo.get("recipes", "r"))["revision"] == 2
    assert len(repo.rows["revisions"]) == 1
    assert (await repo.get("jobs", job["id"]))["state"] == "succeeded"


async def test_queue_paginates_past_unavailable_work(repo):
    future = datetime.now(UTC) + timedelta(days=1)
    for i in range(105):
        await repo.create("jobs", {"kind": "grams", "state": "pending", "available_at": future}, id=f"a{i:03}")
    target = await enqueue(repo)
    # Ensure the real job sorts after the first page.
    row = repo.rows["jobs"].pop(target["id"])
    row["id"] = "z"
    repo.rows["jobs"]["z"] = row
    assert await JobRunner(repo, Settings()).run_once()
    assert repo.rows["jobs"]["z"]["state"] == "succeeded"


async def test_save_and_enqueue_existing_transaction_roll_back(repo):
    with pytest.raises(RuntimeError, match="abort"):
        async with repo.transaction() as tx:
            recipe = await tx.create("recipes", {"revision": 1}, id="new")
            await enqueue_enrichment(tx, recipe)
            raise RuntimeError("abort")
    assert await repo.get("recipes", "new") is None
    assert not repo.rows["jobs"]


async def test_quota_shared_global_user_ip_atomic_and_durable(repo):
    from recipe_creator.ai import AIQuotaExceeded, consume_ai_quota
    settings = Settings(ai_daily_limit=2, ai_ip_daily_limit=2, ai_global_daily_limit=6)
    await consume_ai_quota(repo, settings, user_id="u", ip="127.0.0.1")
    before = deepcopy(repo.rows["usage"])
    for user, ip in (("u", "other"), ("other", "127.0.0.1")):
        with pytest.raises(AIQuotaExceeded):
            await consume_ai_quota(repo, settings, user_id=user, ip=ip)
        assert repo.rows["usage"] == before
    assert len(before) == 3
    assert all(row["count"] == 2 for row in before.values())
    assert all("127.0.0.1" not in str(row) for row in before.values())


async def test_ai_job_persists_provenance_and_uses_quota(repo):
    from pydantic_ai.models.test import TestModel
    from recipe_creator.ai import estimator_agent
    from recipe_creator.ingredients import REGIONAL_ASSUMPTION, ingredient_hash
    item = {"text": "1 cup flour"}
    repo.rows["recipes"]["r"].update(ingredient_groups=[{"ingredients": [item]}], owner_id="u")
    job = await enqueue(repo)
    estimate = {"input_hash": ingredient_hash(item), "grams_low": 115, "grams_high": 130,
                "basis": "All-purpose flour density", "regional_assumption": REGIONAL_ASSUMPTION,
                "assumptions": ["Level spooned cup"]}
    with estimator_agent.override(model=TestModel(custom_output_args=estimate)):
        await JobRunner(repo, Settings(ai_api_key="test-only")).run_once()
    current = await repo.get("recipes", "r")
    enriched = current["ingredient_groups"][0]["ingredients"][0]
    assert enriched["grams"]["low"] == 115
    assert enriched["grams_provenance"]["basis"] == estimate["basis"]
    assert enriched["grams_provenance"]["assumptions"] == estimate["assumptions"]
    assert enriched["grams_provenance"]["model"] == Settings().ai_model
    assert (await repo.get("jobs", job["id"]))["state"] == "succeeded"
    assert len(repo.rows["usage"]) == 2
    assert all(row["count"] == 2 for row in repo.rows["usage"].values())


async def test_refusal_is_persisted_without_fabricated_grams(repo):
    from pydantic_ai.models.test import TestModel
    from recipe_creator.ai import estimator_agent
    from recipe_creator.ingredients import REGIONAL_ASSUMPTION, ingredient_hash
    item = {"text": "1 cup mystery flour"}
    repo.rows["recipes"]["r"]["ingredient_groups"] = [{"ingredients": [item]}]
    job = await enqueue(repo)
    refusal = {"input_hash": ingredient_hash(item), "grams_low": None, "grams_high": None,
               "basis": "Density unknown", "regional_assumption": REGIONAL_ASSUMPTION,
               "assumptions": ["No density assumed"], "refusal_reason": "Unknown ingredient"}
    with estimator_agent.override(model=TestModel(custom_output_args=refusal)):
        await JobRunner(repo, Settings(ai_api_key="test-only")).run_once()
    saved = (await repo.get("recipes", "r"))["ingredient_groups"][0]["ingredients"][0]
    assert saved.get("grams") is None
    assert saved["grams_error"] == "estimation_refused"
    assert saved["grams_provenance"]["refusal_reason"] == "Unknown ingredient"
    assert (await repo.get("jobs", job["id"]))["state"] == "succeeded"


async def test_quota_failure_persisted_without_provider_call(repo):
    from pydantic_ai import models
    previous = models.ALLOW_MODEL_REQUESTS
    models.ALLOW_MODEL_REQUESTS = False
    try:
        repo.rows["recipes"]["r"]["ingredient_groups"] = [{"ingredients": [{"text": "1 cup flour"}]}]
        job = await enqueue(repo)
        await JobRunner(repo, Settings(ai_api_key="test-only", ai_global_daily_limit=0)).run_once()
    finally:
        models.ALLOW_MODEL_REQUESTS = previous
    assert (await repo.get("jobs", job["id"]))["last_error"] == "AIQuotaExceeded"
    assert not repo.rows["usage"]


@pytest.fixture
async def real_repo():
    import os
    from uuid import uuid4
    from recipe_creator.repository import Repository
    url = os.getenv("RECIPE_TEST_DB_URL")
    if not url:
        pytest.skip("Set RECIPE_TEST_DB_URL for real DB job tests")
    settings = Settings(db_url=url, db_user=os.getenv("RECIPE_TEST_DB_USER", "root"),
                        db_password=os.getenv("RECIPE_TEST_DB_PASSWORD", ""),
                        db_namespace="recipe_tests", db_database="jobs_" + uuid4().hex)
    async with Repository(settings) as repo:
        await repo.migrate()
        try:
            yield repo
        finally:
            await repo._connection.query(f"REMOVE DATABASE `{settings.db_database}`;")


@pytest.mark.integration
async def test_real_db_atomic_enqueue_rollback_restart_and_fencing(real_repo):
    repo = real_repo
    with pytest.raises(RuntimeError, match="abort"):
        async with repo.transaction() as tx:
            recipe = await tx.create("recipes", {"title": "Rollback"})
            await enqueue_enrichment(tx, recipe)
            raise RuntimeError("abort")
    assert not await repo.list("recipes")
    assert not await repo.list("jobs")
    async with repo.transaction() as tx:
        recipe = await tx.create("recipes", {"ingredient_groups": [{"ingredients": [{"text": "½ kg flour"}]}]})
        job = await enqueue_enrichment(tx, recipe)
        assert (await enqueue_enrichment(tx, recipe))["id"] == job["id"]
    first = JobRunner(repo, Settings())
    old = await first._claim()
    await repo.update("jobs", job["id"], {"lease_until": datetime.now(UTC) - timedelta(seconds=1)})
    second = JobRunner(repo, Settings())
    current = await second._claim()
    assert current["attempts"] == 2
    await first._finish(old, groups=[])
    assert (await repo.get("jobs", job["id"]))["state"] == "running"
    await second._finish(current, groups=await jobs.enrich_recipe(recipe, Settings()))
    saved = await repo.get("recipes", recipe["id"])
    assert saved["revision"] == 2
    assert saved["ingredient_groups"][0]["ingredients"][0]["grams"] == 500
    assert (await repo.get("jobs", job["id"]))["state"] == "succeeded"
    assert len(await repo.list("revisions")) == 1
    with pytest.raises(ConflictError):
        await repo.save_recipe_revision(recipe["id"], 1, {"title": "Stale browser"})
    assert not await JobRunner(repo, Settings()).run_once()


@pytest.mark.integration
async def test_real_db_quota_concurrency_and_rollback(real_repo):
    from recipe_creator.ai import AIQuotaExceeded, consume_ai_quota
    settings = Settings(ai_daily_limit=4, ai_global_daily_limit=4)
    async def reserve():
        try:
            await consume_ai_quota(real_repo, settings, user_id="u", ip="127.0.0.1")
            return True
        except AIQuotaExceeded:
            return False
    assert sum(await asyncio.gather(*(reserve() for _ in range(6)))) == 2
    rows = await real_repo.list("usage")
    assert len(rows) == 3
    assert all(row["count"] == 4 for row in rows)
    with pytest.raises(AIQuotaExceeded):
        await consume_ai_quota(real_repo, settings, user_id="new", ip="new")
    assert await real_repo.list("usage") == rows


@pytest.mark.integration
async def test_real_db_ai_output_persistence(real_repo):
    from pydantic_ai.models.test import TestModel
    from recipe_creator.ai import estimator_agent
    from recipe_creator.ingredients import REGIONAL_ASSUMPTION, ingredient_hash
    item = {"text": "1 cup flour"}
    user = await real_repo.create("users", {"display_name": "Test"})
    recipe = await real_repo.create("recipes", {"owner_id": user["id"],
        "ingredient_groups": [{"ingredients": [item]}]})
    job = await enqueue_enrichment(real_repo, recipe)
    estimate = {"input_hash": ingredient_hash(item), "grams_low": 115, "grams_high": 130,
        "basis": "Loose flour density", "regional_assumption": REGIONAL_ASSUMPTION,
        "assumptions": ["Level cup"]}
    with estimator_agent.override(model=TestModel(custom_output_args=estimate)):
        await JobRunner(real_repo, Settings(ai_api_key="test-only")).run_once()
    completed = await real_repo.get("jobs", job["id"])
    assert completed["state"] == "succeeded", completed.get("last_error")
    saved = await real_repo.get("recipes", recipe["id"])
    grams = saved["ingredient_groups"][0]["ingredients"][0]["grams"]
    assert grams["input_hash"] == ingredient_hash(item)
    assert grams["basis"] == estimate["basis"]
    assert grams["model"] == Settings().ai_model


@pytest.mark.integration
async def test_real_db_failure_persistence(real_repo, monkeypatch):
    item = {"text": "1 cup flour"}
    recipe = await real_repo.create("recipes", {"ingredient_groups": [{"ingredients": [item]}]})
    failed = await enqueue_enrichment(real_repo, recipe)
    async def fail(*args, **kwargs):
        raise RuntimeError("secret provider payload")
    monkeypatch.setattr(jobs, "estimate_grams", fail)
    await JobRunner(real_repo, Settings(ai_api_key="test-only", job_max_attempts=1)).run_once()
    row = await real_repo.get("jobs", failed["id"])
    assert row["state"] == "failed"
    assert row["last_error"] == "RuntimeError"
