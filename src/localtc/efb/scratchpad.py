"""The scratchpad: notes the pilot writes on the app or the phone, kept in step between them, by flight.

Each note has its text, when it was written and in which phase of the flight, where from (the app or the phone),
whether it's pinned, and its kind: a free note, or a quick one (a clearance, the ATIS, a frequency, a squawk, the
weather, a time). What a note says that looks like a clearance (a squawk, an altitude, a frequency, a runway, a
SID or STAR) is picked out and shown beside it, marked as the pilot's note and not the sim's: it's checked against
what ATC actually gave, never used in place of it.

Kept in ``scratchpad.json`` in the app's data folder: the last ``KEEP`` notes, across flights.
"""

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

KEEP = 300
MAX_TEXT = 2000
KINDS = ("note", "clearance", "atis", "frequency", "squawk", "weather", "time")


def picked(text: str) -> dict[str, str]:
    """What a note says that a clearance would: {"squawk": "4521", "altitude": "5000", "frequency": "124.350",
    "runway": "27R", "procedure": "SEA9", "destination": ...}. Words as written; nothing more is read into them."""
    t = text.upper()
    out: dict[str, str] = {}
    if m := re.search(r"\b(?:SQ(?:UAWK)?|XPDR|CODE)\s*([0-7]{4})\b", t):
        out["squawk"] = m.group(1)
    if m := re.search(r"\b(?:FL\s?(\d{2,3})|(?:CLIMB|MAINTAIN|ALT|INITIAL|INIT)\D{0,12}?(\d{1,2}[,.]?\d{3}|\d{4,5}))\b", t):
        out["altitude"] = f"FL{m.group(1)}" if m.group(1) else m.group(2).replace(",", "").replace(".", "")
    if m := re.search(r"\b(1[1-3]\d[.,]\d{1,3})\b", t):
        out["frequency"] = m.group(1).replace(",", ".")
    if m := re.search(r"\b(?:RWY|RUNWAY|R/W)\s*(\d{1,2}[LRC]?)\b", t):
        out["runway"] = m.group(1)
    if m := re.search(r"\b([A-Z]{3,5}\d[A-Z]?)\b", t):
        out["procedure"] = m.group(1)
    if m := re.search(r"\b(?:INFO(?:RMATION)?|ATIS)\s+([A-Z]+)\b", t):
        out["atis"] = m.group(1)[0]  # "information Kilo": K
    if m := re.search(r"\b(?:CLEARED\s+TO|CLR\s+TO|C\s)\s*([A-Z]{4})\b", t):
        out["destination"] = m.group(1)
    return out


def kind_of(text: str) -> str:
    """A quick note's kind from its words (a free note when nothing fits)."""
    t = text.upper()
    if re.search(r"\bCLEARED\b|\bCLR\b|\bSID\b", t):
        return "clearance"
    if re.search(r"\bINFO(?:RMATION)?\s+[A-Z]+\b|\bATIS\b", t):
        return "atis"
    if re.fullmatch(r"\s*(?:SQ(?:UAWK)?\s*)?[0-7]{4}\s*", t):
        return "squawk"
    if re.fullmatch(r"\s*1[1-3]\d[.,]\d{1,3}\s*", t):
        return "frequency"
    if re.search(r"\b(?:METAR|TAF|WIND|QNH|VIS)\b", t):
        return "weather"
    return "note"


class Scratchpad:
    def __init__(self, path: Path | None, clock=time.time) -> None:
        self.path = path
        self.clock = clock
        self.notes: list[dict] = []
        self._lock = threading.Lock()
        self.listeners: list = []  # called with the notes after every change (the app's page, the companion)
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.notes = [n for n in data.get("notes", []) if isinstance(n, dict) and n.get("id")][-KEEP:]
        except (OSError, ValueError) as exc:
            log.info("Scratchpad unreadable: %s", exc)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"notes": self.notes[-KEEP:]}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            log.info("Scratchpad not saved: %s", exc)

    def _changed(self) -> None:
        self._save()
        for listener in list(self.listeners):
            try:
                listener(self.for_flight(None))
            except Exception:  # noqa: BLE001 - a listener never breaks the notes
                log.debug("Scratchpad listener failed", exc_info=True)

    def add(self, text: str, *, flight: str = "", phase: str = "", source: str = "app", kind: str = "",
            pinned: bool = False) -> dict:
        text = str(text or "").strip()[:MAX_TEXT]
        if not text:
            raise ValueError("an empty note")
        kind = kind if kind in KINDS else kind_of(text)
        note = {"id": uuid.uuid4().hex[:12], "text": text, "kind": kind, "flight": flight, "phase": phase,
                "source": source, "pinned": bool(pinned), "t": self.clock(), "picked": picked(text)}
        with self._lock:
            self.notes.append(note)
            self.notes = self.notes[-KEEP:]
        self._changed()
        return note

    def edit(self, note_id: str, **changes) -> dict:
        with self._lock:
            note = next((n for n in self.notes if n["id"] == note_id), None)
            if note is None:
                raise KeyError(note_id)
            if "text" in changes:
                text = str(changes["text"] or "").strip()[:MAX_TEXT]
                if not text:
                    raise ValueError("an empty note")
                note["text"], note["picked"] = text, picked(text)
            if "pinned" in changes:
                note["pinned"] = bool(changes["pinned"])
            if changes.get("kind") in KINDS:
                note["kind"] = changes["kind"]
            note["edited"] = self.clock()
        self._changed()
        return note

    def delete(self, note_id: str) -> None:
        with self._lock:
            before = len(self.notes)
            self.notes = [n for n in self.notes if n["id"] != note_id]
            if len(self.notes) == before:
                raise KeyError(note_id)
        self._changed()

    def for_flight(self, flight: str | None) -> list[dict]:
        """The notes for a flight (pinned ones always), newest last; None: all of them."""
        with self._lock:
            return [dict(n) for n in self.notes if flight is None or n.get("flight") == flight or n.get("pinned")]

    def checked(self, note: dict, issued: dict[str, str]) -> dict[str, str]:
        """Each picked value against what ATC gave ({element: value}): "same", "differs" or "not given"."""
        out = {}
        for key, value in note.get("picked", {}).items():
            given = issued.get(key)
            if given is None:
                out[key] = "not given"
            else:
                out[key] = "same" if _norm(value) == _norm(given) else "differs"
        return out


def _norm(v: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v).upper()).lstrip("0")
