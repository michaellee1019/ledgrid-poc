"""One pending point and one finite, phase-timed pulse for aimable renderers."""

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, eq=False)
class AimedPoint:
    x: int
    y: int
    strength: float

    @classmethod
    def from_input(cls, kind, x, y, strength, width, height):
        values = (x, y, strength)
        if kind != "primary" or any(type(value) not in (int, float) for value in values):
            return None
        if 0 <= x < width and 0 <= y < height and 0 < strength <= 1:
            return cls(int(x), int(y), strength)
        return None


class PointPulse:
    def __init__(self, duration: float):
        self.duration = duration
        self._lock = Lock()
        self._pending: AimedPoint | None = None
        self._active: AimedPoint | None = None
        self._start = 0.0

    def trigger(self, point: AimedPoint) -> bool:
        with self._lock:
            if self._pending is not None:
                return False
            self._pending = point
        return True

    def sample(self, phase_time: float) -> tuple[AimedPoint | None, float]:
        if self._pending is not None:
            with self._lock:
                self._active, self._pending = self._pending, None
            self._start = phase_time
        age = phase_time - self._start
        if age < 0 or age >= self.duration:
            self._active = None
        return self._active, age / self.duration
