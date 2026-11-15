"""
Weather risk classification — a direct encoding of the assessment's rules.

Threshold table (per checkpoint):

    Condition   Low        Moderate    High        Severe      No Travel
    Wind        <25 mph    25–34       35–44       45–54       >=55 mph
    Rain        <0.10 in/h 0.10–0.25   0.25–0.50   0.50–1.00   >1.00
    Snow        <0.5 in/h  0.5–1.0     1.0–2.0     2.0–3.0     >3.0

Boundary convention: the published ranges share their end points (e.g. 0.25 is
listed under both Moderate and High). We resolve this consistently by treating
each range's *lower* bound as inclusive, so a value equal to a boundary falls in
the more severe level. The only exceptions are the explicit "> 1.00" (rain) and
"> 3.0" (snow) No Travel cells, which are strict, so 1.00 in/hr of rain is
Severe and 1.01 is No Travel. Wind ranges are written with integers
(25–34, 35–44, ...) so we read them as half-open: 34.9 mph is Moderate.

Load rules (wind only, applied after the table):

    * >= 55 mph                      -> No Travel for any load
    * 45–54 mph and load > 30,000 lb -> No Travel
    * 35–44 mph and load > 40,000 lb -> Severe

The overall checkpoint level is the worst of the (load-adjusted) wind level, the
rain level and the snow level.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class RiskLevel(IntEnum):
    """Ordered risk levels. The integer value doubles as the numeric risk score."""

    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    RiskLevel.LOW: "Low",
    RiskLevel.MODERATE: "Moderate",
    RiskLevel.HIGH: "High",
    RiskLevel.SEVERE: "Severe",
    RiskLevel.NO_TRAVEL: "No Travel",
}

# Wind: lower bounds (inclusive) of Moderate, High, Severe, No Travel, in mph.
WIND_MODERATE_MPH = 25.0
WIND_HIGH_MPH = 35.0
WIND_SEVERE_MPH = 45.0
WIND_NO_TRAVEL_MPH = 55.0

# Rain (in/hr): Moderate >= 0.10, High >= 0.25, Severe >= 0.50, No Travel > 1.00.
RAIN_MODERATE = 0.10
RAIN_HIGH = 0.25
RAIN_SEVERE = 0.50
RAIN_NO_TRAVEL_ABOVE = 1.00

# Snow (in/hr): Moderate >= 0.5, High >= 1.0, Severe >= 2.0, No Travel > 3.0.
SNOW_MODERATE = 0.5
SNOW_HIGH = 1.0
SNOW_SEVERE = 2.0
SNOW_NO_TRAVEL_ABOVE = 3.0

# Load rules.
LOAD_NO_TRAVEL_ABOVE_LB = 30_000  # with 45–54 mph wind
LOAD_SEVERE_ABOVE_LB = 40_000  # with 35–44 mph wind


def classify_wind(mph: float) -> RiskLevel:
    if mph >= WIND_NO_TRAVEL_MPH:
        return RiskLevel.NO_TRAVEL
    if mph >= WIND_SEVERE_MPH:
        return RiskLevel.SEVERE
    if mph >= WIND_HIGH_MPH:
        return RiskLevel.HIGH
    if mph >= WIND_MODERATE_MPH:
        return RiskLevel.MODERATE
    return RiskLevel.LOW


def classify_rain(in_per_hr: float) -> RiskLevel:
    if in_per_hr > RAIN_NO_TRAVEL_ABOVE:
        return RiskLevel.NO_TRAVEL
    if in_per_hr >= RAIN_SEVERE:
        return RiskLevel.SEVERE
    if in_per_hr >= RAIN_HIGH:
        return RiskLevel.HIGH
    if in_per_hr >= RAIN_MODERATE:
        return RiskLevel.MODERATE
    return RiskLevel.LOW


def classify_snow(in_per_hr: float) -> RiskLevel:
    if in_per_hr > SNOW_NO_TRAVEL_ABOVE:
        return RiskLevel.NO_TRAVEL
    if in_per_hr >= SNOW_SEVERE:
        return RiskLevel.SEVERE
    if in_per_hr >= SNOW_HIGH:
        return RiskLevel.HIGH
    if in_per_hr >= SNOW_MODERATE:
        return RiskLevel.MODERATE
    return RiskLevel.LOW


def apply_load_rules(wind_level: RiskLevel, load_lb: float) -> tuple[RiskLevel, str | None]:
    """
    Escalate the wind level for heavy loads.

    Returns the adjusted level and a human-readable description of the rule that
    fired (or None). ">= 55 mph -> No Travel" is already produced by the table,
    so only the two load-dependent rules need handling here.
    """
    if wind_level == RiskLevel.SEVERE and load_lb > LOAD_NO_TRAVEL_ABOVE_LB:
        return RiskLevel.NO_TRAVEL, "45–54 mph wind with load over 30,000 lb → No Travel"
    if wind_level == RiskLevel.HIGH and load_lb > LOAD_SEVERE_ABOVE_LB:
        return RiskLevel.SEVERE, "35–44 mph wind with load over 40,000 lb → Severe"
    return wind_level, None


@dataclass(frozen=True)
class RiskAssessment:
    level: RiskLevel
    wind_level: RiskLevel  # after load rules
    rain_level: RiskLevel
    snow_level: RiskLevel
    wind_level_base: RiskLevel  # straight from the table, before load rules
    load_rule: str | None = None
    drivers: tuple[str, ...] = field(default_factory=tuple)  # which conditions set the level

    def to_dict(self) -> dict:
        return {
            "level": self.level.name,
            "label": self.level.label,
            "score": int(self.level),
            "wind_level": self.wind_level.name,
            "wind_level_base": self.wind_level_base.name,
            "rain_level": self.rain_level.name,
            "snow_level": self.snow_level.name,
            "load_rule": self.load_rule,
            "drivers": list(self.drivers),
        }


def assess(wind_mph: float, rain_in_hr: float, snow_in_hr: float, load_lb: float) -> RiskAssessment:
    """Classify one checkpoint's weather for a truck carrying `load_lb` pounds."""
    wind_base = classify_wind(wind_mph)
    wind_level, load_rule = apply_load_rules(wind_base, load_lb)
    rain_level = classify_rain(rain_in_hr)
    snow_level = classify_snow(snow_in_hr)

    level = max(wind_level, rain_level, snow_level)
    drivers: tuple[str, ...] = ()
    if level > RiskLevel.LOW:
        drivers = tuple(
            name
            for name, lvl in (("wind", wind_level), ("rain", rain_level), ("snow", snow_level))
            if lvl == level
        )
    return RiskAssessment(
        level=level,
        wind_level=wind_level,
        rain_level=rain_level,
        snow_level=snow_level,
        wind_level_base=wind_base,
        load_rule=load_rule,
        drivers=drivers,
    )
