import asyncio
from datetime import timedelta
import os
from uuid import uuid4

from argon2 import PasswordHasher
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from recipe_creator import admin, identity
from recipe_creator.photos import PhotoService
from recipe_creator.repository import ConflictError, Repository
from recipe_creator.security import (
    ADMIN_COOKIE, CHALLENGE_COOKIE, DEVICE_COOKIE, SecurityMiddleware, digest, now,
)
from recipe_creator.settings import Settings


@pytest_asyncio.fixture
async def identity_app(tmp_path):
    url = os.getenv("RECIPE_TEST_DB_URL")
    if not url:
        pytest.skip("Set RECIPE_TEST_DB_URL to run against real SurrealDB")
    settings = Settings(db_url=url, db_user=os.getenv("RECIPE_TEST_DB_USER", "root"),
                        db_password=os.getenv("RECIPE_TEST_DB_PASSWORD", ""),
                        db_namespace="identity_" + uuid4().hex, db_database="tests",
                        public_origin="https://testserver", allowed_origins=["https://testserver"],
                        admin_password_hash=PasswordHasher().hash("test-password"),
                        media_root=tmp_path)
    async with Repository(settings) as repo:
        await repo.migrate()
        app = FastAPI()
        app.state.repo = repo
        app.state.settings = settings
        app.state.photos = PhotoService(repo, settings)
        app.include_router(identity.router)
        app.include_router(admin.router)
        app.add_middleware(SecurityMiddleware)

        @app.post("/recipes/{recipe_id}/photos")
        async def echo(request: Request):
            return {"size": len(await request.body())}

        try:
            yield app
        finally:
            await repo._connection.query(f"REMOVE NAMESPACE `{settings.db_namespace}`;")


def client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


async def csrf(browser):
    response = await browser.get("/session")
    assert response.status_code == 200, response.text
    browser.headers.update({"Origin": "https://testserver", "X-CSRF-Token": response.json()["csrf_token"]})
    return response.json()


