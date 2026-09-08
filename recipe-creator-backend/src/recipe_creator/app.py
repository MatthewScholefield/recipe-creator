import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from logly import logger
from logly.integrations.fastapi import LoglyMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import admin, identity, recipes
from .ai import AIQuotaExceeded
from .jobs import JobRunner
from .logging import configure_logging
from .photos import PhotoService
from .repository import ConflictError, NotFoundError, Repository
from .security import SecurityMiddleware, client_ip, get_context, rate_limit, require_user
from .settings import Settings


configure_logging()


async def cleanup_loop(photos):
    while True:
        try:
            await photos.cleanup()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Media cleanup failed ({})", type(exc).__name__)
        await asyncio.sleep(3600)


def error_response(status, message, headers=None):
    return JSONResponse({"error": {"code": str(status), "message": message}, "detail": message},
                        status_code=status, headers=headers)


def create_app(settings: Settings | None = None, repo=None, run_jobs=True):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        database = repo or Repository(settings)
        await database.connect()
        app.state.repo = database
        app.state.photos = PhotoService(database, settings)
        runner = JobRunner(database, settings)
        cleanup = None
        try:
            if run_jobs:
                await runner.start()
                cleanup = asyncio.create_task(cleanup_loop(app.state.photos))
            yield
        finally:
            if cleanup:
                cleanup.cancel()
                await asyncio.gather(cleanup, return_exceptions=True)
            await runner.stop()
            if repo is None:
                await database.close()

    app = FastAPI(title="Recipe Creator", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(LoglyMiddleware)
    app.include_router(identity.router, prefix="/api")
    app.include_router(recipes.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error_response(exc.status_code, str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors()[:8])
        return error_response(422, "Invalid request fields: " + fields)

    @app.exception_handler(ConflictError)
    async def conflict_error(request, exc):
        return error_response(409, "This record changed. Reload before trying again.")

    @app.exception_handler(NotFoundError)
    async def not_found(request, exc):
        return error_response(404, "Record not found")

    @app.exception_handler(AIQuotaExceeded)
    async def ai_quota(request, exc):
        return error_response(429, "AI allowance exhausted; save as text or try later.")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.exception("Request failed ({})", type(exc).__name__)
        return error_response(503, "Service temporarily unavailable")

    @app.middleware("http")
    async def cache_policy(request, call_next):
        response = await call_next(request)
        if request.method == "GET" and request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response

    @app.get("/api/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/api/health/ready")
    async def ready(request: Request):
        try:
            await request.app.state.repo.list("recipes", limit=1)
        except Exception as exc:
            logger.exception("Readiness check failed ({})", type(exc).__name__)
            raise HTTPException(503, "Not ready") from None
        return {"status": "ok"}

    async def photo_output(request, rows, context):
        uploader_ids = {row.get("uploader_id") for row in rows} - {None}
        names = {}
        for user_id in uploader_ids:
            user = await request.app.state.repo.get("users", user_id)
            names[user_id] = user["display_name"] if user else "Unknown contributor"
        return [{**row, "state": row.get("status", "pending"),
                 "uploader_name": names.get(row.get("uploader_id")),
                 "can_delete": context.admin or bool(context.user and row.get("uploader_id") == context.user["id"])}
                for row in rows]

    @app.get("/api/photos")
    async def photos(request: Request, recipe_id: str | None = None, mine: bool = False,
                     offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        context = await require_user(request) if mine else await get_context(request)
        if not mine and not recipe_id:
            raise HTTPException(422, "Specify a recipe or your submissions")
        rows = await request.app.state.photos.visible_photos(recipe_id, context.user["id"] if context.user else None,
                                                             context.admin, mine)
        return {"items": await photo_output(request, rows[offset:offset + limit], context),
                "has_more": offset + limit < len(rows)}

    @app.post("/api/recipes/{recipe_id}/photos", status_code=201)
    async def upload_photo(request: Request, recipe_id: str, file: UploadFile = File(),
                           caption: str = Form(default="", max_length=1000),
                           idempotency_key: str | None = Header(default=None, min_length=1, max_length=200)):
        context = await require_user(request)
        try:
            row = await request.app.state.photos.upload(recipe_id, context.user, file, caption,
                                                        idempotency_key, client_ip(request))
            return (await photo_output(request, [row], context))[0]
        finally:
            await file.close()

    @app.get("/api/photos/{photo_id}/image")
    async def photo_image(request: Request, photo_id: str, thumbnail: bool = False):
        context = await get_context(request)
        path, public = await request.app.state.photos.image(photo_id, context.user["id"] if context.user else None,
                                                            context.admin, thumbnail)
        headers = {"Cache-Control": "public, max-age=0, must-revalidate" if public else "private, no-store"}
        return FileResponse(path, media_type="image/jpeg", headers=headers)

    @app.delete("/api/photos/{photo_id}", status_code=204)
    async def delete_photo(request: Request, photo_id: str):
        context = await get_context(request)
        if not context.user and not context.admin:
            raise HTTPException(401, "Profile required")
        await request.app.state.photos.remove(photo_id, context.user["id"] if context.user else None, context.admin)
        return Response(status_code=204)

    @app.get("/{path:path}", include_in_schema=False)
    async def static(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Endpoint not found")
        root = settings.frontend_dist.resolve()
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(404, "Not found")
        if not candidate.is_file():
            if Path(path).suffix:
                raise HTTPException(404, "Not found")
            candidate = root / "index.html"
        if not candidate.is_file():
            raise HTTPException(404, "Build the frontend first")
        headers = {"Cache-Control": "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"}
        return FileResponse(candidate, headers=headers)

    return app


app = create_app()
