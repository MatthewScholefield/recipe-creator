import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from recipe_creator import identity, recipes, views
from recipe_creator.repository import Repository
from recipe_creator.security import SecurityMiddleware
from recipe_creator.settings import Settings

class NoPhotos:
    async def visible_photos(self, **_kwargs):
        return []




def tracking_app(repo, settings):
    app = FastAPI()
    app.state.repo = repo
    app.state.settings = settings
    app.state.photos = NoPhotos()
    app.include_router(identity.router)
    app.include_router(recipes.router)
    app.add_middleware(SecurityMiddleware)
    return app


def browser(app, ip="192.0.2.1"):
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 123)), base_url="https://testserver")


async def prepare(browser):
    session = await browser.get("/session")
    browser.headers.update({"Origin": "https://testserver", "X-CSRF-Token": session.json()["csrf_token"]})


@pytest_asyncio.fixture
async def tracking(tmp_path):
    url = os.getenv("RECIPE_TEST_DB_URL")
    if not url:
        pytest.skip("Set RECIPE_TEST_DB_URL to run against disposable real SurrealDB")
    settings = Settings(
        db_url=url,
        db_user=os.getenv("RECIPE_TEST_DB_USER", "root"),
        db_password=os.getenv("RECIPE_TEST_DB_PASSWORD", ""),
        db_namespace="views_" + uuid4().hex,
        db_database="tests",
        public_origin="https://testserver",
        allowed_origins=["https://testserver"],
        media_root=tmp_path,
    )
    async with Repository(settings) as repo:
        await repo.migrate()
        recipe = await repo.create("recipes", {"title": "Soup", "status": "published"}, id="soup")
        try:
            yield repo, settings, recipe
        finally:
            await repo._connection.query(f"REMOVE NAMESPACE `{settings.db_namespace}`;")


@pytest.mark.integration
async def test_rolling_hour_persists_across_restart_and_long_absence(tracking, monkeypatch):
    repo, settings, recipe = tracking
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(views, "utcnow", lambda: timestamp)
    app = tracking_app(repo, settings)
    async with browser(app) as first:
        await prepare(first)
        response = await first.post(f"/recipes/{recipe['id']}/views")
        assert response.status_code == 200, response.text
        assert response.json() == {"total_views": 1, "unique_viewers": 1}
        assert views.VIEWER_COOKIE in first.cookies
        timestamp += timedelta(seconds=3599)
        assert (await first.post("/recipes/soup/views")).json() == {"total_views": 1, "unique_viewers": 1}
        timestamp += timedelta(seconds=1)
        assert (await first.post("/recipes/soup/views")).json() == {"total_views": 2, "unique_viewers": 1}
        saved_cookie = first.cookies[views.VIEWER_COOKIE]

    restarted = tracking_app(repo, settings)
    async with browser(restarted) as same:
        same.cookies.set(views.VIEWER_COOKIE, saved_cookie)
        await prepare(same)
        assert (await same.post("/recipes/soup/views")).json() == {"total_views": 2, "unique_viewers": 1}
        timestamp += timedelta(days=31)
        assert (await same.post("/recipes/soup/views")).json() == {"total_views": 3, "unique_viewers": 1}

    async with browser(restarted) as second:
        await prepare(second)
        assert (await second.post("/recipes/soup/views")).json() == {"total_views": 4, "unique_viewers": 2}

    stats = await repo.get("recipe_view_stats", "soup")
    assert stats["total_views"] == sum(stats[name + "_views"] for name in views.CATEGORIES) == 4
    assert stats["unique_viewers"] == sum(stats[name + "_unique_viewers"] for name in views.CATEGORIES) == 2


