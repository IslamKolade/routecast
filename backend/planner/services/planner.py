"""
Trip planning pipeline:

    1. resolve origin / destination (coordinates, or geocode free text)
    2. fetch 3 truck route options                       (routes.py)
    3. place checkpoints every N miles with ETAs         (checkpoints.py)
    4. build the heatmap corridor grid                   (heatmap.py)
    5. fetch hourly forecasts for every needed cell once (weather.py)
    6. weather at each checkpoint's ETA -> risk level    (risk.py)
    7. summarise each route and recommend one            (ranking.py)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from ..exceptions import LocationNotFoundError
from .checkpoints import build_checkpoints
from .geo import METERS_PER_MILE, LatLon, simplify_indices
from .heatmap import HEATMAP_HOURS, MAX_CELLS, HeatmapGrid, build_heatmap, corridor_cells
from .ranking import recommend, summarize_route
from .risk import assess
from .routes import Route, get_route_options
from .tomtom import TomTomClient
from .weather import CHECKPOINT_STEP_DEG, GRID_STEP_DEG, fetch_forecasts, snap

GEOMETRY_TOLERANCE_M = 25.0
# Open-Meteo's free tier allows ~600 location lookups per minute. Keep one plan well below that.
WEATHER_CELL_BUDGET = 500
MIN_HEATMAP_CELLS = 100
MAX_CHECKPOINT_GRID_DEG = 0.5


@dataclass(frozen=True)
class Place:
    label: str
    lat: float | None = None
    lon: float | None = None

    @property
    def point(self) -> LatLon:
        return (self.lat, self.lon)


@dataclass(frozen=True)
class TripRequest:
    origin: Place
    destination: Place
    departure: datetime  # timezone-aware
    load_lb: float
    interval_miles: int


def resolve_place(client: TomTomClient, place: Place) -> Place:
    if place.lat is not None and place.lon is not None:
        return place
    results = client.search(place.label, limit=1, typeahead=False)
    if not results:
        raise LocationNotFoundError(f'Could not find a location for "{place.label}".')
    top = results[0]
    return Place(label=top["label"], lat=top["lat"], lon=top["lon"])


def _route_payload(route: Route, departure: datetime, checkpoints: list[dict], summary) -> dict:
    keep = simplify_indices(route.points, GEOMETRY_TOLERANCE_M)
    return {
        "id": route.id,
        "name": f"Route {route.id}",
        "via": route.roads,
        "distance_miles": round(route.length_m / METERS_PER_MILE, 1),
        "travel_time_s": round(route.travel_time_s),
        "traffic_delay_s": round(route.traffic_delay_s),
        "departure_time": departure.isoformat(),
        "arrival_time": (departure + timedelta(seconds=route.travel_time_s)).isoformat(),
        "geometry": [[round(route.points[i][1], 5), round(route.points[i][0], 5)] for i in keep],
        "timeline_s": [round(route.cum_s[i]) for i in keep],
        "checkpoints": checkpoints,
        "summary": summary.to_dict(),
    }


def plan_trip(trip: TripRequest, client: TomTomClient | None = None) -> dict:
    client = client or TomTomClient()
    origin = resolve_place(client, trip.origin)
    destination = resolve_place(client, trip.destination)

    routes = get_route_options(
        client,
        origin.point,
        destination.point,
        trip.departure,
        gross_weight_lb=trip.load_lb + settings.EMPTY_TRUCK_WEIGHT_LB,
    )
    checkpoints = {r.id: build_checkpoints(r, trip.departure, trip.interval_miles) for r in routes}
    cp_step, cp_cell_count = _checkpoint_grid_step(checkpoints)
    heat_cap = max(MIN_HEATMAP_CELLS, min(MAX_CELLS, WEATHER_CELL_BUDGET - cp_cell_count))
    cells, step = corridor_cells(routes, max_cells=heat_cap)
    grid = HeatmapGrid(cells=cells, step_deg=step)

    # One weather fetch covers the whole trip of every route and the 48 h heatmap window.
    latest_arrival = max(trip.departure + timedelta(seconds=r.travel_time_s) for r in routes)
    window_end = max(latest_arrival, trip.departure + timedelta(hours=HEATMAP_HOURS)) + timedelta(hours=2)
    checkpoint_cells = [snap(cp.position, cp_step) for cps in checkpoints.values() for cp in cps]
    forecasts = fetch_forecasts(checkpoint_cells + grid.cells, trip.departure, window_end)

    route_payloads = []
    summaries = []
    for route in routes:
        cp_payloads = []
        segments = []
        for cp in checkpoints[route.id]:
            sample = forecasts[snap(cp.position, cp_step)].sample_at(cp.eta)
            risk = assess(sample.wind_mph or 0.0, sample.rain_in_hr or 0.0, sample.snow_in_hr or 0.0, trip.load_lb)
            segments.append((cp.covers_miles, risk.level))
            cp_payloads.append(
                {
                    "index": cp.index,
                    "mile": round(cp.mile, 1),
                    "lat": round(cp.position[0], 5),
                    "lon": round(cp.position[1], 5),
                    "eta": cp.eta.isoformat(),
                    "covers_miles": round(cp.covers_miles, 2),
                    "weather": sample.to_dict(),
                    "risk": risk.to_dict(),
                }
            )
        summary = summarize_route(route.id, segments, route.travel_time_s)
        summaries.append(summary)
        route_payloads.append(_route_payload(route, trip.departure, cp_payloads, summary))

    recommendation = recommend(summaries)
    rank_of = {route_id: i + 1 for i, route_id in enumerate(recommendation.ranking)}
    for payload in route_payloads:
        payload["rank"] = rank_of[payload["id"]]
        payload["recommended"] = payload["id"] == recommendation.route_id

    return {
        "request": {
            "origin": {"label": origin.label, "lat": origin.lat, "lon": origin.lon},
            "destination": {"label": destination.label, "lat": destination.lat, "lon": destination.lon},
            "departure_time": trip.departure.isoformat(),
            "load_weight_lb": trip.load_lb,
            "interval_miles": trip.interval_miles,
        },
        "routes": route_payloads,
        "recommendation": recommendation.to_dict(),
        "notes": _notes(routes, cp_step),
        "heatmap": build_heatmap(grid, forecasts, trip.departure, trip.load_lb),
    }


def _checkpoint_grid_step(checkpoints: dict[str, list]) -> tuple[float, int]:
    """
    Precision of checkpoint weather lookups. Normally each checkpoint is looked
    up at its own position (rounded to 0.01°, about 1 km, so checkpoints on a
    stretch shared by two routes reuse one lookup). Only when a trip is so long
    that this would exceed the weather API budget (e.g. coast to coast at
    10-mile sampling) are checkpoints snapped to a coarser grid, just enough to fit.
    """
    step = CHECKPOINT_STEP_DEG
    while True:
        count = len({snap(cp.position, step) for cps in checkpoints.values() for cp in cps})
        if count <= WEATHER_CELL_BUDGET - MIN_HEATMAP_CELLS or step >= MAX_CHECKPOINT_GRID_DEG:
            return step, count
        step = GRID_STEP_DEG if step < GRID_STEP_DEG else round(step + GRID_STEP_DEG, 4)


def _notes(routes: list[Route], cp_step: float) -> list[str]:
    notes = []
    if len(routes) < 3:
        notes.append(
            f"Only {len(routes)} distinct truck route(s) exist between these points; "
            "all available options are shown."
        )
    if cp_step > CHECKPOINT_STEP_DEG:
        notes.append(
            f"Long trip: checkpoint forecasts use a {cp_step:.1f}° weather grid to stay within "
            "the free weather API's rate limit."
        )
    return notes
