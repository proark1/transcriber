"""Safe structured logging shared by Railway web and worker processes."""

from __future__ import annotations

import logging.config
import sys

from pythonjsonlogger.json import JsonFormatter


def configure_logging(level: str) -> None:
    """Write structured application logs without private payload fields."""
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
                "stdout": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": sys.stdout,
                }
            },
            "root": {"handlers": ["stdout"], "level": level},
        }
    )
