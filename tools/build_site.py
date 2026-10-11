"""Build the parts of the website that come from the repository: site/changelog.html (and changelog.json) from the three changelogs, and
version stamps on the pages' own scripts and stylesheets.

    python tools/build_site.py          # writes site/changelog.html and changelog.json, stamps site/*.html, writes site/assets.json

Run by .github/workflows/pages.yml before publishing; neither change is committed. The stamps: the site is
cached for hours (by browsers and Cloudflare), so a page could arrive new with its script old, a dashboard
whose Wrapped link went nowhere. Every ``src="x.js"`` and ``href="x.css"`` gets ``?v=<its content hash>``,
and so do the scripts' own imports (``import("./cardmodel.js")``): a changed file is a new address. Only the Markdown that
CHANGELOG.md uses is understood: ## and ### headings, "- " lists, **bold**, `code` and [links](url). A version dated
"Upcoming" (or "Unreleased") is what's done but not released yet: on the page, hidden until "Show upcoming" is on. A
sidebar lists every version to jump to.

The shared-flight pages (localtc.tech/f/...) aren't the site's own: the account server writes them. It stamps
the site's files they load from ``assets.json``: {file: its content hash}.
"""

import hashlib
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Changelog — LocalTC</title>
<meta name="description" content="Every LocalTC release and what changed in it.">
<link rel="icon" href="logo.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon-180.png">
<link rel="stylesheet" href="styles.css">
</head>
<body>

<header class="topbar">
  <a class="brand" href="./"><img class="brand-logo" src="logo.svg" alt="" width="28" height="28"><span class="brand-mark">LocalTC</span></a>
  <nav class="nav">
    <a href="./#features">Features</a>
    <a href="pricing">Pricing</a>
    <a href="./#install">Install</a>
    <a href="changelog" aria-current="page">Changelog</a>
    <a href="dashboard#support">Support</a>
    <a href="https://github.com/duckiest428/LocalTC">GitHub</a>
  </nav>
  <a class="btn btn-ghost" href="dashboard">Dashboard</a>
</header>

<div class="cl-layout">
<aside class="cl-side" aria-label="Versions">
  <p class="cl-side-h">Versions</p>
{toggle}
{versions}
</aside>
<main class="legal changelog">
  <p class="kicker">Changelog</p>
  <h1>What changed.</h1>
  <p class="updated">Latest: <b>{latest}</b>. LocalTC, the companion app and the website share one version; the app
    and the companion show a new version's notes once, after the update.
    <a href="https://github.com/duckiest428/LocalTC/releases/latest/download/LocalTC-Setup.exe">Download LocalTC-Setup.exe</a></p>
  <div class="cl-tabs" role="tablist">
{tabs}
  </div>
{body}
  <nav class="legal-nav">
    <a href="./">&larr; Back to LocalTC</a>
    <a href="https://github.com/duckiest428/LocalTC/releases">All releases on GitHub</a>
  </nav>
</main>
</div>
<script>
  // One tab each: the Main App, the Companion App and the Website; a link to a version opens its tab.
  (function () {{
    const tabs = [...document.querySelectorAll(".cl-tabs [data-product]")];
    const pick = (product) => {{
      tabs.forEach((t) => t.setAttribute("aria-selected", String(t.dataset.product === product)));
      document.querySelectorAll(".cl-product").forEach((el) => {{ el.hidden = el.dataset.product !== product; }});
    }};
    tabs.forEach((t) => t.addEventListener("click", () => {{ pick(t.dataset.product); history.replaceState(null, "", "#" + t.dataset.product); }}));
    // "Show upcoming": what's done but not released yet, off unless asked for (or linked to).
    const box = document.getElementById("cl-upcoming");
    const show = (on) => {{
      if (box) box.checked = on;
      document.querySelectorAll("[data-upcoming]").forEach((el) => {{ el.hidden = !on; }});
    }};
    box?.addEventListener("change", () => show(box.checked));
    const linked = () => {{
      const id = decodeURIComponent(location.hash.slice(1));
      if (tabs.some((t) => t.dataset.product === id)) {{ pick(id); return; }}
      const el = id && document.getElementById(id);
      pick(el?.closest(".cl-product")?.dataset.product || "app");
      if (el && el.closest("[data-upcoming]")) show(true);
      el?.scrollIntoView();
    }};
    window.addEventListener("hashchange", linked);
    show(false);
    linked();
  }})();
  // The version in view is marked in the sidebar.
  (function () {{
    const links = new Map([...document.querySelectorAll(".cl-versions a")].map((a) => [a.getAttribute("href").slice(1), a]));
    if (!("IntersectionObserver" in window) || !links.size) return;
    const seen = new IntersectionObserver((entries) => {{
      for (const e of entries) if (e.isIntersecting) {{
        links.forEach((a) => a.classList.remove("on"));
        links.get(e.target.id)?.classList.add("on");
      }}
    }}, {{ rootMargin: "0px 0px -70% 0px" }});
    document.querySelectorAll(".changelog .release").forEach((el) => seen.observe(el));
  }})();
