"""A keyboard key as the push-to-talk switch, working while another window (the sim) has focus.

Uses pynput's global keyboard hook. Windows needs nothing; macOS asks to allow Input Monitoring
for the terminal the first time. Joystick buttons go through SimConnect instead (the bridge's
``live.ptt_input``), and ``ptt = "enter"`` uses the terminal with no permissions at all.
"""

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)


def parse_key(name: str):
    """ "ctrl_r", "f13", "scroll_lock", "caps_lock", or a single character like "t"."""
    from pynput import keyboard

    name = name.strip().lower()
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    try:
        return keyboard.Key[name]
    except KeyError:
        raise ValueError(f"unknown push-to-talk key {name!r}; use e.g. ctrl_r, alt_r, f13, scroll_lock, or a letter") \
            from None


class KeyboardPtt:
    def __init__(self, key: str, on_down: Callable[[], None], on_up: Callable[[], None]) -> None:
        self.key = parse_key(key)
        self.key_name = key
        self._on_down, self._on_up = on_down, on_up
        self._held = False
        self._listener = None

    def start(self) -> None:
        from pynput import keyboard

        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self._listener.daemon = True
        self._listener.start()
        log.info("Push-to-talk: hold %s", self.key_name, extra={"console": True})

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()

    def _matches(self, key) -> bool:
        return key == self.key or (hasattr(key, "char") and hasattr(self.key, "char") and key.char
                                   and self.key.char and key.char.lower() == self.key.char)

    def _press(self, key) -> None:
        if self._matches(key) and not self._held:  # ignore auto-repeat while held
            self._held = True
            self._on_down()

    def _release(self, key) -> None:
        if self._matches(key) and self._held:
            self._held = False
            self._on_up()
