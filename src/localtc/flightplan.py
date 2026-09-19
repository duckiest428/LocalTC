"""Flight plans: imported from SimBrief or typed in, and what ATC takes from them.

SimBrief's API (https://developers.navigraph.com/docs/simbrief/fetching-ofp-data) returns the
user's most recent flight plan, "OFP", for a username or pilot ID:
``https://www.simbrief.com/api/xml.fetcher.php?username=<name>&json=1`` (or ``userid=<id>``).
It answers HTTP 400 with ``{"fetch": {"status": "Error: ..."}}`` for an unknown user. Every value
in the JSON is a string. The fields used here:

- ``origin/destination/alternate.icao_code``, ``.name``, ``.plan_rwy``
- ``general.initial_altitude`` (feet), ``general.route``, ``general.icao_airline``, ``general.flight_number``
- ``atc.callsign`` (what ATC calls the flight, e.g. ``DAL123``)
- ``aircraft.icaocode``, ``aircraft.reg``
- ``navlog.fix[]``: ``ident``, ``type``, ``pos_lat``, ``pos_long``, ``altitude_feet``, ``via_airway``,
  ``is_sid_star``, ``stage`` (CLB/CRZ/DSC). The SID and STAR are the airways of the SID/STAR fixes.

The plan is kept in ``flightplan.json`` in the LocalTC data folder. ATC uses its callsign,
destination and cruise altitude today. The route, SID, STAR and fixes are shown and drawn on the map.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Literal

import msgspec

SIMBRIEF_URL = "https://www.simbrief.com/api/xml.fetcher.php"
SIMBRIEF_DISPATCH = "https://dispatch.simbrief.com/options/custom"  # where a new plan is made
ICAO = re.compile(r"^[A-Z0-9]{3,4}$")
CALLSIGN = re.compile(r"^[A-Z0-9]{2,8}$")


class Fix(msgspec.Struct, frozen=True, kw_only=True):
    ident: str
    lat: float
    lon: float
    alt_ft: int = 0
    via: str = ""  # airway, SID or STAR name, or DCT
    kind: str = ""  # wpt, vor, ndb, apt, ...
    stage: str = ""  # CLB, CRZ, DSC


class FlightPlan(msgspec.Struct, kw_only=True):
    source: Literal["manual", "simbrief"] = "manual"
    callsign: str = ""
    origin: str = ""
    destination: str = ""
    alternate: str = ""
    cruise_ft: int = 0
    route: str = ""
    aircraft: str = ""  # ICAO type, e.g. B738
    registration: str = ""
    sid: str = ""
    star: str = ""
    dep_runway: str = ""
    arr_runway: str = ""
    origin_name: str = ""
    destination_name: str = ""
    fixes: list[Fix] = []
    simbrief_id: str = ""  # SimBrief's static id or request time: tells one plan from the next

    def summary(self) -> str:
        parts = [self.callsign or "(sim callsign)", f"{self.origin or '?'}-{self.destination or '?'}"]
        if self.cruise_ft:
            parts.append(f"FL{self.cruise_ft // 100:03d}" if self.cruise_ft >= 18000 else f"{self.cruise_ft:,} ft")
        return " ".join(parts)


class FlightPlanError(ValueError):
    pass


# --- typed in --------------------------------------------------------------------------------------------------


def parse_altitude(text: str | int) -> int:
    """ "FL350", "350" (a flight level), "35000", "3,500", "3500ft" -> feet; "" -> 0."""
    raw = str(text).strip().upper().replace(",", "").replace("FT", "").replace("'", "").strip()
    if not raw:
        return 0
    level = raw.startswith("FL")
    digits = raw[2:].strip() if level else raw
    if not digits.isdigit():
        raise FlightPlanError(f"cruise altitude {text!r}: use feet (3500) or a flight level (FL350)")
    value = int(digits)
    if level or (value < 1000 and len(digits) == 3):
        value *= 100
    if not 500 <= value <= 60000:
        raise FlightPlanError(f"cruise altitude {text!r} is out of range")
    return value


def manual_plan(*, callsign: str = "", origin: str = "", destination: str, cruise: str | int = "",
                alternate: str = "", route: str = "", aircraft: str = "") -> FlightPlan:
    """A plan typed into the app; checks it and puts it in ATC's format."""
    def icao(value: str, what: str, required: bool = False) -> str:
        value = value.strip().upper()
        if not value and not required:
            return ""
        if not ICAO.match(value):
            raise FlightPlanError(f"{what}: {value or 'missing'} (use the airport's ICAO code, e.g. KSEA)")
        return value

    sign = callsign.strip().upper().replace(" ", "").replace("-", "")
    if sign and not CALLSIGN.match(sign):
        raise FlightPlanError(f"callsign {callsign!r}: letters and digits only, e.g. N172LT or DAL123")
    return FlightPlan(source="manual", callsign=sign, origin=icao(origin, "origin"),
                      destination=icao(destination, "destination", required=True), alternate=icao(alternate, "alternate"),
                      cruise_ft=parse_altitude(cruise), route=" ".join(route.upper().split()),
                      aircraft=aircraft.strip().upper())


# --- SimBrief ----------------------------------------------------------------------------------------------------


