"""The website's built page: the changelog renders from CHANGELOG.md, and the current version is on top."""

import importlib.util
import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("name", ["flightsmap.js", "replayplayer.js", "replayplayer.css", "sharecard.js", "sharecard.css",
                                  "wrapped.js", "cardmodel.js", "vendor/land-110m.json", "fonts/inter-latin-var.woff2",
                                  "fonts/jetbrains-mono-latin-var.woff2"])
def test_the_shared_maps_are_the_same_files_in_the_app_and_on_the_site(name):
    site = (ROOT / "site" / name).read_bytes()
    app = (ROOT / "src" / "localtc" / "ui" / "static" / name).read_bytes()
    assert site == app, f"copy site/{name} over src/localtc/ui/static/{name} (or the other way)"


PAGES = ["index.html", "dashboard.html", "privacy.html", "terms.html", "cookies.html"]


def test_every_page_has_the_same_header_links():
    for page in PAGES:
        html = (ROOT / "site" / page).read_text(encoding="utf-8")
        header = html[html.index('<header class="topbar">'):html.index("</header>")]
        assert 'href="dashboard.html#support"' in header, page  # support is a dashboard section: it needs the account
        assert 'href="https://github.com/duckiest428/LocalTC">GitHub</a>' in header, page
        assert 'class="btn btn-ghost" href="dashboard.html"' in header and ">Dashboard</a>" in header, page
        assert ">Logbook</a>" not in header, page
    template = (ROOT / "tools" / "build_site.py").read_text(encoding="utf-8")  # the changelog page's header
    assert ">Dashboard</a>" in template and 'href="dashboard.html#support"' in template and ">Logbook</a>" not in template


def test_the_site_loads_nothing_it_does_not_serve_itself_except_the_api_and_map_tiles():
    import re

    for page in PAGES:
        html = (ROOT / "site" / page).read_text(encoding="utf-8")
        for src in re.findall(r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', html):
            assert not src.startswith(("http:", "https:", "//")), f"{page} loads {src} from elsewhere"


def test_the_dashboard_is_a_sidebar_of_sections_behind_the_sign_in():
    html = (ROOT / "site" / "dashboard.html").read_text(encoding="utf-8")
    side = html[html.index('<nav class="side-nav"'):html.index("</nav>", html.index('<nav class="side-nav"'))]
    import re

    assert re.findall(r'data-page="(\w+)"', side) == ["tracker", "logbook", "wrapped", "support", "account"]
    app = html[html.index('<div class="dash-app" id="dash" hidden>'):]
    for page in ("tracker", "logbook", "wrapped", "support", "account"):
        assert f'<section class="dash-page" id="{page}"' in app  # all inside the signed-in part, hidden until then
    signed_out = html[html.index('id="auth"'):html.index('id="dash"')]
    assert "lb-map" not in signed_out and "support-form" not in signed_out


def test_the_shared_flight_page_gets_the_hashes_of_what_it_loads(tmp_path):
    # The account server writes those pages and stamps their scripts from assets.json: a new sharecard.js
    # is a new address, never yesterday's cached one.
    site = tmp_path / "site"
    site.mkdir()
    for name in build_site.SHARE_PAGE_ASSETS:
        (site / name).write_text((ROOT / "site" / name).read_text(encoding="utf-8"), encoding="utf-8")
    stamps = build_site.asset_stamps(site)
    assert set(stamps) == set(build_site.SHARE_PAGE_ASSETS)
    assert json.loads((site / "assets.json").read_text()) == stamps
    shares = (ROOT / "server" / "src" / "shares.ts").read_text(encoding="utf-8")
    assert all(f'asset("{name}")' in shares for name in build_site.SHARE_PAGE_ASSETS)
