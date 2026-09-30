"""Calcul de la position d'un volet d'après son temps de course.

0 = fermé, 100 = ouvert. Le temps est fourni par l'appelant (secondes,
horloge monotone) pour que le calcul soit testable sans Home Assistant.
"""
from __future__ import annotations

UP = 1
DOWN = -1
STOPPED = 0


class Travel:
    def __init__(self, time_up: float, time_down: float, position: float = 100.0) -> None:
        self.time_up = max(float(time_up), 0.5)
        self.time_down = max(float(time_down), 0.5)
        self._pos = float(position)      # position au dernier départ / arrêt
        self._t0 = 0.0                   # instant du départ
        self.target = float(position)
        self.direction = STOPPED

    # ------------------------------------------------------------------ état
    def _speed(self, direction: int) -> float:
        return 100.0 / (self.time_up if direction == UP else self.time_down)

    def position(self, now: float) -> float:
        if self.direction == STOPPED:
            return self._pos
        p = self._pos + self.direction * self._speed(self.direction) * max(now - self._t0, 0.0)
        return min(p, self.target) if self.direction == UP else max(p, self.target)

    @property
    def moving(self) -> bool:
        return self.direction != STOPPED

    def reached(self, now: float) -> bool:
        return self.moving and abs(self.position(now) - self.target) < 1e-6

    def duration(self, target: float, start: float | None = None) -> float:
        """Temps (s) pour aller de `start` (position actuelle) à `target`."""
        start = self._pos if start is None else start
        if target == start:
            return 0.0
        d = UP if target > start else DOWN
        return abs(target - start) / self._speed(d)

    # -------------------------------------------------------------- actions
    def start(self, target: float, now: float, direction: int | None = None) -> float:
        """Démarre vers `target`. Renvoie la durée prévue du trajet (s)."""
        cur = self.position(now)
        target = min(max(float(target), 0.0), 100.0)
        if direction is None:
            direction = UP if target > cur else DOWN if target < cur else STOPPED
        self._pos, self._t0, self.target, self.direction = cur, now, target, direction
        if direction == STOPPED:
            return 0.0
        return self.duration(target, cur)

    def stop(self, now: float) -> float:
        self._pos = self.position(now)
        self.target = self._pos
        self.direction = STOPPED
        return self._pos

    def set_position(self, position: float) -> None:
        self._pos = self.target = min(max(float(position), 0.0), 100.0)
        self.direction = STOPPED
