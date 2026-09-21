"""Pydantic schema mirroring the `plugin.toml` manifest shape."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dgx_hub.plugins.base import LaunchMode, NetworkMode


class PluginInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    description: str = ""
    docs_url: str | None = None
    schema_version: int = 1


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_url: str


class ProvisionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str]
    idempotent: bool = True
    estimated_duration: str | None = None


class PatchSpec(BaseModel):
    """A minimal, generic find/replace or unified-diff patch applied to a
    file in the cloned repo before launch. Last-resort — see module docstring.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    file: str
    find: str | None = None
    replace: str | None = None
    diff: str | None = None

    @model_validator(mode="after")
    def _exactly_one_patch_kind(self) -> PatchSpec:
        has_find_replace = self.find is not None and self.replace is not None
        has_diff = self.diff is not None
        if has_find_replace == has_diff:
            raise ValueError(
                "PatchSpec requires exactly one of (find + replace) or diff"
            )
        return self


class PortSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container_port: int
    publish_strategy: Literal[
        "loopback-remap", "gateway-network", "fixed-exclusive"
    ] = "loopback-remap"


class DockerOverrides(BaseModel):
    """Partial overrides a [[variant]] applies on top of the plugin's [docker] spec."""

    model_config = ConfigDict(extra="forbid")

    image: str | None = None
    command_args: list[str] | None = None
    network_mode: NetworkMode | None = None
    gpus: str | None = None
    ports: list[PortSpec] | None = None
    volumes: list[str] | None = None


class DockerSpec(BaseModel):
    """The primary, generic launch spec. The CLI renders this into an actual
    `docker run` (or `docker compose`) invocation itself — it never needs to
    shell out to the plugin repo's own wrapper script for this part.
    """

    model_config = ConfigDict(extra="forbid")

    mode: LaunchMode = LaunchMode.DOCKER_RUN
    image: str | None = None
    container_name: str | None = None
    network_mode: NetworkMode = NetworkMode.BRIDGE
    gpus: str | None = "all"
    ipc: str | None = None
    shm_size: str | None = None
    volumes: list[str] = Field(default_factory=list)
    env_passthrough: list[str] = Field(default_factory=list)
    ports: list[PortSpec] = Field(default_factory=list)
    command_args: list[str] = Field(default_factory=list)

    compose_file: str | None = None
    compose_project: str | None = None
    service_name: str | None = None
    generate_command: list[str] | None = None

    @model_validator(mode="after")
    def _mode_specific_requirements(self) -> DockerSpec:
        if self.mode == LaunchMode.DOCKER_RUN:
            if not self.image:
                raise ValueError("[docker].image is required when mode = 'docker-run'")
            if not self.container_name:
                raise ValueError(
                    "[docker].container_name is required when mode = 'docker-run'"
                )
        else:
            if not self.compose_file:
                raise ValueError(
                    "[docker].compose_file is required for docker-compose modes"
                )
            if not self.compose_project:
                raise ValueError(
                    "[docker].compose_project is required for docker-compose modes"
                )
            if not self.service_name:
                raise ValueError(
                    "[docker].service_name is required for docker-compose modes"
                )
            if self.mode == LaunchMode.COMPOSE_GENERATED and not self.generate_command:
                raise ValueError(
                    "[docker].generate_command is required when mode = 'compose-generated'"
                )
        return self


class VariantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str = ""
    default: bool = False
    docker_overrides: DockerOverrides = Field(default_factory=DockerOverrides)


class HealthSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    interval_seconds: float = 2.0
    timeout_seconds: float = 5.0
    startup_grace_seconds: float = 30.0
    expected_first_boot_seconds: float = 300.0


class ResourcesSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_free_disk_gib: float = 0.0
    min_free_memory_gib: float = 0.0
    gpu_required: bool = True


class EnvVarSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["string", "integer", "boolean", "enum"] = "string"
    default: Any = ""
    description: str = ""
    choices: list[str] | None = None
    required: bool = False

    @model_validator(mode="after")
    def _validate_default_against_type(self) -> EnvVarSpec:
        if self.type == "enum":
            if not self.choices:
                raise ValueError("env var of type 'enum' must declare 'choices'")
            if self.default not in self.choices:
                raise ValueError(
                    f"default {self.default!r} is not one of choices {self.choices}"
                )
        elif self.type == "integer":
            try:
                int(self.default)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"default {self.default!r} is not a valid integer"
                ) from exc
        elif self.type == "boolean":
            if isinstance(self.default, bool):
                pass
            elif str(self.default).lower() not in ("true", "false"):
                raise ValueError(f"default {self.default!r} is not a valid boolean")
        return self


class EnvValidationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    message: str


class FallbackSpec(BaseModel):
    """Last-resort opaque script-wrapping escape hatch. Disabled by default —
    only set `enabled = true` when a repo's launch logic genuinely can't be
    replicated declaratively via [docker] (see plugin-from-script skill).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    start_command: list[str] = Field(default_factory=list)
    stop_command: list[str] = Field(default_factory=list)


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin: PluginInfo
    source: SourceSpec
    provision: ProvisionSpec | None = None
    patch: list[PatchSpec] = Field(default_factory=list)
    docker: DockerSpec
    variant: list[VariantSpec] = Field(default_factory=list)
    health: HealthSpec
    resources: ResourcesSpec = Field(default_factory=ResourcesSpec)
    env: dict[str, EnvVarSpec] = Field(default_factory=dict)
    env_validation: list[EnvValidationRule] = Field(default_factory=list)
    fallback: FallbackSpec = Field(default_factory=FallbackSpec)

    @model_validator(mode="after")
    def _variants_are_consistent(self) -> PluginManifest:
        if self.variant:
            ids = [v.id for v in self.variant]
            if len(ids) != len(set(ids)):
                raise ValueError("variant ids must be unique")
            defaults = [v for v in self.variant if v.default]
            if len(defaults) > 1:
                raise ValueError("at most one variant may be marked default = true")
        return self

    def default_variant_id(self) -> str | None:
        for v in self.variant:
            if v.default:
                return v.id
        return self.variant[0].id if self.variant else None

    def get_variant(self, variant_id: str) -> VariantSpec:
        for v in self.variant:
            if v.id == variant_id:
                return v
        raise KeyError(f"no such variant: {variant_id!r}")

    @classmethod
    def from_toml_dict(cls, raw: dict[str, Any]) -> PluginManifest:
        """Reshape a raw parsed-TOML dict into this model's shape."""
        data = dict(raw)
        env_raw = dict(data.get("env", {}))
        validation_raw = env_raw.pop("validation", [])
        data["env"] = env_raw
        data["env_validation"] = validation_raw
        return cls.model_validate(data)

    def resolve_env_values(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        """Resolve final string-valued container env vars from manifest
        defaults plus user overrides, serialized consistently regardless of
        declared type — booleans as lowercase 'true'/'false' (the convention
        these repos' own .env files use), not Python's `str(bool)`.
        """
        overrides = overrides or {}
        resolved: dict[str, str] = {}
        for name, spec in self.env.items():
            raw = overrides.get(name, spec.default)
            if spec.type == "boolean":
                is_true = raw if isinstance(raw, bool) else str(raw).lower() == "true"
                resolved[name] = "true" if is_true else "false"
            elif spec.type == "integer":
                resolved[name] = str(int(raw))
            else:
                resolved[name] = str(raw)
        return resolved
