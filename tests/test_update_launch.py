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
    update.launch_apply(tmp_path, relaunch=False, root=tmp_path)
    for _ in range(100):
        if done.exists():
            break
        time.sleep(0.2)
    assert done.exists()
    assert not subprocess.DETACHED_PROCESS & (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
