import asyncio

import pytest

from localtc.bus import EventBus, pump
from localtc.sim_api import PttPressed, SimLifecycle


def test_fan_out_and_type_filter():
    async def main():
        bus = EventBus()
        everything = bus.subscribe()
        radio_only = bus.subscribe(PttPressed)
        bus.publish(SimLifecycle(t=0.0, kind="sim_start"))
        bus.publish(PttPressed(t=1.0))
        bus.close()
        return [e async for e in everything], [e async for e in radio_only]

    everything, radio_only = asyncio.run(main())
    assert [type(e) for e in everything] == [SimLifecycle, PttPressed]
    assert [type(e) for e in radio_only] == [PttPressed]


def test_bounded_subscriber_drops_oldest():
    async def main():
        bus = EventBus()
        sub = bus.subscribe(maxsize=2)
        for i in range(5):
            bus.publish(PttPressed(t=float(i)))
        sub.close()
        return [e.t async for e in sub], sub.dropped

    times, dropped = asyncio.run(main())
    assert times == [4.0]  # the close sentinel also needed a slot
    assert dropped == 4


def test_closed_subscription_stays_closed_and_bus_rejects_new_subscribers():
    async def main():
        bus = EventBus()
        sub = bus.subscribe()
        bus.close()
        assert [e async for e in sub] == []
        assert [e async for e in sub] == []
        with pytest.raises(RuntimeError):
            bus.subscribe()

    asyncio.run(main())


def test_pump_forwards_until_source_ends():
    class ListSource:
        async def events(self):
            for i in range(3):
                yield PttPressed(t=float(i))

    async def main():
        bus = EventBus()
        sub = bus.subscribe()
        count = await pump(ListSource(), bus)
        bus.close()
        return count, [e async for e in sub]

    count, events = asyncio.run(main())
    assert count == 3 and len(events) == 3
