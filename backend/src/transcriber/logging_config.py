"""Safe structured logging shared by Railway web and worker processes."""

from __future__ import annotations

import logging.config
import sys

from pythonjsonlogger.json import JsonFormatter


def configure_logging(level: str) -> None:
    """Write structured application logs to stderr without private payload fields."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": JsonFormatter,
                    "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
                }
            },
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": sys.stderr,
                }
            },
            "root": {"handlers": ["stderr"], "level": level},
        }
    )
