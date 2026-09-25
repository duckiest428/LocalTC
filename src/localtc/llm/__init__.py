"""Clients for local language models (Ollama). Implements ``atc_core.llm.LlmBackend``."""

from localtc.llm.ollama import OllamaBackend, OllamaStatus, Placement

__all__ = ["OllamaBackend", "OllamaStatus", "Placement"]
