from django.test import SimpleTestCase

from planner.services.ranking import recommend, summarize_route
from planner.services.risk import RiskLevel as R


def route(rid, segments, hours):
    return summarize_route(rid, segments, hours * 3600)


class SummaryTests(SimpleTestCase):
    def test_miles_and_mile_weighted_average(self):
        s = route("A", [(10, R.LOW), (20, R.HIGH), (10, R.SEVERE)], 1)
        self.assertEqual(s.total_miles, 40)
        self.assertEqual(s.high_miles, 20)
        self.assertEqual(s.severe_miles, 10)
        self.assertAlmostEqual(s.avg_risk, (0 * 10 + 2 * 20 + 3 * 10) / 40)
        self.assertEqual(s.max_level, R.SEVERE)

    def test_empty_route(self):
        s = route("A", [], 0)
        self.assertEqual(s.avg_risk, 0)


class RecommendationTests(SimpleTestCase):
    def test_fewest_severe_miles_beats_everything_else(self):
        a = route("A", [(10, R.SEVERE), (90, R.LOW)], 5)
        b = route("B", [(0, R.SEVERE), (100, R.HIGH)], 9)  # lots of High, slower, but no Severe
        rec = recommend([a, b])
        self.assertEqual(rec.route_id, "B")
        self.assertIn("Fewest Severe miles", rec.reason)

    def test_fewest_high_miles_when_severe_tied(self):
        a = route("A", [(30, R.HIGH), (70, R.LOW)], 5)
        b = route("B", [(10, R.HIGH), (90, R.MODERATE)], 6)
        rec = recommend([a, b])
        self.assertEqual(rec.route_id, "B")
        self.assertIn("Fewest High miles", rec.reason)

    def test_lowest_average_risk_when_severe_and_high_tied(self):
        a = route("A", [(50, R.MODERATE), (50, R.LOW)], 5)
        b = route("B", [(10, R.MODERATE), (90, R.LOW)], 6)
        rec = recommend([a, b])
        self.assertEqual(rec.route_id, "B")
        self.assertIn("Lowest average risk", rec.reason)

    def test_shortest_time_when_risk_identical(self):
        a = route("A", [(100, R.LOW)], 6)
        b = route("B", [(100, R.LOW)], 5)
        c = route("C", [(100, R.LOW)], 7)
        rec = recommend([a, b, c])
        self.assertEqual(rec.route_id, "B")
        self.assertEqual(rec.ranking, ["B", "A", "C"])
        self.assertIn("shortest travel time", rec.reason)

    def test_no_travel_miles_rank_before_severe(self):
        a = route("A", [(5, R.NO_TRAVEL), (95, R.LOW)], 4)
        b = route("B", [(50, R.SEVERE), (50, R.LOW)], 6)
        rec = recommend([a, b])
        self.assertEqual(rec.route_id, "B")
        self.assertIn("No Travel", rec.reason)
        self.assertIsNotNone(rec.warning)  # still has severe miles

    def test_warning_when_every_route_has_no_travel(self):
        a = route("A", [(5, R.NO_TRAVEL), (95, R.LOW)], 4)
        b = route("B", [(10, R.NO_TRAVEL), (90, R.LOW)], 4)
        rec = recommend([a, b])
        self.assertEqual(rec.route_id, "A")
        self.assertIn("delaying departure", rec.warning)

    def test_float_noise_does_not_decide(self):
        a = route("A", [(10.000001, R.HIGH), (90, R.LOW)], 6)
        b = route("B", [(10.0, R.HIGH), (90.000001, R.LOW)], 5)
        self.assertEqual(recommend([a, b]).route_id, "B")

    def test_any_real_average_difference_decides_before_travel_time(self):
        # Strictly per the brief: lower average risk wins even by 0.001, even if slower.
        # (Live case Dallas -> Denver: same 25 High miles, C slightly longer.)
        a = route("A", [(25, R.HIGH), (25, R.MODERATE), (832.8, R.LOW)], 11.88)
        c = route("C", [(25, R.HIGH), (25, R.MODERATE), (841.3, R.LOW)], 12.77)
        rec = recommend([a, c])
        self.assertEqual(rec.route_id, "C")
        self.assertIn("Lowest average risk (0.084 vs 0.085 on Route A)", rec.reason)

    def test_small_real_mile_difference_decides(self):
        a = route("A", [(12.53, R.SEVERE), (87.47, R.LOW)], 5)
        b = route("B", [(12.51, R.SEVERE), (87.49, R.LOW)], 6)
        rec = recommend([a, b])
        self.assertEqual(rec.route_id, "B")
        self.assertIn("12.51 mi vs 12.53 mi", rec.reason)

    def test_full_tie_keeps_provider_order(self):
        a = route("A", [(100, R.LOW)], 5)
        b = route("B", [(100, R.LOW)], 5)
        rec = recommend([a, b])
        self.assertEqual(rec.route_id, "A")
        self.assertIn("Tied", rec.reason)

    def test_single_route(self):
        rec = recommend([route("A", [(100, R.LOW)], 5)])
        self.assertEqual(rec.route_id, "A")
        self.assertIsNone(rec.warning)

    def test_requires_routes(self):
        with self.assertRaises(ValueError):
            recommend([])
