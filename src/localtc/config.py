"""Configuration loaded from TOML (``config/localtc.toml`` by default).

Settings changed in the app are saved separately, in ``settings.toml`` in the LocalTC data folder
(``%LOCALAPPDATA%\\LocalTC`` on Windows), and applied on top of the config file. That file holds only
what was changed, so pulling a new ``config/localtc.toml`` never loses them. ``$LOCALTC_SETTINGS``
points somewhere else ("" turns it off).
"""

import os
import re
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import msgspec

DEFAULT_CONFIG_PATH = Path("config/localtc.toml")


def data_dir() -> Path:
    """Where LocalTC keeps models, voices, logs and the app's settings."""
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "LocalTC"
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "localtc"


def settings_path() -> Path | None:
    """The app's saved settings (None: turned off with ``LOCALTC_SETTINGS=""``)."""
    explicit = os.environ.get("LOCALTC_SETTINGS")
    if explicit is not None:
        return Path(explicit) if explicit else None
    return data_dir() / "settings.toml"

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
    ptt_input: str = ""  # a joystick button or key as push-to-talk through the sim, e.g. "joystick:0:button:3"


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
    # From the flight plan (SimBrief or typed in the app); shown and recorded, not yet used by ATC.
    origin: str = ""  # blank = the airport the flight starts at
    alternate: str = ""
    route: str = ""


class AtcConfig(_Section):
    enabled: bool = True
    seed: int = 0  # 0 = derived from the callsign and date
    center_name: str = "Seattle"
    center_mhz: float = 125.1
    strict_callsign: bool = False
    airport_dirs: list[str] = []  # extra folders of <ICAO>.json airport files
    phase: dict[str, float] = {}  # overrides for PhaseThresholds, e.g. taxi_start_kt = 4
    unscripted: bool = True  # traffic calls, altitude checks, "how do you read?", "stand by"


class LlmConfig(_Section):
    """The local language model (Ollama). Without it, or when it's slow, the grammar does the work."""

    enabled: bool = True
    base_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.2:3b"
    understanding: Literal["primary", "fallback", "off"] = "primary"  # primary: every transmission; fallback: only
    phrasing: bool = True  # word replies that have no template (questions, declined requests)
    timeout_s: float = 4.0  # per model call
    budget_s: float = 6.0  # per transmission, including one retry
    max_attempts: int = 2
    keep_alive: str = "1h"
    num_ctx: int = 4096
    replay: Literal["recorded", "live", "off"] = "recorded"  # model answers during a replay


class VoiceConfig(_Section):
    """Speaking to ATC: push-to-talk, the microphone, and Whisper."""

    enabled: bool = False
    ptt: Literal["keyboard", "joystick", "enter"] = "keyboard"
    ptt_key: str = "ctrl_r"  # keyboard: a key held to talk, works while the sim has focus
    ptt_joystick: str = "joystick:0:button:0"  # joystick: an input as MSFS names it
    input_device: str = ""  # blank = the system default microphone; or part of its name, or its number
    model: str = "auto"  # auto: small.en with an NVIDIA GPU, base.en otherwise
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "auto"
    models_dir: str = ""  # blank = %LOCALAPPDATA%\LocalTC\models
    beam_size: int = 5
    vocabulary: bool = True  # prompt Whisper with aviation words and this flight's names
    pre_roll_ms: int = 300  # audio kept from just before the key went down
    tail_ms: int = 250  # audio kept after it came up


class TtsConfig(_Section):
    """ATC's voice: Piper speech, through a radio effect, out of the speakers or headset."""

    enabled: bool = True
    voice: str = "en_US-libritts_r-medium"  # a Piper voice; multi-speaker voices give each controller its own
    voices_dir: str = ""  # blank = %LOCALAPPDATA%\LocalTC\voices
    output_device: str = ""  # blank = the system default output; or part of its name, or its number
    volume: float = 0.8
    rate: float = 1.15  # speaking speed; controllers talk quickly
    radio_effect: bool = True
    static: float = 0.35  # 0-1: hiss and squelch under the voice
    atis: bool = True  # read the ATIS aloud while it's tuned
    copilot: bool = True  # the copilot's calls are spoken too (in a different voice)


class CopilotConfig(_Section):
    mode: Literal["off", "assist", "full"] = "off"  # assist: readbacks + frequency changes; full: every call
    delay_min_s: float = 2.0  # pilot reaction time before speaking
    delay_max_s: float = 4.0


class UiConfig(_Section):
    """The LocalTC app window."""

    dev_mode: bool = False  # record every flight (with audio) and show the tools for sending one in
    port: int = 0  # 0 = any free port on 127.0.0.1
    window: bool = True  # a window of its own (pywebview); false = the default browser
    simbrief_user: str = ""  # SimBrief username or pilot ID, remembered for "New flight"
    copilot: Literal["assist", "full"] = "full"  # what the app's copilot switch turns on
    map_tiles: bool = True  # map background from OpenStreetMap (needs the internet; the rest works offline)


