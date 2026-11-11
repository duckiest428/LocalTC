"""Rides: what other pilots have reported about the turbulence, icing and cloud tops around the flight, the pilot's own
reports, and a smoother level when asked for one.

The reports are the public PIREP feed's (``PilotReports`` events, recorded, so a replay hears the same ones) and this
flight's own (``report_conditions``: "moderate chop at FL350", "tops at 330"). ATC answers from them deterministically:
the nearest worth saying, the level and how far, and how old; the model may word it, saying the same.

- **A ride report**: "any reports of turbulence?" -> "light to moderate chop reported at FL350 by an A321, 40 miles
  ahead, 25 minutes ago; smooth at FL320".
- **A smoother level**: "request a smoother altitude" -> the level within 4,000 ft with the least reported, cleared if
  ``validate`` allows it; nothing better reported, said so.
- **The pilot's own report**: kept, and given to whoever asks after (the snapshot, the Live Map's ops).
"""

import math
import re
from typing import TYPE_CHECKING, Any

from localtc.atc_core.phase import FlightPhase as P
from localtc.atc_core.phraseology import speech
from localtc.atc_core.values import Phrase
from localtc.sim_api import OwnshipState, PilotReport, PilotReports
from localtc.sim_api.geo import bearing_deg, haversine_nm

if TYPE_CHECKING:
    from localtc.atc_core.facilities import Facility

SCALE = {"NEG": 0, "SMTH": 0, "SMOOTH": 0, "TRC": 1, "LGT": 2, "LGT-MOD": 3, "MOD": 4, "MOD-SEV": 5, "SEV": 6, "SEV-EXTM": 7,
         "EXTRM": 7, "EXTM": 7}
SAID = {0: "smooth", 1: "occasional light chop", 2: "light chop", 3: "light to moderate chop", 4: "moderate turbulence",
        5: "moderate to severe turbulence", 6: "severe turbulence", 7: "extreme turbulence"}
ICING_SAID = {1: "trace icing", 2: "light icing", 3: "light to moderate icing", 4: "moderate icing", 5: "moderate to severe icing",
              6: "severe icing", 7: "severe icing"}
NEAR_NM = 150.0  # reports this close, or ahead along the way within AHEAD_NM
AHEAD_NM = 250.0
AHEAD_DEG = 50.0
LEVEL_FT = 4000.0  # within this of the level asked about
MAX_AGE_MIN = 150.0
SMOOTHER_STEPS = (2000, -2000, 4000, -4000)
RIDE_WORDS = re.compile(r"\b(?:ride|rides|smooth|smoother|chop|choppy|bump|bumps|bumpy|turbulence|turbulent|rough)\b", re.I)
TOPS_SAID = re.compile(r"\btops?\b\D{0,12}(?:fl|flight level)?\s*(\d{3})\b", re.I)
LEVEL_SAID = re.compile(r"\b(?:fl|flight level)\s*(\d{3})\b", re.I)


def severity(text: str) -> int:
    """A report's turbulence or icing intensity on ``SCALE`` (-1: not reported)."""
    return SCALE.get((text or "").upper().replace(" ", ""), -1)


def own_report(text: str, own: OwnshipState, aircraft: str = "") -> PilotReport | None:
    """The pilot's own report from their words ("moderate chop at 350", "light rime icing", "tops at 330"), or None."""
    low = (text or "").lower()
    turbulence = ""
    for words, code in (("extreme", "EXTRM"), ("severe", "SEV"), ("moderate to severe", "MOD-SEV"), ("light to moderate", "LGT-MOD"),
                        ("moderate", "MOD"), ("light", "LGT"), ("occasional light", "LGT"), ("smooth", "NEG"), ("no turbulence", "NEG")):
        if re.search(rf"\b{words}\b(?:\s+\w+)?\s*(?:chop|turbulence|bumps)", low) or (code == "NEG" and words in low):
            turbulence = code
            break
    icing = ""
    if "icing" in low or " ice" in low or "rime" in low or "clear ice" in low:
        icing = next((c for w, c in (("severe", "SEV"), ("moderate", "MOD"), ("light", "LGT"), ("trace", "TRC")) if w in low), "LGT")
    tops = TOPS_SAID.search(low)
    level = LEVEL_SAID.search(low)
    if not turbulence and not icing and not tops:
        return None
    alt = int(level.group(1)) * 100 if level else int(round(own.alt_indicated_ft, -2))
    return PilotReport(lat=own.lat, lon=own.lon, alt_ft=alt, turbulence=turbulence, icing=icing,
                       tops_ft=int(tops.group(1)) * 100 if tops else None, aircraft=aircraft, raw=text, own=True)


