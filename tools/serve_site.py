"""Serve site/ on this computer the way GitHub Pages does: /dashboard is dashboard.html, / is index.html.

    python3 tools/serve_site.py [port]          # http://localhost:8000/
"""

import http.server
import sys
from functools import partial
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


class Pages(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        local = Path(super().translate_path(path))
        if not local.suffix and not local.is_dir() and local.with_suffix(".html").is_file():
            return str(local.with_suffix(".html"))
        return str(local)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"http://localhost:{port}/")
    http.server.ThreadingHTTPServer(("", port), partial(Pages, directory=str(SITE))).serve_forever()
