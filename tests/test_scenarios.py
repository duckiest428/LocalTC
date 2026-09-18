"""Golden transcripts: each tests/scenarios/*.toml run offline must match its .golden.txt.

After an intended behavior change: pytest tests/test_scenarios.py --update-goldens, then review the diff.
"""

from pathlib import Path

import pytest

from localtc.scenario import run_scenario

SCENARIOS = sorted((Path(__file__).parent / "scenarios").glob("*.toml"))


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_matches_golden(path, request):
    transcript = run_scenario(path).transcript
    golden = path.with_suffix(".golden.txt")
    if request.config.getoption("--update-goldens") or not golden.exists():
        golden.write_text(transcript, encoding="utf-8")
        if not request.config.getoption("--update-goldens"):
            pytest.fail(f"wrote new golden {golden.name}; review and commit it")
    assert transcript == golden.read_text(encoding="utf-8")


def test_scenarios_are_deterministic():
    path = SCENARIOS[0]
    assert run_scenario(path).transcript == run_scenario(path).transcript
