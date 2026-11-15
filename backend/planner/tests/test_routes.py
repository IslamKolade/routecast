from datetime import datetime, timezone

from django.test import SimpleTestCase

from planner.services.geo import METERS_PER_MILE, haversine_m, simplify_indices
from planner.services.routes import get_route_options, parse_route

from .fakes import FakeTomTom, line, raw_route

DEPARTURE = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
ORIGIN, DEST = (35.0, -101.0), (36.0, -97.0)


class ParseRouteTests(SimpleTestCase):
    def test_vertex_times_follow_instruction_anchors(self):
        pts = line(ORIGIN, DEST, n=61)
        route = parse_route(raw_route(pts, 4 * 3600, slow_first_half=True))
        self.assertEqual(route.cum_s[0], 0)
        self.assertAlmostEqual(route.cum_s[30], 3 * 3600, delta=1)
        self.assertAlmostEqual(route.cum_s[-1], 4 * 3600)
        self.assertTrue(all(b >= a for a, b in zip(route.cum_s, route.cum_s[1:])))

    def test_distance_scaled_to_provider_length(self):
        raw = raw_route(line(ORIGIN, DEST), 3600)
        raw["summary"]["lengthInMeters"] = 400_000
        route = parse_route(raw)
        self.assertAlmostEqual(route.cum_m[-1], 400_000)
        self.assertEqual(route.length_m, 400_000)

    def test_main_roads(self):
        route = parse_route(raw_route(line(ORIGIN, DEST), 3600, roads=("I-40",)))
        self.assertEqual(route.roads, ["I-40"])

    def test_without_guidance_time_is_proportional_to_distance(self):
        raw = raw_route(line((35.0, -100.0), (36.0, -100.0), n=11), 1000)  # due north: equal steps
        raw.pop("guidance")
        route = parse_route(raw)
        self.assertAlmostEqual(route.cum_s[5], 500, delta=1)


class RouteOptionTests(SimpleTestCase):
    def test_uses_three_provider_routes_when_available(self):
        client = FakeTomTom([raw_route(line(ORIGIN, DEST, bulge=b), 3600 * (1 + b)) for b in (0, 0.5, -0.5)])
        routes = get_route_options(client, ORIGIN, DEST, DEPARTURE, 60_000)
        self.assertEqual([r.id for r in routes], ["A", "B", "C"])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["max_alternatives"], 2)
        self.assertEqual(client.calls[0]["gross_weight_lb"], 60_000)

    def test_forces_alternatives_with_avoid_areas(self):
        primary = raw_route(line(ORIGIN, DEST), 3600)
        duplicate = raw_route(line(ORIGIN, DEST), 3700)  # same path: must be rejected
        too_slow = raw_route(line(ORIGIN, DEST, bulge=0.8), 3600 * 2)  # > 1.6x time: rejected
        good1 = raw_route(line(ORIGIN, DEST, bulge=0.4), 4000)
        good2 = raw_route(line(ORIGIN, DEST, bulge=-0.4), 4100)
        client = FakeTomTom([primary], forced_routes=[duplicate, too_slow, good1, good2])
        routes = get_route_options(client, ORIGIN, DEST, DEPARTURE, 60_000)
        self.assertEqual(len(routes), 3)
        self.assertEqual([round(r.travel_time_s) for r in routes], [3600, 4000, 4100])
        forced_calls = [c for c in client.calls if c["avoid_areas"]]
        self.assertTrue(forced_calls)
        # One avoid rectangle per route already found, given as (south-west, north-east).
        sw, ne = forced_calls[0]["avoid_areas"][0]
        self.assertLess(sw[0], ne[0])
        self.assertLess(sw[1], ne[1])

    def test_rejects_routes_that_double_back(self):
        primary = raw_route(line(ORIGIN, DEST), 3600)
        mid = (35.5, -99.0)
        # Drives north up a side road to a dead end and comes back the same way.
        spur = line(ORIGIN, mid, 30) + line(mid, (36.5, -99.0), 20)[1:] + line((36.5, -99.0), mid, 20)[1:] \
            + line(mid, DEST, 30)[1:]
        client = FakeTomTom([primary], forced_routes=[raw_route(spur, 4000)])
        routes = get_route_options(client, ORIGIN, DEST, DEPARTURE, 60_000)
        self.assertEqual(len(routes), 1)

    def test_returns_fewer_when_no_distinct_alternative_exists(self):
        client = FakeTomTom([raw_route(line(ORIGIN, DEST), 3600)], forced_routes=[])
        routes = get_route_options(client, ORIGIN, DEST, DEPARTURE, 60_000)
        self.assertEqual(len(routes), 1)


class GeoTests(SimpleTestCase):
    def test_haversine_one_degree_latitude(self):
        self.assertAlmostEqual(haversine_m((0, 0), (1, 0)) / METERS_PER_MILE, 69.09, places=1)

    def test_simplify_keeps_ends_and_corners(self):
        pts = [(0, 0), (0, 0.5), (0, 1), (0.5, 1), (1, 1)]
        self.assertEqual(simplify_indices(pts, 10), [0, 2, 4])
