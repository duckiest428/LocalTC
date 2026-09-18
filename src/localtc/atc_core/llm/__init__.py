"""The local language model's two jobs: understanding pilots and wording non-routine replies."""

from localtc.atc_core.llm.backend import LlmBackend, LlmReply, LlmRequest, RecordedBackend
from localtc.atc_core.llm.phrase import LlmPhraser
from localtc.atc_core.llm.triggers import is_question, trigger
from localtc.atc_core.llm.understand import LlmInterpreter, build_request, parse_answer

__all__ = [
    "LlmBackend",
    "LlmInterpreter",
    "LlmPhraser",
    "LlmReply",
    "LlmRequest",
    "RecordedBackend",
    "build_request",
    "is_question",
    "parse_answer",
    "trigger",
]
