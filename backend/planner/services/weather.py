"""
Hourly forecasts from Open-Meteo (https://open-meteo.com/en/docs).

Units are requested so that values compare directly with the risk table:
wind in mph, rain and snowfall in inches. Open-Meteo hourly precipitation
values are the total over the *preceding* hour, i.e. an in/hr rate.

Choosing the forecast hour for an ETA
-------------------------------------
* Wind is an instantaneous value at the top of each hour, so we use the hour
  nearest to the ETA (14:20 -> 14:00, 14:40 -> 15:00).
* Rain/snow are totals for the hour ending at the timestamp, so we use the
  record whose hour *contains* the ETA (14:20 -> the 15:00 record, which covers
  14:00–15:00).
* Rain is `rain + showers` (Open-Meteo splits large-scale rain from convective
  showers; both are rain falling on the road).
* Sustained wind (`wind_speed_10m`) is classified; gusts are reported for
  information only.

Lookup positions
----------------
Checkpoints are looked up at their own position, rounded to 0.01° (~1 km), so
checkpoints on a stretch shared by several routes reuse one lookup. Heatmap
cells sit on a 0.1° grid (~11 km, comparable to the forecast models' own
resolution). Every lookup is cached.
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import requests
from django.conf import settings
from django.core.cache import cache, caches

from ..exceptions import UpstreamServiceError
from .geo import LatLon

logger = logging.getLogger(__name__)

API_URL = "https://api.open-meteo.com/v1/forecast"
GRID_STEP_DEG = 0.1  # heatmap grid
CHECKPOINT_STEP_DEG = 0.01  # checkpoint lookup precision (~1 km)
CHUNK_SIZE = 100
MAX_WORKERS = 4
HOUR = timedelta(hours=1)
HOURLY_VARS = ("wind_speed_10m", "wind_gusts_10m", "rain", "showers", "snowfall", "temperature_2m", "weather_code")


def snap(point: LatLon, step: float = GRID_STEP_DEG) -> LatLon:
    lat, lon = point
    return (round(round(lat / step) * step, 4), round(round(lon / step) * step, 4))


def floor_hour(t: datetime) -> datetime:
    return t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class WeatherSample:
    wind_mph: float | None
    gust_mph: float | None
    rain_in_hr: float | None
    snow_in_hr: float | None
    temp_f: float | None
    weather_code: int | None
    wind_time: datetime  # forecast timestamp used for wind
    precip_time: datetime  # forecast timestamp (end of hour) used for rain/snow

    @property
    def complete(self) -> bool:
        return None not in (self.wind_mph, self.rain_in_hr, self.snow_in_hr)

    def to_dict(self) -> dict:
        return {
            "wind_mph": self.wind_mph,
            "gust_mph": self.gust_mph,
            "rain_in_hr": self.rain_in_hr,
            "snow_in_hr": self.snow_in_hr,
            "temp_f": self.temp_f,
            "weather_code": self.weather_code,
            "wind_forecast_time": self.wind_time.isoformat(),
            "precip_forecast_hour_end": self.precip_time.isoformat(),
            "complete": self.complete,
        }


@dataclass(frozen=True)
class Forecast:
    start: datetime  # UTC, top of the hour of the first record
    wind: list
    gust: list
    rain: list  # rain + showers
    snow: list
    temp: list
    code: list

    def _at(self, values: list, index: int):
        return values[index] if 0 <= index < len(values) else None

    def sample_at(self, t: datetime) -> WeatherSample:
        offset_h = (t.astimezone(timezone.utc) - self.start) / HOUR
        wind_i = math.floor(offset_h + 0.5)  # nearest hour
        precip_i = math.floor(offset_h) + 1  # hour that contains t
        return WeatherSample(
            wind_mph=self._at(self.wind, wind_i),
            gust_mph=self._at(self.gust, wind_i),
            rain_in_hr=self._at(self.rain, precip_i),
            snow_in_hr=self._at(self.snow, precip_i),
            temp_f=self._at(self.temp, wind_i),
            weather_code=self._at(self.code, wind_i),
            wind_time=self.start + wind_i * HOUR,
            precip_time=self.start + precip_i * HOUR,
        )


def _sum_or_none(a, b):
    if a is None and b is None:
        return None
    return round((a or 0.0) + (b or 0.0), 4)


def _numbers(values, name: str, hours: int) -> list:
    """Validate one hourly series: a list of at least `hours` numbers (or nulls)."""
    if not isinstance(values, list) or len(values) < hours:
        raise UpstreamServiceError(f"Weather data is missing hourly values for {name}.")
    values = values[:hours]
    if not all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) for v in values):
        raise UpstreamServiceError(f"Weather data for {name} contains non-numeric values.")
    return values


def parse_item(item: dict, start: datetime, end: datetime) -> Forecast:
    """
    Turn one Open-Meteo location result into a Forecast covering [start, end].
    Used for responses fetched by the server and for data relayed by the browser,
    so both are validated the same way.
    """
    hourly = item.get("hourly") if isinstance(item, dict) else None
    if not isinstance(hourly, dict):
        raise UpstreamServiceError("Weather data is missing the hourly section.")
    times = hourly.get("time") or []
    hours = int((end - start) / HOUR) + 1
    try:
        first = datetime.fromisoformat(times[0]).replace(tzinfo=timezone.utc)
    except (IndexError, TypeError, ValueError):
        first = None
    if first != start or len(times) < hours:
        raise UpstreamServiceError("The weather service returned an unexpected time range.")
    series = {name: _numbers(hourly.get(name), name, hours) for name in HOURLY_VARS}
    return Forecast(
        start=start,
        wind=series["wind_speed_10m"],
        gust=series["wind_gusts_10m"],
        rain=[_sum_or_none(r, s) for r, s in zip(series["rain"], series["showers"])],
        snow=series["snowfall"],
        temp=series["temperature_2m"],
        code=series["weather_code"],
    )


class WeatherRateLimitError(UpstreamServiceError):
    """Open-Meteo refused the server's requests (free-tier limit, counted per IP address)."""


