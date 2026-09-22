"""File-based logging for dgx-hub.

`dgx-hub start` renders a live Rich dashboard that owns the terminal, so
anything more detailed than the dashboard's one-line "Detail" column has
nowhere to go on stdout without corrupting that display. Every step worth
recording in detail (provisioning commands, the exact `docker run` argv,
stale-container cleanup, subprocess stdout/stderr) goes to a log file
instead, via `get_logger(__name__)`.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

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
