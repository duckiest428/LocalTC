"""Release helpers for .github/workflows/release.yml.

    python tools/release.py check v0.2.0    # the tag, pyproject.toml, localtc.__version__ and CHANGELOG agree
    python tools/release.py notes 0.2.0     # that version's CHANGELOG section (the release notes)
"""

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localtc.update import changelog_section, parse_version


def versions() -> tuple[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    init = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/localtc/__init__.py").read_text(encoding="utf-8")).group(1)
    return project, init


def check(tag: str) -> int:
    version = tag.removeprefix("v")
    parse_version(version)
    project, init = versions()
    problems = [f"pyproject.toml says {project}"] if project != version else []
    problems += [f"localtc.__version__ says {init}"] if init != version else []
    if not changelog_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version):
        problems.append(f"CHANGELOG.md has no ## [{version}] section")
    for p in problems:
        print(f"{tag}: {p}", file=sys.stderr)
    return 1 if problems else 0


def notes(version: str) -> int:
    print(changelog_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version.removeprefix("v")))
    return 0


if __name__ == "__main__":
    command, arg = sys.argv[1], sys.argv[2]
    sys.exit({"check": check, "notes": notes}[command](arg))
