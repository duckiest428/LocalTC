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
    nearest_airport_interval_s: float = 60.0  # 0 disables automatic airport data fetches


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


class FlightConfig(_Section):
    rules: Literal["IFR"] = "IFR"
    destination: str = ""  # ICAO
    cruise_ft: int = 0  # 0 = unknown
    callsign: str = ""  # override the sim's ATC ID, e.g. "N172LT" or "ASA123"


class AtcConfig(_Section):
    enabled: bool = True
    seed: int = 0  # 0 = derived from the callsign and date
    center_name: str = "Seattle"
    center_mhz: float = 125.1
    strict_callsign: bool = False
    airport_dirs: list[str] = []  # extra folders of <ICAO>.json airport files
    phase: dict[str, float] = {}  # overrides for PhaseThresholds, e.g. taxi_start_kt = 4


class LlmConfig(_Section):
    """The local language model (Ollama). Without it, or when it's slow, the grammar does the work."""

    enabled: bool = True
    base_url: str = "http://localhost:11434"
    model: str = "llama3.2:3b"
    understanding: Literal["primary", "fallback", "off"] = "primary"  # primary: every transmission; fallback: only
    phrasing: bool = True  # word replies that have no template (questions, declined requests)
    timeout_s: float = 2.5  # per model call
    budget_s: float = 4.0  # per transmission, including one retry
    max_attempts: int = 2
    keep_alive: str = "1h"
    num_ctx: int = 4096
    replay: Literal["recorded", "live", "off"] = "recorded"  # model answers during a replay


class CopilotConfig(_Section):
    mode: Literal["off", "assist", "full"] = "off"  # assist: readbacks + frequency changes; full: every call
    delay_min_s: float = 2.0  # pilot reaction time before speaking
    delay_max_s: float = 4.0


class Config(_Section):
    source: SourceConfig = msgspec.field(default_factory=SourceConfig)
    flight: FlightConfig = msgspec.field(default_factory=FlightConfig)
    atc: AtcConfig = msgspec.field(default_factory=AtcConfig)
    llm: LlmConfig = msgspec.field(default_factory=LlmConfig)
    copilot: CopilotConfig = msgspec.field(default_factory=CopilotConfig)
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
