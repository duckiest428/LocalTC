"""PortAudio is restarted only with nothing playing: restarted under a voice on another thread, the heap was
corrupted and the app vanished (0xc0000374, a second after "Microphone reopened as device 3")."""

import threading
import time

from localtc.dsp.portaudio import Gate


class FakeSd:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _terminate(self) -> None:
        self.calls.append("terminate")

    def _initialize(self) -> None:
        self.calls.append("initialize")


def test_no_restart_while_a_voice_plays_and_one_once_its_done():
    gate, sd = Gate(), FakeSd()
    with gate.playing():
        assert not gate.restart(sd, wait_s=0.05) and sd.calls == []
    assert gate.restart(sd, wait_s=0.05) and sd.calls == ["terminate", "initialize"]


def test_a_restart_waits_for_the_voice_to_finish():
    gate, sd = Gate(), FakeSd()
    done = threading.Event()

    def voice() -> None:
        with gate.playing():
            time.sleep(0.2)
        done.set()

    threading.Thread(target=voice).start()
    time.sleep(0.05)
    assert gate.restart(sd, wait_s=2.0)
    assert done.is_set() and sd.calls == ["terminate", "initialize"]


def test_a_stream_kept_open_holds_it_off():
    gate, sd = Gate(), FakeSd()
    assert gate.hold()
    assert not gate.restart(sd, wait_s=0.05)
    gate.let_go()
    assert gate.restart(sd, wait_s=0.05)
