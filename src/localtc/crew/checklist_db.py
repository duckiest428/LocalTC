"""The checklists the copilot reads, from real sources only: the aircraft's own (in its package on this PC), else the
manufacturer's normal checklist for its family (``checklist_data``). An aircraft with neither has none: the copilot
says so and never makes one up.

The aircraft's own come in three forms, all read here:

- **The sim's checklists** (``SimObjects/Airplanes/<aircraft>/Checklist/*.xml``): the sim's own checklist format, its
  checkpoints from the aircraft's ``Library.xml`` (what each item checks, by the sim's variables, and the copilot's
  actions the author gave it).
- **FlyByWire's EFB checklists** (``checklists.json5``: the A380X, Headwind's A339X), with their conditions.
- **Synaptic's** (``checklists.json``: the A220).

Each line keeps where it's from (``ChecklistSet.source``); what the copilot can see of an item comes from its own
reading of the aircraft (``sense``) or the line's condition.
"""

import json
import logging
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

BUNDLED = Path(__file__).with_name("checklist_data")
# The phases a checklist is due in, in flight order (the copilot offers each once there).
PHASES = ("cockpit_prep", "before_start", "after_start", "taxi", "line_up", "after_takeoff", "climb", "cruise",
          "descent", "approach", "landing", "after_landing", "parking", "securing")
PHASE_NAMES = {p: p.replace("_", " ").capitalize() for p in PHASES}
# FlyByWire's flight phases (their EFB's) to these.
FBW_PHASES = {0: "cockpit_prep", 1: "before_start", 2: "taxi", 3: "line_up", 6: "after_takeoff", 7: "after_takeoff",
              8: "approach", 9: "landing", 10: "after_landing", 11: "after_landing", 12: "parking"}
# The sim's checklist steps to these.
MSFS_STEPS = {"PREFLIGHT_GATE": "cockpit_prep", "PREFLIGHT_PUSHBACK": "before_start", "PREFLIGHT_TAXI": "taxi",
              "PREFLIGHT_TAXI_OUT": "taxi", "PREFLIGHT_HOLDING": "line_up", "FLIGHT_RUNWAY": "line_up",
              "FLIGHT_TAKEOFF": "after_takeoff", "FLIGHT_CLIMB": "after_takeoff", "FLIGHT_CRUISE": "cruise",
              "FLIGHT_DESCENT": "descent", "FLIGHT_APPROACH": "approach", "LANDING_APPROACH": "approach",
              "FLIGHT_LANDING": "landing", "LANDING_TOUCHDOWN": "landing", "LANDING_GROUNDROLL": "landing",
              "POSTFLIGHT_TAXI": "after_landing", "LANDING_TAXI_IN": "after_landing", "LANDING_GATE": "parking",
              "POSTFLIGHT_PARKING": "parking"}


@dataclass(frozen=True)
class Line:
    """One item: its challenge and response ("PARKING BRAKE", "SET"); a condition the aircraft shows it by, when its
    author gave one ((variable, comparison, value): "L:A32NX_PARK_BRAKE_LEVER_POS", "==", 1); the copilot's action
    for it, when its author gave one (a key event and its value)."""

    challenge: str
    response: str = ""
    condition: tuple[str, str, float] | None = None
    action: tuple[str, int] | None = None
    note: bool = False  # a line to read, nothing to answer


@dataclass(frozen=True)
class Checklist:
    id: str
    name: str
    phase: str
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class ChecklistSet:
    name: str  # "Airbus A320 family", "FlyByWire A380X"
    source: str  # where it's from, said to the pilot and shown
    origin: str  # "aircraft" (its own package) or "manufacturer" (LocalTC's copy of the type's normal checklist)
    checklists: tuple[Checklist, ...]
    match: tuple[str, ...] = ()

    def get(self, key: str) -> Checklist | None:
        """By its id, its name or its phase ("before takeoff", "line_up")."""
        k = re.sub(r"[\s-]+", "_", key.strip().lower())
        return next((c for c in self.checklists if k in (c.id, c.phase, re.sub(r"[\s-]+", "_", c.name.lower()))), None)

    def due(self, phase: str) -> list[Checklist]:
        return [c for c in self.checklists if c.phase == phase]

    def variables(self) -> tuple[str, ...]:
        """The aircraft's variables its conditions read (to watch while it's loaded)."""
        names = [ln.condition[0] for c in self.checklists for ln in c.lines if ln.condition]
        return tuple(dict.fromkeys(n for n in names if n.startswith("L:")))

    def to_json(self) -> dict:
        return {"name": self.name, "source": self.source, "origin": self.origin,
                "checklists": [{"id": c.id, "name": c.name, "phase": c.phase,
                                "lines": [{"challenge": ln.challenge, "response": ln.response, "note": ln.note,
                                           "sensed": ln.condition is not None or sense_key(ln.challenge) is not None}
                                          for ln in c.lines]} for c in self.checklists]}


