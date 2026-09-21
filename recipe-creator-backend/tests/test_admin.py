from datetime import datetime, timedelta
from threading import get_ident
from types import SimpleNamespace

from recipe_creator import admin

import pytest

from recipe_creator.security import ADMIN_COOKIE, digest, now
from test_identity import client, csrf, identity_app, profile


async def login(browser):
    session = await csrf(browser)
    if not session['user']:
        session = await profile(browser, 'Operator')
    repo = browser._transport.app.state.repo
    await repo.update('users', session['user']['id'], {'is_admin': True})


async def test_admin_users_reports_latest_device_activity(monkeypatch):
    user = {"id": "user", "display_name": "Cook", "state": "active", "trusted": False}
    older, latest = now() - timedelta(days=2), now() - timedelta(hours=2)

    class Repo:
        async def list(self, table, filters=None, limit=500, start=0):
            rows = [user] if table == "users" else [
                {"user_id": "user", "last_used_at": older},
                {"user_id": "user", "last_used_at": latest},
            ]
            return rows[start:start + limit]

    async def allow(_request):
        return None

    monkeypatch.setattr(admin, "require_admin", allow)
    result = await admin.users(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=Repo()))),
                               q="", start=0, limit=20, eligible_owner=True)
    assert result["items"][0]["last_login_at"] == latest


@pytest.mark.integration
async def test_admin_live_user_permission_and_retired_cookies(identity_app):
    repo = identity_app.state.repo
    async with client(identity_app) as browser:
        user = (await profile(browser, 'Admin'))['user']
        assert (await browser.get('/admin/users')).status_code == 403
        assert (await browser.post('/admin/login', json={'password': 'test-password'})).status_code == 404
        await repo.create('admin_sessions', {'secret_hash': digest('old'), 'expires_at': now() + timedelta(days=1)})
        browser.cookies.set(ADMIN_COOKIE, 'old')
        assert not (await browser.get('/session')).json()['admin']
        await login(browser)
        assert (await browser.get('/session')).json()['admin']
        assert 'is_admin' not in (await browser.get('/session')).json()['user']
        await repo.update('users', user['id'], {'is_admin': False})
        assert (await browser.get('/admin/users')).status_code == 403
        await login(browser)
        session = (await browser.get('/session')).json()
        await repo.update('devices', session['device_id'], {'revoked_at': now()})
        assert (await browser.get('/admin/users')).status_code == 401


