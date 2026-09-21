"""On-disk state persistence for running models."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
    """Model id(s) the backend's own /v1/models reported it serves — what
    the gateway routes on. Falls back to the plugin name if discovery
    fails (e.g. the backend isn't OpenAI-/v1/models-compatible)."""

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
        )


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
