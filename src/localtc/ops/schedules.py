"""The departures due from an airport over the next hour or so, for the aircraft at its gates before they push back.

Two free sources, each needing its own free account (``ops.keys``):

- **AirLabs** (``airlabs``): today's schedule, flight by flight with its time (the free plan gives IATA codes and the
  local time; the airline's ICAO code comes from ``IATA_TO_ICAO``).
- **OpenSky Network** (``opensky_id`` / ``opensky_secret``): the departures from the airport the day before, at the
  same hours. Airlines fly the same flights day after day, so yesterday's 14:05 is today's: its callsign, where it
  went, and its aircraft (the type from the aircraft's address, adsbdb.com).

With neither, there's no schedule: the gates have the real aircraft as their transponders come on, and nothing is
made up in their place.
"""

import logging
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

from localtc.ops.fetch import Cache, http_json
from localtc.ops.keys import DataKeys

log = logging.getLogger(__name__)

AIRLABS_URL = "https://airlabs.co/api/v9/schedules"
OPENSKY_TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
OPENSKY_DEPARTURES_URL = "https://opensky-network.org/api/flights/departure"
SCHEDULE_TTL_S = 30 * 60.0  # an airport's schedule fetched every half hour at most
WINDOW_S = 3 * 3600.0  # the departures asked for: the next three hours
DAY_S = 86400.0

# The big airlines' two-letter codes and their ICAO three: AirLabs' free plan names flights by IATA ("BA7").
IATA_TO_ICAO = {
    "AA": "AAL", "DL": "DAL", "UA": "UAL", "WN": "SWA", "AS": "ASA", "B6": "JBU", "NK": "NKS", "F9": "FFT",
    "HA": "HAL", "G4": "AAY", "SY": "SCX", "AC": "ACA", "WS": "WJA", "TS": "TSC", "PD": "POE", "RV": "ROU",
    "QK": "JZA", "AM": "AMX", "Y4": "VOI", "VB": "VIV", "BA": "BAW", "VS": "VIR", "U2": "EZY", "FR": "RYR",
    "LS": "EXS", "BY": "TOM", "AF": "AFR", "KL": "KLM", "LH": "DLH", "LX": "SWR", "OS": "AUA", "SN": "BEL",
    "LO": "LOT", "OK": "CSA", "SK": "SAS", "AY": "FIN", "DY": "NOZ", "D8": "IBK", "EI": "EIN", "IB": "IBE",
    "UX": "AEA", "VY": "VLG", "TP": "TAP", "AZ": "ITY", "TK": "THY", "PC": "PGT", "A3": "AEE", "W6": "WZZ",
    "EW": "EWG", "DE": "CFG", "X3": "TUI", "HV": "TRA", "TO": "TVF", "V7": "VOE", "EK": "UAE", "QR": "QTR",
    "EY": "ETD", "SV": "SVA", "GF": "GFA", "WY": "OMA", "FZ": "FDB", "G9": "ABY", "LY": "ELY", "MS": "MSR",
    "ET": "ETH", "KQ": "KQA", "SA": "SAA", "AT": "RAM", "CX": "CPA", "JL": "JAL", "NH": "ANA", "KE": "KAL",
    "OZ": "AAR", "BR": "EVA", "CI": "CAL", "SQ": "SIA", "TR": "TGW", "TG": "THA", "MH": "MAS", "GA": "GIA",
    "PR": "PAL", "5J": "CEB", "VN": "HVN", "CA": "CCA", "MU": "CES", "CZ": "CSN", "HU": "CHH", "3U": "CSC",
    "AI": "AIC", "6E": "IGO", "UK": "VTI", "QF": "QFA", "VA": "VOZ", "JQ": "JST", "NZ": "ANZ", "LA": "LAN",
    "JJ": "TAM", "G3": "GLO", "AD": "AZU", "AV": "AVA", "CM": "CMP", "AR": "ARG", "MX": "MXA", "FX": "FDX",
    "5X": "UPS", "PO": "PAC", "CV": "CLX", "LD": "AHK", "MM": "APJ", "BC": "SKY", "7C": "JJA", "ZG": "TZP",
    "OO": "SKW", "9E": "EDV", "YX": "RPA", "MQ": "ENY", "OH": "JIA", "YV": "ASH", "ZW": "AWI", "PT": "PDT",
    "QX": "QXE", "C5": "UCA", "G7": "GJS", "CP": "CPZ", "MW": "MUA",
}


@dataclass(frozen=True)
class Departure:
    """One departure due: ``callsign`` as its transponder will send it ("BAW7"), ``airline`` (ICAO), ``off_t`` when
    it's due off the gate (epoch s), ``destination`` (ICAO or IATA, as the source gives it), ``type`` (ICAO, "" not
    known), ``hex`` the aircraft that flew it yesterday (OpenSky), ``source``."""

    callsign: str
    airline: str
    off_t: float
    destination: str = ""
    type: str = ""
    hex: str = ""
    source: str = ""


def icao_callsign(flight_iata: str) -> str:
    """ "BA7" -> "BAW7"; "" when the airline isn't known."""
    code = flight_iata.strip().upper().replace(" ", "")
    for n in (2,):
        prefix, number = code[:n], code[n:]
        if prefix in IATA_TO_ICAO and number[:1].isdigit():
            return IATA_TO_ICAO[prefix] + number.lstrip("0")
    return ""