</script>
</body>
</html>
"""


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r'<code class="inline">\1</code>', text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', text)


UPCOMING = re.compile(r"upcoming|unreleased", re.I)


def releases(markdown: str) -> list[tuple[str, str, bool]]:
    """Every version in the file, newest first: (version, its date, whether it's upcoming)."""
    out = []
    for line in markdown.splitlines():
        if m := re.match(r"## \[([^\]]+)\](?:\s*-\s*(.+))?", line):
            out.append((m.group(1), (m.group(2) or "").strip(), bool(UPCOMING.search(m.group(2) or ""))))
    return out


def sidebar(markdown: str, prefix: str = "") -> tuple[str, str]:
    """(the "Show upcoming" switch, or "" without an upcoming version; the links to each version). ``prefix`` keeps
    one product's version anchors apart from another's ("companion-v0.4.0"; the Main App's are plain "v0.4.0")."""
    links, toggle = [], ""
    for version, date, upcoming in releases(markdown):
        v = html.escape(version)
        if upcoming:
            toggle = ('  <label class="cl-toggle"><input type="checkbox" id="cl-upcoming"> Show upcoming</label>')
            links.append(f'    <a href="#{prefix}v{v}" data-upcoming hidden>{v} <span class="cl-up">upcoming</span></a>')
        else:
            links.append(f'    <a href="#{prefix}v{v}">{v} <span class="cl-date">{html.escape(date)}</span></a>')
    return toggle, "\n".join(links)


def render(markdown: str, prefix: str = "") -> tuple[str, str]:
    """(latest released version, the releases as HTML). The file's own title and preamble are left out; an upcoming
    version is in it, marked, hidden until shown."""
    out: list[str] = []
    item: list[str] | None = None
    latest = ""
    in_list = False

    def close_item() -> None:
        nonlocal item
        if item is not None:
            out.append(f"    <li>{inline(' '.join(item))}</li>")
            item = None

    def close_list() -> None:
        nonlocal in_list
        close_item()
        if in_list:
            out.append("  </ul>")
            in_list = False

    started = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            close_list()
            if started:
                out.append("  </section>")
            started = True
            m = re.match(r"## \[([^\]]+)\](?:\s*-\s*(.+))?", line)
            version, date = (m.group(1), m.group(2) or "") if m else (line[3:], "")
            upcoming = bool(UPCOMING.search(date))
            if not upcoming:
                latest = latest or version
            if upcoming:
                out.append(f'  <section class="release upcoming" id="{prefix}v{html.escape(version)}" data-upcoming hidden>')
                out.append(f'  <h2>{html.escape(version)} <span class="date">Upcoming</span></h2>')
                out.append('  <p class="cl-note">Done, not released yet: these come with the next version, and may '
                           'still change.</p>')
            else:
                out.append(f'  <section class="release" id="{prefix}v{html.escape(version)}">')
                out.append(f'  <h2>{html.escape(version)} <span class="date">{html.escape(date)}</span></h2>')
        elif not started:
            continue
        elif line.startswith("### "):
            close_list()
            out.append(f"  <h3>{inline(line[4:])}</h3>")
        elif line.startswith("- "):
            close_item()
            if not in_list:
                out.append("  <ul>")
                in_list = True
            item = [line[2:].strip()]
        elif line.startswith("  ") and item is not None:
            item.append(line.strip())
        elif line.strip():
            close_list()
            out.append(f"  <p>{inline(line.strip())}</p>")
        else:
            close_item()
    close_list()
    if started:
        out.append("  </section>")
    return latest, "\n".join(out)


ASSET = re.compile(r'((?:src|href)=")([\w./-]+\.(?:js|css))(")')
IMPORT = re.compile(r'(import\("\./)([\w./-]+\.js)("\))')
# What a shared flight's page loads from the site (server/src/shares.ts).
SHARE_PAGE_ASSETS = ("styles.css", "sharecard.css", "sharecard.js", "minireplay.js", "replayplayer.js", "replayplayer.css")


def _hash(site: Path, name: str) -> str | None:
    file = site / name
    return hashlib.sha256(file.read_bytes()).hexdigest()[:10] if file.is_file() else None


def stamp(site: Path) -> list[str]:
    """Version every page's (and script's) own assets by their content; returns the files changed."""
    changed = []
    stamped = lambda m: f"{m.group(1)}{m.group(2)}?v={h}{m.group(3)}" if (h := _hash(site, m.group(2))) else m.group(0)
    # The scripts first, since their imports change their own content, and so their stamp.
    for file in sorted(site.glob("*.js")) + sorted(site.glob("*.html")):
        text = file.read_text(encoding="utf-8")
        new = IMPORT.sub(stamped, text) if file.suffix == ".js" else ASSET.sub(stamped, text)
        if new != text:
            file.write_text(new, encoding="utf-8")
            changed.append(file.name)
    return changed


def asset_stamps(site: Path) -> dict[str, str]:
    """{file: content hash} of what the account server's share pages load; written as site/assets.json."""
    stamps = {name: h for name in SHARE_PAGE_ASSETS if (h := _hash(site, name))}
    (site / "assets.json").write_text(json.dumps(stamps, indent=1) + "\n", encoding="utf-8")
    return stamps


# The changelog page's tabs: (its id, its name, its file, its anchors' prefix). The Main App's file is the one the app
# and the release read; all three carry the same version numbers.
PRODUCTS = (("app", "Main App", "CHANGELOG.md", ""), ("companion", "Companion App", "CHANGELOG-companion.md", "companion-"),
            ("website", "Website", "CHANGELOG-website.md", "website-"))


def sections(markdown: str) -> list[dict]:
    """Each version's notes as Markdown, newest first, for the companion's "what's new" (site/changelog.json)."""
    out: list[dict] = []
    for line in markdown.splitlines():
        if m := re.match(r"## \[([^\]]+)\](?:\s*-\s*(.+))?", line):
            date = (m.group(2) or "").strip()
            out.append({"version": m.group(1), "date": date, "upcoming": bool(UPCOMING.search(date)), "notes": []})
        elif out:
            out[-1]["notes"].append(line)
    for s in out:
        s["notes"] = "\n".join(s["notes"]).strip()
    return out


def page(texts: dict[str, str]) -> str:
    """The changelog page from each product's changelog ({id: its Markdown})."""
    latest = ""
    bodies, navs, tabs, toggle = [], [], [], ""
    for product, name, _file, prefix in PRODUCTS:
        text = texts.get(product, "")
        last, body = render(text, prefix)
        latest = latest or last
        up, versions = sidebar(text, prefix)
        toggle = toggle or up
        tabs.append(f'    <button type="button" role="tab" data-product="{product}" aria-selected="false">{name}</button>')
        bodies.append(f'  <div class="cl-product" data-product="{product}">\n{body}\n  </div>')
        navs.append(f'  <nav class="cl-versions cl-product" data-product="{product}">\n{versions}\n  </nav>')
    return PAGE.format(latest=html.escape(latest), body="\n".join(bodies), toggle=toggle, versions="\n".join(navs),
                       tabs="\n".join(tabs))


def main() -> int:
    texts = {product: (ROOT / file).read_text(encoding="utf-8") for product, _name, file, _prefix in PRODUCTS}
    latest, _body = render(texts["app"])
    (ROOT / "site" / "changelog.html").write_text(page(texts), encoding="utf-8")
    (ROOT / "site" / "changelog.json").write_text(
        json.dumps({product: sections(texts[product]) for product, *_ in PRODUCTS}, indent=1) + "\n", encoding="utf-8")
    print(f"site/changelog.html: latest {latest}")
    if "--no-stamp" not in sys.argv:
        print("stamped:", ", ".join(stamp(ROOT / "site")) or "nothing")
        print("site/assets.json:", asset_stamps(ROOT / "site"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
