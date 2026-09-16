"""Shortcut for `localtc record`. Run with: python tools/record.py --help"""

import sys

from localtc.cli import main

raise SystemExit(main(["record", *sys.argv[1:]]))
