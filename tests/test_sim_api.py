import msgspec
import pytest

from localtc.sim_api import (
    BUS_EVENT_TYPES,
    AtcTransmission,
    OwnshipState,
    PttReleased,
    SessionClock,
    StreamClock,
    TrafficSnapshot,
    TrafficTarget,
    decode_event,
    encode_event,
    event_type,
)
from localtc.sim_api.units import (
    bcd16_to_mhz,
    bco16_to_squawk,
    round_mhz,
    squawk_to_bco16,
    xpdr_mode_name,
)


@pytest.mark.parametrize(
    ("raw", "squawk"),
    [(0x1200, "1200"), (0x7000, "7000"), (0x7700, "7700"), (0x0000, "0000"), (0x4567, "4567")],
)
def test_bco16_decode(raw, squawk):
    assert bco16_to_squawk(raw) == squawk
    assert squawk_to_bco16(squawk) == raw


def test_bco16_falls_back_to_decimal_digits():
    assert bco16_to_squawk(1200) == "1200"
    assert bco16_to_squawk(7000) == "7000"


def test_squawk_validation():
    with pytest.raises(ValueError):
        squawk_to_bco16("1280")


def test_bcd16_and_rounding():
    assert bcd16_to_mhz(0x2345) == pytest.approx(123.45)
    assert round_mhz(118.30000305) == 118.3
    assert round_mhz(124.67500001) == 124.675


def test_xpdr_mode_names():
    assert [xpdr_mode_name(i) for i in range(6)] == ["off", "standby", "test", "on", "alt", "ground"]
    assert xpdr_mode_name(99) == "off"


# Tags are the recording format: changing one breaks existing recordings.
def test_event_tags_are_stable():
    assert sorted(t.__struct_config__.tag for t in BUS_EVENT_TYPES) == [
        "aircraft_identity", "airport_data", "atc_alert", "atc_transmission", "connection_status", "llm_exchange",
        "ownship_state", "phase_changed", "ptt_pressed", "ptt_released", "radio_tuned", "readback_evaluated",
        "sim_lifecycle", "traffic_snapshot", "transcript",
    ]


def _ownship(**overrides):
    fields = dict(
        t=1.25, lat=47.9, lon=-122.28, alt_msl_ft=606.0, alt_indicated_ft=626.0, alt_agl_ft=0.0,
        altimeter_inhg=30.12, hdg_mag=324.0, hdg_true=340.0, ias_kt=0.0, gs_kt=0.0, vs_fpm=0.0,
        on_ground=True, squawk="1200", xpdr_mode="alt", com1_mhz=118.3, com2_mhz=121.5,
    )
    return OwnshipState(**(fields | overrides))


def test_round_trip_all_kinds():
    events = [
        _ownship(),
        TrafficSnapshot(t=2.0, targets=(TrafficTarget(object_id=5, lat=1.0, lon=2.0, alt_ft=3.0,
                                                      hdg_true=4.0, gs_kt=5.0, on_ground=False),)),
        PttReleased(t=3.0, audio_ref="audio/0001.wav"),
        AtcTransmission(t=4.0, station="Paine Tower", frequency_mhz=120.2, text="cleared for takeoff"),
    ]
    for ev in events:
        data = encode_event(ev)
        assert msgspec.json.decode(data)["type"] == event_type(ev)
        assert decode_event(data) == ev


def test_decode_rejects_unknown_type():
    with pytest.raises(msgspec.ValidationError):
        decode_event(b'{"type":"from_the_future","t":1.0}')


def test_clocks():
    now = [100.0]
    clock = SessionClock(lambda: now[0])
    now[0] = 102.5
    assert clock.now() == 2.5
    clock.reset()
    assert clock.now() == 0.0

    stream = StreamClock()
    stream.advance(5.0)
    stream.advance(3.0)
    assert stream.now() == 5.0