def simbrief_url(user: str) -> str:
    """By pilot ID (all digits) or username."""
    user = user.strip()
    if not user:
        raise FlightPlanError("enter your SimBrief username or pilot ID")
    key = "userid" if user.isdigit() else "username"
    return f"{SIMBRIEF_URL}?{urllib.parse.urlencode({key: user, 'json': 1})}"


def fetch_simbrief(user: str, *, timeout_s: float = 15.0) -> FlightPlan:
    """The user's latest SimBrief plan. Raises FlightPlanError with SimBrief's own message on failure."""
    request = urllib.request.Request(simbrief_url(user), headers={"User-Agent": "LocalTC"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:  # 400: unknown user, or no plan yet
        body = exc.read()
        raise FlightPlanError(_simbrief_error(body) or f"SimBrief answered {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise FlightPlanError(f"can't reach SimBrief ({getattr(exc, 'reason', exc)}); check the internet connection")
    try:
        data = json.loads(body)
    except ValueError:
        raise FlightPlanError("SimBrief sent something that isn't a flight plan") from None
    return parse_simbrief(data)


def _simbrief_error(body: bytes) -> str:
    try:
        status = json.loads(body).get("fetch", {}).get("status", "")
    except (ValueError, AttributeError):
        match = re.search(rb"<status>(.*?)</status>", body)
        status = match.group(1).decode(errors="replace") if match else ""
    status = status.removeprefix("Error:").strip()
    return f"SimBrief: {status}" if status else ""


def parse_simbrief(data: dict[str, Any]) -> FlightPlan:
    status = str(_get(data, "fetch", "status"))
    if status and status.lower() != "success":
        raise FlightPlanError(f"SimBrief: {status.removeprefix('Error:').strip()}")
    general, atc, aircraft = data.get("general") or {}, data.get("atc") or {}, data.get("aircraft") or {}
    callsign = str(atc.get("callsign") or "").strip().upper()
    if not callsign:
        callsign = f"{general.get('icao_airline') or ''}{general.get('flight_number') or ''}".strip().upper()
    fixes = []
    for fix in _as_list(_get(data, "navlog", "fix")):
        try:
            fixes.append(Fix(ident=str(fix.get("ident", "")), lat=float(fix["pos_lat"]), lon=float(fix["pos_long"]),
                             alt_ft=_int(fix.get("altitude_feet")), via=str(fix.get("via_airway") or ""),
                             kind=str(fix.get("type") or ""), stage=str(fix.get("stage") or "")))
        except (KeyError, TypeError, ValueError):
            continue
    navlog = _as_list(_get(data, "navlog", "fix"))
    sid = next((str(f.get("via_airway")) for f in navlog if f.get("is_sid_star") == "1" and f.get("stage") == "CLB"
                and f.get("via_airway") not in (None, "", "DCT")), "")
    star = next((str(f.get("via_airway")) for f in reversed(navlog) if f.get("is_sid_star") == "1"
                 and f.get("stage") == "DSC" and f.get("via_airway") not in (None, "", "DCT")), "")
    return FlightPlan(
        source="simbrief",
        callsign=callsign,
        origin=str(_get(data, "origin", "icao_code")).upper(),
        destination=str(_get(data, "destination", "icao_code")).upper(),
        alternate=str(_get(data, "alternate", "icao_code")).upper(),
        cruise_ft=_int(general.get("initial_altitude")),
        route=" ".join(str(general.get("route") or "").split()),
        aircraft=str(aircraft.get("icaocode") or aircraft.get("icao_code") or "").upper(),
        registration=str(aircraft.get("reg") or ""),
        sid=sid, star=star,
        dep_runway=str(_get(data, "origin", "plan_rwy")),
        arr_runway=str(_get(data, "destination", "plan_rwy")),
        origin_name=str(_get(data, "origin", "name")),
        destination_name=str(_get(data, "destination", "name")),
        fixes=fixes,
        simbrief_id=str(_get(data, "params", "static_id") or _get(data, "params", "time_generated")),
    )


def _get(data: Any, *path: str) -> Any:
    for key in path:
        data = data.get(key) if isinstance(data, dict) else None
    return "" if data is None or data == {} else data  # SimBrief writes empty values as {}


def _as_list(value: Any) -> list[dict]:
    if isinstance(value, dict):
        return [value]  # one fix comes as an object, not a list
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


# --- keeping it, and handing it to ATC ---------------------------------------------------------------------------


def save_plan(plan: FlightPlan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(msgspec.json.format(msgspec.json.encode(plan), indent=1))
    tmp.replace(path)


def load_plan(path: Path) -> FlightPlan | None:
    try:
        return msgspec.json.decode(path.read_bytes(), type=FlightPlan)
    except (OSError, msgspec.DecodeError, msgspec.ValidationError):
        return None


def apply_plan(plan: FlightPlan, flight: Any) -> None:
    """Put the plan into a ``FlightConfig``: what ATC clears the flight with."""
    flight.callsign = plan.callsign
    flight.origin = plan.origin
    flight.destination = plan.destination
    flight.alternate = plan.alternate
    flight.cruise_ft = plan.cruise_ft
    flight.route = plan.route
