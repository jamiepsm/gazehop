from __future__ import annotations

from typing import NamedTuple


class Rect(NamedTuple):
    """Screen rectangle in global pixels, origin top-left."""
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def mid(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    def contains(self, p) -> bool:
        return self.x <= p[0] < self.right and self.y <= p[1] < self.bottom

    def inset(self, d: float) -> "Rect":
        """Shrinks by d on every side (negative d grows)."""
        return Rect(self.x + d, self.y + d, self.w - 2 * d, self.h - 2 * d)

    def distance(self, p) -> float:
        dx = max(self.x - p[0], 0, p[0] - self.right)
        dy = max(self.y - p[1], 0, p[1] - self.bottom)
        return (dx * dx + dy * dy) ** 0.5

    def clamp(self, p) -> tuple[float, float]:
        return (min(max(p[0], self.x), self.right), min(max(p[1], self.y), self.bottom))


class Win(NamedTuple):
    """A top-level window as the platform backend sees it."""
    id: int
    rect: Rect
    pid: int
    title: str
