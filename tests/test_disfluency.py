"""People on the radio: an "uh" now and then, in the right places, never in a number or in anything urgent."""

import re

from localtc.atc_core import disfluency

CLEARANCE = "United three eighteen, turn left heading two seven zero, descend and maintain eight thousand"
READBACK = "descend and maintain eight thousand, united four twenty one"


def said(fn, line, n=400, **kw):
    return [fn(line, key=str(i), **kw) for i in range(n)]


def test_a_controller_hesitates_now_and_then_by_their_manner():
    chatty = sum(out != CLEARANCE for out in said(disfluency.atc, CLEARANCE, kind="conversational"))
    strict = sum(out != CLEARANCE for out in said(disfluency.atc, CLEARANCE, kind="strict"))
    busy = sum(out != CLEARANCE for out in said(disfluency.atc, CLEARANCE, kind="conversational", busy=True))
    assert 25 < chatty < 75  # about one call in eight
    assert strict < 20 and busy < chatty  # hardly ever from a strict controller, less on a busy frequency
    assert all("um" not in out.split(", ") for out in said(disfluency.atc, CLEARANCE, rate=1.0))


def test_never_inside_a_value_nor_before_a_sign_off():
    for line in (CLEARANCE, "Alaska twelve, contact Seattle Center one two zero point one, good day",
                 "Delta fifteen thirty eight, runway three one, cleared for takeoff, wind three one zero at eight"):
        for out in said(disfluency.atc, line, rate=1.0):
            assert out.replace("uh, ", "").replace("um, ", "") == line  # only a filler added, the words all there
            assert not re.search(r"\b(heading|runway|maintain|contact|level), (uh|um)", out)
            assert not re.search(r"(uh|um), (good day|two|three|one|zero)", out)


def test_nothing_urgent_and_nothing_short_gets_one():
    for line in ("United three eighteen, go around, climb and maintain three thousand",
                 "Delta twelve, stop immediately, traffic on the runway, I say again, stop immediately"):
        assert all(out == line for out in said(disfluency.atc, line, rate=1.0))
    assert disfluency.atc(CLEARANCE, key="1", rate=1.0, instruction_id="tower.go_around") == CLEARANCE
    assert all(out == "Delta twelve, roger" for out in said(disfluency.atc, "Delta twelve, roger", rate=1.0))


def test_other_pilots_hesitate_where_pilots_do():
    outs = said(disfluency.pilot, READBACK)
    changed = [out for out in outs if out != READBACK]
    assert 30 < len(changed) < 95  # about one in seven
    # A readback: before the callsign at its end, never to start the transmission, never twice.
    assert all(out.endswith(("uh, united four twenty one", "ah, united four twenty one")) for out in changed)
    assert not any(out.lower().startswith(("uh", "um", "ah")) for out in outs)
    call = "Seattle Ground, Delta five thirty-three, ready to taxi"
    calls = [out for out in said(disfluency.pilot, call) if out != call]
    assert calls and all(out.startswith(("Seattle Ground, uh, Delta", "Seattle Ground, ah, Delta")) for out in calls)
    short = "Push and start, tail right, Delta five forty-one"  # mid-readback "Push and start, uh, tail right"
    assert all(out in (short, "Push and start, tail right, uh, Delta five forty-one",
                       "Push and start, tail right, ah, Delta five forty-one") for out in said(disfluency.pilot, short))


def test_the_same_words_at_the_same_time_are_said_the_same_way():
    assert disfluency.pilot(READBACK, key="12.5") == disfluency.pilot(READBACK, key="12.5")  # a replay sounds the same
