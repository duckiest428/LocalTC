"""A centre is never on 0.0 (an empty setting from the old settings page), and a blink of a pause is no pause."""

import asyncio
import time

import msgspec

from helpers.sim_fakes import FakeSimConnect
from localtc.atc_core.facilities import area_center, center_facility
from localtc.config import CENTER_MHZ, CENTER_NAME, AtcConfig
from localtc.sim_api import SimLifecycle
from localtc.sim_bridge.simconnect_source import PAUSE_SETTLE_S, SimConnectSource

from test_sim_bridge import FAST


def test_empty_centre_settings_are_the_defaults_again():
    cfg = msgspec.convert({"center_name": "", "center_mhz": 0.0}, AtcConfig)
    assert (cfg.center_name, cfg.center_mhz) == (CENTER_NAME, CENTER_MHZ)
    kept = msgspec.convert({"center_name": "Denver", "center_mhz": 133.5}, AtcConfig)
    assert (kept.center_name, kept.center_mhz) == ("Denver", 133.5)


def test_a_centre_is_never_given_a_frequency_off_the_airband():
    montreal = area_center("Montreal Centre", [], 0.0)  # CYHU to CYYT: neither end publishes a Montreal centre
    assert montreal.station == "Montreal Centre" and 132.0 <= montreal.mhz <= 135.975
    assert area_center("Montreal Centre", [], 0.0).mhz == montreal.mhz  # the same one each time
    assert 118.0 <= center_facility([], "Seattle", 0.0).mhz < 137.0
    assert area_center("Montreal Centre", [], 132.35).mhz == 132.35


def _lifecycle(pushes):
    fake = FakeSimConnect()

    async def main():
        source = SimConnectSource(FAST, dll_factory=lambda: fake)
        await asyncio.wait_for(source.start(), 3)
        seen = []

        async def run():
            async for ev in source.events():
                if isinstance(ev, SimLifecycle):
                    seen.append(ev)

        task = asyncio.create_task(run())
        for data, wait in pushes:
            fake.push_event("Pause_EX1", data=data)
            await asyncio.sleep(wait)
        task.cancel()
        await source.stop()
        return [(e.kind, e.detail) for e in seen]

    return asyncio.run(main())


def test_an_efb_blink_of_a_pause_goes_nowhere():
    # The Bluebird 757's EFB: full pause, unpaused a tenth of a second later, at every touch.
    assert _lifecycle([(1, 0.1), (0, 0.1), (1, 0.05), (0, PAUSE_SETTLE_S + 0.3)]) == []


def test_a_real_pause_still_goes_out_and_ends():
    start = time.monotonic()
    seen = _lifecycle([(1, PAUSE_SETTLE_S + 0.4), (0, 0.3)])
    assert seen == [("paused", "full"), ("unpaused", "")], seen
    assert time.monotonic() - start >= PAUSE_SETTLE_S


def test_a_microphone_windows_renamed_is_still_found(monkeypatch):
    from localtc.stt import audio

    monkeypatch.setattr(audio, "input_devices", lambda: [(0, "Microsoft Sound Mapper - Input", 44100),
                                                         (3, "Microphone (2- K66)", 48000),
                                                         (7, "Headset Microphone (Realtek(R) Audio)", 48000)])
    assert audio.find_device("Microphone (K66)") == 3
    assert audio.find_device("Realtek") == 7


def test_a_microphone_gone_falls_back_to_the_default_and_is_tried_again(monkeypatch):
    from localtc.stt import audio

    monkeypatch.setattr(audio, "input_devices", lambda: [(0, "Microphone (Realtek Audio)", 48000)])
    capture = audio.AudioCapture.__new__(audio.AudioCapture)
    capture.spec, capture.device, capture._stream, capture._last_block, capture._lost = "Microphone (K66)", 4, None, 0.0, False
    capture._lock, capture._pre, capture._pre_len = __import__("threading").Lock(), __import__("collections").deque(), 0
    opened = []
    monkeypatch.setattr(audio.AudioCapture, "start", lambda self: opened.append(self.device))
    monkeypatch.setattr(audio.AudioCapture, "name", property(lambda self: "default"))
    monkeypatch.setattr(audio.GATE, "restart", lambda sd: True)
    assert capture.refresh() == "default" and opened == [None] and capture.idle_s == 0.0

    def broken(self):
        raise RuntimeError("no device at all")

    monkeypatch.setattr(audio.AudioCapture, "start", broken)
    try:
        capture.refresh()
    except RuntimeError:
        pass
    assert capture.idle_s == float("inf")  # the next press reopens it again, not "too short" for the rest of the flight