def request_params(start: datetime, end: datetime) -> dict:
    """Query parameters for an Open-Meteo request, minus the coordinates.

    Shared by the server and the browser relay, so both ask for exactly the same data.
    """
    return {
        "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "temperature_unit": "fahrenheit",
        "timezone": "GMT",
        "start_hour": start.strftime("%Y-%m-%dT%H:%M"),
        "end_hour": end.strftime("%Y-%m-%dT%H:%M"),
    }


def _cache_key(cell: LatLon, start: datetime, end: datetime) -> str:
    return f"wx:v2:{cell[0]:.2f}:{cell[1]:.2f}:{start:%Y%m%d%H}:{end:%Y%m%d%H}"


def _fetch_chunk(cells: list[LatLon], start: datetime, end: datetime) -> list[Forecast]:
    params = {
        "latitude": ",".join(f"{c[0]:.4f}" for c in cells),
        "longitude": ",".join(f"{c[1]:.4f}" for c in cells),
        **request_params(start, end),
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=settings.HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise UpstreamServiceError("The weather service could not be reached. Please try again.") from exc
    if resp.status_code == 429:
        raise WeatherRateLimitError("The weather service rate limit was reached. Please try again in a minute.")
    if resp.status_code != 200:
        reason = ""
        try:
            reason = resp.json().get("reason", "")
        except ValueError:
            pass
        logger.warning("Open-Meteo error %s: %s", resp.status_code, reason)
        raise UpstreamServiceError(f"The weather service returned an error. {reason}".strip())
    data = resp.json()
    items = data if isinstance(data, list) else [data]
    if len(items) != len(cells):
        raise UpstreamServiceError("The weather service returned an unexpected number of locations.")
    return [parse_item(item, start, end) for item in items]


def window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Whole-hour UTC window used for every lookup and cache key."""
    return floor_hour(start), floor_hour(end)


def cached_forecasts(cells: Iterable[LatLon], start: datetime, end: datetime) -> dict[LatLon, Forecast]:
    """Forecasts already in the cache, for whichever of `cells` have one."""
    start, end = window(start, end)
    keys = {_cache_key(cell, start, end): cell for cell in dict.fromkeys(cells)}
    found = cache.get_many(list(keys))
    return {keys[k]: v for k, v in found.items()}


def store_forecasts(forecasts: dict[LatLon, Forecast], start: datetime, end: datetime) -> None:
    start, end = window(start, end)
    cache.set_many(
        {_cache_key(cell, start, end): f for cell, f in forecasts.items()}, settings.WEATHER_CACHE_SECONDS
    )


# After the server is rate-limited, skip server-side requests for a while: the
# browser relay is used straight away instead of waiting on a request that fails.
_BLOCKED_KEY = "wx:server-rate-limited"
SERVER_BLOCK_SECONDS = 10 * 60


def server_rate_limited() -> bool:
    return bool(caches["shared"].get(_BLOCKED_KEY))


def fetch_forecasts(cells: Iterable[LatLon], start: datetime, end: datetime) -> dict[LatLon, Forecast]:
    """
    Hourly forecasts for every cell, covering [start, end] (UTC, whole hours),
    from the cache or from Open-Meteo. Cells must already be snapped with `snap()`.
    Raises WeatherRateLimitError if Open-Meteo refuses the server.
    """
    start, end = window(start, end)
    unique = list(dict.fromkeys(cells))
    result = cached_forecasts(unique, start, end)
    missing = [c for c in unique if c not in result]
    chunks = [missing[i : i + CHUNK_SIZE] for i in range(0, len(missing), CHUNK_SIZE)]
    if chunks:
        try:
            with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(chunks))) as pool:
                fetched = {}
                for chunk, forecasts in zip(chunks, pool.map(lambda c: _fetch_chunk(c, start, end), chunks)):
                    fetched.update(zip(chunk, forecasts))
        except WeatherRateLimitError:
            caches["shared"].set(_BLOCKED_KEY, True, SERVER_BLOCK_SECONDS)
            raise
        store_forecasts(fetched, start, end)
        result.update(fetched)
    logger.info("Weather: %d cells (%d from cache)", len(unique), len(unique) - len(missing))
    return result