async def profile(browser, name="Alice"):
    await csrf(browser)
    response = await browser.post("/identity", json={"display_name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def connect(source, destination):
    pairing = await source.post("/pairings")
    assert pairing.status_code == 201, pairing.text
    pairing = pairing.json()
    await csrf(destination)
    requested = await destination.post("/pairings/request", json={"code": pairing["code"]})
    assert requested.status_code == 200, requested.text
    return pairing, requested.json()


@pytest.mark.integration
async def test_session_no_identity_csrf_origin_and_body_limits(identity_app):
    async with client(identity_app) as browser:
        initial = await browser.get("/session")
        cookie = initial.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie and "Domain=" not in cookie
        assert initial.headers["cache-control"] == "no-store"
        data = await csrf(browser)
        assert data["user"] is None and not data["admin"]
        assert await identity_app.state.repo.list("users") == []
        assert await identity_app.state.repo.list("devices") == []
        assert (await browser.post("/identity", json={"display_name": "A"}, headers={"X-CSRF-Token": "bad"})).status_code == 403
        assert (await browser.post("/admin/login", json={"password": "test-password"}, headers={"Origin": "https://evil.example"})).status_code == 403
        assert (await browser.post("/identity", content=b"x" * (150 * 1024 + 1))).status_code == 413
        assert (await browser.post("/recipes/r/photos", content=b"x" * (650 * 1024))).status_code == 200
        async def oversized():
            yield b"x" * (75 * 1024)
            yield b"x" * (76 * 1024)
        assert (await browser.post("/identity", content=oversized())).status_code == 413
        assert (await browser.post("/recipes/r/photos", content=b"x" * (650 * 1024 + 1))).status_code == 413


@pytest.mark.integration
async def test_identity_hashes_updates_revocation_and_expiry(identity_app):
    repo = identity_app.state.repo
    async with client(identity_app) as browser:
        created = await profile(browser)
        user_id, device_id = created["user"]["id"], created["device_id"]
        secret = browser.cookies[DEVICE_COOKIE]
        device = await repo.get("devices", device_id)
        assert device["secret_hash"] == digest(secret) and secret not in str(device)
        response = await browser.patch("/identity", json={"display_name": " New name "})
        assert response.json()["user"]["display_name"] == "New name"
        assert "photo_trusted" in response.json()["user"] and "photo_trust" not in response.json()["user"]
        assert (await browser.patch("/identity", json={"display_name": "X", "photo_trusted": True})).status_code == 422
        listing = (await browser.get("/devices")).json()
        assert listing["items"] == listing["devices"] and listing["items"][0]["current"]
        await repo.update("devices", device_id, {"expires_at": now() + timedelta(minutes=1)})
        before = await repo.get("devices", device_id)
        await browser.get("/devices")
        assert await repo.get("devices", device_id) == before
        renewed = await browser.get("/session")
        after = await repo.get("devices", device_id)
        assert after["expires_at"] > before["expires_at"]
        assert after["last_used_at"] >= before["last_used_at"]
        assert browser.cookies[DEVICE_COOKIE] == secret
        assert any(DEVICE_COOKIE in cookie and f"Max-Age={identity_app.state.settings.device_ttl_seconds}" in cookie
                   for cookie in renewed.headers.get_list("set-cookie"))
        await repo.update("devices", device_id, {"expires_at": now() - timedelta(seconds=1)})
        assert (await browser.get("/session")).json()["user"] is None
        assert (await browser.patch("/identity", json={"display_name": "X"})).status_code == 401
        await repo.update("devices", device_id, {"expires_at": now() + timedelta(days=1)})
        await repo.update("users", user_id, {"state": "blocked"})
        assert (await browser.get("/session")).json()["user"] is None
        assert (await browser.post("/identity", json={"display_name": "Bypass"})).status_code == 403
        await repo.update("users", user_id, {"state": "active"})
        assert (await browser.delete("/devices/" + device_id)).status_code == 200
        assert (await repo.get("devices", device_id))["revoked_at"]
        browser.cookies.set(DEVICE_COOKIE, secret)
        assert (await browser.get("/session")).json()["user"] is None


@pytest.mark.integration
async def test_pairing_challenge_approval_single_use_preserves_profiles(identity_app):
    repo = identity_app.state.repo
    async with client(identity_app) as source, client(identity_app) as destination, client(identity_app) as stranger:
        owner = await profile(source, "Owner")
        previous = await profile(destination, "Previous")
        recipe = await repo.create("recipes", {"owner_id": previous["user"]["id"], "title": "Keep me"})
        assert (await source.post("/admin/login", json={"password": "test-password"})).status_code == 200
        assert (await destination.post("/admin/login", json={"password": "test-password"})).status_code == 200
        admin_secret = destination.cookies[ADMIN_COOKIE]
        pairing, requested = await connect(source, destination)
        assert requested["has_existing_profile"] and requested["display_name"] == "Owner"
        assert (await destination.get("/session")).json()["admin"]
        assert (await destination.get("/session")).json()["user"]["id"] == previous["user"]["id"]
        await csrf(stranger)
        path = "/pairings/" + pairing["id"]
        assert (await stranger.get(path)).status_code == 404
        assert (await destination.post(path + "/confirm")).status_code == 403
        assert (await destination.post(path + "/complete", json={"switch_profile": True})).status_code == 409
        assert (await source.post(path + "/confirm")).status_code == 200
        assert (await stranger.post(path + "/complete", json={"switch_profile": True})).status_code == 404
        assert (await destination.post(path + "/complete", json={})).status_code == 422
        results = await asyncio.gather(*(destination.post(path + "/complete", json={"switch_profile": True}) for _ in range(2)))
        assert sorted(result.status_code for result in results) == [200, 404]
        session = (await destination.get("/session")).json()
        assert session["user"]["id"] == owner["user"]["id"] and not session["admin"]
        assert session["device_id"] != owner["device_id"]
        assert source.cookies[DEVICE_COOKIE] != destination.cookies[DEVICE_COOKIE]
        assert ADMIN_COOKIE not in destination.cookies
        assert (await source.get("/session")).json()["admin"]
        admin_session = (await repo.list("admin_sessions", {"secret_hash": digest(admin_secret)}))[0]
        assert admin_session["revoked_at"]
        async with client(identity_app) as replay:
            replay.cookies.set(ADMIN_COOKIE, admin_secret)
            assert (await replay.get("/admin/users")).status_code == 401
        assert (await repo.get("recipes", recipe["id"]))["owner_id"] == previous["user"]["id"]
        assert (await repo.get("users", previous["user"]["id"]))["merged_into"] is None
        assert (await destination.post(path + "/complete", json={"switch_profile": True})).status_code == 404


@pytest.mark.integration
async def test_pairing_source_revocation_and_persistent_ip_budget(identity_app):
    async with client(identity_app) as source, client(identity_app) as destination:
        owner = await profile(source)
        pairing, _ = await connect(source, destination)
        assert (await source.post("/pairings/" + pairing["id"] + "/confirm")).status_code == 200
        assert (await source.delete("/devices/" + owner["device_id"])).status_code == 200
        assert (await destination.get("/pairings/" + pairing["id"])).status_code == 404
        assert (await identity_app.state.repo.get("pairings", pairing["id"]))["status"] == "revoked"
        identity_app.state.settings.pairing_attempt_limit = 2
        for index in range(2):
            response = await destination.post("/pairings/request", json={"code": "BADCODE"}, headers={"X-Forwarded-For": f"192.0.2.{index}"})
        assert response.status_code == 429
        assert len(await identity_app.state.repo.list("usage", {"kind": "pairing_request"})) == 2


@pytest.mark.integration
async def test_canonical_merged_profile_and_blocked_survivor(identity_app):
    async with client(identity_app) as browser:
        created = await profile(browser)
        survivor = await identity_app.state.repo.create("users", {"display_name": "Survivor"})
        await identity_app.state.repo.update("users", created["user"]["id"], {"merged_into": survivor["id"], "state": "merged"})
        assert (await browser.get("/session")).json()["user"]["id"] == survivor["id"]
        await identity_app.state.repo.update("users", survivor["id"], {"state": "blocked"})
        assert (await browser.get("/session")).json()["user"] is None


@pytest.mark.integration
async def test_pairing_token_challenge_cookie_binding_and_expiration(identity_app):
    repo = identity_app.state.repo
    async with client(identity_app) as source, client(identity_app) as destination:
        await profile(source)
        await csrf(destination)
        pairing = (await source.post("/pairings")).json()
        path = "/pairings/" + pairing["id"]
        response = await destination.post("/pairings/request", json={"token": pairing["token"]})
        assert response.status_code == 200
        challenge = destination.cookies[CHALLENGE_COOKIE]
        row = await repo.get("pairings", pairing["id"])
        assert row["challenge_hash"] == digest(challenge)
        assert pairing["token"] not in str(row) and pairing["code"] not in str(row)
        assert "HttpOnly" in response.headers["set-cookie"]
        assert (await source.post(path + "/confirm")).status_code == 200
        destination.cookies.clear()
        await csrf(destination)
        assert (await destination.post(path + "/complete", json={"switch_profile": True})).status_code == 404
        destination.cookies.set(CHALLENGE_COOKIE, challenge)
        await repo.update("pairings", pairing["id"], {"expires_at": now() - timedelta(seconds=1)})
        assert (await destination.post(path + "/complete", json={"switch_profile": True})).status_code == 404
        assert len(await repo.list("devices")) == 1


async def test_middleware_security_headers_and_error_envelopes():
    app = FastAPI()
    app.state.settings = Settings(public_origin="https://testserver", allowed_origins=["https://testserver"])
    app.add_middleware(SecurityMiddleware)

    @app.get("/public")
    async def public():
        return {"ok": True}

    @app.get("/conflict")
    async def conflict():
        raise ConflictError("test conflict")

    async with client(app) as browser:
        public = await browser.get("/public")
        policy = public.headers["content-security-policy"]
        for directive in ("default-src 'self'", "img-src 'self' blob: data:", "worker-src 'self' blob:",
                          "style-src 'self' 'unsafe-inline'", "script-src 'self'", "connect-src 'self'",
                          "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'"):
            assert directive in policy.split("; ")
        responses = [
            (await browser.post("/public"), 403, "origin_not_allowed"),
            (await browser.post("/public", headers={"Origin": "https://testserver"}), 403, "invalid_csrf_token"),
            (await browser.get("/public", headers={"Content-Length": "-1"}), 400, "invalid_content_length"),
            (await browser.get("/public", headers={"Content-Length": str(151 * 1024)}), 413, "request_too_large"),
            (await browser.get("/conflict"), 409, "conflict"),
        ]
        async def oversized():
            yield b"x" * (75 * 1024)
            yield b"x" * (76 * 1024)
        responses.append((await browser.request("GET", "/public", content=oversized()), 413, "request_too_large"))
        for response, status, code in responses:
            assert response.status_code == status
            body = response.json()
            assert body == {"error": {"code": code, "message": body["detail"]}, "detail": body["detail"]}
            assert response.headers["content-security-policy"] == policy
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["x-content-type-options"] == "nosniff"
