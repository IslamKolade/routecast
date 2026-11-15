from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from .services.checkpoints import ALLOWED_INTERVALS_MILES
from .services.planner import Place, TripRequest

MAX_LOAD_LB = 80_000
MAX_DAYS_AHEAD = 7  # keeps the trip plus the 48 h heatmap inside the forecast horizon
PAST_GRACE = timedelta(hours=1)


class PlaceSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=200, trim_whitespace=True)
    lat = serializers.FloatField(required=False, allow_null=True, min_value=-90, max_value=90)
    lon = serializers.FloatField(required=False, allow_null=True, min_value=-180, max_value=180)

    def validate(self, attrs):
        if (attrs.get("lat") is None) != (attrs.get("lon") is None):
            raise serializers.ValidationError("Provide both lat and lon, or neither.")
        return attrs


class PlanRequestSerializer(serializers.Serializer):
    origin = PlaceSerializer()
    destination = PlaceSerializer()
    departure_time = serializers.DateTimeField()
    load_weight_lb = serializers.FloatField(min_value=0, max_value=MAX_LOAD_LB)
    interval_miles = serializers.ChoiceField(choices=ALLOWED_INTERVALS_MILES, default=25)

    def validate_departure_time(self, value):
        now = timezone.now()
        if value < now - PAST_GRACE:
            raise serializers.ValidationError("Departure time cannot be in the past.")
        if value > now + timedelta(days=MAX_DAYS_AHEAD):
            raise serializers.ValidationError(
                f"Departure must be within {MAX_DAYS_AHEAD} days so the forecast covers the trip."
            )
        # A departure a few minutes in the past (e.g. form left open) means "leaving now".
        return max(value, now).replace(microsecond=0)

    def validate(self, attrs):
        o, d = attrs["origin"], attrs["destination"]
        if o.get("lat") is not None and d.get("lat") is not None:
            if abs(o["lat"] - d["lat"]) < 1e-4 and abs(o["lon"] - d["lon"]) < 1e-4:
                raise serializers.ValidationError("Origin and destination must be different.")
        elif o["label"].strip().lower() == d["label"].strip().lower():
            raise serializers.ValidationError("Origin and destination must be different.")
        return attrs

    def to_trip(self) -> TripRequest:
        data = self.validated_data
        return TripRequest(
            origin=Place(**data["origin"]),
            destination=Place(**data["destination"]),
            departure=data["departure_time"],
            load_lb=data["load_weight_lb"],
            interval_miles=int(data["interval_miles"]),
        )
