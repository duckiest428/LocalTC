"""Which checklist a pilot names ("run the before takeoff checklist", "landing checklist", "pause the checklist"):
the phase the copilot finds it by in the aircraft's checklists (``checklist_db``), or the pilot's control of the one
being read. The checklists themselves are real ones only: the aircraft's own, or its family's normal checklist."""

import re

# The words a pilot says for each, to the phase it's read in.
SPOKEN = (("cockpit prep", "cockpit_prep"), ("cockpit preparation", "cockpit_prep"), ("preliminary", "cockpit_prep"),
          ("preflight", "cockpit_prep"), ("before start", "before_start"), ("after start", "after_start"),
          ("before taxi", "taxi"), ("taxi", "taxi"), ("before takeoff", "line_up"), ("before take off", "line_up"),
          ("lineup", "line_up"), ("line up", "line_up"), ("after takeoff", "after_takeoff"),
          ("after take off", "after_takeoff"), ("climb", "after_takeoff"), ("cruise", "cruise"), ("descent", "descent"),
          ("approach", "approach"), ("landing", "landing"), ("after landing", "after_landing"), ("shutdown", "parking"),
          ("parking", "parking"), ("securing", "securing"), ("secure", "securing"))
CONTROL = re.compile(r"\b(pause|hold|stop|resume|continue|skip)\b")


def named(text: str) -> str | None:
    """Which checklist the words name ("run the before takeoff checklist" -> "line_up"), "pause" / "resume" / "skip"
    for the one being read, or None."""
    lowered = " ".join(text.lower().replace("-", " ").split())
    if (m := CONTROL.search(lowered)) and not any(w in lowered for w, _ in SPOKEN if w not in ("climb",)):
        word = m.group(1)
        return {"hold": "pause", "stop": "pause", "continue": "resume"}.get(word, word)
    for words, name in sorted(SPOKEN, key=lambda s: -len(s[0])):
        if words in lowered:
            return name
    return None
