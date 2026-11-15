"""Small, dependency-free geometry helpers. Coordinates are (lat, lon) in degrees."""
from __future__ import annotations

import math
from bisect import bisect_right
from typing import Sequence

EARTH_RADIUS_M = 6_371_008.8
METERS_PER_MILE = 1609.344

LatLon = tuple[float, float]


def haversine_m(a: LatLon, b: LatLon) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def cumulative_distances_m(points: Sequence[LatLon]) -> list[float]:
    """Distance from the first point to each point along the polyline."""
    if not points:
        return []
    out = [0.0]
    for prev, cur in zip(points, points[1:]):
        out.append(out[-1] + haversine_m(prev, cur))
    return out


def interpolate_along(
    points: Sequence[LatLon], cum: Sequence[float], target: float
) -> tuple[LatLon, int, float]:
    """
    Point at distance `target` along the polyline.

    Returns (point, segment_index, fraction) where the point lies on the segment
    points[segment_index] -> points[segment_index + 1] at `fraction` of its length.
    """
    if target <= cum[0]:
        return points[0], 0, 0.0
    if target >= cum[-1]:
        last = len(points) - 1
        return points[-1], max(0, last - 1), 1.0
    i = bisect_right(cum, target) - 1
    seg_len = cum[i + 1] - cum[i]
    f = 0.0 if seg_len == 0 else (target - cum[i]) / seg_len
    a, b = points[i], points[i + 1]
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), i, f


def interpolate_value(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    """Piecewise-linear interpolation of y at x (xs ascending, clamped at the ends)."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect_right(xs, x) - 1
    span = xs[i + 1] - xs[i]
    if span == 0:
        return ys[i + 1]
    return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / span


def _project(points: Sequence[LatLon]) -> list[tuple[float, float]]:
    """Equirectangular projection to metres — accurate enough for local tolerances."""
    if not points:
        return []
    lat0 = math.radians(sum(p[0] for p in points) / len(points))
    k = math.pi / 180 * EARTH_RADIUS_M
    return [(p[1] * k * math.cos(lat0), p[0] * k) for p in points]


def simplify_indices(points: Sequence[LatLon], tolerance_m: float) -> list[int]:
    """
    Ramer–Douglas–Peucker simplification. Returns the indices of the points to
    keep (always including the first and last), so callers can carry parallel
    per-vertex data (e.g. times) along. Iterative to avoid recursion limits on
    long routes.
    """
    n = len(points)
    if n <= 2:
        return list(range(n))
    xy = _project(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    tol2 = tolerance_m * tolerance_m
    while stack:
        start, end = stack.pop()
        ax, ay = xy[start]
        bx, by = xy[end]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        max_d2, max_i = -1.0, -1
        for i in range(start + 1, end):
            px, py = xy[i]
            if seg2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                cx, cy = ax + t * dx, ay + t * dy
                d2 = (px - cx) ** 2 + (py - cy) ** 2
            if d2 > max_d2:
                max_d2, max_i = d2, i
        if max_i != -1 and max_d2 > tol2:
            keep[max_i] = True
            stack.append((start, max_i))
            stack.append((max_i, end))
    return [i for i, k in enumerate(keep) if k]


def offset_point(origin: LatLon, bearing_deg: float, distance_m: float) -> LatLon:
    """Destination point given a start, initial bearing and distance (great circle)."""
    lat1, lon1 = math.radians(origin[0]), math.radians(origin[1])
    brng = math.radians(bearing_deg)
    d = distance_m / EARTH_RADIUS_M
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brng))
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180

