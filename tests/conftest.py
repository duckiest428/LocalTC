def pytest_addoption(parser):
    parser.addoption("--update-goldens", action="store_true", help="rewrite tests/scenarios/*.golden.txt")


import os

# The app's saved settings (on this machine) must never change what the tests see.
os.environ["LOCALTC_SETTINGS"] = ""
# ... nor the real gates fetched from OpenStreetMap: no network, the scenery's gate names.
os.environ["LOCALTC_REAL_GATES"] = ""
os.environ["LOCALTC_METAR"] = ""