# --- reading the formats ---------------------------------------------------------------------------------------------


def json5(text: str):
    """JSON5 as FlyByWire writes it (comments, trailing commas, keys without quotes, single quotes), as JSON."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":  # a string, written out with double quotes
            quote, j, body = ch, i + 1, []
            while j < n and text[j] != quote:
                if text[j] == "\\" and j + 1 < n:
                    body.append(text[j:j + 2])
                    j += 2
                    continue
                body.append('\\"' if text[j] == '"' else text[j])
                j += 1
            out.append('"' + "".join(body) + '"')
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif ch.isalpha() or ch == "_":  # a bare word: a key wants quotes, true/false/null don't
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            k = j
            while k < n and text[k] in " \t\r\n":
                k += 1
            out.append(f'"{word}"' if k < n and text[k] == ":" and word not in ("true", "false", "null") else word)
            i = j
        else:
            out.append(ch)
            i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    return json.loads(cleaned, strict=False)


COMPS = {"EQ": "==", "NE": "!=", "LT": "<", "LE": "<=", "GE": ">=", "GT": ">", None: "=="}


def from_fbw(path: Path, name: str) -> ChecklistSet | None:
    data = json5(path.read_text(encoding="utf-8"))
    lists = []
    for i, c in enumerate(data.get("checklists") or []):
        title = str(c.get("name") or "").strip()
        if not title or title.startswith("<<"):
            continue  # "<< DEPARTURE CHANGE >>": a sub-checklist for when it's needed, not one of the flow
        lines = []
        for item in c.get("items") or []:
            kind = str(item.get("type") or "ITEM").upper()
            if kind == "LINE":
                continue
            cond = None
            for cnd in item.get("condition") or []:
                var = str(cnd.get("varName") or "")
                var = var[2:] if var.startswith("A:") else var
                if var:
                    cond = (var, COMPS.get(str(cnd.get("comp") or "").upper() or None, "=="), float(cnd.get("result", 1)))
                    break
            lines.append(Line(str(item.get("item") or "").strip(), str(item.get("result") or "").strip(), cond,
                              note=kind == "SUBLISTHEADER"))
        phase = FBW_PHASES.get(int(c.get("flightphase") or 0), _phase_of(title))
        lists.append(Checklist(_slug(title) or f"list{i}", title.capitalize(), _phase_of(title) or phase, tuple(lines)))
    return ChecklistSet(name, f"{name}'s own checklists ({path.name})", "aircraft", tuple(lists)) if lists else None


def from_synaptic(path: Path, name: str) -> ChecklistSet | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    lists = []
    for c in data.get("normal") or []:
        title = str(c.get("name") or "").strip()
        lines = tuple(Line(str(i.get("challenge") or "").strip(), str(i.get("response") or "").strip())
                      for i in c.get("items") or [] if i.get("challenge"))
        if title and lines:
            lists.append(Checklist(_slug(title), title, _phase_of(title) or "cockpit_prep", lines))
    return ChecklistSet(name, f"{name}'s own checklists ({data.get('name') or path.name})", "aircraft", tuple(lists)) \
        if lists else None


def from_msfs(folder: Path, name: str) -> ChecklistSet | None:
    """The sim's own checklist format: steps of pages of checkpoints, each from the aircraft's Library.xml (or the
    sim's default library, which isn't readable here: its name is turned into words)."""
    library: dict[str, Line] = {}
    lib = folder / "Library.xml"
    if lib.exists():
        for cp in ET.fromstring(lib.read_text(encoding="utf-8-sig")).iter("Checkpoint"):
            cid = cp.get("Id") or ""
            desc = cp.find("CheckpointDesc")
            challenge = _tt(desc.get("SubjectTT")) if desc is not None else ""
            response = _tt(desc.get("ExpectationTT")) if desc is not None else ""
            cond = action = None
            val = cp.find(".//TestValue/Val")
            if val is not None and val.get("SimVarName"):
                cond = (val.get("SimVarName"), "==", 0.0 if re.search(r"\bOFF\b", response.upper()) else 1.0)
            act = cp.find(".//Action[@Copilot='True']")
            if act is not None and act.get("EventID"):
                action = (act.get("EventID"), int(float(act.get("EventParam") or 0)))
            library[cid] = Line(challenge or _words(cid)[0], response or _words(cid)[1], cond, action)
    lists = []
    for xml in sorted(folder.glob("*.xml")):
        if xml.name == "Library.xml":
            continue
        try:
            root = ET.fromstring(xml.read_text(encoding="utf-8-sig"))
        except ET.ParseError:
            continue
        for step in root.iter("Step"):
            step_phase = MSFS_STEPS.get(step.get("ChecklistStepId") or "", "cockpit_prep")
            for page in step.iter("Page"):
                title = _tt(page.get("SubjectTT")) or step_phase.replace("_", " ")
                phase = _phase_of(title) or step_phase
                lines = tuple(library.get(ref) or Line(*_words(ref)) for cp in page.iter("Checkpoint")
                              if (ref := cp.get("ReferenceId")))
                if lines:
                    lists.append(Checklist(_slug(title), title.capitalize(), phase, lines))
    return ChecklistSet(name, f"{name}'s own checklists (the sim's checklist)", "aircraft", tuple(lists)) if lists else None


def bundled(folder: Path = BUNDLED) -> list[ChecklistSet]:
    out = []
    for path in sorted(folder.glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        s = data.get("set", {})
        lists = tuple(Checklist(c["id"], c["name"], c["phase"],
                                tuple(Line(a.lstrip("- ").strip(), b) for a, b in c["items"])) for c in data.get("list", []))
        out.append(ChecklistSet(s["name"], s["source"], "manufacturer", lists, tuple(s.get("match", ()))))
    return out


# --- finding an aircraft's -------------------------------------------------------------------------------------------


def package_roots() -> list[Path]:
    """Where MSFS 2024 (and 2020) keeps the packages on this PC: the InstalledPackagesPath of its UserCfg.opt."""
    import os

    roots = []
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    appdata = Path(os.environ.get("APPDATA", ""))
    for cfg in (local / "Packages/Microsoft.Limitless_8wekyb3d8bbwe/LocalCache/UserCfg.opt",
                appdata / "Microsoft Flight Simulator 2024/UserCfg.opt",
                local / "Packages/Microsoft.FlightSimulator_8wekyb3d8bbwe/LocalCache/UserCfg.opt",
                appdata / "Microsoft Flight Simulator/UserCfg.opt"):
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if m := re.search(r'InstalledPackagesPath\s+"([^"]+)"', text):
            roots.append(Path(m.group(1)))
    return roots


@dataclass
class Library:
    """Every checklist on this PC: the installed aircraft's own (found once, by the titles in their aircraft.cfg) and
    the manufacturers'."""

    roots: list[Path] = field(default_factory=list)
    _own: list[tuple[tuple[str, ...], tuple[str, ...], Path, str]] | None = None  # (titles, types, where, format)
    _bundled: list[ChecklistSet] | None = None

    def _scan(self) -> list[tuple[tuple[str, ...], tuple[str, ...], Path, str]]:
        if self._own is not None:
            return self._own
        found = []
        for root in self.roots:
            for community in ("Community", "Community2024"):
                base = root / community
                if not base.is_dir():
                    continue
                for package in base.iterdir():
                    if not package.is_dir():
                        continue
                    try:
                        found += _package_checklists(package)
                    except OSError as exc:
                        log.info("Checklists in %s not read: %s", package.name, exc)
        self._own = found
        log.info("Checklists: %d aircraft on this PC have their own", len(found))
        return found

    def for_aircraft(self, title: str, model: str = "") -> ChecklistSet | None:
        """The aircraft's own checklists if its package has them, else its family's normal checklist; None: none."""
        words = f"{title} {model}".upper()
        # The surest first; of a package's, its EFB's (FlyByWire's, Synaptic's: their conditions) before the sim's.
        rank = {"fbw": 0, "synaptic": 1, "msfs": 2}
        ranked = sorted(((match_score(titles, types, title, model), i, path, kind)
                         for i, (titles, types, path, kind) in enumerate(self._scan())),
                        key=lambda x: (-x[0], rank[x[3]], x[1]))
        for score, i, path, kind in ranked:
            if score <= 0:
                break
            name = next((t for t in self._scan()[i][0] if not t.startswith("_")), path.name)
            try:
                got = {"fbw": from_fbw, "synaptic": from_synaptic, "msfs": from_msfs}[kind](path, name)
            except (OSError, ValueError, KeyError, ET.ParseError) as exc:
                log.info("Checklists at %s unreadable: %s", path, exc)
                got = None
            if got is not None:
                return got
        if self._bundled is None:
            self._bundled = bundled()
        best = max(self._bundled, key=lambda s: max((len(m) for m in s.match if m.upper() in words), default=0),
                   default=None)
        if best is not None and any(m.upper() in words for m in best.match):
            return best
        return None


