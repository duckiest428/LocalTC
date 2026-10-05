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
    intercom_input: str = ""  # ... and as the intercom key (talking to the copilot)


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


class LogbookConfig(_Section):
    """The logbook: a line per live flight, kept on this computer (see localtc.logbook)."""

    enabled: bool = True


class AccountConfig(_Section):
    """The optional account (see localtc.account). Nothing is sent anywhere until the pilot signs in."""

    api_url: str = "https://api.localtc.tech"
    dashboard_url: str = "https://localtc.tech/dashboard.html"
    sync: bool = True  # upload new logbook lines after each flight, when signed in
    companion: bool = True  # the flight's status for the companion app, when signed in
    companion_lan: bool = True  # a phone on the same network connects to this PC directly (port below)
    companion_port: int = 47800
    # Away from this network, the phone's map and radio log come through the server: position, traffic and
    # the radio text, only while a phone watches, held in memory there and never stored.
    companion_remote_map: bool = True
    # After each flight, also upload its replay (the track and the radio transcript, never audio) so it plays on
    # localtc.tech and the phone. Off: only the flights the pilot uploads from the Logbook.
    upload_replays: bool = False


class RouteFix(msgspec.Struct, frozen=True, kw_only=True):
    """A fix of the filed route, as ATC needs it (see atc_core.route)."""

    ident: str
    lat: float
    lon: float
    alt_ft: int = 0
    stage: str = ""  # CLB, CRZ, DSC
    time_s: int = 0  # planned seconds from takeoff
    via: str = ""  # the airway, SID or STAR it's reached by ("GIIBS4"); "" in plans saved before it was kept


class FlightConfig(_Section):
    rules: Literal["IFR", "VFR"] = "IFR"
    destination: str = ""  # ICAO
    cruise_ft: int = 0  # 0 = unknown
    callsign: str = ""  # override the sim's ATC ID, e.g. "N172LT" or "ASA123"
    # From the flight plan (SimBrief or typed in the app): recorded with the flight, and used by ATC.
    origin: str = ""  # blank = the airport the flight starts at
    alternate: str = ""
    route: str = ""
    sid: str = ""  # named in the IFR clearance ("via the MONTN2 departure, then as filed")
    star: str = ""
    dep_runway: str = ""  # the plan's runways (SimBrief's plan_rwy); ATC gives them only with [atc] enforce_fpln_runways
    arr_runway: str = ""
    approach: Literal["auto", "visual", "ils", "rnav"] = "auto"  # auto: what the airport has, the weather and the aircraft allow
    fixes: list[RouteFix] = []  # the plan's route; set from the flight plan for each flight, not saved


class AtcConfig(_Section):
    enabled: bool = True
    seed: int = 0  # 0 = derived from the callsign and date
    center_name: str = "Seattle"
    center_mhz: float = 125.1
    strict_callsign: bool = False
    radio_range: bool = True  # an airport's frequencies reach only so far: tower 20-60 nm, ground 6 nm (by altitude)
    chatter: bool = True  # other flights heard on the frequency now and then (no traffic behind them, nothing to answer)
    # Runways: off, ATC gives the runway in use (the ATIS's, else the best for the wind), whatever the plan says;
    # on, the flight plan's departure and arrival runways (SimBrief or typed in), where the airport has them.
    enforce_fpln_runways: bool = False
    # The runway in use goes the way the sim's own traffic is taking off and landing (when the wind allows), so the
    # flight isn't sent head-on into it; off: by the wind alone.
    traffic_runways: bool = True
    real_gates: bool = True  # the airports' real gate names and international gates, from OpenStreetMap
    callsign_check: bool = True  # another aircraft's callsign heard (or one digit off on a new call): "say again your callsign"
    # FAA or ICAO wording: "auto" by where the controller is (US and Canada FAA, elsewhere ICAO), or always one.
    phraseology: Literal["auto", "faa", "icao"] = "auto"
    transition_ft: int = 0  # 0 = the region's (18,000 ft in North America, 3,000-18,500 elsewhere); or this everywhere
    airport_dirs: list[str] = []  # extra folders of <ICAO>.json airport files
    phase: dict[str, float] = {}  # overrides for PhaseThresholds, e.g. taxi_start_kt = 4
    unscripted: bool = True  # traffic calls, altitude checks, "how do you read?"
    # Notices on each airport's ATIS (a taxiway closed, an ILS out, bird activity ...), a few per session, and ATC
    # works to them: a closed runway isn't used, an ILS out isn't an approach.
    notams: bool = True


