"""LocalTC's own package for the sim: the taxi guidance's arrow and stop bar (tools/make_taxi_marks.py makes them),
copied into the sim's Community folder when the guidance is turned on. The sim reads its packages when it starts: a
package new there shows from the sim's next start.
"""

import json
import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

PACKAGE = Path(__file__).with_name("localtc-taxi-guidance")
FILETIME_EPOCH_S = 11_644_473_600  # 1601 to 1970: layout.json's dates are Windows file times


def community_folders(roots: list[Path] | None = None) -> list[Path]:
    """The sim's Community folders on this PC (MSFS 2024's first)."""
    if roots is None:
        from localtc.crew.checklist_db import package_roots

        roots = package_roots()
    return [root / "Community" for root in roots if (root / "Community").is_dir()]


def _version(package: Path) -> str:
    try:
        return str(json.loads((package / "manifest.json").read_text(encoding="utf-8")).get("package_version", ""))
    except (OSError, ValueError):
        return ""


def _layout(package: Path) -> None:
    """layout.json: every file of the package with its size and date, as the sim wants it."""
    content = []
    for path in sorted(p for p in package.rglob("*") if p.is_file() and p.name not in ("layout.json", "manifest.json")):
        stat = path.stat()
        content.append({"path": path.relative_to(package).as_posix(), "size": stat.st_size,
                        "date": int((stat.st_mtime + FILETIME_EPOCH_S) * 10_000_000)})
    (package / "layout.json").write_text(json.dumps({"content": content}, indent=2), encoding="utf-8")


def install(roots: list[Path] | None = None) -> list[Path]:
    """The package in each Community folder that hasn't this version of it; the folders it was put in (the sim needs
    a restart to see it there)."""
    put = []
    for community in community_folders(roots):
        target = community / PACKAGE.name
        if target.is_dir() and _version(target) == _version(PACKAGE) and (target / "layout.json").exists():
            continue
        try:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns("__pycache__"))
            _layout(target)
        except OSError as exc:
            log.warning("Taxi guidance: couldn't put its package in %s: %s", community, exc)
            continue
        log.info("Taxi guidance: its package is in %s (shown from the sim's next start)", target)
        put.append(target)
    return put


def installed(roots: list[Path] | None = None) -> bool:
    """The package is in a Community folder."""
    return any((c / PACKAGE.name / "layout.json").exists() for c in community_folders(roots))
