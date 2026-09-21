"""On-disk state persistence for running models.

Necessary because the CLI process that ran `start` typically exits before a
later `status`/`stop` invocation — state must be recoverable from disk, not
held in memory. Written atomically (temp file + os.replace).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass

from dgx_hub.config import state_file


@dataclass
class ModelRunRecord:
    name: str
    variant_id: str | None
    container_name: str | None
    backend_address: str
    port: int
    state: str
    started_at: str


def _read_all() -> dict[str, dict]:
    path = state_file()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _write_all(data: dict[str, dict]) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def load_all() -> dict[str, ModelRunRecord]:
    return {name: ModelRunRecord(**rec) for name, rec in _read_all().items()}


def save(record: ModelRunRecord) -> None:
    data = _read_all()
    data[record.name] = asdict(record)
    _write_all(data)


def remove(name: str) -> None:
    data = _read_all()
    data.pop(name, None)
    _write_all(data)


def get(name: str) -> ModelRunRecord | None:
    rec = _read_all().get(name)
    return ModelRunRecord(**rec) if rec else None