@pytest.mark.integration
async def test_cleanup_removes_only_expired_transient_rows(tracking):
    repo, _settings, recipe = tracking
    timestamp = datetime(2026, 2, 1, tzinfo=UTC)
    viewer = await repo.create("viewers", {}, id="retained")
    membership = await repo.create("recipe_viewers", {
        "recipe_id": recipe["id"], "viewer_id": viewer["id"], "first_view_at": timestamp,
        "last_counted_at": timestamp, "first_category": "anonymous",
    }, id="retained_membership")
    stats = await repo.create("recipe_view_stats", {
        "recipe_id": recipe["id"], "total_views": 1, "unique_viewers": 1,
        "anonymous_views": 1, "anonymous_unique_viewers": 1,
    }, id=recipe["id"])
    expired = await repo.create("viewer_credentials", {
        "viewer_id": viewer["id"], "expires_at": timestamp - timedelta(seconds=1),
    }, id="expired")
    current = await repo.create("viewer_credentials", {
        "viewer_id": viewer["id"], "expires_at": timestamp + timedelta(seconds=1),
    }, id="current")
    window = await repo.create("view_ip_windows", {
        "entries": [{"viewer_id": viewer["id"], "last_counted_at": timestamp - timedelta(hours=2)}],
        "expires_at": timestamp - timedelta(seconds=1),
    }, id="expired_window")

    assert await views.cleanup_view_tracking(repo, timestamp) == 2
    assert await repo.get("viewer_credentials", expired["id"]) is None
    assert await repo.get("view_ip_windows", window["id"]) is None
    assert await repo.get("viewer_credentials", current["id"])
    assert await repo.get("recipe_viewers", membership["id"])
    assert await repo.get("recipe_view_stats", stats["id"])


@pytest.mark.integration
async def test_ip_window_caps_new_identities_but_allows_members_and_expires(tracking, monkeypatch):
    repo, settings, _recipe = tracking
    timestamp = datetime(2026, 3, 1, tzinfo=UTC)
    monkeypatch.setattr(views, "utcnow", lambda: timestamp)
    app = tracking_app(repo, settings)
    clients = [browser(app, "192.0.2.10") for _ in range(11)]
    try:
        for current in clients:
            await prepare(current)
        for index, current in enumerate(clients[:10], 1):
            assert (await current.post("/recipes/soup/views")).json()["total_views"] == index
        before = len(await repo.list("viewers"))
        denied = await clients[10].post("/recipes/soup/views", headers={"X-Forwarded-For": "198.51.100.90"})
        assert denied.status_code == 200 and denied.json() == {"total_views": 10, "unique_viewers": 10}
        assert len(await repo.list("viewers")) == before

        second_recipe = await repo.create("recipes", {"title": "Stew", "status": "published"}, id="stew")
        assert (await clients[0].post(f"/recipes/{second_recipe['id']}/views")).json() == {
            "total_views": 1, "unique_viewers": 1,
        }
        timestamp += timedelta(seconds=3600)
        assert (await clients[10].post("/recipes/soup/views")).json() == {
            "total_views": 11, "unique_viewers": 11,
        }
    finally:
        await asyncio.gather(*(current.aclose() for current in clients))


@pytest.mark.integration
async def test_backend_classification_is_historical_and_blocked_device_is_not_anonymous(tracking, monkeypatch):
    repo, settings, _recipe = tracking
    timestamp = datetime(2026, 4, 1, tzinfo=UTC)
    monkeypatch.setattr(views, "utcnow", lambda: timestamp)
    app = tracking_app(repo, settings)

    async with browser(app, "192.0.2.20") as anonymous:
        await prepare(anonymous)
        await anonymous.post("/recipes/soup/views")

    async def named_browser(name, ip, **changes):
        current = browser(app, ip)
        await prepare(current)
        created = await current.post("/identity", json={"display_name": name})
        assert created.status_code == 201, created.text
        user_id = created.json()["user"]["id"]
        if changes:
            await repo.update("users", user_id, changes)
        return current, user_id

    named, named_id = await named_browser("Anonymous", "192.0.2.21")
    trusted, _trusted_id = await named_browser("Trusted", "192.0.2.22", trusted=True)
    administrator, _admin_id = await named_browser("Admin", "192.0.2.23", is_admin=True)
    try:
        await named.post("/recipes/soup/views")
        await trusted.post("/recipes/soup/views")
        await administrator.post("/recipes/soup/views")
        stats = await repo.get("recipe_view_stats", "soup")
        assert (stats["anonymous_views"], stats["named_views"], stats["trusted_views"]) == (1, 2, 1)
        assert (stats["anonymous_unique_viewers"], stats["named_unique_viewers"],
                stats["trusted_unique_viewers"]) == (1, 2, 1)

        await repo.update("users", named_id, {"trusted": True})
        timestamp += timedelta(seconds=3600)
        await named.post("/recipes/soup/views")
        stats = await repo.get("recipe_view_stats", "soup")
        assert stats["trusted_views"] == 2 and stats["trusted_unique_viewers"] == 1
        assert stats["named_views"] == 2 and stats["named_unique_viewers"] == 2

        await repo.update("users", named_id, {"state": "blocked"})
        timestamp += timedelta(seconds=3600)
        unchanged = await named.post("/recipes/soup/views")
        assert unchanged.json() == {"total_views": 5, "unique_viewers": 4}
        assert len(await repo.list("viewers")) == 4
    finally:
        await named.aclose()
        await trusted.aclose()
        await administrator.aclose()


