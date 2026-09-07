from datetime import timedelta
from threading import get_ident

from recipe_creator import admin

import pytest

from recipe_creator.security import ADMIN_COOKIE, digest, now
from test_identity import client, csrf, identity_app, profile


async def login(browser):
    await csrf(browser)
    response = await browser.post("/admin/login", json={"password": "test-password"})
    assert response.status_code == 200, response.text


@pytest.mark.integration
async def test_admin_separate_hash_session_logout_and_limits(identity_app, monkeypatch):
    repo = identity_app.state.repo
    event_thread = get_ident()
    verify = admin.password_hasher.verify
    worker_threads = []
    def checked_verify(*args):
        worker_threads.append(get_ident())
        assert get_ident() != event_thread
        return verify(*args)
    monkeypatch.setattr(type(admin.password_hasher), "verify", lambda self, *args: checked_verify(*args))
    async with client(identity_app) as browser:
        await profile(browser, "Admin")
        assert (await browser.get("/admin/users")).status_code == 401
        assert (await browser.post("/admin/login", json={"password": "wrong"})).status_code == 401
        await login(browser)
        secret = browser.cookies[ADMIN_COOKIE]
        rows = await repo.list("admin_sessions")
        assert rows[0]["secret_hash"] == digest(secret)
        assert (await browser.get("/session")).json()["admin"]
        await repo.update("admin_sessions", rows[0]["id"], {"expires_at": now() - timedelta(seconds=1)})
        assert (await browser.get("/admin/users")).status_code == 401
        await login(browser)
        secret = browser.cookies[ADMIN_COOKIE]
        assert (await browser.post("/admin/logout")).status_code == 200
        browser.cookies.set(ADMIN_COOKIE, secret)
        assert (await browser.get("/admin/users")).status_code == 401
        identity_app.state.settings.login_attempt_limit = 3
        assert (await browser.post("/admin/login", json={"password": "test-password"})).status_code == 429
        assert worker_threads


@pytest.mark.integration
async def test_admin_users_owner_revisions_restore_and_audit(identity_app):
    repo = identity_app.state.repo
    async with client(identity_app) as browser:
        user = (await profile(browser))["user"]
        recipe = await repo.create("recipes", {"title": "Original", "source_text": "Do not rewrite me", "status": "published"})
        assert (await browser.post(f"/admin/recipes/{recipe['id']}/owner", json={"owner_id": user["id"]})).status_code == 401
        await login(browser)
        users = (await browser.get("/admin/users", params={"q": "alice"})).json()
        assert users["total"] == 1 and users["items"] == users["users"]
        patch = await browser.patch("/admin/users/" + user["id"], json={"photo_trusted": True})
        assert patch.json()["user"]["photo_trusted"]
        assert (await repo.get("users", user["id"]))["photo_trust"]
        assigned = await browser.post(f"/admin/recipes/{recipe['id']}/owner", json={"owner_id": user["id"]})
        assert assigned.status_code == 200, assigned.text
        current = await repo.save_recipe_revision(recipe["id"], assigned.json()["revision"], {"title": "Changed", "deleted_at": now()})
        listing = (await browser.get(f"/admin/recipes/{recipe['id']}/revisions")).json()
        assert listing["items"] == listing["revisions"]
        revisions = listing["items"]
        old = next(row for row in revisions if row["content"]["title"] == "Original")
        url = f"/admin/recipes/{recipe['id']}/restore"
        assert (await browser.post(url, json={"revision_id": old["id"], "expected_revision": 1})).status_code == 409
        restored = await browser.post(url, json={"revision_id": old["id"], "expected_revision": current["revision"]})
        assert restored.status_code == 200, restored.text
        row = await repo.get("recipes", recipe["id"])
        assert row["title"] == "Original" and row["deleted_at"] is None
        assert row["owner_id"] == user["id"]
        assert row["source_text"] == "Do not rewrite me"
        listing = (await browser.get("/admin/audit")).json()
        assert listing["items"] == listing["events"]
        actions = {row["action"] for row in listing["items"]}
        assert {"user.update", "recipe.owner", "recipe.restore"} <= actions


