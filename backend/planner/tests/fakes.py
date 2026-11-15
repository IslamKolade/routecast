"""Test doubles for the third-party APIs."""
from __future__ import annotations

import threading
from datetime import timedelta
from unittest import mock

from planner.services.geo import cumulative_distances_m


def raw_route(points, travel_time_s, slow_first_half=False, roads=("I-40",)):
    """Build a TomTom-shaped route object for the given (lat, lon) points."""
    cum = cumulative_distances_m(points)
    length = cum[-1]
    mid = len(points) // 2
    first_half_time = travel_time_s * (0.75 if slow_first_half else cum[mid] / length)
    instructions = [
        {"routeOffsetInMeters": 0, "travelTimeInSeconds": 0, "pointIndex": 0, "roadNumbers": list(roads)},
        {"routeOffsetInMeters": cum[mid], "travelTimeInSeconds": first_half_time, "pointIndex": mid,
         "roadNumbers": list(roads)},
        {"routeOffsetInMeters": length, "travelTimeInSeconds": travel_time_s, "pointIndex": len(points) - 1},
    ]
    return {
        "summary": {"lengthInMeters": round(length), "travelTimeInSeconds": travel_time_s, "trafficDelayInSeconds": 0},
        "legs": [{"points": [{"latitude": a, "longitude": b} for a, b in points]}],
        "guidance": {"instructions": instructions},
    }


def line(a, b, n=60, bulge=0.0):
    """n points from a to b, optionally bowed sideways (in degrees of longitude) in the middle."""
    pts = []
    for i in range(n):
        f = i / (n - 1)
        bow = bulge * 4 * f * (1 - f)
        pts.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f + bow))
    return pts


class FakeTomTom:
    """Returns `routes` for normal requests and `forced_routes` (in order) for avoid-area requests."""

    def __init__(self, routes, forced_routes=None):
        self.routes = routes
        self.forced_routes = list(forced_routes or [])
        self.calls = []
        self._lock = threading.Lock()

    def search(self, query, limit=6, typeahead=True):
        return [{"label": f"{query} (geocoded)", "lat": 35.0, "lon": -101.0, "type": "Geography"}]

    def calculate_route(self, locations, *, departure, gross_weight_lb, max_alternatives=0, avoid_areas=None):
        self.calls.append({"locations": list(locations), "gross_weight_lb": gross_weight_lb,
                           "max_alternatives": max_alternatives, "avoid_areas": avoid_areas})
        if not avoid_areas:
            return self.routes[: max_alternatives + 1]
        with self._lock:
            if not self.forced_routes:
                from planner.exceptions import NoRouteError
                raise NoRouteError("no alternative")
            return [self.forced_routes.pop(0)]


def open_meteo_response(params, wind=10.0, rain=0.0, snow=0.0):
    """A fake Open-Meteo multi-location response matching the request's time window."""
    from datetime import datetime

    start = datetime.strptime(params["start_hour"], "%Y-%m-%dT%H:%M")
    end = datetime.strptime(params["end_hour"], "%Y-%m-%dT%H:%M")
    hours = int((end - start) / timedelta(hours=1)) + 1
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    n = len(params["latitude"].split(","))
    item = {
        "hourly": {
            "time": times,
            "wind_speed_10m": [wind] * hours,
            "wind_gusts_10m": [wind + 10] * hours,
            "rain": [rain] * hours,
            "showers": [0.0] * hours,
            "snowfall": [snow] * hours,
            "temperature_2m": [45.0] * hours,
            "weather_code": [0] * hours,
        }
    }
    resp = mock.Mock(status_code=200)
    resp.json.return_value = [item] * n if n > 1 else item
    return resp
