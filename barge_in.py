"""Confirm realtime speech interruptions against sustained 16 kHz microphone energy."""

import audioop
from collections import deque


class BargeInConfirmation:
    # Require 300 ms above the floor within 440 ms, tolerating small
    # gaps between syllables or consecutive low-energy consonants.
    FRAME_BYTES = 640
    MIN_RMS = 160
    WINDOW_FRAMES = 22
    MIN_VOICED_FRAMES = 15

    def __init__(self):
        self.frames = deque(maxlen=self.WINDOW_FRAMES)
        self.tail = b''
        self.pending = False

    def reset(self):
        self.frames.clear()
        self.tail = b''
        self.pending = False

    def feed(self, pcm):
        pcm = self.tail + pcm
        end = len(pcm) - len(pcm) % self.FRAME_BYTES
        for start in range(0, end, self.FRAME_BYTES):
            frame = pcm[start:start + self.FRAME_BYTES]
            self.frames.append(audioop.rms(frame, 2) >= self.MIN_RMS)
        self.tail = pcm[end:]
        return self._confirm()

    def speech_started(self):
        self.pending = True
        return self._confirm()

    def _confirm(self):
        if not self.pending or sum(self.frames) < self.MIN_VOICED_FRAMES:
            return False
        self.pending = False
        return True
