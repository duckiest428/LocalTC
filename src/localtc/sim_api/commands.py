"""Commands a consumer can send to a SimSource."""

from typing import Union

import msgspec


class SimCommand(msgspec.Struct, frozen=True, kw_only=True, tag_field="command"):
    pass


class RequestAirportData(SimCommand, tag="request_airport_data"):
    """Fetch an airport's layout; the source answers with an ``AirportData`` event."""

    icao: str


class SetComFrequency(SimCommand, tag="set_com_frequency"):
    """Tune a COM radio's active frequency (the copilot changing frequencies)."""

    hz: int  # e.g. 120425000; 25 kHz channel names like "120.42" must already be expanded
    radio: int = 1


AnySimCommand = Union[RequestAirportData, SetComFrequency]
