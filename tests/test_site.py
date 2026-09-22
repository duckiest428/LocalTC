"""The website's built page: the changelog renders from CHANGELOG.md, and the current version is on top."""

import importlib.util
from pathlib import Path

from localtc import __version__

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_site", ROOT / "tools" / "build_site.py")
build_site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_site)


def test_the_changelog_renders_with_this_version_first():
    latest, body = build_site.render((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert latest == __version__
    assert f'id="v{__version__}"' in body
    assert "**" not in body and "<strong>" in body


def test_markdown_is_escaped_not_trusted():
    _, body = build_site.render("## [1.0.0] - 2026-01-01\n- <script>x</script> and [a](javascript:alert(1))\n")
    assert "<script>" not in body and 'href="javascript' not in body
