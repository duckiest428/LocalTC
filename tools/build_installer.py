"""Build LocalTC-Setup.exe with NSIS (installer/localtc.nsi): an installer that carries LocalTC (the committed source,
git HEAD), a Python of its own and every package LocalTC needs as Windows wheels, so it installs without downloading
anything and without a console window. makensis runs on Windows, macOS (``brew install makensis``) and Linux.

    python tools/build_installer.py                 # dist/LocalTC-Setup.exe
    python tools/build_installer.py --release       # and the release's other files: LocalTC-X.Y.Z.zip (what the
                                                    #   app's updater installs), SHA256SUMS and notes.md
    python tools/build_installer.py --out installer/LocalTC-Setup.exe

The packages are resolved for 64-bit Windows and the carried Python's version with uv (``pip install uv``), which
reads pyproject.toml's environment markers for Windows wherever it runs, then fetched with pip. Downloads are kept in
build/installer-cache between builds.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "installer" / "localtc.nsi"
CACHE = ROOT / "build" / "installer-cache"
# The Python the setup carries: python.org's NuGet package (a whole Python with venv and pip, nothing to install).
PYTHON = "3.12.10"
PYTHON_URL = f"https://www.nuget.org/api/v2/package/python/{PYTHON}"
PYTHON_TAG = "312"
BUILD_REQUIREMENTS = ("hatchling", "editables")  # pip builds the editable install offline from these


def version() -> str:
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "src/localtc/__init__.py").read_text(encoding="utf-8")).group(1)


def makensis() -> str:
    found = shutil.which("makensis")
    for candidate in (found, *(Path(os.environ.get(v, "")) / "NSIS" / "makensis.exe"
                               for v in ("ProgramFiles(x86)", "ProgramFiles"))):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    sys.exit("makensis not found: install NSIS (brew install makensis, choco install nsis, apt install nsis)")


def uv() -> list[str]:
    if found := shutil.which("uv"):
        return [found]
    try:
        import uv as module  # noqa: F401 - installed with pip install uv

        return [sys.executable, "-m", "uv"]
    except ImportError:
        sys.exit("uv not found: pip install uv (it resolves the packages for Windows from any system)")


def runtime(into: Path) -> Path:
    """The carried Python, unpacked: the NuGet package's tools/ folder."""
    package = CACHE / f"python-{PYTHON}.nupkg"
    if not package.is_file():
        print(f"Downloading Python {PYTHON} (NuGet)")
        package.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(PYTHON_URL, timeout=120) as response:
            package.write_bytes(response.read())
    with zipfile.ZipFile(package) as z:
        for member in z.namelist():
            if member.startswith("tools/") and not member.endswith("/"):
                target = into / member[len("tools/"):]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(member))
    if not (into / "python.exe").is_file() or not (into / "Lib" / "venv").is_dir():
        sys.exit("The Python package isn't laid out as expected (no python.exe or venv)")
    return into


