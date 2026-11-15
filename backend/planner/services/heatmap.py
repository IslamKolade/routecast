"""
Weather heatmap along the trip corridor.

The corridor is every grid cell within `buffer_km` of any route. The grid step
is a multiple of the 0.1° weather grid, grown until the corridor has at most
MAX_CELLS cells, so a short trip gets a fine grid and a cross-country trip a
coarser one while the payload and the number of weather lookups stay bounded.

For each hour 0..48 after departure we store, per cell, the overall risk level
(using the same rules and load weight as the checkpoints) plus the raw wind /
rain / snow values. The frontend receives every hour up front, so moving the
forecast slider never triggers a network request.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .geo import LatLon
from .risk import assess, classify_rain, classify_snow
from .routes import Route
from .weather import GRID_STEP_DEG, Forecast

HEATMAP_HOURS = 48
MAX_CELLS = 300
KM_PER_DEG_LAT = 111.195


def _buffer_km(routes: list[Route]) -> float:
    longest_km = max(r.length_m for r in routes) / 1000
    return min(max(0.06 * longest_km, 25.0), 100.0)


def _cells_for(routes: list[Route], step_deg: float, buffer_km: float) -> list[LatLon]:
    step_km = step_deg * KM_PER_DEG_LAT
    cells: set[tuple[int, int]] = set()
    for route in routes:
        n = max(2, int(route.length_m / 1000 / (step_km / 2)) + 1)
        for s in range(n):
            lat, lon = route.position_at_distance(route.length_m * s / (n - 1))
            dlat = buffer_km / KM_PER_DEG_LAT
            dlon = buffer_km / (KM_PER_DEG_LAT * max(math.cos(math.radians(lat)), 0.1))
            i0, i1 = math.floor((lat - dlat) / step_deg), math.ceil((lat + dlat) / step_deg)
            j0, j1 = math.floor((lon - dlon) / step_deg), math.ceil((lon + dlon) / step_deg)
            coslat = math.cos(math.radians(lat))
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    if (i, j) in cells:
                        continue
                    dy = (i * step_deg - lat) * KM_PER_DEG_LAT
                    dx = (j * step_deg - lon) * KM_PER_DEG_LAT * coslat
                    if dx * dx + dy * dy <= buffer_km * buffer_km:
                        cells.add((i, j))
    return [(round(i * step_deg, 4), round(j * step_deg, 4)) for i, j in sorted(cells)]


def corridor_cells(routes: list[Route], max_cells: int = MAX_CELLS) -> tuple[list[LatLon], float]:
    buffer_km = _buffer_km(routes)
    # Initial guess from the longest route's corridor area (alternatives mostly overlap it),
    # then coarsen until the corridor fits under the cap.
    area_km2 = max(r.length_m for r in routes) / 1000 * 2 * buffer_km
    k = max(1, math.floor(math.sqrt(area_km2 / max_cells) / (GRID_STEP_DEG * KM_PER_DEG_LAT)))
    while True:
        step = round(GRID_STEP_DEG * k, 4)
        cells = _cells_for(routes, step, buffer_km)
        if len(cells) <= max_cells:
            return cells, step
        k += 1


@dataclass
class HeatmapGrid:
    cells: list[LatLon]
    step_deg: float


def _round(v, digits):
    return None if v is None else round(v, digits)


def build_heatmap(
    grid: HeatmapGrid, forecasts: dict[LatLon, Forecast], departure: datetime, load_lb: float
) -> dict:
    frames = []
    for h in range(HEATMAP_HOURS + 1):
        t = departure + timedelta(hours=h)
        frame = {"time": t.isoformat(), "level": [], "wind": [], "rain": [], "snow": [], "rain_level": [],
                 "snow_level": [], "wind_level": []}
        for cell in grid.cells:
            s = forecasts[cell].sample_at(t)
            if s.complete:
                a = assess(s.wind_mph, s.rain_in_hr, s.snow_in_hr, load_lb)
                frame["level"].append(int(a.level))
                frame["wind_level"].append(int(a.wind_level))
                frame["rain_level"].append(int(classify_rain(s.rain_in_hr)))
                frame["snow_level"].append(int(classify_snow(s.snow_in_hr)))
            else:
                for key in ("level", "wind_level", "rain_level", "snow_level"):
                    frame[key].append(None)
            frame["wind"].append(_round(s.wind_mph, 1))
            frame["rain"].append(_round(s.rain_in_hr, 3))
            frame["snow"].append(_round(s.snow_in_hr, 3))
        frames.append(frame)
    return {
        "start_time": departure.isoformat(),
        "hours": HEATMAP_HOURS,
        "step_deg": grid.step_deg,
        "cells": [[lon, lat] for lat, lon in grid.cells],
        "frames": frames,
    }
