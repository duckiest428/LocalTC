def pytest_addoption(parser):
    parser.addoption("--update-goldens", action="store_true", help="rewrite tests/scenarios/*.golden.txt")


import os

# The app's saved settings (on this machine) must never change what the tests see.
os.environ["LOCALTC_SETTINGS"] = ""