@pytest.mark.integration
async def test_concurrent_claims_converge_without_lost_counts(tracking, monkeypatch):
    repo, settings, _recipe = tracking
    timestamp = datetime(2026, 5, 1, tzinfo=UTC)
    monkeypatch.setattr(views, "utcnow", lambda: timestamp)
    app = tracking_app(repo, settings)
    async with browser(app, "192.0.2.30") as same:
        await prepare(same)
        await same.post("/recipes/soup/views")
        timestamp += timedelta(seconds=3600)
        responses = await asyncio.gather(*(same.post("/recipes/soup/views") for _ in range(2)))
        for response in responses:
            if response.status_code == 409:
                response = await same.post("/recipes/soup/views")
            assert response.status_code == 200
        assert (await repo.get("recipe_view_stats", "soup"))["total_views"] == 2

    distinct = [browser(app, f"192.0.2.{40 + index}") for index in range(5)]
    try:
        await asyncio.gather(*(prepare(current) for current in distinct))
        responses = await asyncio.gather(*(current.post("/recipes/soup/views") for current in distinct))
        for current, response in zip(distinct, responses):
            if response.status_code == 409:
                response = await current.post("/recipes/soup/views")
            assert response.status_code == 200
        stats = await repo.get("recipe_view_stats", "soup")
        assert stats["total_views"] == 7 and stats["unique_viewers"] == 6
    finally:
        await asyncio.gather(*(current.aclose() for current in distinct))


@pytest.mark.integration
async def test_guest_naming_binds_existing_lifetime_identity(tracking, monkeypatch):
    repo, settings, _recipe = tracking
    timestamp = datetime(2026, 6, 1, tzinfo=UTC)
    monkeypatch.setattr(views, "utcnow", lambda: timestamp)
    app = tracking_app(repo, settings)
    async with browser(app, "192.0.2.70") as current:
        await prepare(current)
        await current.post("/recipes/soup/views")
        guest = (await repo.list("viewers"))[0]
        created = await current.post("/identity", json={"display_name": "Named"})
        assert created.status_code == 201
        timestamp += timedelta(seconds=3600)
        assert (await current.post("/recipes/soup/views")).json() == {
            "total_views": 2, "unique_viewers": 1,
        }
        user = await repo.get("users", created.json()["user"]["id"])
        assert user["viewer_id"] == guest["id"]
        bound = await repo.get("viewers", guest["id"])
        assert bound["user_id"] == user["id"]
        stats = await repo.get("recipe_view_stats", "soup")
        assert stats["anonymous_unique_viewers"] == 1 and stats["named_unique_viewers"] == 0


