from datetime import datetime, timedelta, timezone

from django.test import SimpleTestCase

from planner.services.checkpoints import build_checkpoints, checkpoint_distances, coverage
from planner.services.geo import METERS_PER_MILE as MI
from planner.services.routes import Route


def straight_route(miles: float, hours: float, slow_first_half: bool = False) -> Route:
    """A due-north route; optionally the first half takes 75% of the time."""
    n = 101
    length = miles * MI
    # 1 degree of latitude ≈ 69.09 miles; the exact value is irrelevant since cum_m is given.
    points = [(30 + (miles / 69.09) * i / (n - 1), -100.0) for i in range(n)]
    cum_m = [length * i / (n - 1) for i in range(n)]
    total = hours * 3600
    if slow_first_half:
        cum_s = [
            (0.75 * total * (i / 50)) if i <= 50 else (0.75 * total + 0.25 * total * (i - 50) / 50)
            for i in range(n)
        ]
    else:
        cum_s = [total * i / (n - 1) for i in range(n)]
    return Route(points=points, cum_m=cum_m, cum_s=cum_s, length_m=length, travel_time_s=total, traffic_delay_s=0)


class DistanceTests(SimpleTestCase):
    def test_even_multiple(self):
        d = checkpoint_distances(100 * MI, 25)
        self.assertEqual([round(x / MI, 6) for x in d], [0, 25, 50, 75, 100])

    def test_uneven_length_ends_at_destination(self):
        d = checkpoint_distances(60 * MI, 25)
        self.assertEqual([round(x / MI, 6) for x in d], [0, 25, 50, 60])

    def test_route_shorter_than_interval(self):
        d = checkpoint_distances(8 * MI, 10)
        self.assertEqual([round(x / MI, 6) for x in d], [0, 8])

    def test_intervals(self):
        for interval, expected_count in ((10, 31), (25, 13), (50, 7)):
            with self.subTest(interval=interval):
                self.assertEqual(len(checkpoint_distances(300 * MI, interval)), expected_count)


class CoverageTests(SimpleTestCase):
    def test_coverage_sums_to_route_length(self):
        d = checkpoint_distances(60 * MI, 25)
        c = coverage(d)
        self.assertAlmostEqual(sum(c), 60 * MI)
        self.assertEqual([round(x / MI, 6) for x in c], [12.5, 25, 17.5, 5])

    def test_single_point(self):
        self.assertEqual(coverage([0.0]), [0.0])


class EtaTests(SimpleTestCase):
    def setUp(self):
        self.departure = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)

    def test_eta_uses_route_timing(self):
        cps = build_checkpoints(straight_route(100, 2), self.departure, 25)
        self.assertEqual(cps[0].eta, self.departure)
        self.assertEqual(cps[2].eta, self.departure + timedelta(hours=1))
        self.assertEqual(cps[-1].eta, self.departure + timedelta(hours=2))

    def test_eta_follows_uneven_speed(self):
        # First 50 miles take 3 h of a 4 h trip: mile 50 must be at +3 h, not +2 h.
        cps = build_checkpoints(straight_route(100, 4, slow_first_half=True), self.departure, 25)
        self.assertEqual(cps[2].eta, self.departure + timedelta(hours=3))
        self.assertEqual(cps[1].eta, self.departure + timedelta(hours=1.5))

    def test_positions_and_miles(self):
        cps = build_checkpoints(straight_route(100, 2), self.departure, 50)
        self.assertEqual([round(c.mile, 3) for c in cps], [0, 50, 100])
        self.assertAlmostEqual(cps[1].position[0], 30 + (100 / 69.09) / 2)
