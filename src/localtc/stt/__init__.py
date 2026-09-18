"""Pilot speech-to-text: push-to-talk capture and faster-whisper, publishing ``Transcript`` events.

Depends only on ``localtc.sim_api`` and ``localtc.bus`` (never the bridge or the ATC core), so it
runs and is tested on any OS against recordings. faster-whisper, sounddevice and pynput are
imported lazily: without them, typed input still works.
"""

from localtc.stt.vocabulary import VocabularyHints, build_prompt, fixup

__all__ = ["VocabularyHints", "build_prompt", "fixup"]
