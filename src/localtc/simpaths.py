"""Where MSFS 2020 and 2024 keep their packages on this PC, and the aircraft installed there.

MSFS 2024 lists its aircraft over SimConnect (EnumerateSimObjectsAndLiveries); MSFS 2020 can't, so its are read from
the packages' own aircraft.cfg: every ``[FLTSIM.n]`` title, each a model and livery in one (2020 has no separate
liveries). Nothing here needs the sim running.
"""

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

# UserCfg.opt of each sim, Microsoft Store and Steam, MSFS 2024's first.
USER_CFGS = {
    "2024": ("Packages/Microsoft.Limitless_8wekyb3d8bbwe/LocalCache/UserCfg.opt", "Microsoft Flight Simulator 2024/UserCfg.opt"),
    "2020": ("Packages/Microsoft.FlightSimulator_8wekyb3d8bbwe/LocalCache/UserCfg.opt", "Microsoft Flight Simulator/UserCfg.opt"),
}
TITLE = re.compile(r'^\s*title\s*=\s*"?([^";\r\n]*?)"?\s*(?:;.*)?$', re.I | re.M)


def package_roots(sim: str = "") -> list[Path]:
    """The InstalledPackagesPath of each sim's UserCfg.opt (``sim``: "2020" or "2024" for one of them)."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    appdata = Path(os.environ.get("APPDATA", ""))
    roots = []
    for which, (store, steam) in USER_CFGS.items():
        if sim and sim != which:
            continue
        for cfg in (local / store, appdata / steam):
            try:
                text = cfg.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if m := re.search(r'InstalledPackagesPath\s+"([^"]+)"', text):
                roots.append(Path(m.group(1)))
    return roots


def _packages(root: Path):
    """Each package folder under a root: Community's, and Official's (Official/Steam/<package>, .../OneStore/...)."""
    for base in (root / "Community", *((root / "Official").glob("*") if (root / "Official").is_dir() else ())):
        try:
            yield from (p for p in base.iterdir() if p.is_dir())
        except OSError:
            continue


def aircraft_titles(roots: list[Path]) -> list[str]:
    """Every aircraft title in the packages under ``roots`` (the ones whose aircraft.cfg can be read: an encrypted
    package's can't), in the order found, each once."""
    titles: dict[str, None] = {}
    for root in roots:
        for package in _packages(root):
            for cfg in package.glob("SimObjects/Airplanes/*/aircraft.cfg"):
                try:
                    text = cfg.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for title in TITLE.findall(text):
                    if title.strip():
                        titles.setdefault(title.strip(), None)
    log.info("Aircraft installed: %d titles read from the packages", len(titles))
    return list(titles)