def wheels(into: Path, work: Path) -> Path:
    """Every package LocalTC needs on Windows, as wheels: resolved for Windows, downloaded, and the few published only
    as source built here (they must be pure Python)."""
    target = ["--python-platform", "x86_64-pc-windows-msvc", "--python-version", f"{PYTHON_TAG[0]}.{PYTHON_TAG[1:]}"]
    pinned = work / "requirements.txt"
    build_in = work / "build.in"
    build_in.write_text("\n".join(BUILD_REQUIREMENTS) + "\n", encoding="utf-8")
    subprocess.run([*uv(), "pip", "compile", str(ROOT / "pyproject.toml"), str(build_in), *target, "--quiet",
                    "--no-header", "--no-annotate", "-o", str(pinned)], check=True)
    cache = CACHE / "wheels"
    cache.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check", "--no-deps",
                    "--prefer-binary", "--platform", "win_amd64", "--python-version", PYTHON_TAG, "--implementation",
                    "cp", "--abi", f"cp{PYTHON_TAG}", "-d", str(cache), "-r", str(pinned)], check=True)
    wanted = {_name(line.split("==")[0]): line.split("==")[1].strip()
              for line in pinned.read_text(encoding="utf-8").splitlines() if "==" in line}
    into.mkdir(parents=True, exist_ok=True)
    for path in cache.iterdir():
        name, ver = _dist(path.name)
        if wanted.get(name) != ver:
            continue
        if path.suffix == ".whl":
            shutil.copy2(path, into / path.name)
            continue
        built = work / "built"
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--quiet", "--disable-pip-version-check", "--no-deps",
                        "-w", str(built), str(path)], check=True)
        for wheel in built.glob("*.whl"):
            if not wheel.name.endswith("-none-any.whl"):
                sys.exit(f"{path.name} has no Windows wheel and isn't pure Python")
            shutil.move(str(wheel), into / wheel.name)
    have = {_dist(p.name)[0] for p in into.glob("*.whl")}
    if missing := sorted(set(wanted) - have):
        sys.exit(f"No wheel for: {', '.join(missing)}")
    return into


def _name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dist(filename: str) -> tuple[str, str]:
    """("proxy-tools", "0.1.0") from a wheel's or an sdist's file name."""
    if filename.endswith(".whl"):
        name, ver = filename.split("-")[:2]
    else:
        stem = re.sub(r"\.(tar\.gz|zip|tar\.bz2)$", "", filename)
        name, _, ver = stem.rpartition("-")
    return _name(name), ver


def source(into: Path, work: Path) -> Path:
    """The committed source, as a release has it (.gitattributes decides what's in)."""
    archive = work / "source.zip"
    subprocess.run(["git", "archive", "--format=zip", "-o", str(archive), "HEAD"], cwd=ROOT, check=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(into)
    return into


def build(out: Path) -> Path:
    v = version()
    numeric = re.match(r"\d+\.\d+\.\d+", v).group(0)  # the exe's version resource wants numbers only
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        licence = work / "LICENSE.txt"  # the licence page wants Windows line endings
        licence.write_bytes((ROOT / "LICENSE").read_text(encoding="utf-8").replace("\r\n", "\n")
                            .replace("\n", "\r\n").encode("utf-8"))
        defines = {
            "AppVersion": numeric, "OutFile": str(out), "LicenseFile": str(licence),
            "SourceDir": str(source(work / "source", work)),
            "RuntimeDir": str(runtime(work / "runtime")),
            "WheelsDir": str(wheels(work / "wheels", work)),
        }
        flag = "/" if os.name == "nt" else "-"
        subprocess.run([makensis(), f"{flag}V2", *(f"{flag}D{k}={val}" for k, val in defines.items()), str(SCRIPT)],
                       check=True)
    return out


def release_files(setup: Path) -> None:
    """LocalTC-X.Y.Z.zip (what the app's updater fetches and installs), notes.md and SHA256SUMS, beside the setup."""
    v = version()
    dist = setup.parent
    archive = dist / f"LocalTC-{v}.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--prefix=LocalTC-{v}/", "-o", str(archive), "HEAD"],
                   cwd=ROOT, check=True)
    sys.path.insert(0, str(ROOT / "src"))
    from localtc.update import changelog_section

    (dist / "notes.md").write_text(changelog_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), v) + "\n",
                                   encoding="utf-8")
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in (archive, setup))
    (dist / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
    for p in (archive, dist / "notes.md", dist / "SHA256SUMS"):
        print(p)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "LocalTC-Setup.exe", help="where the exe goes")
    parser.add_argument("--release", action="store_true", help="also the release zip, SHA256SUMS and notes.md")
    args = parser.parse_args()
    built = build(args.out)
    print(f"{built} ({built.stat().st_size // 2**20} MB)")
    if args.release:
        release_files(built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
