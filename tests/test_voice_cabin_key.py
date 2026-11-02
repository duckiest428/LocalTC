"""The cabin's own key: the same microphone, the crew's words, heard as the cabin's."""

from localtc.sim_api import IntercomHeard, IntercomPressed, IntercomReleased
from localtc.stt.service import CABIN, INTERCOM, _radio_of, _to


def test_the_key_says_who_is_being_talked_to():
    assert _to(CABIN) == "cabin" and _to(INTERCOM) == ""
    assert _radio_of(IntercomPressed(t=0.0, to="cabin")) == CABIN
    assert _radio_of(IntercomReleased(t=0.0)) == INTERCOM


def test_a_recording_from_before_the_key_reads_as_the_copilots():
    assert IntercomHeard(t=0.0, text="flaps two").to == ""