@pytest.mark.integration
async def test_viewer_merge_rekeys_memberships_and_reconciles_overlapping_unique(tracking):
    repo, _settings, _recipe = tracking
    first = datetime(2026, 7, 1, tzinfo=UTC)
    source_user = await repo.create("users", {"display_name": "Source", "viewer_id": "a"}, id="source")
    target_user = await repo.create("users", {"display_name": "Target", "viewer_id": "b"}, id="target")
    await repo.create("viewers", {"user_id": source_user["id"]}, id="a")
    await repo.create("viewers", {"user_id": target_user["id"]}, id="b")
    stew = await repo.create("recipes", {"title": "Stew", "status": "published"}, id="stew")
    await repo.create("recipe_viewers", {
        "recipe_id": "soup", "viewer_id": "a", "first_view_at": first,
        "last_counted_at": first + timedelta(hours=1), "first_category": "anonymous",
    }, id=views.digest("soup\0a"))
    await repo.create("recipe_viewers", {
        "recipe_id": "soup", "viewer_id": "b", "first_view_at": first,
        "last_counted_at": first + timedelta(hours=2), "first_category": "named",
    }, id=views.digest("soup\0b"))
    await repo.create("recipe_view_stats", {
        "recipe_id": "soup", "total_views": 5, "unique_viewers": 2,
        "anonymous_views": 2, "named_views": 3,
        "anonymous_unique_viewers": 1, "named_unique_viewers": 1,
    }, id="soup")
    await repo.create("recipe_viewers", {
        "recipe_id": stew["id"], "viewer_id": "a", "first_view_at": first,
        "last_counted_at": first, "first_category": "anonymous",
    }, id=views.digest("stew\0a"))
    await repo.create("recipe_view_stats", {
        "recipe_id": stew["id"], "total_views": 1, "unique_viewers": 1,
        "anonymous_views": 1, "anonymous_unique_viewers": 1,
    }, id=stew["id"])

    async with repo.transaction() as tx:
        source = await tx.get("users", "source")
        target = await tx.get("users", "target")
        assert await views.reconcile_merged_users(tx, source, target) == "b"

    overlap = await repo.get("recipe_viewers", views.digest("soup\0b"))
    stats = await repo.get("recipe_view_stats", "soup")
    assert overlap["first_category"] == "anonymous"
    assert overlap["last_counted_at"] == (first + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    assert stats["total_views"] == 5 and stats["unique_viewers"] == 1
    assert stats["anonymous_unique_viewers"] == 1 and stats["named_unique_viewers"] == 0
    assert await repo.get("recipe_viewers", views.digest("soup\0a")) is None
    assert await repo.get("recipe_viewers", views.digest("stew\0b"))
    assert (await repo.get("recipe_view_stats", "stew"))["unique_viewers"] == 1
    assert (await repo.get("viewers", "a"))["merged_into"] == "b"


@pytest.mark.integration
async def test_popular_uses_global_qualification_beyond_first_page(tracking):
    repo, settings, _recipe = tracking
    async with repo.transaction() as tx:
        for index in range(61):
            await tx.create("recipes", {
                "title": f"Breakfast {index:02}", "status": "published", "tags": ["breakfast"],
            }, id=f"breakfast_{index:02}")
        for recipe_id, title, total, unique in (
            ("five", "Five", 5, 3),
            ("single", "Single", 20, 1),
            ("alpha", "Alpha", 6, 2),
            ("beta", "Beta", 6, 2),
        ):
            await tx.create("recipes", {"title": title, "status": "published"}, id=recipe_id)
            await tx.create("recipe_view_stats", {
                "recipe_id": recipe_id, "total_views": total, "unique_viewers": unique,
            }, id=recipe_id)
    app = tracking_app(repo, settings)
    async with browser(app) as current:
        hidden = (await current.get("/recipes")).json()
        assert hidden["popular"] == []
        assert {item["id"] for item in hidden["items"]}.isdisjoint({"alpha", "beta"})

        async with repo.transaction() as tx:
            for recipe_id, title, total in (
                ("gamma", "Gamma", 6),
                ("highest", "Highest", 9),
            ):
                await tx.create("recipes", {"title": title, "status": "published"}, id=recipe_id)
                await tx.create("recipe_view_stats", {
                    "recipe_id": recipe_id, "total_views": total, "unique_viewers": 2,
                }, id=recipe_id)
        recipes.invalidate_catalog(repo)
        listed = (await current.get("/recipes")).json()
        assert [item["id"] for item in listed["popular"]] == ["highest", "alpha", "beta"]
        assert {item["id"] for item in listed["popular"]}.isdisjoint(
            {item["id"] for item in listed["items"]}
        )
        assert (await current.get("/recipes?tag=breakfast")).json()["popular"] == []
