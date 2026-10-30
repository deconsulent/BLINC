# gateway/timebase.py
# One clock for the gateway, the demo scenario and the fake BMS, so they agree on time.
# The manual version lets tests step time without sleeping.
import time


class Timebase:
    def __init__(self):
        self._start = time.monotonic()

    def now_s(self) -> float:
        return time.monotonic() - self._start

    def now_ms(self) -> int:
        return int(self.now_s() * 1000)


class ManualTimebase(Timebase):
    def __init__(self):
        self._t = 0.0

    def now_s(self) -> float:
        return self._t

    def advance_ms(self, ms: float) -> None:
        self._t += ms / 1000.0
