from django.core.cache import cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import PlanRequestSerializer
from .services.planner import plan_trip
from .services.tomtom import TomTomClient

GEOCODE_CACHE_SECONDS = 24 * 3600


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})


@api_view(["GET"])
def geocode(request):
    """Place autocomplete: GET /api/geocode?q=dallas"""
    query = (request.query_params.get("q") or "").strip()
    if len(query) < 3:
        return Response({"results": []})
    query = query[:120]
    key = f"geo:v1:{query.lower()}"
    results = cache.get(key)
    if results is None:
        results = TomTomClient().search(query)
        cache.set(key, results, GEOCODE_CACHE_SECONDS)
    return Response({"results": results})


@api_view(["POST"])
def plan(request):
    """Plan a trip: routes, checkpoint weather/risk, recommendation and heatmap."""
    serializer = PlanRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return Response(plan_trip(serializer.to_trip()))
