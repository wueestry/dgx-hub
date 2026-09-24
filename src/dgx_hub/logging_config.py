"""File-based logging for dgx-hub."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from dgx_hub.config import user_state_dir

_ROOT_LOGGER_NAME = "dgx_hub"
_configured = False


def log_file_path() -> Path:
    return user_state_dir() / "logs" / "dgx-hub.log"


def _configure_once() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    path = log_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """A logger under the `dgx_hub` namespace, writing to `log_file_path()`."""
    _configure_once()
    return logging.getLogger(name)


@contextmanager
def console_logging(console: Console, level: int = logging.INFO) -> Iterator[None]:
    """Mirror `dgx_hub` log records to `console` (in addition to the log file)
    for the duration of the block. Sharing the console with a `rich.live.Live`
    display makes the lines print above the live table instead of tearing it.
    """
    _configure_once()
    handler = RichHandler(
        console=console,
        level=level,
        show_path=False,
        markup=False,
        rich_tracebacks=False,
        log_time_format="[%X]",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
