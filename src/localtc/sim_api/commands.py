"""Commands a consumer can send to a SimSource."""

from typing import Union

import msgspec


class SimCommand(msgspec.Struct, frozen=True, kw_only=True, tag_field="command"):
    pass


class RequestAirportData(SimCommand, tag="request_airport_data"):
    """Fetch an airport's layout; the source answers with an ``AirportData`` event."""

    icao: str


AnySimCommand = Union[RequestAirportData]
