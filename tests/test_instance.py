"""One LocalTC app at a time (ui/instance.py): a second launch brings the first one's window up and ends."""

from localtc.ui.instance import Instance


def test_a_second_app_finds_the_first(tmp_path):
    first, second = Instance(tmp_path), Instance(tmp_path)
    assert first.acquire()
    first.publish("http://127.0.0.1:50123/")
    assert not second.acquire()
    assert second.running_url() == "http://127.0.0.1:50123/"
    first._file.close()  # the first one ends: the next launch is the app
    assert Instance(tmp_path).acquire()


def test_nothing_running_to_show(tmp_path):
    assert Instance(tmp_path).running_url() is None and not Instance(tmp_path).show_running()