def _package_checklists(package: Path) -> list[tuple[tuple[str, ...], tuple[str, ...], Path, str]]:
    """A package's checklists: (its aircraft's titles, their ICAO types, the file or folder, its format)."""
    out = []
    planes = package / "SimObjects" / "Airplanes"
    titles_of: dict[Path, tuple[str, ...]] = {}
    types: list[str] = []
    if planes.is_dir():
        for plane in planes.iterdir():
            cfg = plane / "aircraft.cfg"
            titles: tuple[str, ...] = ()
            if cfg.exists():
                text = cfg.read_text(encoding="utf-8", errors="replace")
                titles = tuple(dict.fromkeys(t.strip() for t in re.findall(r'^\s*title\s*=\s*"?([^"\n;]+)', text,
                                                                            re.M | re.I)))
                types += re.findall(r'^\s*icao_type_designator\s*=\s*"?([A-Z0-9]{2,4})', text, re.M | re.I)
            titles_of[plane] = titles + (plane.name,)
            if (plane / "Checklist").is_dir() and any((plane / "Checklist").glob("*.xml")):
                out.append((titles_of[plane], tuple(dict.fromkeys(types)), plane / "Checklist", "msfs"))
    every = tuple(t for ts in titles_of.values() for t in ts) or (package.name,)
    kinds = tuple(dict.fromkeys(types))
    for f in package.glob("config/**/checklists.json5"):
        out.append((every, kinds, f, "fbw"))
    for f in package.glob("Config/Default/checklists.json"):
        out.append((every + (package.name, "A220"), kinds or ("BCS1", "BCS3"), f, "synaptic"))
    return out