def level_words(alt_ft: int) -> Phrase:
    return Phrase(speech.altitude_display(alt_ft), speech.altitude(alt_ft))


class RideMixin:
    """The engine's side (``AtcEngine``)."""

    _pireps: dict[str, PilotReports]
    own_reports: list[PilotReport]
    _pireps_t: float

    def _on_pilot_reports(self: Any, ev: PilotReports) -> None:
        self._pireps[ev.area] = ev
        self._pireps_t = ev.t

    def _reports_near(self: Any, own: OwnshipState, level_ft: float | None = None) -> list[tuple[float, PilotReport, bool]]:
        """The reports that matter here: (miles off, it, ahead), nearest first; at about ``level_ft`` when given."""
        out: dict[str, tuple[float, PilotReport, bool]] = {}
        age_now = (own.t - self._pireps_t) / 60.0 if self._pireps_t else 0.0
        for report in [r for ev in self._pireps.values() for r in ev.reports] + list(self.own_reports):
            if report.age_min + max(0.0, age_now) > MAX_AGE_MIN and not report.own:
                continue
            if level_ft is not None and abs(report.alt_ft - level_ft) > LEVEL_FT:
                continue
            miles = haversine_nm(own.lat, own.lon, report.lat, report.lon)
            off = abs(((bearing_deg(own.lat, own.lon, report.lat, report.lon) - own.hdg_true + 540) % 360) - 180)
            ahead = off <= AHEAD_DEG and miles <= AHEAD_NM
            if miles <= NEAR_NM or ahead:
                out[report.raw or f"{report.lat},{report.lon},{report.alt_ft}"] = (miles, report, ahead)
        return sorted(out.values(), key=lambda x: x[0])

    def _ride_message(self: Any, own: OwnshipState | None, text: str = "") -> Phrase | None:
        """The ride reports worth saying, or a plain "nothing reported"."""
        if own is None:
            return None
        asked = LEVEL_SAID.search(text or "")
        level = int(asked.group(1)) * 100 if asked else (self.state.assignments.altitude_ft or own.alt_indicated_ft)
        near = self._reports_near(own, level)
        bumpy = [x for x in near if severity(x[1].turbulence) >= 2]
        icy = [x for x in near if severity(x[1].icing) >= 1 and "ic" in (text or "").lower()]
        smooth = [x for x in near if severity(x[1].turbulence) == 0]
        tops = [x for x in self._reports_near(own) if x[1].tops_ft and ("top" in (text or "").lower() or not bumpy)]
        parts: list[Phrase] = []
        if bumpy:
            worst = max(bumpy[:4], key=lambda x: severity(x[1].turbulence))
            parts.append(self._said_report(worst, SAID[min(7, severity(worst[1].turbulence))], own))
        if icy:
            parts.append(self._said_report(icy[0], ICING_SAID[min(7, max(1, severity(icy[0][1].icing)))], own))
        if smooth and (not bumpy or smooth[0][1].alt_ft != bumpy[0][1].alt_ft):
            alt = smooth[0][1].alt_ft
            parts.append(Phrase(f"smooth reported at {level_words(alt).display}", f"smooth reported at {level_words(alt).spoken}"))
        if tops:
            alt = tops[0][1].tops_ft
            parts.append(Phrase(f"tops reported {level_words(alt).display}", f"tops reported {level_words(alt).spoken}"))
        if not parts:
            return Phrase("no recent ride reports near your level", "no recent ride reports near your level")
        return sum(parts[1:], parts[0])

    def _said_report(self: Any, item: tuple[float, PilotReport, bool], what: str, own: OwnshipState) -> Phrase:
        miles, report, ahead = item
        where = "in your area" if miles < 20 else f"{int(round(miles, -1))} miles {'ahead' if ahead else 'from you'}"
        where_said = "in your area" if miles < 20 else f"{speech.number_words(int(round(miles, -1)))} miles {'ahead' if ahead else 'from you'}"
        kind = speech.aircraft_type(report.aircraft) if report.aircraft and not report.own else ("", "")
        article = "an" if kind[0][:1].upper() in "AEIOU" else "a"
        by = f" by {article} {kind[0]}" if kind[0] else ""
        by_said = f" by {article} {kind[1]}" if kind[0] else ""
        age = "" if report.own or report.age_min < 15 else f", {int(round(report.age_min / 5) * 5)} minutes ago"
        level = level_words(report.alt_ft)
        return Phrase(f"{what} reported at {level.display}{by}, {where}{age}", f"{what} reported at {level.spoken}{by_said}, {where_said}{age}")

    def _smoother_request(self: Any, interp: Any, facility: "Facility", t: float, own: OwnshipState | None) -> bool:
        """ "Request a smoother ride", "any better levels for the chop?": a level with less reported. True: handled."""
        text = interp.text or ""
        if own is None or own.on_ground or not RIDE_WORDS.search(text) or isinstance(interp.values.get("altitude"), int):
            return False
        if facility.controller not in ("center", "departure", "approach"):
            return False
        here = self.state.assignments.altitude_ft or int(round(own.alt_indicated_ft, -2))
        best: tuple[float, int] | None = None
        for step in SMOOTHER_STEPS:
            level = here + step
            if not 1000 < level <= 45000 or (step > 0 and self.state.phase in (P.APPROACH, P.LANDING)):
                continue
            if not self.validate(own, altitude_ft=level).ok:
                continue
            reports = [x for x in self._reports_near(own, level) if abs(x[1].alt_ft - level) <= 1000]
            if not reports:
                continue
            score = sum(severity(x[1].turbulence) for x in reports) / len(reports)
            if best is None or score < best[0]:
                best = (score, level)
        mine = [x for x in self._reports_near(own, here) if abs(x[1].alt_ft - here) <= 1000]
        mine_score = sum(severity(x[1].turbulence) for x in mine) / len(mine) if mine else 2.0  # the pilot says it's bumpy
        if best is None or best[0] >= mine_score:
            self._schedule(t, "common.smoother_none", {"message": Phrase("nothing better reported near you, advise if you want to try another level",
                                                                          "nothing better reported near you, advise if you want to try another level")},
                           facility, expects_readback=False)
            return True
        level = best[1]
        why = SAID[min(7, int(math.floor(best[0])))]
        message = Phrase(f"{why} reported at {level_words(level).display}", f"{why} reported at {level_words(level).spoken}")
        self._schedule(t, "common.smoother_climb" if level > here else "common.smoother_descend", {"message": message, "altitude": level},
                       facility, on_issue=lambda: self._assign(altitude_ft=level))
        return True

    def _take_report(self: Any, text: str, own: OwnshipState | None) -> PilotReport | None:
        """The pilot's own PIREP, kept for whoever asks next."""
        if own is None:
            return None
        report = own_report(text, own, self.state.flight.aircraft_type or "")
        if report is not None:
            self.own_reports.append(report)
            self._ops_changed = True
        return report

    def _clearance_message(self: Any) -> Phrase | None:
        """What the flight's cleared for now, from the state: "cleared to FL350, direct KEPEC, expect ILS 26L"."""
        st = self.state
        a = st.assignments
        parts: list[tuple[str, str]] = []
        if a.altitude_ft:
            parts.append((f"maintain {speech.altitude_display(a.altitude_ft)}", f"maintain {speech.altitude(a.altitude_ft)}"))
        if a.heading:
            parts.append((f"heading {a.heading:03d}", f"heading {speech.heading(a.heading)}"))
        if a.speed_kt:
            parts.append((f"{a.speed_kt} knots", f"{speech.speed(a.speed_kt)}"))
        direct = next((x for x in reversed(self.amendments) if x.state == "active" and x.fix), None)
        if direct is not None and not a.heading:
            parts.append((f"direct {direct.fix}", f"direct {direct.fix}"))
        if a.approach and "approach" in st.clearances:
            parts.append((f"cleared {a.approach.replace(' RWY ', ' runway ')} approach", f"cleared {a.approach.replace(' RWY ', ' runway ')} approach"))
        elif a.approach:
            parts.append((f"expect {a.approach.replace(' RWY ', ' runway ')}", f"expect {a.approach.replace(' RWY ', ' runway ')}"))
        if "landing" in st.clearances and a.arrival_runway:
            parts.append((f"cleared to land runway {a.arrival_runway}", f"cleared to land runway {speech.runway(a.arrival_runway)}"))
        elif "takeoff" in st.clearances and a.departure_runway:
            parts.append((f"cleared for takeoff runway {a.departure_runway}", f"cleared for takeoff runway {speech.runway(a.departure_runway)}"))
        elif "taxi" in st.clearances and a.departure_runway and a.taxi_route:
            parts.append((f"taxi to runway {a.departure_runway} via {', '.join(a.taxi_route)}",
                          f"taxi to runway {speech.runway(a.departure_runway)} via {speech.taxi_route(tuple(a.taxi_route))}"))
        if a.squawk:
            parts.append((f"squawk {a.squawk}", f"squawk {speech.squawk(a.squawk)}"))
        if not parts:
            return None
        return Phrase(", ".join(p[0] for p in parts), ", ".join(p[1] for p in parts))