LlmMode = Literal["scripted", "semi", "mostly_llm", "llm", "off"]
# The setting before the modes: "primary" was the model reading every call, "fallback" the grammar first.
LEGACY_LLM_MODES = {"primary": "llm", "fallback": "semi", "off": "off"}


class LlmConfig(_Section):
    """The local language model (Ollama). Without it, or when it's slow, the grammar does the work."""

    enabled: bool = True
    base_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.2:3b"
    # How much ATC leans on the model rather than its script, for reading calls and for wording replies (see
    # atc_core.llm.understand.MODES): scripted, semi, mostly_llm, llm, or off (the grammar and templates alone).
    mode: LlmMode = "mostly_llm"
    phrasing: bool = True  # the model may word replies at all (off: templates only, whatever the mode)
    # The model's answers may go past what the sim's data says (a number, a closure, a runway it isn't told about):
    # off, a reply saying anything the facts don't is turned away. Instructions are never allowed either way.
    beyond_facts: bool = False
    timeout_s: float = 4.0  # per model call (Quick Settings → ATC → Language model timing)
    budget_s: float = 6.0  # per transmission, including one retry (at least timeout_s)
    # A question or anything off the script that the model couldn't answer in time: the app shows the controller
    # thinking and the model gets this long, once. The sim shares the machine, and a model that answers in 2 s idle
    # can take 8 in flight.
    patience_s: float = 15.0
    max_attempts: int = 2
    # How long the model stays loaded after a flight ("0": unload at once). During one, LocalTC keeps it loaded:
    # a model that has to load mid-flight misses the call.
    keep_alive: str = "1h"
    num_ctx: int = 3072  # a call needs up to about 2,100 tokens (tests/test_llm.py holds it to that); more is memory for nothing
    # Run the model on the CPU only: the graphics card is left to the sim. Answers take a little longer, so the
    # timeouts above are doubled. Applies when the model loads (LocalTC reloads it at the next flight).
    cpu_only: bool = True  # the graphics card and its memory belong to the sim: the model takes the CPU
    cpu_threads: int = 0  # CPU threads for the model; 0: Ollama's choice (every physical core)
    replay: Literal["recorded", "live", "off"] = "recorded"  # model answers during a replay


class VoiceConfig(_Section):
    """Speaking to ATC: push-to-talk, the microphone, and Whisper."""

    enabled: bool = False
    ptt: Literal["keyboard", "joystick", "enter"] = "keyboard"
    ptt_key: str = "ctrl_r"  # keyboard: a key held to talk, works while the sim has focus
    ptt_joystick: str = "joystick:0:button:0"  # joystick: an input as MSFS names it
    # The intercom: a second key (or button) held to talk to the copilot instead of ATC. Blank: none.
    intercom_key: str = "alt_r"  # keyboard
    intercom_joystick: str = ""  # joystick, as MSFS names it
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


class CrewConfig(_Section):
    """The copilot as Pilot Monitoring, on the intercom: takes spoken commands and works the aircraft."""

    enabled: bool = True  # with voice input on: the intercom key talks to the copilot
    voice_sex: Literal["female", "male", "any"] = "any"  # the copilot's voice
    voice_pick: int = 0  # which of that sex's voices (0-7); the copilot uses it on the radio too
    # The language model on the intercom (needs [llm]): "off" the fixed commands and common questions only;
    # "questions" also answers anything else from what it knows; "full" also takes commands said in other words
    # (read back for your "confirm" before it acts).
    llm: Literal["off", "questions", "full"] = "full"


