"""
Thin client for the TomTom Search (geocoding) and Routing APIs.

Docs:
  * https://developer.tomtom.com/search-api/documentation/search-service/fuzzy-search
  * https://developer.tomtom.com/routing-api/documentation/tomtom-maps/calculate-route
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Sequence
from urllib.parse import quote

import requests
from django.conf import settings

from ..exceptions import ConfigurationError, NoRouteError, UpstreamServiceError
from .geo import LatLon

logger = logging.getLogger(__name__)

BASE_URL = "https://api.tomtom.com"
LB_TO_KG = 0.45359237


class TomTomClient:
    def __init__(self, api_key: str | None = None, timeout: float | None = None, session=None):
        self.api_key = api_key if api_key is not None else settings.TOMTOM_API_KEY
        self.timeout = timeout if timeout is not None else settings.HTTP_TIMEOUT_SECONDS
        # Plain requests.get by default: calls may run in parallel threads.
        self.session = session or requests

    def _get(self, path: str, params: dict, json_body: dict | None = None) -> requests.Response:
        if not self.api_key:
            raise ConfigurationError("TOMTOM_API_KEY is not configured on the server.")
        url = f"{BASE_URL}{path}"
        params = {**params, "key": self.api_key}
        try:
            if json_body is not None:
                return self.session.post(url, params=params, json=json_body, timeout=self.timeout)
            return self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            # Never log the URL: it contains the API key.
            logger.warning("TomTom request failed: %s", type(exc).__name__)
            raise UpstreamServiceError("The routing service could not be reached. Please try again.") from exc

    # ------------------------------------------------------------------ search
    def search(self, query: str, limit: int = 6, typeahead: bool = True) -> list[dict]:
        params = {"limit": limit, "typeahead": str(typeahead).lower(), "language": "en-US"}
        if settings.GEOCODE_COUNTRY_SET:
            params["countrySet"] = settings.GEOCODE_COUNTRY_SET
        resp = self._get(f"/search/2/search/{quote(query, safe='')}.json", params)
        if resp.status_code != 200:
            logger.warning("TomTom search error %s", resp.status_code)
            raise UpstreamServiceError("The geocoding service returned an error.")
        results = []
        for item in resp.json().get("results", []):
            pos = item.get("position") or {}
            if "lat" not in pos or "lon" not in pos:
                continue
            address = item.get("address", {}).get("freeformAddress", "")
            name = (item.get("poi") or {}).get("name")
            label = f"{name}, {address}" if name and address else (name or address)
            if not label:
                continue
            results.append({"label": label, "lat": pos["lat"], "lon": pos["lon"], "type": item.get("type", "")})
        return results

    # ----------------------------------------------------------------- routing
    def calculate_route(
        self,
        locations: Sequence[LatLon],
        *,
        departure: datetime,
        gross_weight_lb: float,
        max_alternatives: int = 0,
        avoid_areas: Sequence[tuple[LatLon, LatLon]] | None = None,
    ) -> list[dict]:
        """
        Truck route(s) between `locations` (origin, optional via points, destination).
        `avoid_areas` are (south-west, north-east) rectangles the route must not enter.
        Returns TomTom's raw route objects; the first one is TomTom's best route.
        """
        path_locations = ":".join(f"{lat:.6f},{lon:.6f}" for lat, lon in locations)
        # TomTom rejects departure times in the past.
        now = datetime.now(timezone.utc)
        depart_at = "now" if departure <= now + timedelta(minutes=1) else departure.astimezone(
            timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "travelMode": "truck",
            "vehicleCommercial": "true",
            "vehicleWeight": int(round(gross_weight_lb * LB_TO_KG)),
            "routeType": "fastest",
            "traffic": "true",
            "departAt": depart_at,
            "maxAlternatives": max_alternatives,
            "instructionsType": "text",
            "language": "en-US",
            "routeRepresentation": "polyline",
        }
        body = None
        if avoid_areas:
            body = {
                "avoidAreas": {
                    "rectangles": [
                        {
                            "southWestCorner": {"latitude": sw[0], "longitude": sw[1]},
                            "northEastCorner": {"latitude": ne[0], "longitude": ne[1]},
                        }
                        for sw, ne in avoid_areas
                    ]
                }
            }
        resp = self._get(f"/routing/1/calculateRoute/{path_locations}/json", params, json_body=body)
        if resp.status_code == 200:
            routes = resp.json().get("routes", [])
            if not routes:
                raise NoRouteError("No truck route was found between these locations.")
            return routes

        message = _error_message(resp)
        logger.warning("TomTom routing error %s: %s", resp.status_code, message)
        if resp.status_code == 400 and message and "no route" in message.lower():
            raise NoRouteError("No truck route was found between these locations.")
        if resp.status_code in (403, 401):
            raise ConfigurationError("The routing API key was rejected.")
        if resp.status_code == 429:
            raise UpstreamServiceError("The routing service rate limit was reached. Please wait and retry.")
        if resp.status_code == 400:
            raise NoRouteError(f"The routing service could not plan this trip: {message or 'bad request'}")
        raise UpstreamServiceError("The routing service returned an error. Please try again.")


def _error_message(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return ""
    if isinstance(data, dict):
        err = data.get("error") or {}
        detailed = data.get("detailedError") or {}
        return str(err.get("description") or detailed.get("message") or data.get("errorText") or "")
    return ""
