"""Build the parts of the website that come from the repository: site/changelog.html from CHANGELOG.md.

    python tools/build_site.py          # writes site/changelog.html

Run by .github/workflows/pages.yml before publishing; the page isn't committed. Only the Markdown that
CHANGELOG.md uses is understood: ## and ### headings, "- " lists, **bold**, `code` and [links](url).
"""

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
    <a href="dashboard.html">Logbook</a>
  </nav>
  <a class="btn btn-ghost" href="https://github.com/duckiest428/LocalTC">GitHub</a>
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


def main() -> int:
    latest, body = render((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    (ROOT / "site" / "changelog.html").write_text(PAGE.format(latest=html.escape(latest), body=body), encoding="utf-8")
    print(f"site/changelog.html: latest {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
