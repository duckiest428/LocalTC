"""Commands a consumer can send to a SimSource."""

from typing import Union

import msgspec


class SimCommand(msgspec.Struct, frozen=True, kw_only=True, tag_field="command"):
    pass


class RequestAirportData(SimCommand, tag="request_airport_data"):
    """Fetch an airport's layout; the source answers with an ``AirportData`` event."""

    icao: str


class RequestArrival(SimCommand, tag="request_arrival"):
    """Fetch an airport's arrival procedure (STAR) by name, with its restrictions; answered with ``ArrivalData``."""

    icao: str
    name: str


class SetComFrequency(SimCommand, tag="set_com_frequency"):
    """Tune a COM radio's active frequency (the copilot changing frequencies)."""

    hz: int  # e.g. 120425000; 25 kHz channel names like "120.42" must already be expanded
    radio: int = 1


class SendSimEvent(SimCommand, tag="send_sim_event"):
    """A key event, as the sim's controls send it ("GEAR_DOWN", "FLAPS_SET" with 0-16383, "HEADING_BUG_SET" with
    270): the copilot working the aircraft (localtc.crew)."""

    name: str
    value: int = 0
    index: int = 0  # which one, for events that take it second (KOHLSMAN_SET: 2 is the first officer's altimeter)


class SetInputEvent(SimCommand, tag="set_input_event"):
    """Move a cockpit control through its MSFS 2024 input event (a B: var, "LIGHTING_LANDING_1"), the way the
    aircraft's own switches do: what an aircraft whose panel ignores the old key events needs. The name as the
    aircraft lists it (case doesn't matter)."""

    name: str
    value: float = 0.0


class TurnKnob(SimCommand, tag="turn_knob"):
    """Turn a knob that moves a step at a time (an MSFS 2024 FCU knob's input event, +1/-1 whatever it's set to)
    until ``var`` reads ``target``: read, a few steps, read again (sim_bridge/knob.py). ``step``: one step's change of
    the reading; ``wrap`` 360 for a heading."""

    name: str
    var: str
    unit: str
    target: float
    step: float = 1.0
    wrap: float = 0.0


class SpawnAiAircraft(SimCommand, tag="spawn_ai_aircraft"):
    """EXPERIMENTAL (traffic control): put an AI aircraft in the sim. ``kind`` "parked": still, where it's put (a
    non-ATC aircraft at its stand); "enroute": flying ``plan`` (a .PLN path, without the extension) under the sim's
    own AI, from ``plan_position`` along it. The sim answers with ``AiObjectAssigned``."""

    request_id: int
    kind: str  # parked, enroute
    title: str
    livery: str = ""
    tail: str = ""
    flight_number: int = -1
    lat: float = 0.0
    lon: float = 0.0
    alt_ft: float = 0.0
    heading: float = 0.0
    on_ground: bool = True
    airspeed_kt: float = 0.0
    plan: str = ""
    plan_position: float = 0.0


class RemoveAiAircraft(SimCommand, tag="remove_ai_aircraft"):
    """EXPERIMENTAL: take out an aircraft LocalTC created (only those: the sim's own can't be removed)."""

    object_id: int


class EnumerateModels(SimCommand, tag="enumerate_models"):
    """EXPERIMENTAL: ask for the installed aircraft and liveries (answered with ``ModelList``)."""


class SetSimVar(SimCommand, tag="set_sim_var"):
    """Write a variable an aircraft exposes, for the switches its key events don't reach: an add-on's L:var
    ("L:INI_SEATBELTS_SWITCH") in MSFS 2024."""

    name: str
    unit: str = "number"
    value: float = 0.0


AnySimCommand = Union[RequestAirportData, RequestArrival, SetComFrequency, SendSimEvent, SetSimVar, SetInputEvent, TurnKnob,
                      SpawnAiAircraft, RemoveAiAircraft, EnumerateModels]