class Config(_Section):
    source: SourceConfig = msgspec.field(default_factory=SourceConfig)
    flight: FlightConfig = msgspec.field(default_factory=FlightConfig)
    atc: AtcConfig = msgspec.field(default_factory=AtcConfig)
    llm: LlmConfig = msgspec.field(default_factory=LlmConfig)
    copilot: CopilotConfig = msgspec.field(default_factory=CopilotConfig)
    voice: VoiceConfig = msgspec.field(default_factory=VoiceConfig)
    tts: TtsConfig = msgspec.field(default_factory=TtsConfig)
    live: LiveConfig = msgspec.field(default_factory=LiveConfig)
    replay: ReplayConfig = msgspec.field(default_factory=ReplayConfig)
    recorder: RecorderConfig = msgspec.field(default_factory=RecorderConfig)
    ui: UiConfig = msgspec.field(default_factory=UiConfig)


class ConfigError(ValueError):
    pass


def with_recorded(cfg: Config, recorded: dict) -> Config:
    """``cfg`` with the flight and ATC settings a recording was made with (its header ``config``), so replaying it
    reproduces the same decisions: the same squawk, runways and phrasing."""
    data = msgspec.to_builtins(cfg)
    for section in ("flight", "atc"):
        if isinstance(recorded.get(section), dict):
            data[section] = {**data[section], **recorded[section]}
    try:
        return msgspec.convert(data, Config)
    except msgspec.ValidationError:
        return cfg  # a recording from an older version with settings that no longer exist


def load_config(path: str | Path | None = None, env: Mapping[str, str] = os.environ, *,
                settings: Path | None | Literal["default"] = "default") -> Config:
    """Load config from ``path``, ``$LOCALTC_CONFIG`` or the default file; missing default = defaults.
    Then the app's saved settings on top (``settings``: a file, None for none, or the default one).

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
    overlay_path = settings_path() if settings == "default" else settings
    if overlay_path is not None and overlay_path.is_file():
        try:
            data = merge(data, tomllib.loads(overlay_path.read_text(encoding="utf-8")))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"{overlay_path}: {exc} (delete it to go back to config/localtc.toml)") from exc

    try:
        cfg = msgspec.convert(data, Config)
    except msgspec.ValidationError as exc:
        raise ConfigError(f"{config_path}: {exc}") from exc

    if kind := env.get("LOCALTC_SOURCE"):
        if kind not in ("live", "replay"):
            raise ConfigError(f"LOCALTC_SOURCE must be 'live' or 'replay', got {kind!r}")
        cfg.source.kind = kind
    return cfg


# --- the app's saved settings --------------------------------------------------------------------------------


def merge(base: dict, over: Mapping) -> dict:
    """``base`` with ``over`` on top, section by section."""
    out = dict(base)
    for key, value in over.items():
        out[key] = merge(out[key], value) if isinstance(value, Mapping) and isinstance(out.get(key), dict) else value
    return out


def diff(base: Any, changed: Any) -> Any:
    """What ``changed`` sets differently from ``base`` (both plain dicts): just the changes, by section."""
    if isinstance(base, dict) and isinstance(changed, dict):
        out = {}
        for key, value in changed.items():
            d = diff(base.get(key), value) if key in base else value
            if d is not None and d != {}:
                out[key] = d
        return out
    return None if base == changed else changed


def save_settings(cfg: Config, *, base: Config | None = None, path: Path | None = None) -> Path:
    """Save what ``cfg`` changes relative to the config file (``base``, default: loaded without settings)."""
    path = path or settings_path() or data_dir() / "settings.toml"
    base = base if base is not None else load_config(settings=None)
    changes = diff(msgspec.to_builtins(base), msgspec.to_builtins(cfg)) or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("# Saved by the LocalTC app: only the settings changed from config/localtc.toml.\n"
                   "# Delete this file to go back to that file's settings.\n\n" + dump_toml(changes), encoding="utf-8")
    tmp.replace(path)
    return path


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def dump_toml(data: Mapping, _prefix: str = "") -> str:
    """TOML for plain settings: strings, numbers, booleans, lists of those, and nested sections."""
    plain = [(k, v) for k, v in data.items() if not isinstance(v, Mapping) and v is not None]
    tables = [(k, v) for k, v in data.items() if isinstance(v, Mapping)]
    lines = [f"{_key(k)} = {_value(v)}" for k, v in plain]
    out = "\n".join(lines) + ("\n" if lines else "")
    for key, table in tables:
        name = f"{_prefix}.{_key(key)}" if _prefix else _key(key)
        body = dump_toml(table, name)
        if body.strip():
            out += f"\n[{name}]\n" + body
    return out


def _key(key: str) -> str:
    return key if _BARE_KEY.match(key) else _value(key)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    raise TypeError(f"can't write {value!r} as a setting")
