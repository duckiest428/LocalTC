"""The ramp's frequency: worldwide, the same at every airport. Here so the ATC and the ground crew agree what it is."""

RAMP_MHZ = 119.990  # a real 8.33 kHz channel, so a radio's own knobs reach it (a real controller on it always comes first)
RAMP_WINDOW_MHZ = 0.002  # what's read back isn't exact; the channels either side are real ones


def on_ramp(mhz: float | None, ramp_mhz: float = RAMP_MHZ) -> bool:
    return mhz is not None and abs(mhz - ramp_mhz) <= RAMP_WINDOW_MHZ