class SessionConfig(_Section):
    """When a flight ends by itself."""

    # Parked at a gate or stand at the destination after landing (stopped, taxi done): the flight is stopped, as the
    # Stop button would.
    auto_stop_at_gate: bool = True
    auto_stop_in_replay: bool = False  # ... in a replay too (off: a replay plays to its end)
    gate_radius_m: float = 30.0  # this close to a gate or parking spot counts as at it
    auto_stop_delay_s: float = 8.0  # time for ATC's last words before the flight stops


class UiConfig(_Section):
    """The LocalTC app window."""

    dev_mode: bool = False  # record every flight (with audio) and show the tools for sending one in
    port: int = 0  # 0 = any free port on 127.0.0.1
    window: bool = True  # a window of its own (pywebview); false = the default browser
    simbrief_user: str = ""  # SimBrief username or pilot ID, remembered for "New flight"
    copilot: Literal["assist", "full"] = "full"  # what the app's copilot switch turns on
    # What the app connects to. Its own setting, never [source] kind: that is the command line's development
    # default (a recording), and the app must always fly the sim unless the pilot asks for a replay here.
    source: SourceKind = "live"
    map_tiles: bool = True  # map background from OpenStreetMap (needs the internet; the rest works offline)
    # Which airports Airport Lookup starts with; the page writes back whatever the filters are set to.
    lookup_kinds: list[str] = msgspec.field(default_factory=lambda: ["international"])
    # New versions from GitHub Releases: "notify" says so and installs when asked, "auto" downloads and
    # installs when LocalTC closes, "off" never asks GitHub.
    updates: Literal["notify", "auto", "off"] = "notify"
    coffee_clicked: bool = False  # the Buy me a coffee button hides for good once it's been clicked
    # Keep the window above the others, the sim's included: "off", "flying" (while connected to the sim), "always".
    on_top: Literal["off", "flying", "always"] = "off"


class Config(_Section):
    source: SourceConfig = msgspec.field(default_factory=SourceConfig)
    flight: FlightConfig = msgspec.field(default_factory=FlightConfig)
    atc: AtcConfig = msgspec.field(default_factory=AtcConfig)
    llm: LlmConfig = msgspec.field(default_factory=LlmConfig)
    session: SessionConfig = msgspec.field(default_factory=SessionConfig)
    copilot: CopilotConfig = msgspec.field(default_factory=CopilotConfig)
    crew: CrewConfig = msgspec.field(default_factory=CrewConfig)
    voice: VoiceConfig = msgspec.field(default_factory=VoiceConfig)
    tts: TtsConfig = msgspec.field(default_factory=TtsConfig)
    live: LiveConfig = msgspec.field(default_factory=LiveConfig)
    replay: ReplayConfig = msgspec.field(default_factory=ReplayConfig)
    recorder: RecorderConfig = msgspec.field(default_factory=RecorderConfig)
    logbook: LogbookConfig = msgspec.field(default_factory=LogbookConfig)
    account: AccountConfig = msgspec.field(default_factory=AccountConfig)
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
    if isinstance(recorded.get("atc"), dict) and "notams" not in recorded["atc"]:
        data["atc"]["notams"] = False  # recorded before ATIS notices: replayed as it was flown
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
            data = _migrate(tomllib.load(fh))
    elif explicit:
        raise ConfigError(f"config file not found: {config_path}")
    overlay_path = settings_path() if settings == "default" else settings
    if overlay_path is not None and overlay_path.is_file():
        try:
            data = merge(data, _migrate(tomllib.loads(overlay_path.read_text(encoding="utf-8"))))
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


def _migrate(data: dict) -> dict:
    """One file's settings from an older version, in today's names: ``[llm] understanding`` is ``[llm] mode`` (the
    app's next save writes it so). Each file on its own, so a saved choice still wins over the config file's."""
    llm = data.get("llm")
    if isinstance(llm, dict) and "understanding" in llm:
        llm = dict(llm)
        old = llm.pop("understanding")
        llm.setdefault("mode", LEGACY_LLM_MODES.get(old, old))
        data = {**data, "llm": llm}
    return data


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
