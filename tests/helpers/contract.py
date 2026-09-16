"""The SimSource contract, shared by the replay tests and the live Windows test.

Any source that passes can be swapped in via config without downstream changes.
"""

import asyncio
import math

from localtc.sim_api import BUS_EVENT_TYPES, SessionInfo, SimSource, decode_event, encode_event


async def check_source_contract(
    source: SimSource, *, min_events: int = 1, max_events: int = 200, timeout: float = 10.0
) -> tuple[SessionInfo, list]:
    assert isinstance(source, SimSource), "must structurally implement SimSource"

    info = await asyncio.wait_for(source.start(), timeout)
    assert isinstance(info, SessionInfo)
    assert info.source_kind in ("live", "replay")

    events: list = []

    async def collect() -> None:
        async for ev in source.events():
            events.append(ev)
            if len(events) >= max_events:
                break

    try:
        await asyncio.wait_for(collect(), timeout)
    except TimeoutError:
        pass  # a live source never ends by itself; judge what arrived

    assert len(events) >= min_events, f"expected at least {min_events} events, got {len(events)}"
    last_t = -math.inf
    for ev in events:
        assert isinstance(ev, BUS_EVENT_TYPES), f"unexpected event type {type(ev)}"
        assert ev.t >= last_t, f"t went backwards: {last_t} -> {ev.t}"
        last_t = ev.t
        assert decode_event(encode_event(ev)) == ev, "event must survive a JSON round-trip"
    assert source.clock.now() >= events[-1].t - 1e-9

    await source.stop()

    async def drain() -> None:
        async for _ in source.events():
            pass

    await asyncio.wait_for(drain(), timeout)  # events() must end after stop()
    return info, events
