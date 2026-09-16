"""Pilot speech-to-text via faster-whisper. Consumes PttReleased audio, publishes Transcript. (Phase 1+)

Depends only on localtc.sim_api and localtc.bus, never on localtc.sim_bridge,
so it can be developed and tested on any OS against recordings.
"""
