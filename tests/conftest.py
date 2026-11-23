def pytest_addoption(parser):
    parser.addoption("--update-goldens", action="store_true", help="rewrite tests/scenarios/*.golden.txt")


import os

# The app's saved settings (on this machine) must never change what the tests see.
os.environ["LOCALTC_SETTINGS"] = ""
# ... nor the real gates fetched from OpenStreetMap: no network, the scenery's gate names.
os.environ["LOCALTC_REAL_GATES"] = ""
os.environ["LOCALTC_METAR"] = ""
# ... nor the EFB's notes and achievements on this machine: kept nowhere.
os.environ["LOCALTC_EFB_DIR"] = ""


import pytest


@pytest.fixture(autouse=True)
def _whisper_fresh():
    """Each test starts with no Whisper loaded and the graphics card not yet failed (both kept for a run of the app)."""
    from localtc.stt import whisper

    whisper._LOADED.clear()
    whisper._CARD_FAILED = ""
    yield
    whisper._LOADED.clear()
    whisper._CARD_FAILED = ""