def match_score(titles: tuple[str, ...], types: tuple[str, ...], title: str, model: str) -> int:
    """How surely a package's checklists are this aircraft's: its exact title 3; a distinctive word of its titles
    ("A380X", "787-10") in this title 2; the same ICAO type 1; 0 not this aircraft."""
    from localtc.atc_core.airport.aircraft_size import designator

    t = title.strip().upper()
    if any(x.strip().upper() == t for x in titles):
        return 3
    words = {w for x in titles for w in re.split(r"[\s_()]+", x.upper()) if len(w) >= 4 and re.search(r"\d", w)}
    if any(w in t for w in words):
        return 2
    mine = {m for m in (model.strip().upper(), designator(title), designator(model)) if m}
    return 1 if mine & {k.upper() for k in types} else 0


# --- what the copilot can see of an item -----------------------------------------------------------------------------

# The aircraft's state the copilot reads itself, by what an item's challenge names.
SENSORS = (
    (r"\bSEAT ?BELTS?\b|\bSIGNS\b|\bPASSENGER SIGNS\b", "seatbelt"),
    (r"\bBEACON\b|\bANTI[ -]?COLL", "beacon"),
    (r"\bPARK(?:ING)? BR(?:AKE|K)\b|\bPARK BRK\b", "parking_brake"),
    (r"\bFLAP(?:S| SETTING)\b|\bFLAPS\b", "flaps"),
    (r"\b(?:LDG |LANDING )?GEAR\b", "gear"),
    (r"\bSPEED ?BRAKE\b|\b(?:GND )?SPLRS?\b|\bSPOILERS?\b", "spoilers"),
    (r"\bAUTO ?BR(?:AKE|K)\b", "autobrake"),
    (r"\bTCAS\b|\bTRANSPONDER\b|\bXPDR\b", "transponder"),
    (r"\bBARO REF\b|\bALTIMETERS?\b", "altimeter"),
    (r"\bSTROBE", "strobe"),
    (r"\bFUEL(?: QTY| QUANTITY)?$|\bFUEL QUANTITY\b|^FUEL$", "fuel"),
)


