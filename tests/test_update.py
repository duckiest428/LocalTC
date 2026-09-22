"""Updates from GitHub Releases, without the network: versions, the daily check, checking and unpacking."""

import hashlib
import io
import json
import re
import tomllib
import zipfile
from pathlib import Path

import pytest

from localtc import __version__, update

ROOT = Path(__file__).resolve().parents[1]


def test_one_version_everywhere():
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"] == __version__
    assert update.changelog_section((ROOT / "CHANGELOG.md").read_text(), __version__), "CHANGELOG.md needs this version"


@pytest.mark.parametrize(("a", "b"), [("0.2.0", "0.1.9"), ("1.0.0", "0.99.99"), ("0.2.0", "0.2.0-rc1"), ("v0.10.0", "0.9.0")])
def test_newer(a, b):
    assert update.is_newer(a, b) and not update.is_newer(b, a)


def test_nonsense_is_never_newer():
    assert not update.is_newer("latest", "0.1.0")


def zipped(version: str, files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(f"LocalTC-{version}/{name}", text)
    return buf.getvalue()


class FakeGitHub:
    def __init__(self, version: str = "9.0.0", *, corrupt: bool = False) -> None:
        self.zip = zipped(version, {"pyproject.toml": "[project]\n", "src/localtc/__init__.py": ""})
        digest = hashlib.sha256(b"x" if corrupt else self.zip).hexdigest()
        self.sums = f"{digest}  LocalTC-{version}.zip\n{'0' * 64}  LocalTC-Setup.exe\n".encode()
        self.release = {"tag_name": f"v{version}", "body": "- things", "html_url": "https://example/r",
                        "assets": [{"name": f"LocalTC-{version}.zip", "browser_download_url": "zip"},
                                   {"name": "SHA256SUMS", "browser_download_url": "sums"}]}
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        return {"zip": self.zip, "sums": self.sums}.get(url) or json.dumps(self.release).encode()


def test_a_newer_release_is_found_and_asked_for_once_a_day(tmp_path):
    github = FakeGitHub()
    cache = tmp_path / "latest.json"
    found = update.check(fetch=github, cache=cache, now=1000.0)
    assert found is not None and found.version == "9.0.0"
    update.check(fetch=github, cache=cache, now=1000.0 + 3600)
    assert len(github.calls) == 1
    update.check(fetch=github, cache=cache, now=1000.0 + update.CHECK_EVERY_S)
    assert len(github.calls) == 2


def test_offline_is_quiet(tmp_path):
    def offline(url: str) -> bytes:
        raise OSError("no network")

    assert update.check(fetch=offline, cache=tmp_path / "latest.json", now=0.0) is None


def test_the_same_version_is_not_an_update(tmp_path):
    assert update.check(fetch=FakeGitHub(__version__), cache=tmp_path / "l.json", now=0.0) is None


def test_a_release_without_its_zip_is_ignored():
    assert update.parse_release({"tag_name": "v9.0.0", "assets": []}) is None


def test_download_checks_the_hash_and_unpacks(tmp_path):
    github = FakeGitHub()
    release = update.parse_release(github.release)
    payload = update.download(release, fetch=github, into=tmp_path)
    assert (payload / "pyproject.toml").is_file()
    assert update.staged(into=tmp_path, current="0.1.0") == ("9.0.0", payload)
    assert update.staged(into=tmp_path, current="9.0.0") is None


def test_a_tampered_download_is_refused(tmp_path):
    github = FakeGitHub(corrupt=True)
    with pytest.raises(ValueError, match="tampered"):
        update.download(update.parse_release(github.release), fetch=github, into=tmp_path)
    assert update.staged(into=tmp_path, current="0.1.0") is None


def test_a_zip_reaching_outside_its_folder_is_refused(tmp_path):
    github = FakeGitHub()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.txt", "x")
    github.zip = buf.getvalue()
    github.sums = f"{hashlib.sha256(github.zip).hexdigest()}  LocalTC-9.0.0.zip\n".encode()
    with pytest.raises(ValueError, match="unsafe"):
        update.download(update.parse_release(github.release), fetch=github, into=tmp_path)


def test_a_git_checkout_is_not_updated_over(tmp_path):
    (tmp_path / ".git").mkdir()
    ok, why = update.can_apply(tmp_path, platform="win32")
    assert not ok and "git pull" in why
    assert not update.can_apply(tmp_path, platform="darwin")[0]


def test_an_installed_copy_can_be(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / "pyproject.toml").write_text("")
    assert update.can_apply(tmp_path, platform="win32") == (True, "")


def test_the_apply_script_keeps_the_environment_and_recordings(tmp_path):
    staged = tmp_path / "updates" / "9.0.0"
    (staged / "tree" / "LocalTC-9.0.0").mkdir(parents=True)
    (staged / "ready").write_text("9.0.0")
    script = update.apply_script(staged / "tree" / "LocalTC-9.0.0", Path("C:/LocalTC"), 1234, relaunch=True)
    assert "Wait-Process -Id 1234" in script and "/MIR" in script
    assert re.search(r"/XD .*\.venv.*recordings", script) and "unins*.*" in script
    assert "localtc-app.exe" in script
    assert f'Remove-Item -Recurse -Force "{staged}"' in script


def test_release_notes_come_from_the_changelog():
    text = "# Changelog\n\n## [1.1.0] - 2026-10-01\n- new\n\n## [1.0.0] - 2026-09-01\n- old\n"
    assert update.changelog_section(text, "1.1.0") == "- new"
    assert update.changelog_section(text, "1.0.0") == "- old"
    assert update.changelog_section(text, "2.0.0") == ""
