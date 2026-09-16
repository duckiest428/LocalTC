"""Shortcut for `localtc replay`. Run with: python tools/replay.py --help"""

import sys

from localtc.cli import main

raise SystemExit(main(["replay", *sys.argv[1:]]))
