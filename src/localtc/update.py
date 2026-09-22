"""Updates from GitHub Releases: is there a newer LocalTC, fetch it, check it, and swap it in after the app exits.

A release carries ``LocalTC-X.Y.Z.zip`` (the source tree the installer set up, see ``.gitattributes``) and
``SHA256SUMS``. The check runs at most once a day and says nothing when offline. An update is downloaded
to ``<data dir>/updates/X.Y.Z``, its hash checked against ``SHA256SUMS``, and applied by a small PowerShell
script that waits for the app to close, mirrors the new files over the install (keeping ``.venv``, the
recordings and SimConnect.dll), reinstalls the package into the same environment and starts the app again.

Only an install made by the installer is updated this way. A git checkout updates with ``git pull``; the
app just says a new version is out.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from localtc import __version__
from localtc.config import data_dir

log = logging.getLogger(__name__)

REPO = "duckiest428/LocalTC"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_EVERY_S = 24 * 3600
# Never touched by an update: the Python environment, the pilot's recordings, the SDK's DLL if it was copied
# in, the launcher the installer wrote, and the uninstaller.
KEEP_DIRS = (".venv", "recordings")
KEEP_FILES = ("SimConnect.dll", "LocalTC.cmd", "unins*.*")

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def parse_version(text: str) -> tuple:
    """ "v1.2.3" -> (1, 2, 3, (1,)); a pre-release sorts before its release: "1.2.3-rc1" -> (1, 2, 3, (0, 'rc1'))."""
    m = _VERSION.match(text.strip())
    if not m:
        raise ValueError(f"not a version: {text!r}")
    major, minor, patch, pre = m.groups()
    return int(major), int(minor), int(patch), (0, pre) if pre else (1,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    try:
        return parse_version(candidate) > parse_version(current)
    except ValueError:
        return False


@dataclass
class Release:
    version: str
    notes: str
    page: str  # the release on GitHub
    zip_url: str
    sums_url: str
    published: str = ""


def parse_release(data: dict) -> Release | None:
    """A GitHub API release, if it has what an update needs."""
    tag = str(data.get("tag_name", ""))
    if data.get("draft") or not _VERSION.match(tag):
        return None
    version = tag.lstrip("v")
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets", [])}
    zip_url, sums_url = assets.get(f"LocalTC-{version}.zip"), assets.get("SHA256SUMS")
    if not zip_url or not sums_url:
        return None
    return Release(version=version, notes=str(data.get("body") or ""), page=str(data.get("html_url") or ""),
                   zip_url=zip_url, sums_url=sums_url, published=str(data.get("published_at") or ""))


def _fetch(url: str, timeout_s: float = 10.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"LocalTC/{__version__}",
                                                   "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.read()


def updates_dir() -> Path:
    return data_dir() / "updates"


def check(*, force: bool = False, fetch: Callable[[str], bytes] = _fetch, cache: Path | None = None,
          now: float | None = None, current: str = __version__) -> Release | None:
    """The latest release if it is newer than this one. Asks GitHub at most once a day (``force`` asks now);
    offline or rate-limited, it answers from the last check, or None."""
    cache = cache or updates_dir() / "latest.json"
    now = time.time() if now is None else now
    cached: dict = {}
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    release_data = cached.get("release")
    if force or now - float(cached.get("checked_at", "-inf")) >= CHECK_EVERY_S:
        try:
            release = parse_release(json.loads(fetch(LATEST_URL)))
            release_data = asdict(release) if release is not None else None
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"checked_at": now, "release": release_data}), encoding="utf-8")
        except Exception as exc:  # offline, rate-limited, GitHub down: no news is fine
            log.info("Update check failed: %s", exc)
    release = Release(**release_data) if release_data else None
    return release if release is not None and is_newer(release.version, current) else None


def install_root() -> Path:
    return Path(__file__).resolve().parents[2]


def can_apply(root: Path | None = None, platform: str = sys.platform) -> tuple[bool, str]:
    """Whether this copy can update itself, and if not, why."""
    root = root or install_root()
    if platform != "win32":
        return False, "Updates install themselves on Windows. Here, update with git pull."
    if (root / ".git").exists():
        return False, "This is a git checkout: update it with git pull (or GitHub Desktop)."
    if not (root / ".venv").is_dir() or not (root / "pyproject.toml").is_file():
        return False, "This copy wasn't set up by the installer; run the latest LocalTC-Setup.exe instead."
    return True, ""


def expected_hash(sums: str, name: str) -> str | None:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def download(release: Release, *, fetch: Callable[[str], bytes] = _fetch, into: Path | None = None) -> Path:
    """Fetch, verify and unpack a release. Returns the folder holding the new tree (with pyproject.toml)."""
    into = into or updates_dir()
    name = f"LocalTC-{release.version}.zip"
    want = expected_hash(fetch(release.sums_url).decode("utf-8", "replace"), name)
    if want is None:
        raise ValueError(f"SHA256SUMS doesn't list {name}")
    data = fetch(release.zip_url)
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise ValueError(f"{name} is corrupt or was tampered with (sha256 {got}, expected {want})")
    target = into / release.version
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    archive = target / name
    archive.write_bytes(data)
    tree = target / "tree"
    with zipfile.ZipFile(archive) as z:
        for member in z.namelist():  # nothing may land outside the folder ("zip slip")
            if not (tree / member).resolve().is_relative_to(tree.resolve()):
                raise ValueError(f"unsafe path in the update: {member}")
        z.extractall(tree)
    archive.unlink()
    payload = _payload(tree)
    if not (payload / "pyproject.toml").is_file():
        raise ValueError("the update has no pyproject.toml")
    (target / "ready").write_text(release.version, encoding="utf-8")
    return payload


def _payload(tree: Path) -> Path:
    """The release zip holds one folder (LocalTC-X.Y.Z/); the tree is inside it."""
    if not tree.is_dir() or (tree / "pyproject.toml").exists():
        return tree
    roots = [p for p in tree.iterdir() if p.is_dir()]
    return roots[0] if len(roots) == 1 else tree


def staged(into: Path | None = None, current: str = __version__) -> tuple[str, Path] | None:
    """A downloaded, verified update newer than this version, waiting to be applied."""
    into = into or updates_dir()
    found = []
    for ready in into.glob("*/ready"):
        version = ready.parent.name
        payload = _payload(ready.parent / "tree")
        if is_newer(version, current) and (payload / "pyproject.toml").is_file():
            found.append((parse_version(version), version, payload))
    if not found:
        return None
    _, version, payload = max(found)
    return version, payload


def apply_script(payload: Path, root: Path, pid: int, *, relaunch: bool) -> str:
    """PowerShell that waits for the app (``pid``) to exit, then installs ``payload`` over ``root``."""
    excluded_dirs = " ".join(f'"{root / d}"' for d in KEEP_DIRS)
    excluded_files = " ".join(f'"{f}"' for f in KEEP_FILES)
    version_dir = next(p for p in (payload, *payload.parents) if (p / "ready").exists() or p == p.parent)
    python = root / ".venv" / "Scripts" / "python.exe"
    app = root / ".venv" / "Scripts" / "localtc-app.exe"
    lines = [
        "$ErrorActionPreference = 'Continue'",
        f"$log = '{updates_dir() / 'apply.log'}'",
        f"Wait-Process -Id {pid} -Timeout 120 -ErrorAction SilentlyContinue",
        f"robocopy \"{payload}\" \"{root}\" /MIR /NFL /NDL /NJH /R:3 /W:2 /XD {excluded_dirs} /XF {excluded_files} *>> $log",
        "if ($LASTEXITCODE -ge 8) { Add-Content $log 'robocopy failed'; exit 1 }",
        # The GPU extra stays if it was installed: its CUDA libraries are what make Whisper run on the card.
        f"$extra = ''; & \"{python}\" -c \"import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('nvidia.cublas') else 1)\"",
        "if ($LASTEXITCODE -eq 0) { $extra = '[cuda]' }",
        f"& \"{python}\" -m pip install --quiet -e \"{root}$extra\" *>> $log",
        f"Remove-Item -Recurse -Force \"{version_dir}\" -ErrorAction SilentlyContinue",
    ]
    if relaunch:
        lines.append(f"Start-Process -FilePath \"{app}\" -WorkingDirectory \"{root}\"")
    return "\r\n".join(lines) + "\r\n"


def launch_apply(payload: Path, *, relaunch: bool, root: Path | None = None) -> None:
    """Start the apply script, detached, to run once this process has exited."""
    root = root or install_root()
    ok, why = can_apply(root)
    if not ok:
        raise RuntimeError(why)
    script = updates_dir() / "apply.ps1"
    script.write_text(apply_script(payload, root, os.getpid(), relaunch=relaunch), encoding="utf-8")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) \
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", str(script)], creationflags=flags, close_fds=True)
    log.info("Update will be applied from %s after LocalTC exits", payload)


def changelog_section(text: str, version: str) -> str:
    """The notes for one version from CHANGELOG.md ("## [1.2.0] - 2026-10-01" up to the next "## ")."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = f"[{version}]" in line or line[3:].strip().split()[0:1] == [version]
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()
