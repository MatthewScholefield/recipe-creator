"""Application logging configured around Logly and third-party integrations."""

import logging
import sys

from logly import logger
from logly.integrations.stdlib import InterceptHandler


_CONFIGURED = False
_LOG_FORMAT = "{time:%Y-%m-%d %H:%M:%S} | {level: <8} | {filename}:{function}:{line} - {message}\n{exception}"


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
        logger.configure(handlers=[{"sink": sys.stderr if sink is None else sink, "level": "DEBUG", "format": _LOG_FORMAT}])
        _CONFIGURED = True