@pytest.mark.integration
async def test_merge_transaction_idempotency_restrictions_usage_and_pagination(identity_app, monkeypatch):
    repo = identity_app.state.repo
    async with client(identity_app) as source_browser, client(identity_app) as browser:
        source = await profile(source_browser, "Source")
        target = await profile(browser, "Target")
        source_id, target_id = source["user"]["id"], target["user"]["id"]
        pairing = (await source_browser.post("/pairings")).json()
        await login(browser)
        await browser.patch("/admin/users/" + source_id, json={"state": "blocked", "photo_trusted": True})
        timestamp = now()
        day = timestamp.date().isoformat()
        photo_target_key = "photo_attempt_" + digest(day + "\0user:" + target_id)
        ai_target_scope = digest("user:" + target_id)
        ai_target_key = digest(f"ai:{day}:{ai_target_scope}")
        async with repo.transaction() as tx:
            for index in range(3):
                recipe = await tx.create("recipes", {"title": str(index), "owner_id": source_id})
                await tx.create("photos", {"recipe_id": recipe["id"], "uploader_id": source_id})
            for index in range(501):
                await tx.create("devices", {"user_id": source_id, "secret_hash": digest(str(index)),
                                            "expires_at": timestamp + timedelta(days=1)})
            await tx.create("usage", {"kind": "photo_attempt", "scope": "user:" + source_id, "count": 4,
                                      "expires_at": timestamp + timedelta(days=2)})
            await tx.create("usage", {"kind": "photo_attempt", "scope": "user:" + target_id, "count": 2,
                                      "expires_at": timestamp + timedelta(days=2)}, id=photo_target_key)
            await tx.create("usage", {"kind": "ai", "scope": digest("user:" + source_id), "count": 7,
                                      "expires_at": timestamp + timedelta(days=1)})
        original_list = type(repo).list
        calls = []
        async def tracked_list(self, table, filters=None, limit=100, start=0):
            calls.append((table, start))
            return await original_list(self, table, filters, limit=limit, start=start)
        monkeypatch.setattr(type(repo), "list", tracked_list)
        preview = await browser.get("/admin/merge/preview", params={"source_id": source_id, "target_id": target_id})
        assert preview.status_code == 200, preview.text
        body_preview = await browser.post("/admin/merge/preview", json={"source_id": source_id, "target_id": target_id})
        assert body_preview.status_code == 200 and body_preview.json() == preview.json()
        assert (await browser.post("/admin/merge/preview", json={"source_id": source_id})).status_code == 422
        assert preview.json()["source"]["recipes"] == 3
        assert preview.json()["result"] == {"state": "blocked", "photo_trusted": False}
        body = {"source_id": source_id, "target_id": target_id, "confirm": True}
        merged = await browser.post("/admin/merge", json=body)
        assert merged.status_code == 200, merged.text
        assert merged.json()["user"]["state"] == "blocked"
        assert not merged.json()["user"]["photo_trusted"]
        assert (await repo.get("users", source_id))["merged_into"] == target_id
        assert not await repo.list("recipes", {"owner_id": source_id})
        assert len(await repo.list("recipes", {"owner_id": target_id})) == 3
        assert len(await repo.list("photos", {"uploader_id": target_id})) == 3
        assert len(await repo.list("devices", {"user_id": target_id}, limit=1000)) == 503
        assert ("devices", 500) in calls
        assert (await repo.get("pairings", pairing["id"]))["status"] == "revoked"
        assert (await repo.get("usage", photo_target_key))["count"] == 6
        assert (await repo.get("usage", ai_target_key))["count"] == 7
        again = await browser.post("/admin/merge", json=body)
        assert again.json()["already_merged"]
        assert (await repo.get("usage", photo_target_key))["count"] == 6
        assert len(await repo.list("audit", {"action": "user.merge"})) == 1
        assert (await source_browser.get("/session")).json()["user"] is None
        assert (await browser.get("/session")).json()["admin"]
        assert (await browser.post("/admin/merge", json={"source_id": target_id, "target_id": source_id, "confirm": True})).status_code == 409


@pytest.mark.integration
async def test_merge_rolls_back_every_write(identity_app, monkeypatch):
    from recipe_creator import admin
    repo = identity_app.state.repo
    async with client(identity_app) as browser:
        source = await repo.create("users", {"display_name": "Source"})
        target = await repo.create("users", {"display_name": "Target"})
        recipe = await repo.create("recipes", {"owner_id": source["id"]})
        await login(browser)
        async def fail(*args):
            raise RuntimeError("injected failure")
        monkeypatch.setattr(admin, "merge_usage", fail)
        with pytest.raises(RuntimeError, match="injected"):
            await browser.post("/admin/merge", json={"source_id": source["id"], "target_id": target["id"], "confirm": True})
        assert (await repo.get("recipes", recipe["id"]))["owner_id"] == source["id"]
        assert (await repo.get("users", source["id"]))["merged_into"] is None
        assert await repo.list("audit") == []


@pytest.mark.integration
async def test_admin_photo_moderation_delegates_and_audits(identity_app):
    repo = identity_app.state.repo
    recipe = await repo.create("recipes", {"title": "Photographed", "status": "published"})
    uploader = await repo.create("users", {"display_name": "Photographer"})
    photo = await repo.create("photos", {"recipe_id": recipe["id"], "uploader_id": uploader["id"], "status": "pending"})
    async with client(identity_app) as browser:
        await csrf(browser)
        assert (await browser.get("/admin/photos")).status_code == 401
        assert (await browser.post(f"/admin/photos/{photo['id']}/moderate", json={"state": "approved"})).status_code == 401
        await login(browser)
        listing = (await browser.get("/admin/photos")).json()
        assert listing["items"] == listing["photos"] and listing["items"][0]["id"] == photo["id"]
        result = await browser.post(f"/admin/photos/{photo['id']}", json={"state": "approved"})
        assert result.status_code == 200, result.text
        assert (await repo.get("photos", photo["id"]))["status"] == "approved"
        assert len(await repo.list("audit", {"action": "photo.moderate"})) == 1
