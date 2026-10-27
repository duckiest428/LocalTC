"""LocalTC's Python environment, made by LocalTC-Setup.exe with the Python it carries (``<root>/runtime``).

The setup unpacks LocalTC, that Python and every package LocalTC needs (as wheels), then runs this, hidden, its
output in the setup's window:

    runtime\\python.exe -I install\\setup_env.py --root <install folder> --wheels <the wheels it carries>

It makes ``<root>/.venv`` on that Python (or moves an older install's environment onto it, keeping what's in it, such
as the GPU libraries), installs LocalTC into it from the wheels with nothing downloaded, and writes the LocalTC.cmd
launcher. Ollama, the models, GPU support and WebView2 are installed afterwards from the app (localtc.components).
Running it again repairs an install.
"""

import argparse
import shutil
import subprocess
import sys
import venv
from pathlib import Path

LAUNCHER = """@echo off
rem LocalTC from a console: the app, or a command, e.g.
rem   LocalTC.cmd run --source live --voice --destination CYQB --cruise-ft 12000
cd /d "%~dp0"
".venv\\Scripts\\localtc.exe" %*
if errorlevel 1 pause
"""


def read_cfg(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip().lower()] = value.strip()
    return out


def environment(env: Path, runtime: Path) -> Path:
    """The environment's python.exe, on ``runtime``: made, moved onto it, or (another Python version) made again."""
    python = env / "Scripts" / "python.exe"
    cfg = env / "pyvenv.cfg"
    if cfg.is_file() and python.is_file():
        info = read_cfg(cfg)
        version = info.get("version_info") or info.get("version") or ""
        same_minor = version.split(".")[:2] == [str(sys.version_info.major), str(sys.version_info.minor)]
        if same_minor:
            if Path(info.get("home", "")).resolve() != runtime.resolve():
                print("Moving LocalTC's environment onto the Python it now carries (what's installed in it stays)")
                venv.EnvBuilder(upgrade=True, with_pip=False).create(env)
            return python
        print(f"The environment is on Python {version}: making it again on Python {sys.version.split()[0]}")
        shutil.rmtree(env)
    elif env.exists():
        shutil.rmtree(env)  # a broken one
    print(f"Making LocalTC's environment (Python {sys.version.split()[0]})")
    venv.EnvBuilder(with_pip=True).create(env)
    return python


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--wheels", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / "pyproject.toml").is_file():
        print(f"{root} doesn't hold LocalTC (no pyproject.toml)")
        return 1
    python = environment(root / ".venv", Path(sys.executable).resolve().parent)
    print("Installing LocalTC and its packages (from the setup, nothing is downloaded) ...")
    done = subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(args.wheels),
                           "--disable-pip-version-check", "--no-warn-script-location", "--progress-bar", "off",
                           "--upgrade-strategy", "only-if-needed", "-e", str(root)])
    if done.returncode != 0:
        print(f"pip stopped with exit code {done.returncode}")
        return done.returncode
    check = subprocess.run([str(root / ".venv" / "Scripts" / "localtc.exe"), "--version"], capture_output=True, text=True)
    if check.returncode != 0:
        print(f"LocalTC doesn't start: {check.stderr.strip() or check.stdout.strip()}")
        return 1
    print(check.stdout.strip())
    (root / "LocalTC.cmd").write_text(LAUNCHER.replace("\n", "\r\n"), encoding="ascii", newline="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
