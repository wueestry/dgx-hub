"""On-disk state persistence for running models."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import TypeAdapter

from dgx_hub.config import state_file
from dgx_hub.plugins.base import ContainerHandle, RuntimeKind


@dataclass
class ModelRunRecord:
    name: str
    variant_id: str | None
    container_name: str | None
    backend_address: str
    port: int
    state: str
    started_at: str
    kind: str = RuntimeKind.DOCKER_RUN.value
    compose_project: str | None = None
    compose_file: str | None = None
    service_name: str | None = None
    served_model_ids: list[str] = field(default_factory=list)
    gateway_address: str = ""
    image_digest: str | None = None
    source_commit: str | None = None
    error: str | None = None

    def to_handle(self) -> ContainerHandle:
        """Reconstruct the ContainerHandle this record described, so
        status/stop/logs address the right runtime (docker run vs compose)
        instead of assuming DOCKER_RUN.
        """
        return ContainerHandle(
            kind=RuntimeKind(self.kind),
            container_name=self.container_name,
            compose_project=self.compose_project,
            compose_file=Path(self.compose_file) if self.compose_file else None,
            service_name=self.service_name,
            backend_address=self.backend_address,
            gateway_address=self.gateway_address,
        )


class StateError(RuntimeError):
    """State is invalid; the original file is left intact for recovery."""


_thread_lock = threading.RLock()


@contextmanager
def process_lock(label: str = "state") -> Iterator[None]:
    path = state_file().with_suffix(f".{label}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock, path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read_all() -> dict[str, dict]:
    path = state_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        # Legacy registries are migrated on their next write.
        if "schema_version" in data:
            if data["schema_version"] != 1:
                raise ValueError("unsupported schema version")
            data = data["models"]
        if not isinstance(data, dict):
            raise ValueError("expected a model registry")
        for name, raw in data.items():
            record = TypeAdapter(ModelRunRecord).validate_python(raw)
            if record.name != name or not isinstance(record.port, int):
                raise ValueError("invalid name or port")
            RuntimeKind(record.kind)
            from dgx_hub.plugins.base import ModelState

            ModelState(record.state)
        return data
    except (ValueError, TypeError, KeyError) as exc:
        raise StateError(f"Invalid state in {path}: {exc}. File preserved for recovery.") from exc


def _write_all(data: dict[str, dict]) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"schema_version": 1, "models": data}, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def load_all() -> dict[str, ModelRunRecord]:
    return {name: ModelRunRecord(**rec) for name, rec in _read_all().items()}


def save(record: ModelRunRecord) -> None:
    with process_lock():
        data = _read_all()
        data[record.name] = asdict(record)
        _write_all(data)


def remove(name: str) -> None:
    with process_lock():
        data = _read_all()
        data.pop(name, None)
        _write_all(data)


def get(name: str) -> ModelRunRecord | None:
    rec = _read_all().get(name)
    return ModelRunRecord(**rec) if rec else None


def save_many(records: list[ModelRunRecord]) -> None:
    """Commit a validated launch batch as one atomic reservation."""
    with process_lock():
        data = _read_all()
        data.update({record.name: asdict(record) for record in records})
        _write_all(data)
