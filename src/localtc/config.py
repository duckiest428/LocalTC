"""Configuration loaded from TOML (``config/localtc.toml`` by default)."""

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import msgspec

DEFAULT_CONFIG_PATH = Path("config/localtc.toml")

SourceKind = Literal["live", "replay"]


class _Section(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    pass


class SourceConfig(_Section):
    kind: SourceKind = "replay"


class LiveConfig(_Section):
    dll_path: str = ""
    app_name: str = "LocalTC"
    ownship_hz: float = 4.0
    traffic_interval_s: float = 3.0
    traffic_radius_m: int = 50_000
    retry_max_s: float = 15.0
    connect_timeout_s: float = 0.0  # 0 = wait indefinitely


class ReplayConfig(_Section):
    path: str = ""
    speed: float = 1.0
    start_at: float = 0.0
    end_at: float | None = None
    loop: bool = False
    include_radio: bool = True


class RecorderConfig(_Section):
    enabled: bool = True
    dir: str = "recordings"
    flush_interval_s: float = 1.0
    compress: bool = False


class Config(_Section):
    source: SourceConfig = msgspec.field(default_factory=SourceConfig)
    live: LiveConfig = msgspec.field(default_factory=LiveConfig)
    replay: ReplayConfig = msgspec.field(default_factory=ReplayConfig)
    recorder: RecorderConfig = msgspec.field(default_factory=RecorderConfig)


class ConfigError(ValueError):
    pass


def load_config(path: str | Path | None = None, env: Mapping[str, str] = os.environ) -> Config:
    """Load config from ``path``, ``$LOCALTC_CONFIG`` or the default file; missing default = defaults.

    ``$LOCALTC_SOURCE`` overrides ``source.kind``.
    """
    explicit = path or env.get("LOCALTC_CONFIG")
    config_path = Path(explicit) if explicit else DEFAULT_CONFIG_PATH
    data: dict = {}
    if config_path.is_file():
        with open(config_path, "rb") as fh:
            data = tomllib.load(fh)
    elif explicit:
        raise ConfigError(f"config file not found: {config_path}")

    try:
        cfg = msgspec.convert(data, Config)
    except msgspec.ValidationError as exc:
        raise ConfigError(f"{config_path}: {exc}") from exc

    if kind := env.get("LOCALTC_SOURCE"):
        if kind not in ("live", "replay"):
            raise ConfigError(f"LOCALTC_SOURCE must be 'live' or 'replay', got {kind!r}")
        cfg.source.kind = kind
    return cfg
