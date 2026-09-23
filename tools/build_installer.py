"""Build LocalTC-Setup.exe with NSIS (installer/localtc.nsi). makensis runs on Windows, macOS
(``brew install makensis``) and Linux (``apt install nsis``).

    python tools/build_installer.py                 # the release's setup: fetches the latest GitHub release
    python tools/build_installer.py --offline       # a test build: carries the committed source (git HEAD)
                                                    #   and needs no release; lands in installer/
    python tools/build_installer.py --out dist/LocalTC-Setup.exe
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "installer" / "localtc.nsi"


def version() -> str:
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "src/localtc/__init__.py").read_text(encoding="utf-8")).group(1)


def makensis() -> str:
    found = shutil.which("makensis")
    for candidate in (found, *(Path(os.environ.get(v, "")) / "NSIS" / "makensis.exe"
                               for v in ("ProgramFiles(x86)", "ProgramFiles"))):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    sys.exit("makensis not found: install NSIS (brew install makensis, choco install nsis, apt install nsis)")


def build(out: Path, offline: bool) -> Path:
    v = version()
    numeric = re.match(r"\d+\.\d+\.\d+", v).group(0)  # the exe's version resource wants numbers only
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        licence = Path(tmp) / "LICENSE.txt"  # the licence page wants Windows line endings
        licence.write_bytes((ROOT / "LICENSE").read_text(encoding="utf-8").replace("\r\n", "\n")
                            .replace("\n", "\r\n").encode("utf-8"))
        defines = {"AppVersion": numeric, "OutFile": str(out), "LicenseFile": str(licence)}
        if offline:
            source = Path(tmp) / f"LocalTC-{v}.zip"
            subprocess.run(["git", "archive", "--format=zip", f"--prefix=LocalTC-{v}/", "-o", str(source), "HEAD"],
                           cwd=ROOT, check=True)
            defines["SourceZip"] = str(source)
        flag = "/" if os.name == "nt" else "-"
        cmd = [makensis(), f"{flag}V2", *(f"{flag}D{k}={val}" for k, val in defines.items()), str(SCRIPT)]
        subprocess.run(cmd, check=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="carry the committed source instead of downloading a release")
    parser.add_argument("--out", type=Path, help="where the exe goes (default: installer/ for --offline, else dist/)")
    args = parser.parse_args()
    out = args.out or ROOT / ("installer" if args.offline else "dist") / "LocalTC-Setup.exe"
    built = build(out, args.offline)
    print(f"{built} ({built.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
