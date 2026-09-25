"""Build the parts of the website that come from the repository: site/changelog.html from CHANGELOG.md, and
version stamps on the pages' own scripts and stylesheets.

    python tools/build_site.py          # writes site/changelog.html, stamps site/*.html

Run by .github/workflows/pages.yml before publishing; neither change is committed. The stamps: the site is
cached for hours (by browsers and Cloudflare), so a page could arrive new with its script old, a dashboard
whose Wrapped link went nowhere. Every ``src="x.js"`` and ``href="x.css"`` gets ``?v=<its content hash>``,
and so do the scripts' own imports (``import("./cardmodel.js")``): a changed file is a new address. Only the Markdown that
CHANGELOG.md uses is understood: ## and ### headings, "- " lists, **bold**, `code` and [links](url).
"""

import hashlib
import html
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
  <a class="brand" href="index.html"><img class="brand-logo" src="logo.svg" alt="" width="28" height="28"><span class="brand-mark">LocalTC</span></a>
  <nav class="nav">
    <a href="index.html#features">Features</a>
    <a href="index.html#install">Install</a>
    <a href="changelog.html" aria-current="page">Changelog</a>
    <a href="dashboard.html#support">Support</a>
    <a href="https://github.com/duckiest428/LocalTC">GitHub</a>
  </nav>
  <a class="btn btn-ghost" href="dashboard.html">Dashboard</a>
</header>

<main class="legal changelog">
  <p class="kicker">Changelog</p>
  <h1>What changed.</h1>
  <p class="updated">Latest: <b>{latest}</b>. The app shows the new version's notes when it offers the update.
    <a href="https://github.com/duckiest428/LocalTC/releases/latest/download/LocalTC-Setup.exe">Download LocalTC-Setup.exe</a></p>
{body}
  <nav class="legal-nav">
    <a href="index.html">&larr; Back to LocalTC</a>
    <a href="https://github.com/duckiest428/LocalTC/releases">All releases on GitHub</a>
  </nav>
</main>
</body>
</html>
"""


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r'<code class="inline">\1</code>', text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', text)


def render(markdown: str) -> tuple[str, str]:
    """(latest version, the releases as HTML). The file's own title and preamble are left out."""
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
            latest = latest or version
            out.append(f'  <section class="release" id="v{html.escape(version)}">')
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


def main() -> int:
    latest, body = render((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    (ROOT / "site" / "changelog.html").write_text(PAGE.format(latest=html.escape(latest), body=body), encoding="utf-8")
    print(f"site/changelog.html: latest {latest}")
    if "--no-stamp" not in sys.argv:
        print("stamped:", ", ".join(stamp(ROOT / "site")) or "nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
