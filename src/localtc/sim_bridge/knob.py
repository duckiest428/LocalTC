"""Turning a cockpit knob to a value: what an MSFS 2024 FCU needs (the stock Airbus's speed, heading and altitude knobs
are input events that move one step per frame, whatever value they're set to, and speed up when turned steadily).

So it goes in closed loop: read the variable the knob drives, turn it a few steps towards the target (fewer as it
nears), let it settle, read again, until it's there or it stops moving.
"""

from dataclasses import dataclass, field

STEP_GAP_S = 0.02  # one step a frame or so: sent faster, steps in the same frame count once
SETTLE_S = 0.35  # after a burst, before the next read: the knob's speed-up wears off
BURST = 20  # at most this many steps between reads (the speed-up builds after ~30)
MAX_READS = 40
READ_TIMEOUT_S = 2.0
STALLED_READS = 3  # the reading didn't move for this many bursts: the knob doesn't drive it, give up


@dataclass
class KnobTurn:
    name: str  # the input event
    target: float
    step: float  # what one step moves the reading (the smallest: 1 kt, 1 degree, 100 ft)
    wrap: float = 0.0  # 360 for a heading: the short way round
    reads: int = 0
    stalled: int = 0
    last: float | None = None
    pending: list[float] = field(default_factory=list)  # the steps still to send in this burst (+1/-1)
    next_t: float = 0.0
    reading: bool = False  # a read is out
    done: bool = False

    def error(self, value: float) -> float:
        err = self.target - value
        if self.wrap:
            err = (err + self.wrap / 2) % self.wrap - self.wrap / 2
        return err

    def on_value(self, value: float, now: float) -> None:
        """The variable as just read: turn towards the target, or stop."""
        self.reading = False
        self.reads += 1
        err = self.error(value)
        if abs(err) <= self.step / 2 or self.reads >= MAX_READS:
            self.done = True
            return
        self.stalled = self.stalled + 1 if self.last is not None and value == self.last else 0
        if self.stalled >= STALLED_READS:
            self.done = True
            return
        self.last = value
        steps = abs(err) / self.step
        n = min(BURST, int(steps) // 2) if steps > 4 else 1  # half the way at most: the speed-up overshoots
        self.pending = [1.0 if err > 0 else -1.0] * max(1, n)
        self.next_t = now

    def due(self, now: float) -> str | None:
        """What to do now: "step" (send ``pending.pop()``), "read", or None (wait)."""
        if self.done or now < self.next_t:
            return None
        if self.pending:
            self.next_t = now + (STEP_GAP_S if len(self.pending) > 1 else SETTLE_S)
            return "step"
        self.reading = True  # asked again if no answer comes in this long
        self.next_t = now + READ_TIMEOUT_S
        return "read"
