"""The ramp's frequency: worldwide, the same at every airport. Here so the ATC and the ground crew agree what it is."""

RAMP_MHZ = 119.999
# 119.999 is on neither radio grid: a set frequency is taken as it is by the sim, but a radio's own knobs stop at 119.990
# or 120.000. Those count as the ramp too (a real controller on one of them always comes first).
RAMP_WINDOW_MHZ = 0.0105


def on_ramp(mhz: float | None, ramp_mhz: float = RAMP_MHZ) -> bool:
    return mhz is not None and abs(mhz - ramp_mhz) <= RAMP_WINDOW_MHZ
