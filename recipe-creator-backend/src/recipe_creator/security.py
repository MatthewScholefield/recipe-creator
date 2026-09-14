import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import hmac
import re
import secrets

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from .repository import ConflictError


DEVICE_COOKIE = "recipe_device"
ADMIN_COOKIE = "recipe_admin"
CSRF_COOKIE = "recipe_csrf"
CHALLENGE_COOKIE = "recipe_pairing"
CSRF_PROCESS_KEY = secrets.token_bytes(32)


def csrf_token(settings, cookie):
    configured = settings.session_secret.get_secret_value()
    key = configured.encode() if configured else CSRF_PROCESS_KEY
    return hmac.new(key, cookie.encode(), "sha256").hexdigest()


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def now():
    return datetime.now(UTC)


def active(row):
    if not row or row.get("revoked_at") or row.get("state") == "blocked":
        return False
    expiry = row.get("expires_at")
    if not expiry:
        return False
    if isinstance(expiry, str):
        expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    return expiry > now()


def record_id(value, table):
    if value.startswith(table + ":"):
        value = value[len(table) + 1:]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise HTTPException(422, "Invalid record ID")
    return value


async def all_rows(repo, table, filters=None):
    result = []
    start = 0
    while True:
        rows = await repo.list(table, filters, limit=500, start=start)
        result.extend(rows)
        if len(rows) < 500:
            return result
        start += len(rows)


async def canonical_user(repo, user_id):
    seen = set()
    while user_id and user_id not in seen:
        seen.add(user_id)
        user = await repo.get("users", user_id)
        if not user or user.get("state") == "blocked":
            return None
        if not user.get("merged_into"):
            return user if user.get("state") == "active" else None
        user_id = user["merged_into"]
    return None


@dataclass
class Context:
    user: dict | None = None
    device: dict | None = None
    admin: bool = False
    admin_session: dict | None = None


async def resolve_context(request: Request, repo) -> Context:
    context = Context()
    secret = request.cookies.get(DEVICE_COOKIE, "")
    if secret and len(secret) <= 128:
        rows = await repo.list("devices", {"secret_hash": digest(secret)}, limit=2)
        if len(rows) == 1 and active(rows[0]):
            user = await canonical_user(repo, rows[0]["user_id"])
            if user:
                context.user, context.device = user, rows[0]
    context.admin = bool(context.user and context.user.get("is_admin", False))
    return context


async def get_context(request: Request) -> Context:
    return await resolve_context(request, request.app.state.repo)


async def require_user(request: Request) -> Context:
    context = await get_context(request)
    if not context.user:
        raise HTTPException(401, "Active profile required")
    return context


async def require_admin(request: Request) -> Context:
    context = await get_context(request)
    if not context.user:
        raise HTTPException(401, "Active profile required")
    if not context.admin:
        raise HTTPException(403, "Admin permission required")
    return context


async def authorize(request, tx, admin=False):
    context = await resolve_context(request, tx)
    if not context.user:
        raise HTTPException(401, "Active profile required")
    if admin and not context.admin:
        raise HTTPException(403, "Admin permission required")
    await tx.update("devices", context.device["id"], {"last_used_at": now()})
    await tx.update("users", context.user["id"], {})
    return context


def public_user(user):
    if user is None:
        return None
    return {"id": user["id"], "display_name": user["display_name"],
            "state": user["state"], "trusted": user["trusted"],
            "merged_into": user.get("merged_into")}


def set_cookie(response, request, name, value, ttl):
    response.set_cookie(name, value, max_age=ttl, secure=request.app.state.settings.secure_cookies,
                        httponly=True, samesite="lax", path="/")


def clear_cookie(response, request, name):
    response.delete_cookie(name, path="/", secure=request.app.state.settings.secure_cookies,
                           httponly=True, samesite="lax")


def client_ip(request):
    return request.client.host if request.client else "unknown"


async def retry_transaction(repo, operation, attempts=4):
    for attempt in range(attempts):
        try:
            async with repo.transaction() as tx:
                result = await operation(tx)
            return result
        except ConflictError:
            if attempt == attempts - 1:
                raise HTTPException(409, "Concurrent update; retry") from None
            await asyncio.sleep(0.01 * (attempt + 1) + secrets.randbelow(6) / 1000)


async def rate_limit(request, kind, limit, window=300):
    timestamp = now()
    period = int(timestamp.timestamp()) // window
    scopes = (("ip:" + digest(client_ip(request)), limit), ("global", limit * 20))

    async def charge(tx):
        denied = False
        for scope, budget in scopes:
            key = digest(f"{kind}:{scope}:{period}")
            row = await tx.get("usage", key)
            count = (row["count"] if row else 0) + 1
            data = {"scope": scope, "kind": kind, "count": count,
                    "expires_at": timestamp + timedelta(seconds=window * 2)}
            if row:
                await tx.compare_and_swap("usage", key, row["revision"], data)
            else:
                await tx.create("usage", data, id=key)
            denied |= count > budget
        return denied

    if await retry_transaction(request.app.state.repo, charge):
        raise HTTPException(429, "Attempt limit exceeded", headers={"Retry-After": str(window)})


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        settings = request.app.state.settings

        mutation = scope["method"] not in {"GET", "HEAD", "OPTIONS"}

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([(b"referrer-policy", b"no-referrer"), (b"x-content-type-options", b"nosniff")])
                if not any(key.lower() == b"content-security-policy" for key, _ in headers):
                    headers.append((b"content-security-policy",
                                    b"default-src 'self'; img-src 'self' blob: data:; "
                                    b"worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
                                    b"script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                                    b"base-uri 'none'; form-action 'self'"))
                if mutation or message["status"] >= 400 or re.search(r"/(session|identity|devices|pairings|admin)(/|$)", scope["path"]):
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        async def reject(status, code, detail):
            await JSONResponse({"error": {"code": code, "message": detail}, "detail": detail},
                               status_code=status)(scope, receive, secure_send)
        if mutation:
            origin = request.headers.get("origin")
            if not origin or origin == "null" or origin not in settings.allowed_origins:
                await reject(403, "origin_not_allowed", "Origin not allowed")
                return
            cookie = request.cookies.get(CSRF_COOKIE, "")
            token = request.headers.get("x-csrf-token", "")
            if (not re.fullmatch(r"[A-Za-z0-9_-]{43}", cookie)
                    or not re.fullmatch(r"[0-9a-f]{64}", token)
                    or not hmac.compare_digest(csrf_token(settings, cookie), token)):
                await reject(403, "invalid_csrf_token", "Invalid CSRF token")
                return
        photo_upload = scope["method"] == "POST" and bool(re.search(r"/recipes/[^/]+/photos/?$", scope["path"]))
        cap = (650 if photo_upload else 150) * 1024
        length = request.headers.get("content-length")
        if length is not None:
            try:
                declared = int(length)
                if declared < 0:
                    raise ValueError
            except ValueError:
                await reject(400, "invalid_content_length", "Invalid Content-Length")
                return
            if declared > cap:
                await reject(413, "request_too_large", "Request body too large")
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > cap:
                await reject(413, "request_too_large", "Request body too large")
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        try:
            await self.app(scope, replay, secure_send)
        except ConflictError:
            await reject(409, "conflict", "Concurrent update; retry")
