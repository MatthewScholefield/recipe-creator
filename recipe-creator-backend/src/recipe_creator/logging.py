"""Application logging configured around Logly and third-party integrations."""

import logging
import sys

from logly import logger
from logly.integrations.stdlib import InterceptHandler


_CONFIGURED = False


def _has_exception(record) -> bool:
    return record["exception"] is not None


async def logly_dispatch(request, call_next):
    """Supply the dispatch required by Logly 0.2.2's BaseHTTPMiddleware."""
    try:
        return await call_next(request)
    except Exception as exc:
        # Handle failures before Logly (or Uvicorn) logs raw exception details.
        return await request.app.exception_handlers[Exception](request, exc)


def configure_logging(*, sink=None) -> None:
    """Send standard-library and dependency logging through Logly."""
    global _CONFIGURED
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, format="", force=True)
    if not _CONFIGURED or sink is not None:
        target = sys.stderr if sink is None else sink
        logger.configure(handlers=[
            # No format override: keep Logly's colored, single-line default.
            {"sink": target, "level": "DEBUG"},
            # Logly 0.2.2's default omits tracebacks. Add only the traceback;
            # putting {exception} in the main format creates a blank line.
            {"sink": target, "level": "DEBUG", "format": "{exception}", "filter": _has_exception},
        ])
        _CONFIGURED = True
