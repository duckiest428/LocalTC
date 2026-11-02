"""The plan's off-block time against the sim's clock: what the copilot and the cabin crew go by when the flight is
behind. A plan far from the clock (the sim set to another hour, a plan for tonight flown now) isn't a schedule anyone's
held to: ``off_block_in`` says None for it, and nobody comments.
"""

DAY_S = 86400
HELD_TO_S = 3 * 3600  # further from the sim's clock than this, the plan's time isn't this flight's


def from_clock_s(sched_out_s: int, zulu_s: float | None) -> float | None:
    """Seconds from the sim's clock to the plan's off-block (negative: gone by), the nearest way round the day; None
    when either isn't known."""
    if not sched_out_s or zulu_s is None:
        return None
    return (sched_out_s % DAY_S - zulu_s + DAY_S / 2) % DAY_S - DAY_S / 2


def off_block_in(sched_out_s: int, zulu_s: float | None) -> float | None:
    """``from_clock_s``, or None when the plan's too far from the clock to be held to."""
    away = from_clock_s(sched_out_s, zulu_s)
    return away if away is not None and abs(away) <= HELD_TO_S else None
