"""Filesystem locations for dgx-hub: built-in data, user config/state, cloned repos."""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

APP_NAME = "dgx-hub"


def builtin_plugins_dir() -> Path:
    """The `plugins/` directory shipped alongside the source checkout."""
    override = os.environ.get("DGX_HUB_BUILTIN_PLUGINS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / "plugins"


def user_config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME))


def user_plugins_dir() -> Path:
    return user_config_dir() / "plugins"


def user_data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME))


def repos_dir() -> Path:
    return user_data_dir() / "repos"


def user_state_dir() -> Path:
    return Path(platformdirs.user_state_dir(APP_NAME))


def state_file() -> Path:
    return user_state_dir() / "state.json"
