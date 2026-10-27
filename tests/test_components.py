"""What the app installs beside itself (localtc.components), and the setup that leaves those to it."""

import sys
import venv
from pathlib import Path

import pytest

from localtc import components, update

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "install"))
import setup_env  # noqa: E402 - install/setup_env.py, the setup's environment script


def test_each_component_is_listed_with_whether_it_is_worth_installing():
    parts = {c.id: c for c in components.status(platform="win32", gpu="")}
    assert set(parts) == {"ollama", "gpu", "webview2"}
    assert parts["gpu"].needed is False  # no NVIDIA card: nothing to put Whisper on
    assert parts["ollama"].needed and parts["ollama"].supported
    with_card = {c.id: c for c in components.status(platform="win32", gpu="NVIDIA GeForce RTX 4070")}
    assert with_card["gpu"].needed and "RTX 4070" in with_card["gpu"].detail


def test_off_windows_nothing_is_installed_from_the_app():
    parts = components.status(platform="darwin", gpu="")
    assert not any(c.supported for c in parts)
    if sys.platform != "win32":
        assert components.install("ollama") is False


def test_the_gpu_libraries_are_the_cuda_extra():
    wanted = components.cuda_requirements(ROOT)
    assert any(r.startswith("nvidia-cublas-cu12") for r in wanted)
    assert any(r.startswith("nvidia-cudnn-cu12") for r in wanted)
    assert all(";" not in r for r in wanted)  # the markers are for pip on any system; the app is on Windows
    assert components.cuda_requirements(ROOT / "nowhere") == list(components.CUDA_PACKAGES)


def test_ollama_is_only_started_when_it_is_installed_and_not_answering(monkeypatch):
    monkeypatch.setattr(components, "ollama_exe", lambda: None)
    assert components.start_ollama(wait_s=0, running=lambda: True) is True
    assert components.start_ollama(wait_s=0, running=lambda: False) is False


def test_an_update_keeps_the_setups_python():
    assert {".venv", "runtime", "recordings"} <= set(update.KEEP_DIRS)
    script = update.apply_script(Path("C:/x/updates/0.5.5/tree"), Path("C:/LocalTC"), 1, relaunch=False)
    assert "runtime" in script and ".venv" in script


def test_the_setup_carries_its_scripts_and_leaves_the_rest_to_the_app():
    nsi = (ROOT / "installer" / "localtc.nsi").read_text(encoding="utf-8")
    assert "setup_env.py" in nsi and "shortcuts.ps1" in nsi
    assert "install.ps1" not in nsi and "bootstrap" not in nsi  # no console window, no install script
    assert "MUI_FINISHPAGE_RUN" in nsi and "localtc.ico" in nsi
    for name in ("setup_env.py", "shortcuts.ps1", "localtc.ico"):
        assert (ROOT / "install" / name).is_file()


def test_the_environment_is_made_on_the_carried_python(tmp_path):
    env = tmp_path / ".venv"
    runtime = Path(sys.executable).resolve().parent
    if sys.platform != "win32":  # setup_env is Windows' (Scripts\python.exe); its venv logic is checked by hand here
        pytest.skip("the environment's layout is Windows'")
    python = setup_env.environment(env, runtime)
    assert python.is_file()
    assert Path(setup_env.read_cfg(env / "pyvenv.cfg")["home"]).resolve() == runtime


def test_an_older_environment_on_another_python_is_made_again(tmp_path, monkeypatch):
    env = tmp_path / ".venv"
    (env / "Scripts").mkdir(parents=True)
    (env / "Scripts" / "python.exe").write_bytes(b"")
    (env / "pyvenv.cfg").write_text("home = C:\\Python311\nversion_info = 3.11.9\n", encoding="utf-8")
    made = []
    monkeypatch.setattr(venv.EnvBuilder, "create", lambda self, path: made.append((Path(path), self.upgrade)))
    setup_env.environment(env, tmp_path / "runtime")
    assert made == [(env, False)] and not (env / "pyvenv.cfg").exists()  # removed, then made fresh


def test_an_older_environment_on_the_same_python_is_kept_and_moved(tmp_path, monkeypatch):
    env = tmp_path / ".venv"
    (env / "Scripts").mkdir(parents=True)
    (env / "Scripts" / "python.exe").write_bytes(b"")
    (env / "Lib" / "site-packages" / "nvidia").mkdir(parents=True)
    major, minor = sys.version_info[:2]
    (env / "pyvenv.cfg").write_text(f"home = C:\\Python{major}{minor}\nversion_info = {major}.{minor}.4\n", encoding="utf-8")
    made = []
    monkeypatch.setattr(venv.EnvBuilder, "create", lambda self, path: made.append((Path(path), self.upgrade)))
    setup_env.environment(env, tmp_path / "runtime")
    assert made == [(env, True)]  # repointed onto the carried Python ...
    assert (env / "Lib" / "site-packages" / "nvidia").is_dir()  # ... with what was installed in it (the GPU libraries)
