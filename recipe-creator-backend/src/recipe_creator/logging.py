"""Application logging configured around Logly and third-party integrations."""

import logging

from logly.integrations.stdlib import InterceptHandler


async def logly_dispatch(request, call_next):
    """Supply the dispatch required by Logly 0.2.2's BaseHTTPMiddleware."""
    try:
        return await call_next(request)
    except Exception as exc:
        # Handle failures before Logly (or Uvicorn) logs raw exception details.
        return await request.app.exception_handlers[Exception](request, exc)


def configure_logging() -> None:
    """Send standard-library and dependency logging through Logly."""
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, format="", force=True)
