"""
Route options: turn TomTom responses into `Route` objects with per-vertex
distance and time, and make sure we return three distinct options.

Per-vertex time (ETA)
---------------------
TomTom's guidance instructions carry `pointIndex` (a vertex of the polyline)
and `travelTimeInSeconds` (cumulative, traffic-aware travel time from the start
to that point). These are used as time anchors; between two anchors time is
interpolated by distance. This keeps realistic speed differences (city streets
vs. interstate) instead of assuming one average speed for the whole trip.

Three options
-------------
We first ask TomTom for the best route plus 2 alternatives. When the road
network does not offer enough alternatives, we force a different path by asking
TomTom to *avoid an area* around the middle of the routes found so far. The
router then picks the best road around it, which gives realistic
alternatives (a parallel highway), unlike forcing a via point, which can
create dead-end detours. A forced route is kept only if it is genuinely
different, not absurdly slow, and does not double back on itself.
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from ..exceptions import NoRouteError, PlannerError
from .geo import (
    LatLon,
    cumulative_distances_m,
    interpolate_along,
    interpolate_value,
    offset_point,
)
from .tomtom import TomTomClient

logger = logging.getLogger(__name__)

TARGET_ROUTE_COUNT = 3
FORCED_BATCH_SIZE = 3  # forced-alternative requests run in parallel batches
AVOID_FRACTIONS = (0.5, 0.35, 0.65)  # where along the routes to place avoid areas
MAX_TIME_RATIO = 1.6  # a forced alternative may take at most 60% longer than the best route
MIN_DISTINCT_FRACTION = 0.10  # at least 10% of a new route must be away from existing ones
MAX_BACKTRACK_FRACTION = 0.02  # reject routes that drive the same road twice (dead-end spurs)


@dataclass
class Route:
    points: list[LatLon]
    cum_m: list[float]  # distance from origin at each vertex (scaled to provider length)
    cum_s: list[float]  # seconds from departure at each vertex
    length_m: float
    travel_time_s: float
    traffic_delay_s: float
    roads: list[str] = field(default_factory=list)
    id: str = ""

    def position_at_distance(self, distance_m: float) -> LatLon:
        return interpolate_along(self.points, self.cum_m, distance_m)[0]

    def time_at_distance(self, distance_m: float) -> float:
        return interpolate_value(self.cum_m, self.cum_s, distance_m)


def parse_route(raw: dict) -> Route:
    summary = raw.get("summary", {})
    points: list[LatLon] = [
        (p["latitude"], p["longitude"]) for leg in raw.get("legs", []) for p in leg.get("points", [])
    ]
    if len(points) < 2:
        raise NoRouteError("The routing service returned an empty route.")

    raw_cum = cumulative_distances_m(points)
    length_m = float(summary.get("lengthInMeters") or raw_cum[-1])
    scale = length_m / raw_cum[-1] if raw_cum[-1] > 0 else 1.0
    cum_m = [d * scale for d in raw_cum]

    total_s = float(summary.get("travelTimeInSeconds") or 0)
    instructions = (raw.get("guidance") or {}).get("instructions", [])
    cum_s = _vertex_times(cum_m, total_s, instructions)

    return Route(
        points=points,
        cum_m=cum_m,
        cum_s=cum_s,
        length_m=length_m,
        travel_time_s=total_s,
        traffic_delay_s=float(summary.get("trafficDelayInSeconds") or 0),
        roads=_main_roads(instructions, length_m),
    )


def _vertex_times(cum_m: list[float], total_s: float, instructions: list[dict]) -> list[float]:
    n = len(cum_m)
    anchors: dict[int, float] = {0: 0.0, n - 1: total_s}
    for ins in instructions:
        idx, t = ins.get("pointIndex"), ins.get("travelTimeInSeconds")
        if isinstance(idx, int) and isinstance(t, (int, float)) and 0 < idx < n - 1:
            anchors[idx] = float(t)
    xs, ys = [], []
    last_t = 0.0
    for idx in sorted(anchors):
        # Guard against non-monotonic data so time never runs backwards.
        t = min(max(anchors[idx], last_t), total_s)
        xs.append(cum_m[idx])
        ys.append(t)
        last_t = t
    return [interpolate_value(xs, ys, d) for d in cum_m]


def _main_roads(instructions: list[dict], length_m: float, limit: int = 3) -> list[str]:
    """Road numbers (e.g. I-40) carrying the most distance, in driving order."""
    totals: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for i, ins in enumerate(instructions[:-1]):
        numbers = ins.get("roadNumbers") or []
        if not numbers:
            continue
        span = float(instructions[i + 1].get("routeOffsetInMeters", 0)) - float(ins.get("routeOffsetInMeters", 0))
        name = numbers[0]
        totals[name] = totals.get(name, 0.0) + max(span, 0.0)
        first_seen.setdefault(name, i)
    major = [r for r, d in totals.items() if d >= 0.05 * length_m]
    major = sorted(major, key=lambda r: totals[r], reverse=True)[:limit]
    return sorted(major, key=lambda r: first_seen[r])


# --------------------------------------------------------------------- options
def get_route_options(
    client: TomTomClient,
    origin: LatLon,
    destination: LatLon,
    departure: datetime,
    gross_weight_lb: float,
    count: int = TARGET_ROUTE_COUNT,
) -> list[Route]:
    raw_routes = client.calculate_route(
        [origin, destination],
        departure=departure,
        gross_weight_lb=gross_weight_lb,
        max_alternatives=count - 1,
    )
    routes = [parse_route(r) for r in raw_routes[:count]]
    if len(routes) < count:
        _add_forced_alternatives(client, routes, origin, destination, departure, gross_weight_lb, count)
    for i, route in enumerate(routes):
        route.id = chr(ord("A") + i)
    return routes


Rectangle = tuple[LatLon, LatLon]  # (south-west corner, north-east corner)


def _add_forced_alternatives(
    client: TomTomClient,
    routes: list[Route],
    origin: LatLon,
    destination: LatLon,
    departure: datetime,
    gross_weight_lb: float,
    count: int,
) -> None:
    primary = routes[0]

    def attempt(avoid: list[Rectangle]) -> Route | None:
        try:
            raw = client.calculate_route(
                [origin, destination], departure=departure, gross_weight_lb=gross_weight_lb, avoid_areas=avoid
            )
            return parse_route(raw[0])
        except PlannerError as exc:
            logger.info("Forced alternative rejected: %s", exc.message)
            return None

    # Requests take seconds each, so a batch runs in parallel; results are still
    # accepted in candidate order, so the outcome does not depend on timing.
    for scale in (1.0, 1.8):
        batch = [_avoid_areas(routes, fraction, scale) for fraction in AVOID_FRACTIONS]
        with ThreadPoolExecutor(max_workers=FORCED_BATCH_SIZE) as pool:
            candidates = list(pool.map(attempt, batch))
        for candidate in candidates:
            if len(routes) >= count:
                return
            if candidate is None or candidate.travel_time_s > primary.travel_time_s * MAX_TIME_RATIO:
                continue
            if _is_distinct(candidate, routes) and _backtrack_fraction(candidate) <= MAX_BACKTRACK_FRACTION:
                routes.append(candidate)
        if len(routes) >= count:
            return


def _avoid_areas(routes: list[Route], fraction: float, scale: float) -> list[Rectangle]:
    """A square around the point at `fraction` along each existing route."""
    boxes = []
    for route in routes:
        half = min(max(0.07 * route.length_m, 5_000), 60_000) * scale
        center = route.position_at_distance(route.length_m * fraction)
        sw = offset_point(offset_point(center, 180, half), 270, half)
        ne = offset_point(offset_point(center, 0, half), 90, half)
        boxes.append((sw, ne))
    return boxes


def _sample(route: Route, n: int = 150) -> list[LatLon]:
    return [route.position_at_distance(route.length_m * i / (n - 1)) for i in range(n)]


def _to_xy(points: list[LatLon], lat0_deg: float) -> list[tuple[float, float]]:
    """Equirectangular projection to metres around latitude `lat0_deg`."""
    k = 111_195.0  # metres per degree of latitude
    kx = k * math.cos(math.radians(lat0_deg))
    return [(p[1] * kx, p[0] * k) for p in points]


def _is_distinct(candidate: Route, existing: list[Route]) -> bool:
    threshold_m = max(2_000.0, 0.01 * candidate.length_m)
    samples = _sample(candidate)
    lat0 = sum(p[0] for p in samples) / len(samples)
    others_xy = _to_xy([p for r in existing for p in _sample(r)], lat0)
    away = 0
    for x, y in _to_xy(samples, lat0):
        nearest = min((x - ox) ** 2 + (y - oy) ** 2 for ox, oy in others_xy)
        if nearest > threshold_m**2:
            away += 1
    return away / len(samples) >= MIN_DISTINCT_FRACTION


def _backtrack_fraction(route: Route, n: int = 200) -> float:
    """
    Share of the route that passes within 1 km of a point it already drove
    through much earlier, i.e. an out-and-back spur. Normal routes score ~0.
    """
    samples = _sample(route, n)
    xy = _to_xy(samples, sum(p[0] for p in samples) / n)
    step_m = route.length_m / (n - 1)
    min_gap = max(3, math.ceil(max(10_000.0, 0.03 * route.length_m) / step_m))
    near2 = 1_000.0**2
    hits = 0
    for i in range(min_gap + 1, n):
        p = xy[i]
        # Distance to the earlier path *segments* (not just sample points), so samples
        # that fall between two outbound samples are still matched.
        if any(_seg_dist2(p, xy[j], xy[j + 1]) < near2 for j in range(i - min_gap)):
            hits += 1
    return hits / n


def _seg_dist2(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / seg2))
    cx, cy = a[0] + t * dx, a[1] + t * dy
    return (p[0] - cx) ** 2 + (p[1] - cy) ** 2
