"""Collect one 16 kHz PCM question for the DeepSeek voice-qa endpoint."""

from collections import deque

import webrtcvad


class VoiceTurn:
    RATE = 16000
    FRAME_MS = 20
    FRAME_BYTES = RATE * FRAME_MS // 1000 * 2
    START_FRAMES = 5
    PREROLL_FRAMES = 12
    END_FRAMES = 50
    TRAILING_FRAMES = 10
    MAX_BYTES = 110 * RATE * 2

    def __init__(self):
        self.vad = webrtcvad.Vad(2)
        self.tail = b''
        self.preroll = deque(maxlen=self.PREROLL_FRAMES)
        self.start_window = deque(maxlen=self.START_FRAMES)
        self.audio = bytearray()
        self.quiet_frames = 0

    def feed(self, pcm):
        started = False
        pcm = self.tail + pcm
        frame_end = len(pcm) - len(pcm) % self.FRAME_BYTES
        self.tail = pcm[frame_end:]
        for index in range(0, frame_end, self.FRAME_BYTES):
            frame = pcm[index:index + self.FRAME_BYTES]
            voiced = self.vad.is_speech(frame, self.RATE)
            if not self.audio:
                self.preroll.append(frame)
                self.start_window.append(voiced)
                if sum(self.start_window) >= 3:
                    started = True
                    self.audio.extend(b''.join(self.preroll))
                continue
            self.audio.extend(frame)
            self.quiet_frames = 0 if voiced else self.quiet_frames + 1
            if self.quiet_frames >= self.END_FRAMES or len(self.audio) >= self.MAX_BYTES:
                trim = max(0, self.quiet_frames - self.TRAILING_FRAMES) * self.FRAME_BYTES
                return started, bytes(self.audio[:len(self.audio) - trim])
        return started, None
