from datetime import timedelta
import hmac
import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import SessionResponse
from .security import (
    ADMIN_COOKIE, CHALLENGE_COOKIE, CSRF_COOKIE, DEVICE_COOKIE, active, all_rows,
    authorize, canonical_user, clear_cookie, csrf_token, digest, get_context, now, public_user,
    rate_limit, record_id, require_user, resolve_context, retry_transaction, set_cookie,
)


router = APIRouter()


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("A nonempty printable display name is required")
        return value


class PairingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str | None = Field(default=None, min_length=1, max_length=32)
    token: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def one_credential(self):
        if bool(self.code) == bool(self.token):
            raise ValueError("Supply exactly one code or token")
        return self


class CompleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    switch_profile: Literal[True]


async def issue_device(tx, user_id, settings):
    secret = secrets.token_urlsafe(32)
    device = await tx.create("devices", {"user_id": user_id, "secret_hash": digest(secret),
                                         "last_used_at": now(),
                                         "expires_at": now() + timedelta(seconds=settings.device_ttl_seconds)})
    return device, secret


async def invalidate_pairings(tx, user_ids=(), device_ids=()):
    for row in await all_rows(tx, "pairings"):
        if (row["source_user_id"] in user_ids or row["source_device_id"] in device_ids) and row["status"] in {"pending", "requested", "approved"}:
            await tx.compare_and_swap("pairings", row["id"], row["revision"], {"status": "revoked"})


async def live_pairing(tx, pairing_id):
    pairing = await tx.get("pairings", record_id(pairing_id, "pairings"))
    if not active(pairing) or pairing["status"] not in {"pending", "requested", "approved"}:
        raise HTTPException(404, "Pairing unavailable")
    device = await tx.get("devices", pairing["source_device_id"])
    user = await canonical_user(tx, pairing["source_user_id"])
    if not active(device) or not user or device["user_id"] != user["id"] or user["id"] != pairing["source_user_id"]:
        raise HTTPException(404, "Pairing unavailable")
    return pairing, device, user


def challenge_matches(request, pairing):
    challenge = request.cookies.get(CHALLENGE_COOKIE, "")
    return bool(challenge and len(challenge) <= 128 and pairing.get("challenge_hash") and
                hmac.compare_digest(digest(challenge), pairing["challenge_hash"]))


def pairing_output(pairing, user, existing=False):
    return {"id": pairing["id"], "display_name": user["display_name"],
            "status": pairing["status"], "expires_at": pairing["expires_at"],
            "has_existing_profile": existing}


@router.get("/session", response_model=SessionResponse)
async def session(request: Request, response: Response):
    settings = request.app.state.settings
    context = await get_context(request)
    if context.user:
        async def renew(tx):
            current = await resolve_context(request, tx)
            if current.user:
                timestamp = now()
                current.device = await tx.update("devices", current.device["id"], {
                    "last_used_at": timestamp,
                    "expires_at": timestamp + timedelta(seconds=settings.device_ttl_seconds)})
                await tx.update("users", current.user["id"], {})
            return current

        context = await retry_transaction(request.app.state.repo, renew)
        if context.user:
            set_cookie(response, request, DEVICE_COOKIE, request.cookies[DEVICE_COOKIE], settings.device_ttl_seconds)
    token = request.cookies.get(CSRF_COOKIE, "")
    if len(token) != 43 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in token):
        token = secrets.token_urlsafe(32)
    set_cookie(response, request, CSRF_COOKIE, token, 86400)
    return {"user": public_user(context.user), "device_id": context.device["id"] if context.device else None,
            "admin": context.admin, "csrf_token": csrf_token(request.app.state.settings, token)}


@router.post("/identity", status_code=201)
async def create_identity(body: ProfileInput, request: Request, response: Response):
    await rate_limit(request, "identity_create", 10, 3600)

    async def create(tx):
        context = await resolve_context(request, tx)
        if context.user:
            raise HTTPException(409, "Profile already exists")
        old_secret = request.cookies.get(DEVICE_COOKIE)
        if old_secret:
            rows = await tx.list("devices", {"secret_hash": digest(old_secret)}, limit=1)
            if rows:
                raise HTTPException(403, "Existing credential is unavailable; explicitly clear this browser's profile first")
        user = await tx.create("users", {"display_name": body.display_name})
        device, secret = await issue_device(tx, user["id"], request.app.state.settings)
        return user, device, secret

    user, device, secret = await retry_transaction(request.app.state.repo, create)
    set_cookie(response, request, DEVICE_COOKIE, secret, request.app.state.settings.device_ttl_seconds)
    return {"user": public_user(user), "device_id": device["id"]}


@router.patch("/identity")
async def update_identity(body: ProfileInput, request: Request):
    async def update(tx):
        context = await authorize(request, tx)
        return await tx.update("users", context.user["id"], {"display_name": body.display_name})

    result = await retry_transaction(request.app.state.repo, update)
    from .recipes import invalidate_catalog
    invalidate_catalog(request.app.state.repo)
    return {"user": public_user(result)}


@router.get("/devices")
async def devices(request: Request):
    context = await require_user(request)
    rows = await all_rows(request.app.state.repo, "devices", {"user_id": context.user["id"]})
    fields = ("id", "label", "created_at", "last_used_at", "expires_at", "revoked_at")
    items = [{**{key: row.get(key) for key in fields}, "current": row["id"] == context.device["id"]} for row in rows]
    return {"items": items, "devices": items}


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: str, request: Request, response: Response):
    device_id = record_id(device_id, "devices")

    async def revoke(tx):
        context = await authorize(request, tx)
        device = await tx.get("devices", device_id)
        if not device or device["user_id"] != context.user["id"]:
            raise HTTPException(404, "Device not found")
        await tx.update("devices", device_id, {"revoked_at": now()})
        await invalidate_pairings(tx, device_ids=(device_id,))
        return context.device["id"] == device_id

    if await retry_transaction(request.app.state.repo, revoke):
        clear_cookie(response, request, DEVICE_COOKIE)
    return {"ok": True}