@pytest.mark.integration
async def test_admin_users_owner_revisions_restore_and_audit(identity_app):
    repo = identity_app.state.repo
    async with client(identity_app) as browser:
        user = (await profile(browser))["user"]
        recipe = await repo.create("recipes", {"title": "Original", "source_text": "Do not rewrite me", "status": "published"})
        assert (await browser.post(f"/admin/recipes/{recipe['id']}/owner", json={"owner_id": user["id"], "expected_revision": 1})).status_code == 403
        await login(browser)
        current = (await browser.get("/session")).json()
        await repo.update("devices", current["device_id"], {"last_used_at": now() - timedelta(days=5)})
        latest = now() - timedelta(hours=2)
        await repo.create("devices", {"user_id": user["id"], "secret_hash": digest("other-device"),
                                      "last_used_at": latest, "expires_at": now() + timedelta(days=1)})
        users = (await browser.get("/admin/users", params={"q": "alice"})).json()
        assert users["total"] == 1 and users["items"] == users["users"]
        observed = datetime.fromisoformat(users["items"][0]["last_login_at"].replace("Z", "+00:00"))
        assert observed == latest
        patch = await browser.patch("/admin/users/" + user["id"], json={"trusted": True})
        assert patch.json()["user"]["trusted"]
        assert (await repo.get("users", user["id"]))["trusted"]
        assigned = await browser.post(f"/admin/recipes/{recipe['id']}/owner", json={"owner_id": user["id"], "expected_revision": 1})
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
        target = {"user": await repo.create("users", {"display_name": "Target"})}
        source_id, target_id = source["user"]["id"], target["user"]["id"]
        pairing = (await source_browser.post("/pairings")).json()
        await login(browser)
        await browser.patch("/admin/users/" + source_id, json={"state": "blocked", "trusted": True})
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
        assert preview.json()["result"] == {"state": "blocked", "trusted": False}
        body = {"source_id": source_id, "target_id": target_id, "confirm": True}
        merged = await browser.post("/admin/merge", json=body)
        assert merged.status_code == 200, merged.text
        assert merged.json()["user"]["state"] == "blocked"
        assert not merged.json()["user"]["trusted"]
        assert (await repo.get("users", source_id))["merged_into"] == target_id
        assert not await repo.list("recipes", {"owner_id": source_id})
        assert len(await repo.list("recipes", {"owner_id": target_id})) == 3
        assert len(await repo.list("photos", {"uploader_id": target_id})) == 3
        assert len(await repo.list("devices", {"user_id": target_id}, limit=1000)) == 502
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
async def test_owner_revision_one_race_eligibility_restore_and_rollback(identity_app, monkeypatch):
    import asyncio
    from recipe_creator import recipes
    repo = identity_app.state.repo
    identity_app.include_router(recipes.router)
    async with client(identity_app) as browser:
        await login(browser)
        operator = (await csrf(browser))['user']
        target = await repo.create('users', {'display_name': 'Duplicate'})
        blocked = await repo.create('users', {'display_name': 'Duplicate', 'state': 'blocked'})
        merged = await repo.create('users', {'display_name': 'Duplicate', 'state': 'merged', 'merged_into': target['id']})
        directory = (await browser.get('/admin/users?q=duplicate&eligible_owner=true&limit=1')).json()
        assert directory['total'] == 1 and directory['items'][0]['id'] == target['id'] and not directory['has_more']
        assert not {'secret_hash', 'payload'} & directory['items'][0].keys()
        recipe = (await browser.post('/recipes', json={'title': 'Original', 'source_text': 'Exact\r\nprose'})).json()
        rid = recipe['id']
        url = f'/admin/recipes/{rid}/owner'
        for user in (blocked, merged):
            assert (await browser.post(url, json={'owner_id': user['id'], 'expected_revision': 1})).status_code == 422
        assert (await browser.post(url, json={'owner_id': target['id']})).status_code == 422
        results = await asyncio.gather(*(browser.post(url, json={'owner_id': target['id'], 'expected_revision': 1}) for _ in range(2)))
        assert sorted(result.status_code for result in results) == [200, 409]
        result = next(result.json() for result in results if result.status_code == 200)
        assert result['author_name'] == 'Duplicate' and result['revision'] == 2
        assert len(await repo.list('revisions', {'recipe_id': rid, 'revision': 1})) == 1
        assert (await repo.get('recipes', rid))['source_text'] == 'Exact\r\nprose'
        invalid = await repo.create('revisions', {'recipe_id': rid, 'revision': 99, 'content': {'tags': ['Dinner', 'breakfast']}})
        assert (await browser.post(f'/admin/recipes/{rid}/restore', json={'revision_id': invalid['id'], 'expected_revision': 2})).status_code == 422
        assert (await repo.get('recipes', rid))['revision'] == 2
        async def fail(*args, **kwargs):
            raise RuntimeError('audit failure')
        monkeypatch.setattr(admin, 'audit', fail)
        with pytest.raises(RuntimeError, match='audit failure'):
            await browser.post(url, json={'owner_id': operator['id'], 'expected_revision': 2})
        assert (await repo.get('recipes', rid))['owner_id'] == target['id']
        assert (await repo.get('recipes', rid))['revision'] == 2
        assert not await repo.list('revisions', {'recipe_id': rid, 'revision': 2})


@pytest.mark.integration
@pytest.mark.parametrize("admin_side", ["source", "target", "both"])
async def test_merge_allows_admin_profiles_and_preserves_target_permission(identity_app, admin_side):
    repo = identity_app.state.repo
    async with client(identity_app) as browser:
        await login(browser)
        source = await repo.create("users", {"display_name": "source", "is_admin": admin_side in {"source", "both"}})
        target = await repo.create("users", {"display_name": "target", "is_admin": admin_side in {"target", "both"}})
        body = {"source_id": source["id"], "target_id": target["id"]}
        directory = (await browser.get("/admin/users?eligible_merge=true")).json()
        assert {source["id"], target["id"]} <= {user["id"] for user in directory["items"]}
        preview = await browser.post("/admin/merge/preview", json=body)
        assert preview.status_code == 200, preview.text
        merged = await browser.post("/admin/merge", json={**body, "confirm": True})
        assert merged.status_code == 200, merged.text
        assert not (await repo.get("users", source["id"]))["is_admin"]
        assert (await repo.get("users", target["id"]))["is_admin"] is (admin_side in {"target", "both"})


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


@pytest.mark.integration
async def test_admin_grant_requires_admin_csrf_and_updates_live_session(identity_app):
    repo = identity_app.state.repo
    async with client(identity_app) as operator, client(identity_app) as member, client(identity_app) as anonymous:
        target = (await profile(member, "New admin"))["user"]
        url = "/admin/users/" + target["id"]
        await csrf(anonymous)
        assert (await anonymous.patch(url, json={"is_admin": True})).status_code == 401
        assert (await member.patch(url, json={"is_admin": True})).status_code == 403
        await login(operator)
        operator_session = (await operator.get("/session")).json()
        assert (await operator.patch(url, json={"is_admin": True},
                                     headers={"X-CSRF-Token": "bad"})).status_code == 403
        assert not (await repo.get("users", target["id"]))["is_admin"]
        assert not await repo.list("audit", {"action": "user.admin.grant"})
        assert not (await member.get("/session")).json()["admin"]

        promoted = await operator.patch(url, json={"is_admin": True, "trusted": True})
        assert promoted.status_code == 200, promoted.text
        persisted = await repo.get("users", target["id"])
        assert persisted["is_admin"] and persisted["trusted"]
        assert (await member.get("/session")).json()["admin"]
        listing = await member.get("/admin/users", params={"q": target["id"]})
        assert listing.status_code == 200, listing.text
        assert listing.json()["items"][0]["is_admin"]
        events = await repo.list("audit", {"action": "user.admin.grant"})
        assert len(events) == 1
        event = events[0]
        assert event["target"] == target["id"]
        assert event["actor_id"] == operator_session["user"]["id"]
        assert event["device_id"] == operator_session["device_id"]
        assert event["previous_is_admin"] is False and event["is_admin"] is True
        assert (await operator.patch(url, json={"is_admin": False})).status_code == 422
        assert (await member.get("/session")).json()["admin"]