class Schedules:
    """``departures(icao, now)``: the departures from ``icao`` due from ``now`` on (blocking; off the event loop)."""

    def __init__(self, keys: DataKeys, cache: Cache, fetch=http_json, clock=time.time) -> None:
        self.keys, self.cache, self.fetch, self.clock = keys, cache, fetch, clock
        self._token: tuple[str, float] | None = None

    @property
    def available(self) -> bool:
        return self.keys.has("airlabs") or self.keys.has("opensky")

    def departures(self, icao: str, now: float | None = None, lon: float = 0.0) -> list[Departure]:
        """``lon``: the airport's longitude (its time zone, where the source gives only local times)."""
        now = self.clock() if now is None else now
        icao = icao.upper()
        if self.keys.has("airlabs"):
            got = self.cache.get(f"airlabs:{icao}", SCHEDULE_TTL_S, lambda: self._airlabs(icao, lon), source="AirLabs")
            if got is not None and got.data:
                return [d for d in (_departure(r) for r in got.data) if d is not None and d.off_t >= now - 3600]
        if self.keys.has("opensky"):
            hour = int(now // 3600)
            got = self.cache.get(f"opensky:{icao}:{hour}", SCHEDULE_TTL_S, lambda: self._opensky(icao, now),
                                 source="OpenSky Network")
            if got is not None and got.data:
                return [d for d in (_departure(r) for r in got.data) if d is not None and d.off_t >= now - 3600]
        return []

    # --- AirLabs -----------------------------------------------------------------------------------------------

    def _airlabs(self, icao: str, lon: float = 0.0) -> list[dict]:
        data = self.fetch(AIRLABS_URL, params={"dep_icao": icao, "api_key": self.keys.get("airlabs")})
        rows = (data or {}).get("response") or []
        offset = local_offset_s([_naive_ts(r.get("dep_time") or "") for r in rows], self.clock(), lon)
        out = []
        for r in rows:
            callsign = (r.get("flight_icao") or "").upper() or icao_callsign(r.get("flight_iata") or "")
            local = _naive_ts(r.get("dep_time") or "")
            off = r.get("dep_time_ts") or _naive_ts(r.get("dep_time_utc") or "") or (local - offset if local else None)
            if not callsign or not off or (r.get("status") or "scheduled") in ("cancelled", "landed"):
                continue
            delayed = r.get("dep_delayed") or r.get("delayed") or 0
            out.append({"callsign": callsign, "airline": callsign[:3], "off_t": float(off) + 60.0 * float(delayed or 0),
                        "destination": r.get("arr_icao") or r.get("arr_iata") or "", "type": r.get("aircraft_icao") or "",
                        "source": "AirLabs"})
        return out

    # --- OpenSky -----------------------------------------------------------------------------------------------

    def _bearer(self) -> str:
        now = self.clock()
        if self._token is not None and now < self._token[1]:
            return self._token[0]
        body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.keys.get("opensky_id"),
                                       "client_secret": self.keys.get("opensky_secret")}).encode()
        got = self.fetch(OPENSKY_TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        token = got["access_token"]
        self._token = (token, now + float(got.get("expires_in", 1800)) - 60)
        return token

    def _opensky(self, icao: str, now: float) -> list[dict]:
        """Yesterday's departures over the same hours, moved on a day."""
        begin = int(now - DAY_S)
        rows = self.fetch(OPENSKY_DEPARTURES_URL, params={"airport": icao, "begin": begin, "end": int(begin + WINDOW_S)},
                          headers={"Authorization": f"Bearer {self._bearer()}"}) or []
        out = []
        for r in rows:
            callsign = (r.get("callsign") or "").strip().upper()
            seen = r.get("firstSeen")
            if not callsign or not seen or not callsign[:3].isalpha():
                continue  # a registration, not an airline's flight: not a schedule
            out.append({"callsign": callsign, "airline": callsign[:3], "off_t": float(seen) + DAY_S,
                        "destination": r.get("estArrivalAirport") or "", "hex": (r.get("icao24") or "").lower(),
                        "source": "OpenSky Network (yesterday's)"})
        return out


def _departure(row: dict) -> Departure | None:
    try:
        return Departure(callsign=row["callsign"], airline=row.get("airline", ""), off_t=float(row["off_t"]),
                         destination=row.get("destination", ""), type=row.get("type", ""), hex=row.get("hex", ""),
                         source=row.get("source", ""))
    except (KeyError, TypeError, ValueError):
        return None


def _naive_ts(text: str) -> float | None:
    """ "2026-10-12 14:05" as seconds, read as if it were UTC (None when it isn't a time)."""
    try:
        when = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc).timestamp()


def local_offset_s(local_times: list[float | None], now_utc: float, lon: float) -> float:
    """The airport's offset from UTC, from its schedule: the free plan gives only local times, and a busy airport's
    first departure of the next hours is due about now. Rounded to the half hour; one that doesn't fit the longitude
    within three hours (a quiet airport's first flight in the afternoon) gives way to the longitude's."""
    by_longitude = round(lon / 15.0) * 3600.0
    times = sorted(t for t in local_times if t)
    if not times:
        return by_longitude
    guess = round((times[0] - now_utc) / 1800.0) * 1800.0
    return guess if abs(guess - by_longitude) <= 3 * 3600 and -12 * 3600 <= guess <= 14 * 3600 else by_longitude
