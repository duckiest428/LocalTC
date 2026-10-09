"""A yoke or joystick button as the push-to-talk or intercom switch, read straight from Windows.

MSFS 2024 accepts a button bound through SimConnect (MapInputEventToClientEvent) and then never sends its presses, so
the buttons are read here instead: Windows' joystick API (winmm) answers whichever window has focus, the sim's
included, and leaves the button to the sim as well. Buttons are named as MSFS names them, ``joystick:<n>:button:<b>``:
the n-th controller Windows lists, its button b counted from 0.
"""

import ctypes
import logging
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

POLL_S = 0.01  # 100 times a second: a press is never missed and costs nothing
RETRY_S = 2.0  # a controller unplugged: looked for again this often
MAX_DEVICES = 16
NAME = re.compile(r"^\s*joystick\s*:\s*(\d+)\s*:\s*button\s*:\s*(\d+)\s*$", re.I)


def parse_button(name: str) -> tuple[int, int]:
    """ "joystick:0:button:3" -> (0, 3)."""
    m = NAME.match(name or "")
    if not m:
        raise ValueError(f"not a joystick button: {name!r} (it's written joystick:0:button:3)")
    return int(m.group(1)), int(m.group(2))


def button_name(device: int, button: int) -> str:
    return f"joystick:{device}:button:{button}"


if sys.platform == "win32":
    from ctypes import wintypes

    class _JoyInfoEx(ctypes.Structure):
        _fields_ = [(n, wintypes.DWORD) for n in (
            "dwSize dwFlags dwXpos dwYpos dwZpos dwRpos dwUpos dwVpos dwButtons dwButtonNumber dwPOV dwReserved1 "
            "dwReserved2").split()]

    class _JoyCaps(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("szPname", ctypes.c_wchar * 32)] + [
            (n, ctypes.c_uint) for n in ("wXmin wXmax wYmin wYmax wZmin wZmax wNumButtons wPeriodMin wPeriodMax wRmin "
                                         "wRmax wUmin wUmax wVmin wVmax wCaps wMaxAxes wNumAxes wMaxButtons").split()
        ] + [("szRegKey", ctypes.c_wchar * 32), ("szOEMVxD", ctypes.c_wchar * 260)]

    JOY_RETURNBUTTONS = 0x80


class _Winmm:
    def __init__(self) -> None:
        self.lib = ctypes.WinDLL("winmm")

    def buttons(self, device: int) -> int | None:
        """The device's buttons as bits (button 0 is bit 0), or None if it isn't there."""
        info = _JoyInfoEx()
        info.dwSize, info.dwFlags = ctypes.sizeof(info), JOY_RETURNBUTTONS
        if self.lib.joyGetPosEx(device, ctypes.byref(info)) != 0:
            return None
        return info.dwButtons

    def caps(self, device: int) -> "_JoyCaps | None":
        caps = _JoyCaps()
        if self.lib.joyGetDevCapsW(device, ctypes.byref(caps), ctypes.sizeof(caps)) != 0:
            return None
        return caps


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    buttons: int


def _oem_name(vid: int, pid: int) -> str:
    """The controller's own name ("T.Flight Hotas One"): winmm only says "Microsoft PC-joystick driver"."""
    import winreg

    path = rf"System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick\OEM\VID_{vid:04X}&PID_{pid:04X}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, path) as key:
                return str(winreg.QueryValueEx(key, "OEMName")[0])
        except OSError:
            continue
    return ""


def devices() -> list[Device]:
    """The controllers Windows has connected, numbered as MSFS numbers them."""
    if sys.platform != "win32":
        return []
    try:
        mm = _Winmm()
    except OSError:
        return []
    found = []
    for i in range(MAX_DEVICES):
        if mm.buttons(i) is None or (caps := mm.caps(i)) is None:
            continue
        name = _oem_name(caps.wMid, caps.wPid) or caps.szPname or f"Controller {i}"
        found.append(Device(i, name, int(caps.wNumButtons)))
    return found


def detect(timeout_s: float = 15.0, stop: threading.Event | None = None) -> str | None:
    """The first button pressed (one already held doesn't count) on any controller, as MSFS names it."""
    if sys.platform != "win32":
        return None
    mm = _Winmm()
    held = {i: b for i in range(MAX_DEVICES) if (b := mm.buttons(i)) is not None}
    end = time.monotonic() + timeout_s
    while time.monotonic() < end and not (stop is not None and stop.is_set()):
        for i, before in list(held.items()):
            now = mm.buttons(i)
            if now is None:
                continue
            pressed = now & ~before
            if pressed:
                return button_name(i, (pressed & -pressed).bit_length() - 1)
            held[i] = now
        time.sleep(POLL_S)
    return None


class JoystickButtons:
    """Buttons held to talk: ``bind`` each with its press and release, then ``start``. One thread polls them all."""

    def __init__(self) -> None:
        self._bound: list[tuple[int, int, Callable[[], None], Callable[[], None], str]] = []
        self._held: dict[tuple[int, int], bool] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def bind(self, name: str, on_down: Callable[[], None], on_up: Callable[[], None], what: str) -> None:
        device, button = parse_button(name)
        self._bound.append((device, button, on_down, on_up, what))

    def start(self) -> bool:
        if not self._bound:
            return False
        if sys.platform != "win32":
            log.warning("Joystick buttons are read on Windows only")
            return False
        self._thread = threading.Thread(target=self._run, name="joystick", daemon=True)
        self._thread.start()
        for device, button, _, _, what in self._bound:
            log.info("%s: hold %s", what, button_name(device, button), extra={"console": True})
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        mm = _Winmm()
        devices_used = sorted({device for device, *_ in self._bound})
        retry_at: dict[int, float] = {}  # a controller not connected -> when to look for it again
        while not self._stop.is_set():
            now = time.monotonic()
            states: dict[int, int | None] = {}
            for device in devices_used:
                if retry_at.get(device, 0) > now:
                    states[device] = None
                    continue
                states[device] = bits = mm.buttons(device)
                if bits is None:
                    if device not in retry_at:
                        log.warning("No controller %d connected for the talk buttons (looking again every %.0f s)",
                                    device, RETRY_S)
                    retry_at[device] = now + RETRY_S
                elif retry_at.pop(device, None) is not None:
                    log.info("Controller %d connected", device)
            for device, button, on_down, on_up, what in self._bound:
                bits = states[device]
                down = bits is not None and bool(bits >> button & 1)  # unplugged while held: released
                if down != self._held.get((device, button), False):
                    self._held[(device, button)] = down
                    try:
                        (on_down if down else on_up)()
                    except Exception:  # noqa: BLE001  the switch must keep working
                        log.exception("%s: handler failed", what)
            self._stop.wait(POLL_S)