@router.post("/pairings", status_code=201)
async def create_pairing(request: Request):
    await require_user(request)
    await rate_limit(request, "pairing_create", request.app.state.settings.pairing_attempt_limit)
    token = secrets.token_urlsafe(32)
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    code = "".join(secrets.choice(alphabet) for _ in range(8))

    async def create(tx):
        context = await authorize(request, tx)
        return await tx.create("pairings", {"source_user_id": context.user["id"],
                                           "source_device_id": context.device["id"],
                                           "token_hash": digest(token), "code_hash": digest(code),
                                           "expires_at": now() + timedelta(seconds=request.app.state.settings.pairing_ttl_seconds)})

    pairing = await retry_transaction(request.app.state.repo, create)
    return {"id": pairing["id"], "code": code, "token": token, "expires_at": pairing["expires_at"]}


@router.post("/pairings/request")
async def request_pairing(body: PairingInput, request: Request, response: Response):
    await rate_limit(request, "pairing_request", request.app.state.settings.pairing_attempt_limit)
    filters = {"token_hash": digest(body.token)} if body.token else {"code_hash": digest(body.code.upper().replace("-", "").replace(" ", ""))}
    challenge = secrets.token_urlsafe(32)

    async def bind(tx):
        rows = await tx.list("pairings", filters, limit=2)
        if len(rows) != 1:
            raise HTTPException(404, "Pairing unavailable")
        pairing, device, user = await live_pairing(tx, rows[0]["id"])
        if pairing["status"] != "pending":
            raise HTTPException(409, "Pairing already requested")
        context = await resolve_context(request, tx)
        if context.device and context.device["id"] == device["id"]:
            raise HTTPException(409, "Use a different browser to connect")
        if pairing["attempts"] >= request.app.state.settings.pairing_attempt_limit:
            raise HTTPException(429, "Pairing attempt limit exceeded")
        pairing = await tx.compare_and_swap("pairings", pairing["id"], pairing["revision"], {
            "status": "requested", "challenge_hash": digest(challenge),
            "destination_user_id": context.user["id"] if context.user else None,
            "attempts": pairing["attempts"] + 1})
        await tx.update("devices", device["id"], {})
        await tx.update("users", user["id"], {})
        return pairing_output(pairing, user, bool(context.user))

    result = await retry_transaction(request.app.state.repo, bind)
    set_cookie(response, request, CHALLENGE_COOKIE, challenge, request.app.state.settings.pairing_ttl_seconds)
    return result


@router.get("/pairings/{pairing_id}")
async def pairing_status(pairing_id: str, request: Request):
    pairing, device, user = await live_pairing(request.app.state.repo, pairing_id)
    context = await get_context(request)
    source = context.device and context.device["id"] == device["id"]
    if not source and not challenge_matches(request, pairing):
        raise HTTPException(404, "Pairing unavailable")
    return pairing_output(pairing, user, bool(pairing.get("destination_user_id")))


@router.post("/pairings/{pairing_id}/confirm")
async def confirm_pairing(pairing_id: str, request: Request):
    async def confirm(tx):
        context = await authorize(request, tx)
        pairing, device, user = await live_pairing(tx, pairing_id)
        if context.device["id"] != device["id"]:
            raise HTTPException(403, "Only the source device may approve")
        if pairing["status"] != "requested":
            raise HTTPException(409, "Pairing is not awaiting approval")
        pairing = await tx.compare_and_swap("pairings", pairing["id"], pairing["revision"], {"status": "approved"})
        return pairing_output(pairing, user, bool(pairing.get("destination_user_id")))

    return await retry_transaction(request.app.state.repo, confirm)


@router.post("/pairings/{pairing_id}/complete")
async def complete_pairing(pairing_id: str, body: CompleteInput, request: Request, response: Response):
    await rate_limit(request, "pairing_complete", request.app.state.settings.pairing_attempt_limit)

    async def complete(tx):
        pairing, source_device, user = await live_pairing(tx, pairing_id)
        if not challenge_matches(request, pairing):
            raise HTTPException(404, "Pairing unavailable")
        if pairing["status"] != "approved":
            raise HTTPException(409, "Source approval is required")
        await tx.compare_and_swap("pairings", pairing["id"], pairing["revision"], {"status": "consumed", "challenge_hash": None})
        await tx.update("devices", source_device["id"], {})
        await tx.update("users", user["id"], {})
        device, secret = await issue_device(tx, user["id"], request.app.state.settings)
        # Switching profiles explicitly logs this browser out of admin; pairing
        # never copies the source device's separate admin credential.
        context = await resolve_context(request, tx)
        if context.admin_session:
            await tx.update("admin_sessions", context.admin_session["id"], {"revoked_at": now()})
        return user, device, secret

    user, device, secret = await retry_transaction(request.app.state.repo, complete)
    set_cookie(response, request, DEVICE_COOKIE, secret, request.app.state.settings.device_ttl_seconds)
    clear_cookie(response, request, CHALLENGE_COOKIE)
    clear_cookie(response, request, ADMIN_COOKIE)
    return {"user": public_user(user), "device_id": device["id"]}
