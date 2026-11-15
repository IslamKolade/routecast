"""
Trip planning pipeline:

    1. resolve origin / destination (coordinates, or geocode free text)
    2. fetch 3 truck route options                       (routes.py)
    3. place checkpoints every N miles with ETAs         (checkpoints.py)
    4. build the heatmap corridor grid                   (heatmap.py)
    5. fetch hourly forecasts for every needed cell once (weather.py)
    6. weather at each checkpoint's ETA -> risk level    (risk.py)
    7. summarise each route and recommend one            (ranking.py)

Weather relay
-------------
Open-Meteo's free tier limits requests per IP address. On shared hosting (e.g.
Render's free tier) many apps share one outgoing IP, so the server can be
rate-limited by other people's traffic. When that happens, steps 1–4 are saved
as a short-lived draft and the response asks the browser to download the
missing forecasts with exactly the same query (from the viewer's own IP) and
post them back. Steps 6–7 then run on the server as usual, so all risk and
recommendation logic stays in Django; only the download is relayed.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import caches

from ..exceptions import LocationNotFoundError, PlanExpiredError, PlannerError, UpstreamServiceError
from .geo import METERS_PER_MILE, LatLon, simplify_indices
from .heatmap import HEATMAP_HOURS, MAX_CELLS, HeatmapGrid, build_heatmap, corridor_cells
from .ranking import recommend, summarize_route
from .risk import assess
from .routes import Route, get_route_options
from .tomtom import TomTomClient
from .checkpoints import Checkpoint, build_checkpoints
from .weather import (
    API_URL,
    CHECKPOINT_STEP_DEG,
    CHUNK_SIZE,
    GRID_STEP_DEG,
    Forecast,
    WeatherRateLimitError,
    cached_forecasts,
    fetch_forecasts,
    parse_item,
    request_params,
    server_rate_limited,
    snap,
    store_forecasts,
    window,
)

GEOMETRY_TOLERANCE_M = 25.0
# Open-Meteo's free tier allows ~600 location lookups per minute per IP. Keep one plan well
# below that, so a viewer can plan a couple of trips a minute even when the browser relays.
WEATHER_CELL_BUDGET = 350
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


@dataclass
class PlanDraft:
    """Everything computed before the weather is known (steps 1–4)."""

    trip: TripRequest
    origin: Place
    destination: Place
    routes: list[Route]
    checkpoints: dict[str, list[Checkpoint]]
    cp_step: float
    grid: HeatmapGrid
    window_start: datetime
    window_end: datetime

    def checkpoint_cell(self, cp: Checkpoint) -> LatLon:
        return snap(cp.position, self.cp_step)

    @property
    def cells(self) -> list[LatLon]:
        """Every weather cell this plan needs, without duplicates."""
        cp_cells = [self.checkpoint_cell(cp) for cps in self.checkpoints.values() for cp in cps]
        return list(dict.fromkeys(cp_cells + self.grid.cells))


DRAFT_SECONDS = 15 * 60
_DRAFT_KEY = "plan-draft:{}"


def prepare_plan(trip: TripRequest, client: TomTomClient | None = None) -> PlanDraft:
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

    # One weather window covers the whole trip of every route and the 48 h heatmap.
    latest_arrival = max(trip.departure + timedelta(seconds=r.travel_time_s) for r in routes)
    window_end = max(latest_arrival, trip.departure + timedelta(hours=HEATMAP_HOURS)) + timedelta(hours=2)
    start, end = window(trip.departure, window_end)
    return PlanDraft(
        trip=trip,
        origin=origin,
        destination=destination,
        routes=routes,
        checkpoints=checkpoints,
        cp_step=cp_step,
        grid=HeatmapGrid(cells=cells, step_deg=step),
        window_start=start,
        window_end=end,
    )


def plan_trip(trip: TripRequest, client: TomTomClient | None = None) -> dict:
    """
    Plan a trip. Returns the finished plan, or — when the server cannot get the
    weather itself — a `weather_required` response for the browser relay.
    """
    draft = prepare_plan(trip, client)
    cells = draft.cells
    if not server_rate_limited():
        try:
            forecasts = fetch_forecasts(cells, draft.window_start, draft.window_end)
            return finish_plan(draft, forecasts)
        except WeatherRateLimitError:
            pass
    forecasts = cached_forecasts(cells, draft.window_start, draft.window_end)
    missing = [c for c in cells if c not in forecasts]
    if not missing:
        return finish_plan(draft, forecasts)

    plan_id = secrets.token_urlsafe(16)
    caches["shared"].set(_DRAFT_KEY.format(plan_id), (draft, missing), DRAFT_SECONDS)
    return {
        "status": "weather_required",
        "plan_id": plan_id,
        "weather_request": {
            "url": API_URL,
            "params": request_params(draft.window_start, draft.window_end),
            "cells": [[lat, lon] for lat, lon in missing],
            "chunk_size": CHUNK_SIZE,
        },
    }


def complete_plan(plan_id: str, items: list) -> dict:
    """Finish a plan with forecasts relayed by the browser, in the order they were requested."""
    stored = caches["shared"].get(_DRAFT_KEY.format(plan_id))
    if stored is None:
        raise PlanExpiredError("This plan has expired. Please plan the trip again.")
    draft, missing = stored
    if not isinstance(items, list) or len(items) != len(missing):
        raise PlannerError(f"Expected forecasts for {len(missing)} locations, got {len(items) if isinstance(items, list) else 0}.")
    try:
        relayed = {cell: parse_item(item, draft.window_start, draft.window_end) for cell, item in zip(missing, items)}
    except UpstreamServiceError as exc:
        raise PlannerError(f"Invalid weather data: {exc.message}") from exc
    store_forecasts(relayed, draft.window_start, draft.window_end)

    forecasts = cached_forecasts(draft.cells, draft.window_start, draft.window_end)
    forecasts.update(relayed)
    if any(c not in forecasts for c in draft.cells):
        raise PlanExpiredError("Some weather data expired. Please plan the trip again.")
    caches["shared"].delete(_DRAFT_KEY.format(plan_id))
    return finish_plan(draft, forecasts)


def finish_plan(draft: PlanDraft, forecasts: dict[LatLon, Forecast]) -> dict:
    """Steps 6–7: risk at every checkpoint, route summaries, recommendation, heatmap."""
    trip = draft.trip
    route_payloads = []
    summaries = []
    for route in draft.routes:
        cp_payloads = []
        segments = []
        for cp in draft.checkpoints[route.id]:
            sample = forecasts[draft.checkpoint_cell(cp)].sample_at(cp.eta)
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
        "status": "complete",
        "request": {
            "origin": {"label": draft.origin.label, "lat": draft.origin.lat, "lon": draft.origin.lon},
            "destination": {
                "label": draft.destination.label,
                "lat": draft.destination.lat,
                "lon": draft.destination.lon,
            },
            "departure_time": trip.departure.isoformat(),
            "load_weight_lb": trip.load_lb,
            "interval_miles": trip.interval_miles,
        },
        "routes": route_payloads,
        "recommendation": recommendation.to_dict(),
        "notes": _notes(draft.routes, draft.cp_step),
        "heatmap": build_heatmap(draft.grid, forecasts, trip.departure, trip.load_lb),
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
