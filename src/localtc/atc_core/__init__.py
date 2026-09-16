"""ATC logic: deterministic state machine for clearances, handoffs, sequencing and phase tracking, with a local LLM (via Ollama) for non-standard phrasing, ambiguity and emergencies. (Phase 1+)

Depends only on localtc.sim_api and localtc.bus, never on localtc.sim_bridge,
so it can be developed and tested on any OS against recordings.
"""
