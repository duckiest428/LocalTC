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


def test_the_flights_map_is_the_same_file_in_the_app_and_on_the_site():
    site = (ROOT / "site" / "flightsmap.js").read_bytes()
    app = (ROOT / "src" / "localtc" / "ui" / "static" / "flightsmap.js").read_bytes()
    assert site == app, "copy site/flightsmap.js over src/localtc/ui/static/flightsmap.js (or the other way)"


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
    assert [p for p in ("tracker", "logbook", "support", "account") if f'data-page="{p}"' in side] == \
        ["tracker", "logbook", "support", "account"]
    app = html[html.index('<div class="dash-app" id="dash" hidden>'):]
    for page in ("tracker", "logbook", "support", "account"):
        assert f'<section class="dash-page" id="{page}"' in app  # all inside the signed-in part, hidden until then
    signed_out = html[html.index('id="auth"'):html.index('id="dash"')]
    assert "lb-map" not in signed_out and "support-form" not in signed_out
