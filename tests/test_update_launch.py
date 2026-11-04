"""The script that installs an update really runs."""

import subprocess
import sys
import time

import pytest

from localtc import update


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' PowerShell")
def test_the_update_script_is_really_run(tmp_path, monkeypatch):
    """Started with DETACHED_PROCESS, PowerShell exited at once: the app closed and nothing was installed."""
    done = tmp_path / "ran.txt"
    monkeypatch.setattr(update, "updates_dir", lambda: tmp_path)
    monkeypatch.setattr(update, "can_apply", lambda root: (True, ""))
    monkeypatch.setattr(update, "apply_script", lambda *a, **k: f"Set-Content '{done}' 'ran'\r\n")
    started = []
    popen = subprocess.Popen
    monkeypatch.setattr(update.subprocess, "Popen", lambda *a, **k: started.append(popen(*a, **k)) or started[-1])
    update.launch_apply(tmp_path, relaunch=False, root=tmp_path)
    # (A cold PowerShell on a busy CI runner can take a long while to start: only its having ended without doing it is a failure.)
    deadline = time.monotonic() + 90
    while not done.exists() and time.monotonic() < deadline and started[0].poll() is None:
        time.sleep(0.2)
    time.sleep(0.5)  # (it may have just ended, having written it)
    assert done.exists(), f"PowerShell's exit code: {started[0].poll()} (None: still running after 90 s)"
    assert not subprocess.DETACHED_PROCESS & (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
