"""structlog setup.

Call `setup_logging()` once at CLI entry / pipeline start. Subsequent
`structlog.get_logger()` calls in any module pick up the global config.
"""

from __future__ import annotations

import logging
import sys

import structlog


def setup_logging(json_output: bool = False, level: str = "INFO") -> None:
    """Configure structlog + stdlib logging in one shot.

    Args:
        json_output: True for prod (machine-parseable), False for dev console.
        level: Root log level. Anything below this is dropped before processors run.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, level=log_level, format="%(message)s")

    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
