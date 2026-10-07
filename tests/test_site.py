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


@pytest.mark.parametrize("name", ["flightsmap.js", "atcmap.js", "atcmap.css", "replayplayer.js", "replayplayer.css", "sharecard.js", "sharecard.css",
                                  "wrapped.js", "cardmodel.js", "vendor/land-110m.json", "fonts/inter-latin-var.woff2",
                                  "fonts/jetbrains-mono-latin-var.woff2"])
def test_the_shared_maps_are_the_same_files_in_the_app_and_on_the_site(name):
    site = (ROOT / "site" / name).read_bytes()
    app = (ROOT / "src" / "localtc" / "ui" / "static" / name).read_bytes()
    assert site == app, f"copy site/{name} over src/localtc/ui/static/{name} (or the other way)"


PAGES = ["index.html", "dashboard.html", "privacy.html", "terms.html", "cookies.html", "pricing.html"]


def test_every_page_has_the_same_header_links():
    for page in PAGES:
        html = (ROOT / "site" / page).read_text(encoding="utf-8")
        header = html[html.index('<header class="topbar">'):html.index("</header>")]
        assert 'href="dashboard#support"' in header, page  # support is a dashboard section: it needs the account
        assert 'href="https://github.com/duckiest428/LocalTC">GitHub</a>' in header, page
        assert 'class="btn btn-ghost" href="dashboard"' in header and ">Dashboard</a>" in header, page
        assert ">Logbook</a>" not in header, page
    template = (ROOT / "tools" / "build_site.py").read_text(encoding="utf-8")  # the changelog page's header
    assert ">Dashboard</a>" in template and 'href="dashboard#support"' in template and ">Logbook</a>" not in template


def test_the_site_loads_nothing_it_does_not_serve_itself_except_the_api_and_map_tiles():
    import re

    for page in [*PAGES, "tracker.html"]:
        html = (ROOT / "site" / page).read_text(encoding="utf-8")
        for src in re.findall(r'<(?:script|link)(?![^>]*rel="canonical")[^>]+(?:src|href)="([^"]+)"', html):
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


def test_the_dashboard_tracker_is_a_preview_and_the_talking_is_on_its_full_screen_page():
    dashboard = (ROOT / "site" / "dashboard.html").read_text(encoding="utf-8")
    full = (ROOT / "site" / "tracker.html").read_text(encoding="utf-8")
    assert 'id="tr-say"' not in dashboard and 'href="tracker"' in dashboard
    assert 'id="tr-radio"' not in dashboard  # the radio is the full screen's, not the preview's
    assert 'id="tr-say"' in full and 'data-to="crew"' in full and 'data-to="com2"' in full and 'id="tr-map"' in full
    for page in (dashboard, full):  # the same tracker code, after what it builds on
        scripts = __import__("re").findall(r'<script src="([\w./-]+)"', page)
        assert scripts.index("api.js") < scripts.index("tracker.js")
        assert scripts.index("atcmap.js") < scripts.index("tracker.js")


def test_the_pages_link_to_each_other_without_html():
    """/dashboard, not /dashboard.html (GitHub Pages serves the page either way)."""
    import re

    for page in [*PAGES, "tracker.html"]:
        text = (ROOT / "site" / page).read_text(encoding="utf-8")
        assert not re.findall(r'href="(?!https?:)[^"]*\.html', text), page
    assert ".html" not in build_site.PAGE.split("<main")[0]



def test_the_sitemap_lists_the_public_pages_without_html():
    import re

    xml = (ROOT / "site" / "sitemap.xml").read_text(encoding="utf-8")
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    assert urls[0] == "https://localtc.tech/" and all(".html" not in u for u in urls)
    for name in ("pricing", "changelog", "privacy", "terms", "cookies"):
        assert f"https://localtc.tech/{name}" in urls
    assert "Sitemap: https://localtc.tech/sitemap.xml" in (ROOT / "site" / "robots.txt").read_text(encoding="utf-8")


def test_every_plan_costs_nothing():
    import re

    html = (ROOT / "site" / "pricing.html").read_text(encoding="utf-8")
    prices = re.findall(r'<p class="price"><b>([^<]+)</b>', html)
    assert len(prices) >= 3 and set(prices) == {"$0"}
    assert html.count("LocalTC-Setup.exe") >= len(prices)  # a download on every plan
