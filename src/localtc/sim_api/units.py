"""Decoding for SimConnect value encodings. Pure Python so it's tested on any OS."""

XPDR_MODES = ("off", "standby", "test", "on", "alt", "ground")


def xpdr_mode_name(state: int) -> str:
    """Map ``TRANSPONDER STATE`` (0-5) to a mode name; unknown values read as "off"."""
    state = int(state)
    return XPDR_MODES[state] if 0 <= state < len(XPDR_MODES) else "off"


def bco16_to_squawk(value: int) -> str:
    """Decode a Bco16 transponder code: ``0x1200`` -> ``"1200"``.

    If the value isn't valid BCO but its decimal digits form a valid squawk
    (e.g. ``1200``), the sim evidently sent plain digits, so use those.
    """
    value = int(value) & 0xFFFF
    digits = [(value >> shift) & 0xF for shift in (12, 8, 4, 0)]
    if all(d <= 7 for d in digits):
        return "".join(str(d) for d in digits)
    decimal = f"{value:04d}"
    if len(decimal) == 4 and all(c in "01234567" for c in decimal):
        return decimal
    return "".join(f"{d:X}" for d in digits)


def squawk_to_bco16(code: str) -> int:
    if len(code) != 4 or any(c not in "01234567" for c in code):
        raise ValueError(f"invalid squawk {code!r}")
    return int(code, 16)


def bcd16_to_mhz(value: int) -> float:
    """Decode a BCD16 COM frequency: ``0x2345`` -> ``123.45``."""
    value = int(value) & 0xFFFF
    digits = f"{value:04X}"
    if not digits.isdigit():
        raise ValueError(f"invalid BCD16 frequency 0x{value:04X}")
    return 100 + int(digits) / 100


def round_mhz(mhz: float) -> float:
    """Round to 1 kHz, enough for 8.33 kHz channel spacing."""
    return round(mhz, 3)