def test_a_controller_waits_for_the_check_in_before_its_own_calls():
    """San Juan Departure's SIGMET came four seconds after the pilot tuned it, before a word from them."""
    from localtc.atc_core.engine import MISSED_CHECKIN_S
    from localtc.atc_core.values import Phrase
    from test_unscripted import cruising_engine

    engine, own = cruising_engine()
    engine.handle(own)  # Seattle Center tuned, nothing said on it yet
    center = engine.state.comms.tuned
    t = own.t + 4
    engine._schedule(t, "common.info", {"message": Phrase("convective SIGMET in your area", "convective sigmet in your area")},
                     center, delay=False, expects_readback=False)
    assert engine._flush(t) == []  # held: the pilot hasn't checked in
    assert engine._scheduled and engine._scheduled[0].held
    engine.state.comms.last_pilot_t = t + 2  # "Seattle Center, N172LT, 5,000"
    engine._answering = True
    engine._schedule(t + 3, "common.roger", {}, center, delay=False, expects_readback=False)
    engine._answering = False
    said = [tx.instruction_id for tx in engine._flush(t + 3)]
    assert said == ["common.roger"]  # the answer first ...
    later = [tx.instruction_id for tx in engine._flush(t + 9)]
    assert later == ["common.info"]  # ... then what waited
    # Never a word from the pilot: after MISSED_CHECKIN_S the controller calls anyway.
    engine2, own2 = cruising_engine()
    engine2.handle(own2)
    engine2._schedule(own2.t + 1, "common.info", {"message": Phrase("x", "x")}, engine2.state.comms.tuned, delay=False,
                      expects_readback=False)
    assert engine2._flush(own2.t + 1) == []
    assert [tx.instruction_id for tx in engine2._flush(own2.t + MISSED_CHECKIN_S + 1)] == ["common.info"]


def test_a_sigmet_over_the_flight_is_in_its_area_with_its_tops():
    from localtc.atc_core.routing import Hazard

    golf4 = Hazard(id="GOLF 4", kind="sigmet", name="TJZS", polygon=((22.017, -67.6), (19.167, -64.117), (18.0, -64.283),
                                                                      (16.267, -68.0), (19.05, -67.95), (22.017, -67.6)),
                   ceiling_ft=43000, hazard="TS")
    assert golf4.contains(18.466, -65.971)  # San Juan, as the flight took off
    import msgspec as ms
    from test_unscripted import cruising_engine

    engine, own = cruising_engine()
    engine.handle(own)
    here = ms.structs.replace(own, lat=18.466, lon=-65.971)
    engine._weather_ahead(here, engine.state.comms.tuned, golf4, [], 0, 0.0)
    said = engine._scheduled[-1].slots["message"].display
    assert said.startswith("convective SIGMET in your area, thunderstorms up to FL430, deviations left or right of course approved"), said
    assert "miles ahead" not in said


def test_san_juan_is_the_faas_clearance_and_center():
    from helpers.airports import make_airport

    from localtc.atc_core.airspace import Airspace
    from localtc.atc_core.facilities import airport_facilities

    sju = make_airport("TJSJ", "LUIS MUNOZ MARIN INTL", (18.4394, -66.0018), 9, 80, ("08", "26"), {
        "clearance": (126.4, "SAN JUAN"), "ground": (121.7, "SAN JUAN"), "tower": (132.05, "SAN JUAN")})
    by = {f.controller: f for f in airport_facilities(sju, role="departure")}
    assert by["clearance"].station == "San Juan Clearance"
    area = Airspace.load().center_at(19.0, -66.0)
    assert area is not None and area.name == "San Juan Center"


def test_a_microphone_plugged_in_after_the_app_started_is_found_at_the_flights_start(monkeypatch):
    """PortAudio's list was the one from the app's start: the K66 back since stopped every flight from starting."""
    from localtc.stt import audio

    lists = iter([[(0, "Microsoft Sound Mapper - Input", 44100)],
                  [(0, "Microsoft Sound Mapper - Input", 44100), (1, "Microphone (K66)", 48000)]])
    current = {"list": next(lists)}
    monkeypatch.setattr(audio, "input_devices", lambda: current["list"])

    def restart(sd):
        current["list"] = next(lists)
        return True

    monkeypatch.setattr(audio.GATE, "restart", restart)
    assert audio._find_at_start("Microphone (K66)") == 1
    current["list"] = [(0, "Microsoft Sound Mapper - Input", 44100)]
    monkeypatch.setattr(audio.GATE, "restart", lambda sd: True)
    assert audio._find_at_start("Microphone (K66)") is None  # gone altogether: the default input, the flight goes on


def test_a_held_call_waits_for_the_answer_the_model_is_still_working_on():
    """Punta Cana Departure: "climbing 6,000" heard, the model nine seconds over its answer, and the SIGMET went out
    before "radar contact"."""
    from localtc.atc_core.engine import ANSWER_FIRST_S
    from localtc.atc_core.values import Phrase
    from test_unscripted import cruising_engine

    engine, own = cruising_engine()
    engine.handle(own)
    center = engine.state.comms.tuned
    t = own.t + 4
    engine.state.comms.last_pilot_t = t  # the check-in: the model still working on its answer
    engine._deferred = object()
    engine._schedule(t + 1, "common.info", {"message": Phrase("convective SIGMET", "convective sigmet")}, center,
                     delay=False, expects_readback=False)
    assert engine._flush(t + 1) == []
    engine._deferred = None  # "radar contact" said
    assert [tx.instruction_id for tx in engine._flush(t + 10)] == ["common.info"]
    # No answer at all: it goes after ANSWER_FIRST_S
    engine2, own2 = cruising_engine()
    engine2.handle(own2)
    t2 = own2.t + 4
    engine2.state.comms.last_pilot_t = t2
    engine2._deferred = object()  # (and never answered)
    engine2._schedule(t2 + 1, "common.info", {"message": Phrase("x", "x")}, engine2.state.comms.tuned, delay=False,
                      expects_readback=False)
    assert engine2._flush(t2 + 1) == []
    assert [tx.instruction_id for tx in engine2._flush(t2 + ANSWER_FIRST_S + 1)] == ["common.info"]
