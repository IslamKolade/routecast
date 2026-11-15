"""
Route summary and recommendation.

Each checkpoint represents a stretch of road (see `checkpoints.py`), so a route
can be summarised as "miles driven at each risk level". Routes are then ranked
lexicographically — a criterion only matters when every earlier one is tied:

    0. Fewest No Travel miles   (not in the brief's list; see note below)
    1. Fewest Severe miles
    2. Fewest High miles
    3. Lowest average risk      (mile-weighted; Low=0 … No Travel=4)
    4. Shortest travel time

Note on No Travel: the brief's preference list starts at Severe, but No Travel
is strictly worse than Severe, so a route that would require driving through
No Travel conditions must never beat one that doesn't. We therefore rank on
No Travel miles first. When every route has No Travel miles, the recommendation
is still made but flagged with a warning.

Comparisons are strict: values are rounded to 6 decimal places only, which
removes floating-point noise (e.g. 12.5 vs 12.500000000001) and nothing else.
So any real difference decides, exactly as the brief's list says, however small.
The explanation shown to the user prints enough decimals to make that
difference visible (e.g. "0.112 vs 0.113").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .risk import RiskLevel

# Rounding used only to cancel floating-point noise in comparisons.
_NOISE_DECIMALS = 6


@dataclass(frozen=True)
class RouteRiskSummary:
    route_id: str
    total_miles: float
    miles_by_level: dict[RiskLevel, float]
    avg_risk: float
    max_level: RiskLevel
    travel_time_s: float

    @property
    def no_travel_miles(self) -> float:
        return self.miles_by_level[RiskLevel.NO_TRAVEL]

    @property
    def severe_miles(self) -> float:
        return self.miles_by_level[RiskLevel.SEVERE]

    @property
    def high_miles(self) -> float:
        return self.miles_by_level[RiskLevel.HIGH]

    def sort_key(self) -> tuple:
        return (
            round(self.no_travel_miles, _NOISE_DECIMALS),
            round(self.severe_miles, _NOISE_DECIMALS),
            round(self.high_miles, _NOISE_DECIMALS),
            round(self.avg_risk, _NOISE_DECIMALS),
            round(self.travel_time_s),
        )

    def to_dict(self) -> dict:
        return {
            "total_miles": round(self.total_miles, 1),
            "miles_by_level": {lvl.name: round(m, 1) for lvl, m in self.miles_by_level.items()},
            "no_travel_miles": round(self.no_travel_miles, 1),
            "severe_miles": round(self.severe_miles, 1),
            "high_miles": round(self.high_miles, 1),
            "avg_risk": round(self.avg_risk, 4),
            "max_level": self.max_level.name,
        }


def summarize_route(
    route_id: str, segments: Iterable[tuple[float, RiskLevel]], travel_time_s: float
) -> RouteRiskSummary:
    """`segments` is a sequence of (miles, level) — one per checkpoint."""
    miles_by_level = {lvl: 0.0 for lvl in RiskLevel}
    total = 0.0
    weighted = 0.0
    max_level = RiskLevel.LOW
    for miles, level in segments:
        miles_by_level[level] += miles
        total += miles
        weighted += miles * int(level)
        max_level = max(max_level, level)
    avg = weighted / total if total > 0 else 0.0
    return RouteRiskSummary(
        route_id=route_id,
        total_miles=total,
        miles_by_level=miles_by_level,
        avg_risk=avg,
        max_level=max_level,
        travel_time_s=travel_time_s,
    )


@dataclass(frozen=True)
class Recommendation:
    route_id: str
    ranking: list[str]
    reason: str
    warning: str | None

    def to_dict(self) -> dict:
        return {
            "route_id": self.route_id,
            "ranking": self.ranking,
            "reason": self.reason,
            "warning": self.warning,
        }


def _fmt_duration(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _distinguishing_digits(a: float, b: float, minimum: int) -> int:
    """Fewest decimals (at least `minimum`) at which a and b print differently."""
    for digits in range(minimum, _NOISE_DECIMALS + 1):
        if f"{a:.{digits}f}" != f"{b:.{digits}f}":
            return digits
    return _NOISE_DECIMALS


def _explain(best: RouteRiskSummary, runner_up: RouteRiskSummary) -> str:
    """Name the first criterion on which the winner beat the runner-up."""
    criteria = [
        ("Fewest No Travel miles", best.no_travel_miles, runner_up.no_travel_miles, 1, " mi"),
        ("Fewest Severe miles", best.severe_miles, runner_up.severe_miles, 1, " mi"),
        ("Fewest High miles", best.high_miles, runner_up.high_miles, 1, " mi"),
        ("Lowest average risk", best.avg_risk, runner_up.avg_risk, 2, ""),
    ]
    for label, a, b, digits, unit in criteria:
        if round(a, _NOISE_DECIMALS) != round(b, _NOISE_DECIMALS):
            shown = _distinguishing_digits(a, b, digits)
            return f"{label} ({a:.{shown}f}{unit} vs {b:.{shown}f}{unit} on Route {runner_up.route_id})"
    if round(best.travel_time_s) != round(runner_up.travel_time_s):
        return (
            "Equal weather risk; shortest travel time "
            f"({_fmt_duration(best.travel_time_s)} vs {_fmt_duration(runner_up.travel_time_s)} "
            f"on Route {runner_up.route_id})"
        )
    return f"Tied with Route {runner_up.route_id} on every criterion"


def recommend(summaries: Sequence[RouteRiskSummary]) -> Recommendation:
    if not summaries:
        raise ValueError("At least one route is required")
    # sorted() is stable, so exact ties keep the routing provider's order.
    ranked = sorted(summaries, key=lambda s: s.sort_key())
    best = ranked[0]
    if len(ranked) == 1:
        reason = "Only one route was available"
    else:
        reason = _explain(best, ranked[1])

    warning = None
    if best.no_travel_miles > 0:
        warning = (
            "Every route crosses No Travel conditions at the expected time. "
            "Consider delaying departure."
        )
    elif best.severe_miles > 0:
        warning = "The safest route still includes Severe conditions. Drive with extra caution or delay."
    return Recommendation(
        route_id=best.route_id,
        ranking=[s.route_id for s in ranked],
        reason=reason,
        warning=warning,
    )
