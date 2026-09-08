"""Application logging configured around Logly and third-party integrations."""

import logging

from logly.integrations.stdlib import InterceptHandler


def configure_logging() -> None:
    """Send standard-library and dependency logging through Logly."""
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, format="", force=True)
