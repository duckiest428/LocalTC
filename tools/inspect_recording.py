"""Shortcut for `localtc inspect`. Run with: python tools/inspect_recording.py --help"""

import sys

from localtc.cli import main

raise SystemExit(main(["inspect", *sys.argv[1:]]))