def sense_key(challenge: str) -> str | None:
    up = challenge.upper()
    for pattern, key in SENSORS:
        if re.search(pattern, up):
            return key
    return None


def fill(text: str, values: dict[str, str]) -> str:
    """A response's {placeholders} from the flight ("CONF {flaps}" -> "CONF 1+F"), "___" for one not known."""
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1)) or "___", text)


def _tt(key: str | None) -> str:
    """ "TT:GAME.CHECKLIST_PARKING_BRAKE" -> "PARKING BRAKE" (the sim's words aren't readable here)."""
    if not key:
        return ""
    k = re.sub(r"^TT:", "", key.strip(), flags=re.I)
    k = k.split(".")[-1] if re.fullmatch(r"[A-Z0-9_.]+", k.upper()) and "." in k else k
    k = re.sub(r"^(?:CHECKLIST|GAME|CLUE|FEEDBACK)_", "", k.upper())
    return k.replace("_", " ").strip().upper()


def _words(reference: str) -> tuple[str, str]:
    """A checkpoint's id as words: "Battery_Switch_On_B78X" -> ("BATTERY SWITCH", "ON")."""
    parts = [p for p in re.split(r"_+", reference) if p and not re.fullmatch(r"[A-Z]\d{2}[A-Z]|B78X|A[0-9]{3}N?", p.upper())]
    states = ("ON", "OFF", "ARMED", "ARM", "SET", "AUTO", "CLOSED", "OPEN", "IDLE", "CUTOFF", "NORM", "ENGAGED",
              "DISENGAGED", "RETRACTED", "EXTENDED", "UP", "DOWN", "CHECKED", "GUARD", "STANDBY")
    response = []
    while parts and parts[-1].upper() in states:
        response.insert(0, parts.pop().upper())
    return " ".join(parts).upper(), " ".join(response) or "CHECKED"


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")


def _phase_of(title: str) -> str:
    t = title.upper().replace("TAKE OFF", "TAKEOFF")
    for words, phase in (("COCKPIT PREP", "cockpit_prep"), ("BEFORE START", "before_start"), ("AFTER START", "after_start"),
                         ("BEFORE TAXI", "taxi"), ("TAXI", "taxi"), ("LINE", "line_up"), ("BEFORE TAKEOFF", "line_up"),
                         ("AFTER TAKEOFF", "after_takeoff"), ("CLIMB", "after_takeoff"), ("CRUISE", "cruise"),
                         ("DESCENT", "descent"), ("APPROACH", "approach"), ("LANDING", "landing"),
                         ("AFTER LANDING", "after_landing"), ("PARKING", "parking"), ("SHUTDOWN", "parking"),
                         ("SECUR", "securing"), ("POWER", "cockpit_prep"), ("PREFLIGHT", "cockpit_prep")):
        if words in t:
            if words == "LANDING" and "AFTER" in t:
                return "after_landing"
            if words == "TAXI" and "AFTER" in t:
                continue
            return phase
    return ""
