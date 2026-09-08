import logging

import pytest
from fastapi import HTTPException

from recipe_creator import app, jobs, recipes
from recipe_creator.logging import configure_logging
from recipe_creator.settings import Settings


class Logger:
    def __init__(self):
        self.calls = []

    def exception(self, message, *args):
        self.calls.append((message, args))


def test_configure_logging_intercepts_stdlib_records():
    configure_logging()
    assert [type(handler).__name__ for handler in logging.getLogger().handlers] == ["InterceptHandler"]


def test_app_registers_logly_middleware():
    application = app.create_app(run_jobs=False)
    assert any(middleware.cls.__name__ == "LoglyMiddleware" for middleware in application.user_middleware)


async def test_parse_failure_is_logged_without_exposing_error(monkeypatch):
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
    assert logger.calls == [("Recipe parsing failed ({})", ("RuntimeError",))]


async def test_job_polling_failure_is_logged_without_error_contents(monkeypatch):
    runner = jobs.JobRunner(None, Settings())
    logger = Logger()

    async def fail():
        runner._stop.set()
        raise RuntimeError("provider secret token")

    monkeypatch.setattr(runner, "run_once", fail)
    monkeypatch.setattr(jobs, "logger", logger)
    await runner.run()
    assert logger.calls == [("Enrichment polling failed ({})", ("RuntimeError",))]
