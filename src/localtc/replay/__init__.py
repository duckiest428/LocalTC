"""Plays recordings back through the same SimSource interface as the live bridge."""

from localtc.replay.reader import Recording, RecordingFormatError
from localtc.replay.source import ReplaySource

__all__ = ["Recording", "RecordingFormatError", "ReplaySource"]
