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
from django.core.cache import cache

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


def _parse(item: dict, start: datetime) -> Forecast:
    hourly = item.get("hourly") or {}
    times = hourly.get("time") or []
    if not times or datetime.fromisoformat(times[0]).replace(tzinfo=timezone.utc) != start:
        raise UpstreamServiceError("The weather service returned an unexpected time range.")
    rain = [_sum_or_none(r, s) for r, s in zip(hourly["rain"], hourly["showers"])]
    return Forecast(
        start=start,
        wind=hourly["wind_speed_10m"],
        gust=hourly["wind_gusts_10m"],
        rain=rain,
        snow=hourly["snowfall"],
        temp=hourly["temperature_2m"],
        code=hourly["weather_code"],
    )


def _cache_key(cell: LatLon, start: datetime, end: datetime) -> str:
    return f"wx:v1:{cell[0]:.2f}:{cell[1]:.2f}:{start:%Y%m%d%H}:{end:%Y%m%d%H}"


def _fetch_chunk(cells: list[LatLon], start: datetime, end: datetime) -> list[Forecast]:
    params = {
        "latitude": ",".join(f"{c[0]:.4f}" for c in cells),
        "longitude": ",".join(f"{c[1]:.4f}" for c in cells),
        "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "temperature_unit": "fahrenheit",
        "timezone": "GMT",
        "start_hour": start.strftime("%Y-%m-%dT%H:%M"),
        "end_hour": end.strftime("%Y-%m-%dT%H:%M"),
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=settings.HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise UpstreamServiceError("The weather service could not be reached. Please try again.") from exc
    if resp.status_code == 429:
        raise UpstreamServiceError(
            "The weather service rate limit was reached. Try again in a minute or use a wider checkpoint interval."
        )
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
    return [_parse(item, start) for item in items]


def fetch_forecasts(cells: Iterable[LatLon], start: datetime, end: datetime) -> dict[LatLon, Forecast]:
    """
    Hourly forecasts for every grid cell, covering [start, end] (UTC, whole hours).
    Cells must already be snapped with `snap()`.
    """
    start, end = floor_hour(start), floor_hour(end)
    unique = list(dict.fromkeys(cells))
    result: dict[LatLon, Forecast] = {}
    missing: list[LatLon] = []
    for cell in unique:
        cached = cache.get(_cache_key(cell, start, end))
        if cached is not None:
            result[cell] = cached
        else:
            missing.append(cell)

    chunks = [missing[i : i + CHUNK_SIZE] for i in range(0, len(missing), CHUNK_SIZE)]
    if chunks:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(chunks))) as pool:
            for chunk, forecasts in zip(chunks, pool.map(lambda c: _fetch_chunk(c, start, end), chunks)):
                for cell, forecast in zip(chunk, forecasts):
                    result[cell] = forecast
                    cache.set(_cache_key(cell, start, end), forecast, settings.WEATHER_CACHE_SECONDS)
    logger.info("Weather: %d cells (%d from cache)", len(unique), len(unique) - len(missing))
    return result
