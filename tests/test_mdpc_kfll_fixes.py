"""Punta Cana to Fort Lauderdale (A320, the copilot on the radios), tests/fixtures/real_mdpc_kfll: a departure of
LocalTC's off 10L, head-on, as the flight was cleared to land 28R (it passed over it at 600 ft); a hold "number 8"
with the arrival stream flowing; Miami Center answering a check-in nobody made after the ATIS was listened to."""

from pathlib import Path

import pytest

from localtc.atc_core.atis.real import parse
from localtc.atc_core.llm.phrase import check_reworded
from localtc.config import load_config, with_recorded
from localtc.replay import Recording
from localtc.scenario import Scenario, ScenarioMeta, run

FLIGHT = Path(__file__).parent / "fixtures" / "real_mdpc_kfll"
FLL_ATIS = ("FLL ATIS INFO Q 0153Z. 33005KT 10SM FEW023 BKN210 BKN250 21/18 A3000 (THREE ZERO ZERO ZERO) RMK AO2 SLP158 "
            "T02110178. ARRIVALS EXPECT ILS APCH RWY 28R OR 28L. DEPG RWYS RWY 28R AND 28L. PARL OPS ARE BEING CNTD. TWR "
            "FREQ IS 119.3, CTC CD ON 121.4. NOTAM. TWY A CLSD BTWN FEDEX RAMP AND SHELTAIR NORTH. RY 10L ALS OTS, RY 10L, "
            "28R, 28L THRESHOLD LGTS OTS. CRANES INVOF THE AIRPORT. ...ADVS YOU HAVE INFO Q.")


@pytest.fixture(scope="module")
def replay() -> list[str]:
    cfg = with_recorded(load_config(), Recording(FLIGHT).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(FLIGHT)), flight=cfg.flight, atc=cfg.atc)
    return run(scenario, FLIGHT, recording=FLIGHT, recorded_pilot=True, copilot="full").lines


def when(line: str) -> float:
    return float(line.split("]")[0].strip("[ "))


def atc(lines: list[str], start: float, end: float) -> list[str]:
    return [line for line in lines if " ATC " in line and start <= when(line) <= end]


def test_departing_runways_written_rwys_rwy_are_read():
    """"DEPG RWYS RWY 28R AND 28L" wasn't read; the first runways anywhere were taken instead, 10L from the lights-out
    notice, and LocalTC's departures went off 10L into the arrivals on 28R."""
    info = parse(FLL_ATIS)
    assert info.departing == ("28R", "28L")


def test_no_landing_clearance_with_a_takeoff_coming_the_other_way(replay):
    """JBU1590 rolled on 10L from 12,093 s, towards the flight on its final for 28R, cleared to land at 12,098 s."""
    tower = atc(replay, 12090, 12160)
    assert not any("cleared to land" in line for line in tower)
    assert any("go around" in line for line in tower)


def test_no_hold_with_the_arrival_stream_flowing(replay):
    assert not any("hold at CRNVL" in line for line in atc(replay, 9000, 12000))


def test_back_from_the_atis_is_no_new_check_in(replay):
    """Tuned to Fort Lauderdale's ATIS and straight back at 9,696 s: Miami Center said "roger, descend via the OLAHS3
    arrival" 47 s later to nobody's call."""
    assert not any("Miami Center" in line and "roger" in line for line in atc(replay, 9690, 9800))


def test_when_able_isnt_added_to_an_instruction():
    assert check_reworded('{"reply":"cross runway 09 when able"}', "cross runway 09", ("JetBlue 174",)) == "cross runway 09"
    assert "when able" in check_reworded('{"reply":"vacate left B6 when able"}', "vacate left B6 when able",
                                         ("JetBlue 174",))


def test_the_star_asked_for_is_flown_without_headings(tmp_path):
    """"Is there a way to request to follow the published STAR instead of receiving radar vectors?" There wasn't:
    "request the STAR" was nothing, "can we fly the STAR instead of vectors" asked for vectors. Now it's direct to the
    arrival's next fix ahead, and no headings on the way there."""
    import gzip

    from localtc.atc_core.readback import Interpretation

    cut = tmp_path / "arrival"
    cut.mkdir()
    with gzip.open(FLIGHT / "session.jsonl.gz", "rt", encoding="utf-8") as src, \
            gzip.open(cut / "session.jsonl.gz", "wt", encoding="utf-8") as out:
        for line in src:
            if '"t":' not in line or float(line.split('"t":', 1)[1].split(",", 1)[0].split("}", 1)[0]) <= 9900.0:
                out.write(line)
    cfg = with_recorded(load_config(), Recording(cut).header.config)
    scenario = Scenario(scenario=ScenarioMeta(recording=str(cut)), flight=cfg.flight, atc=cfg.atc)
    engine = run(scenario, cut, recording=cut, recorded_pilot=True, copilot="full").engine
    own, center = engine.state.aircraft, engine.state.comms.tuned
    assert center is not None and center.controller == "center"
    star = [f.ident for f in engine.route.procedure(engine.cfg.star, "DSC")]
    engine._scheduled.clear()
    engine._star_request(Interpretation(kind="request", intent="request_star", text="request the STAR"), center, own.t, own)
    said = engine._scheduled[-1]
    assert said.instruction_id == "common.direct" and said.slots["fix"] in star
    said.on_issue()
    assert engine._star_asked is not None and engine._star_asked[0] == said.slots["fix"]
    assert engine._radar_vectors(own) is False  # no headings on the way to it
