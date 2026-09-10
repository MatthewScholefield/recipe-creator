import logging
from io import StringIO

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from logly import logger as logly_logger

from recipe_creator import app, jobs, recipes, server
from recipe_creator.logging import configure_logging
from recipe_creator.settings import Settings


class Logger:
    def __init__(self):
        self.calls = []
        self.exception = None

    def opt(self, *, exception):
        self.exception = exception
        return self

    def error(self, message, *args):
        self.calls.append((message, args))


@pytest.fixture
def log_output():
    output = StringIO()
    sink = logly_logger.add(output, format="{message}\n{exception}", enqueue=False)
    try:
        yield output
    finally:
        logly_logger.remove(sink)


def test_configure_logging_intercepts_stdlib_records(log_output):
    configure_logging()
    assert [type(handler).__name__ for handler in logging.getLogger().handlers] == ["InterceptHandler"]
    logging.getLogger(__name__).info("stdlib integration works")
    assert "stdlib integration works" in log_output.getvalue()


def test_configure_logging_uses_colored_single_line_default(monkeypatch):
    class Terminal(StringIO):
        def isatty(self):
            return True

    output = Terminal()
    monkeypatch.setattr("recipe_creator.logging._CONFIGURED", False)
    monkeypatch.setattr("recipe_creator.logging.sys.stderr", output)
    configure_logging()
    logly_logger.info("first message")
    logly_logger.warning("second message")

    rendered = output.getvalue()
    assert "\x1b[" in rendered
    assert [line for line in rendered.splitlines() if line] == rendered.splitlines()
    assert len(rendered.splitlines()) == 2


async def test_logly_middleware_serves_session_through_asgi_stack(log_output):
    application = app.create_app(run_jobs=False)
    # ASGITransport does not run lifespan; anonymous sessions need no database.
    application.state.repo = object()
    async with AsyncClient(transport=ASGITransport(app=application), base_url="https://testserver") as client:
        response = await client.get("/api/session", headers={"authorization": "Bearer private-token"})
        rejected = await client.post("/api/identity", json={"display_name": "private name"})
        missing = await client.get("/api/not-a-route")

    assert response.status_code == 200
    assert response.json()["user"] is None
    assert len(response.json()["csrf_token"]) == 64
    assert "recipe_csrf" in response.cookies
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert rejected.status_code == 403
    assert missing.status_code == 404
    output = log_output.getvalue()
    assert "GET /api/session " in output
    assert "POST /api/identity " in output
    assert "private-token" not in output
    assert "private name" not in output


def test_configure_logging_preserves_exception_details_in_default_sink():
    output = StringIO()
    configure_logging(sink=output)
    try:
        raise RuntimeError("database unavailable")
    except RuntimeError as exc:
        logly_logger.opt(exception=exc).error("Request failed")

    rendered = output.getvalue()
    assert "Request failed" in rendered
    assert "RuntimeError: database unavailable" in rendered
    assert "Traceback" in rendered


async def test_logly_middleware_logs_unexpected_failure_with_traceback(monkeypatch, log_output):
    application = app.create_app(run_jobs=False)

    async def fail(request):
        raise RuntimeError("provider secret token")

    monkeypatch.setattr(app.identity, "get_context", fail)
    # Keep raise_app_exceptions=True: the stack must handle the error itself.
    async with AsyncClient(transport=ASGITransport(app=application), base_url="https://testserver") as client:
        response = await client.get("/api/session", headers={"authorization": "Bearer private-token"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Service temporarily unavailable"
    assert "provider secret token" not in response.text
    output = log_output.getvalue()
    assert "Request failed" in output
    assert "RuntimeError: provider secret token" in output
    assert "Traceback" in output
    assert "private-token" not in output


def test_server_keeps_uvicorn_logging_integration(monkeypatch):
    calls = []
    monkeypatch.setattr("sys.argv", ["recipe-creator-server"])
    monkeypatch.setattr(server, "setup_uvicorn_logging", lambda **kwargs: calls.append(("logging", kwargs)))
    monkeypatch.setattr(server.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    server.main()
    assert calls[0] == ("logging", {"format": None})
    args, kwargs = calls[1]
    assert args == ("recipe_creator.app:create_app",)
    assert kwargs["factory"] is True
    assert kwargs["log_config"] is None


async def test_parse_failure_logs_exception_and_returns_generic_503(monkeypatch):
    async def allow(*args, **kwargs):
        return None

    async def fail(*args, **kwargs):
        raise RuntimeError("provider secret token")

    logger = Logger()
    request = type("Request", (), {"app": type("App", (), {"state": type("State", (), {
        "repo": None, "settings": Settings(),
    })()})()})()
    monkeypatch.setattr(recipes, "require_user", allow)
    monkeypatch.setattr(recipes, "retry_transaction", allow)
    monkeypatch.setattr(recipes.ai, "parse_recipe", fail)
    monkeypatch.setattr(recipes, "logger", logger)
    with pytest.raises(HTTPException, match="temporarily unavailable") as error:
        await recipes.parse(request, recipes.ParseRequest(source_text="private recipe text"))
    assert error.value.status_code == 503
    assert logger.calls == [("Recipe parsing failed", ())]
    assert isinstance(logger.exception, RuntimeError)
    assert str(logger.exception) == "provider secret token"


async def test_job_polling_failure_logs_exception(monkeypatch):
    runner = jobs.JobRunner(None, Settings())
    logger = Logger()

    async def fail():
        runner._stop.set()
        raise RuntimeError("provider secret token")

    monkeypatch.setattr(runner, "run_once", fail)
    monkeypatch.setattr(jobs, "logger", logger)
    await runner.run()
    assert logger.calls == [("Enrichment polling failed", ())]
    assert isinstance(logger.exception, RuntimeError)
    assert str(logger.exception) == "provider secret token"


async def test_ingredient_enrichment_failure_logs_exception(monkeypatch, log_output):
    recipe = {
        "id": "r",
        "revision": 1,
        "source_text": "",
        "ingredient_groups": [],
    }
    job = {
        "id": "j",
        "recipe_id": "r",
        "input_revision": 1,
        "input_hash": jobs.enrichment_hash(recipe),
    }

    class Repository:
        async def get(self, table, identifier):
            assert (table, identifier) == ("recipes", "r")
            return recipe

    runner = jobs.JobRunner(Repository(), Settings())
    finished = []

    async def claim():
        return job

    async def fail(*args, **kwargs):
        raise RuntimeError("provider failure details")

    async def finish(claimed, groups=None, error=None):
        finished.append((claimed, error))

    monkeypatch.setattr(runner, "_claim", claim)
    monkeypatch.setattr(runner, "_finish", finish)
    monkeypatch.setattr(jobs, "enrich_recipe", fail)

    assert await runner.run_once()
    assert finished == [(job, "RuntimeError")]
    output = log_output.getvalue()
    assert "Ingredient enrichment failed" in output
    assert "RuntimeError: provider failure details" in output
    assert "Traceback" in output
