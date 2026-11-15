"""
Checkpoint sampling.

Checkpoints are placed every `interval_miles` (10, 25 or 50) along the route,
starting at the origin (mile 0) and always ending at the destination, even
when the route length is not a multiple of the interval.

Each checkpoint stands for the stretch of road around it: from the midpoint
with the previous checkpoint to the midpoint with the next one. With 25-mile
sampling a mid-route checkpoint therefore represents 25 miles, the origin and
destination 12.5 miles each (or less for a short final gap). The represented
miles always add up exactly to the route length, which is what makes
"Severe miles" and "High miles" meaningful.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .geo import METERS_PER_MILE, LatLon
from .routes import Route

ALLOWED_INTERVALS_MILES = (10, 25, 50)
# Skip a final interval shorter than this so the destination doesn't get a near-duplicate checkpoint.
_MIN_TAIL_M = 0.05 * METERS_PER_MILE


@dataclass(frozen=True)
class Checkpoint:
    index: int
    distance_m: float
    position: LatLon
    eta: datetime
    covers_m: float  # length of road this checkpoint represents

    @property
    def mile(self) -> float:
        return self.distance_m / METERS_PER_MILE

    @property
    def covers_miles(self) -> float:
        return self.covers_m / METERS_PER_MILE


def checkpoint_distances(length_m: float, interval_miles: float) -> list[float]:
    step = interval_miles * METERS_PER_MILE
    distances = []
    d = 0.0
    while d < length_m - _MIN_TAIL_M:
        distances.append(d)
        d += step
    distances.append(length_m)
    return distances


def coverage(distances: list[float]) -> list[float]:
    """Length of road each sample represents (midpoint to midpoint)."""
    if len(distances) == 1:
        return [0.0]
    out = []
    for i, d in enumerate(distances):
        start = distances[0] if i == 0 else (distances[i - 1] + d) / 2
        end = distances[-1] if i == len(distances) - 1 else (d + distances[i + 1]) / 2
        out.append(end - start)
    return out


def build_checkpoints(route: Route, departure: datetime, interval_miles: float) -> list[Checkpoint]:
    distances = checkpoint_distances(route.length_m, interval_miles)
    covers = coverage(distances)
    return [
        Checkpoint(
            index=i,
            distance_m=d,
            position=route.position_at_distance(d),
            eta=departure + timedelta(seconds=route.time_at_distance(d)),
            covers_m=c,
        )
        for i, (d, c) in enumerate(zip(distances, covers))
    ]
