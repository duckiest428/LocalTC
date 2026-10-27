"""What LocalTC uses beside its own package, installed from inside the app: Ollama (the local language model's
engine), the CUDA libraries that put Whisper on an NVIDIA card, and Edge WebView2 (the app's window).

LocalTC-Setup.exe installs only LocalTC itself (Python and its packages, which it carries). These come after, from the
setup guide or Quick Settings, each when the pilot asks for it, with its progress shown. Windows only: elsewhere they
are installed by hand (``status`` says so), and a git checkout's ``Install LocalTC.cmd`` still installs everything.

Every download is over HTTPS from its maker (ollama.com, Microsoft, PyPI), and an installer only runs once Windows
has checked its signature.
"""

import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from localtc import __version__
from localtc.config import data_dir

log = logging.getLogger(__name__)

Progress = Callable[[str, float | None], None]  # (message, fraction 0-1 or None when unknown)

OLLAMA_SETUP = "https://ollama.com/download/OllamaSetup.exe"
WEBVIEW2_SETUP = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"  # the Evergreen bootstrapper
# The CUDA 12 / cuDNN 9 libraries ctranslate2 needs (pyproject.toml's "cuda" extra, which is read when it's there).
CUDA_PACKAGES = ("nvidia-cublas-cu12>=12", "nvidia-cudnn-cu12>=9,<10")
_WEBVIEW2_KEYS = (
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ("HKEY_CURRENT_USER", r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Component:
    id: str  # ollama, gpu, webview2
    label: str
    installed: bool
    needed: bool  # worth installing on this computer
    size: str  # the download, in words
    detail: str = ""
    supported: bool = True  # False: not installable from the app here (not Windows)


def status(*, platform: str = sys.platform, gpu: str | None = None) -> list[Component]:
    """Each component, and whether it's there. ``gpu``: the NVIDIA card's name ("" for none; None to look)."""
    windows = platform == "win32"
    if gpu is None:
        from localtc.models import _nvidia

        gpu = _nvidia()[0]
    exe = ollama_exe()
    ollama = Component("ollama", "Ollama, the engine for the language model on this PC", exe is not None, True,
                       "about 1 GB", "" if exe else "Without it LocalTC understands you with its grammar only, or the cloud model.",
                       supported=windows)
    if not windows and exe is None:
        ollama.detail = "Install it from ollama.com."
    cuda = cuda_installed()
    gpu_part = Component("gpu", "Whisper on your graphics card", cuda, bool(gpu), "about 1 GB",
                         f"{gpu}: hears you faster and more accurately." if gpu else "Needs an NVIDIA graphics card.",
                         supported=windows)
    view = webview2_installed() if windows else True
    window = Component("webview2", "Microsoft Edge WebView2, the app's window", view, windows, "about 2 MB",
                       "" if view else "Without it LocalTC opens in your browser.", supported=windows)
    return [ollama, gpu_part, window]


def to_dicts(parts: list[Component]) -> list[dict]:
    return [asdict(p) for p in parts]


def install(component: str, progress: Progress | None = None) -> bool:
    say = progress or (lambda message, fraction: None)
    if sys.platform != "win32":
        say("Install it by hand on this system", None)
        return False
    if component == "ollama":
        return install_ollama(say)
    if component == "gpu":
        return install_cuda(say)
    if component == "webview2":
        return install_webview2(say)
    raise ValueError(f"unknown component {component!r}")


# --- Ollama -------------------------------------------------------------------------------------------------------


def ollama_exe() -> Path | None:
    found = shutil.which("ollama")
    if found:
        return Path(found)
    local = os.environ.get("LOCALAPPDATA")
    if local and (Path(local) / "Programs" / "Ollama" / "ollama.exe").is_file():
        return Path(local) / "Programs" / "Ollama" / "ollama.exe"
    return None


def install_ollama(say: Progress) -> bool:
    if ollama_exe() is None:
        setup = _download(OLLAMA_SETUP, "OllamaSetup.exe", "Ollama", say)
        _check_signature(setup)
        say("Installing Ollama ...", None)
        # Inno Setup's silent install, for this user (no administrator rights).
        done = subprocess.run([str(setup), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES", "/SP-"],
                              creationflags=NO_WINDOW, timeout=900)
        setup.unlink(missing_ok=True)
        if done.returncode != 0 or ollama_exe() is None:
            say(f"Ollama's installer stopped (exit code {done.returncode}): install it from ollama.com", None)
            return False
    if not start_ollama(wait_s=30):
        say("Ollama is installed but didn't start: start the Ollama app", None)
        return False
    say("Ollama is running", 1.0)
    return True


def ollama_running(timeout_s: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=timeout_s):
            return True
    except OSError:
        return False


def start_ollama(*, wait_s: float = 15.0, running: Callable[[], bool] = ollama_running) -> bool:
    """Start Ollama's server if it's installed and not running yet (after a restart its tray app usually has).
    True once it answers."""
    if running():
        return True
    exe = ollama_exe()
    if exe is None:
        return False
    try:
        subprocess.Popen([str(exe), "serve"], creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except OSError as exc:
        log.info("Couldn't start Ollama: %s", exc)
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if running():
            return True
        time.sleep(0.5)
    return False


# --- Whisper on the GPU -------------------------------------------------------------------------------------------


def cuda_installed() -> bool:
    try:
        return importlib.util.find_spec("nvidia.cublas") is not None and importlib.util.find_spec("nvidia.cudnn") is not None
    except (ImportError, ValueError):
        return False


def cuda_requirements(root: Path | None = None) -> list[str]:
    """The "cuda" extra from pyproject.toml beside the app (the same list the installer and updates use)."""
    from localtc.update import install_root

    try:
        project = tomllib.loads(((root or install_root()) / "pyproject.toml").read_text(encoding="utf-8"))
        wanted = project["project"]["optional-dependencies"]["cuda"]
        return [r.split(";")[0].strip() for r in wanted] or list(CUDA_PACKAGES)
    except (OSError, KeyError, ValueError):
        return list(CUDA_PACKAGES)


def install_cuda(say: Progress) -> bool:
    """pip, into the app's own environment: the libraries are Python packages (the update keeps them)."""
    say("Downloading the CUDA libraries (about 1 GB) ...", None)
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off",
           *cuda_requirements()]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=NO_WINDOW)
    tail = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        tail = (tail + [line])[-5:]
        if line.startswith(("Collecting", "Downloading", "Installing")):
            say(line[:120], None)
    if proc.wait() != 0:
        say(f"pip failed: {tail[-1] if tail else 'no output'}", None)
        return False
    say("Whisper uses the graphics card from the next flight", 1.0)
    return True


# --- WebView2 -----------------------------------------------------------------------------------------------------


def webview2_installed() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    for hive, key in _WEBVIEW2_KEYS:
        try:
            with winreg.OpenKey(getattr(winreg, hive), key) as k:
                version, _ = winreg.QueryValueEx(k, "pv")
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def install_webview2(say: Progress) -> bool:
    setup = _download(WEBVIEW2_SETUP, "MicrosoftEdgeWebview2Setup.exe", "WebView2", say)
    _check_signature(setup)
    say("Installing WebView2 ...", None)
    done = subprocess.run([str(setup), "/silent", "/install"], creationflags=NO_WINDOW, timeout=900)
    setup.unlink(missing_ok=True)
    if done.returncode != 0 and not webview2_installed():
        say(f"WebView2's installer stopped (exit code {done.returncode})", None)
        return False
    say("WebView2 is installed: the window is used from LocalTC's next start", 1.0)
    return True


# --- downloads ----------------------------------------------------------------------------------------------------


def _download(url: str, name: str, what: str, say: Progress) -> Path:
    target = data_dir() / "downloads" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    request = urllib.request.Request(url, headers={"User-Agent": f"LocalTC/{__version__}"})
    with urllib.request.urlopen(request, timeout=30) as response, partial.open("wb") as out:
        if not response.geturl().startswith("https://"):
            raise RuntimeError(f"{what} was offered over an insecure address")
        total = int(response.headers.get("Content-Length") or 0)
        got, last = 0, 0.0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            got += len(chunk)
            if time.monotonic() - last > 0.5:
                last = time.monotonic()
                mb = f"{got // 2**20} of {total // 2**20} MB" if total else f"{got // 2**20} MB"
                say(f"Downloading {what}: {mb}", got / total if total else None)
    partial.replace(target)
    return target


def _check_signature(path: Path) -> None:
    """Windows' own check of the installer's Authenticode signature: it must be valid before it runs."""
    script = f"(Get-AuthenticodeSignature -LiteralPath '{path}').Status.ToString()"
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
    if out.stdout.strip() != "Valid":
        path.unlink(missing_ok=True)
        raise RuntimeError(f"{path.name} isn't validly signed ({out.stdout.strip() or out.stderr.strip()}): not run")
