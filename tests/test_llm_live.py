"""The seeded edge cases against a real local model. Skipped unless Ollama is running with the model.

    pytest -m ollama -s          (or: localtc llm eval)
"""

import pytest

from localtc.atc_core.llm import LlmInterpreter
from localtc.config import load_config
from localtc.llm import OllamaBackend
from localtc.llm.eval import format_results, load_cases, run_cases

pytestmark = pytest.mark.ollama


@pytest.fixture(scope="module")
def backend():
    llm = load_config().llm
    backend = OllamaBackend(model=llm.model, base_url=llm.base_url)
    status = backend.status(timeout_s=1.0)
    if not status.reachable or not status.has(llm.model):
        pytest.skip(f"Ollama with {llm.model} isn't available at {llm.base_url}")
    return backend


def test_seeded_cases_with_the_real_model(backend):
    results = run_cases(LlmInterpreter(backend, timeout_s=30.0, budget_s=60.0), load_cases())
    print("\n" + format_results(results))
    passed = sum(r.passed for r in results)
    assert passed >= 0.85 * len(results), format_results([r for r in results if not r.passed])
