"""Build the sim's LocalTC toolbar panel (tools/sim_panel) with the MSFS 2024 SDK's package tool, into
src/localtc/simpkg/localtc-panel, the package the app puts in the sims' Community folders.

    python tools/build_sim_panel.py            # MSFS 2024 running (the package tool hands the build to the sim)

The panel's definition (InGamePanels/localtc-panel.xml) has to be compiled to the sim's binary .spb, which only the
package tool does; the rest is copied. Build it once and commit the result: the app needs no SDK.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "tools" / "sim_panel" / "localtc-panel.xml"
TARGET = ROOT / "src" / "localtc" / "simpkg" / "localtc-panel"
SDK = Path(os.environ.get("MSFS2024_SDK", r"C:\MSFS 2024 SDK"))


def main() -> int:
    tool = SDK / "Tools" / "bin" / "fspackagetool.exe"
    if not tool.exists():
        print(f"No package tool at {tool} (install the MSFS 2024 SDK, or set MSFS2024_SDK)")
        return 1
    with tempfile.TemporaryDirectory() as out:
        run = subprocess.run([str(tool), str(PROJECT), "-outputdir", out, "-tempdir", out, "-rebuild", "-nopause"],
                             cwd=PROJECT.parent, capture_output=True, text=True)
        print(run.stdout[-3000:], run.stderr[-2000:])
        built = next(Path(out).rglob("localtc-panel/manifest.json"), None)
        if built is None or not any(built.parent.rglob("*.spb")):
            print("Nothing built: start MSFS 2024 first (the package tool builds through the sim), then run this again.")
            return 1
        if TARGET.exists():
            shutil.rmtree(TARGET)
        shutil.copytree(built.parent, TARGET, ignore=shutil.ignore_patterns("layout.json"))
    print(f"Built: {TARGET} (commit it; the app installs it in the Community folders)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