@pytest.mark.integration
@pytest.mark.parametrize(("state", "merged", "requested_state", "status"), [
    ("blocked", False, None, 422),
    ("blocked", False, "active", 422),
    ("active", False, "blocked", 422),
    ("merged", True, None, 409),
    ("active", True, None, 409),
    ("merged", False, None, 409),
])
async def test_admin_grant_rejects_ineligible_profiles(identity_app, state, merged, requested_state, status):
    repo = identity_app.state.repo
    survivor = await repo.create("users", {"display_name": "Survivor"})
    target = await repo.create("users", {"display_name": "Ineligible", "state": state,
                                         "merged_into": survivor["id"] if merged else None})
    async with client(identity_app) as browser:
        await login(browser)
        body = {"is_admin": True, "trusted": True}
        if requested_state:
            body["state"] = requested_state
        result = await browser.patch("/admin/users/" + target["id"], json=body)
        assert result.status_code == status, result.text
        persisted = await repo.get("users", target["id"])
        assert not persisted["is_admin"] and not persisted["trusted"]
        assert persisted["state"] == state
        assert not await repo.list("audit", {"target": target["id"]})


@pytest.mark.integration
async def test_admin_grant_rolls_back_when_audit_fails(identity_app, monkeypatch):
    repo = identity_app.state.repo
    target = await repo.create("users", {"display_name": "Candidate"})
    original_audit = admin.audit

    async def fail_grant_audit(tx, context, action, target, **data):
        if action == "user.admin.grant":
            raise RuntimeError("audit unavailable")
        await original_audit(tx, context, action, target, **data)

    monkeypatch.setattr(admin, "audit", fail_grant_audit)
    async with client(identity_app) as browser:
        await login(browser)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await browser.patch("/admin/users/" + target["id"], json={"is_admin": True, "trusted": True})
        persisted = await repo.get("users", target["id"])
        assert not persisted["is_admin"] and not persisted["trusted"]
        assert not await repo.list("audit", {"target": target["id"]})


@pytest.mark.integration
async def test_admin_directory_hides_merged_profiles_and_keeps_transitive_ids(identity_app):
    repo = identity_app.state.repo
    async with client(identity_app) as browser:
        await login(browser)
        ancestor = await repo.create("users", {"display_name": "Directory A"})
        intermediate = await repo.create("users", {"display_name": "Directory B"})
        survivor = await repo.create("users", {"display_name": "Directory C", "is_admin": True})
        blocked = await repo.create("users", {"display_name": "Directory D", "state": "blocked"})
        for source, target in [(ancestor, intermediate), (intermediate, survivor)]:
            response = await browser.post("/admin/merge", json={
                "source_id": source["id"], "target_id": target["id"], "confirm": True,
            })
            assert response.status_code == 200, response.text
        await repo.create("users", {"display_name": "Directory orphan", "state": "merged"})
        await repo.create("users", {"display_name": "Directory linked", "merged_into": blocked["id"]})

        first = (await browser.get("/admin/users", params={"q": "Directory", "limit": 1})).json()
        assert first["total"] == 2 and first["has_more"]
        assert first["items"] == first["users"]
        assert first["items"][0]["id"] == survivor["id"]
        assert first["items"][0]["is_admin"]
        assert set(first["items"][0]["merged_user_ids"]) == {ancestor["id"], intermediate["id"]}
        second = (await browser.get("/admin/users", params={"q": "Directory", "limit": 1, "start": 1})).json()
        assert second["items"][0]["id"] == blocked["id"]
        assert second["total"] == 2 and not second["has_more"]

        searched = (await browser.get("/admin/users", params={"q": survivor["id"]})).json()
        assert set(searched["items"][0]["merged_user_ids"]) == {ancestor["id"], intermediate["id"]}
        assert (await browser.get("/admin/users", params={"q": ancestor["id"]})).json()["total"] == 0
        owners = (await browser.get("/admin/users", params={"q": "Directory", "eligible_owner": True})).json()
        assert [row["id"] for row in owners["items"]] == [survivor["id"]]
        mergeable = (await browser.get("/admin/users", params={"q": "Directory", "eligible_merge": True})).json()
        assert {row["id"] for row in mergeable["items"]} == {survivor["id"], blocked["id"]}
