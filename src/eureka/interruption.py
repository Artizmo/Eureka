"""Bounded, conservative local interruption detection for 8 kHz PCM calls.

This is not an acoustic echo canceller; thresholds require controlled-phone validation.
"""
from __future__ import annotations

import heapq
import math
import operator
import struct
import time
from collections import deque


class InterruptionDetector:
    FRAME_BYTES = 320

    def __init__(self):
        self._reference = deque(maxlen=40)
        self._pre_roll = deque(maxlen=25)
        self._partial = bytearray()
        self._voiced = 0
        self._levels = deque(maxlen=8)

    def played(self, frame: bytes) -> None:
        if len(frame) == self.FRAME_BYTES:
            self._reference.append((time.monotonic(), struct.unpack('<160h', frame)))

    @staticmethod
    def _correlation(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        aa = sum(x * x for x in a)
        bb = sum(y * y for y in b)
        return dot / math.sqrt(aa * bb) if aa and bb else 0.0

    def _remove_echo(self, samples):
        now = time.monotonic()
        while self._reference and now - self._reference[0][0] > 1.0:
            self._reference.popleft()
        reference = [s for _, frame in self._reference for s in frame]
        if len(reference) < 160:
            return samples
        # Examine every sample offset, then verify the strongest decimated
        # candidates against the complete frame before subtracting echo.
        target = samples[::8]
        powers = [sum(x * x for x in reference[i:i + 160:8]) for i in range(8)]
        candidates = []
        for offset in range(len(reference) - 159):
            phase = offset % 8
            if offset >= 8:
                powers[phase] += reference[offset + 152] ** 2 - reference[offset - 8] ** 2
            power = powers[phase]
            if power:
                dot = sum(map(operator.mul, target, reference[offset:offset + 160:8]))
                score = dot * dot / power
                if len(candidates) < 16:
                    heapq.heappush(candidates, (score, offset))
                elif score > candidates[0][0]:
                    heapq.heapreplace(candidates, (score, offset))
        best = (0.0, None)
        for _, offset in candidates:
            candidate = reference[offset:offset + 160]
            correlation = abs(self._correlation(samples, candidate))
            if correlation > best[0]:
                best = correlation, offset
        if best[0] < 0.78:
            return samples
        candidate = reference[best[1]:best[1] + 160]
        power = sum(x * x for x in candidate)
        if not power:
            return samples
        gain = sum(x * y for x, y in zip(samples, candidate)) / power
        return tuple(x - gain * y for x, y in zip(samples, candidate))

    def feed(self, payload: bytes) -> list[bytes] | None:
        self._partial.extend(payload)
        while len(self._partial) >= self.FRAME_BYTES:
            frame = bytes(self._partial[:self.FRAME_BYTES])
            del self._partial[:self.FRAME_BYTES]
            self._pre_roll.append(frame)
            raw = struct.unpack('<160h', frame)
            mean = sum(raw) / 160
            samples = tuple(x - mean for x in raw)
            rms = math.sqrt(sum(x * x for x in samples) / 160)
            if rms >= 450 and self._reference:
                samples = self._remove_echo(samples)
                rms = math.sqrt(sum(x * x for x in samples) / 160)
            periodicity = max((abs(self._correlation(samples[:-lag], samples[lag:]))
                               for lag in range(20, 81, 2)), default=0) if rms >= 450 else 0
            if rms >= 450 and periodicity >= 0.58:
                self._voiced += 1
                self._levels.append(rms)
                if self._voiced >= 4 and max(self._levels) >= min(self._levels) * 1.15:
                    captured = list(self._pre_roll)
                    if self._partial:
                        captured.append(bytes(self._partial))
                        self._partial.clear()
                    return captured
            else:
                self._voiced = 0
                self._levels.clear()
        return None

    def unfinished(self) -> list[bytes]:
        if not self._voiced:
            return []
        return list(self._pre_roll) + ([bytes(self._partial)] if self._partial else [])
