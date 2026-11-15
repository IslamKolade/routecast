from django.test import SimpleTestCase

from planner.services.risk import (
    RiskLevel as R,
    apply_load_rules,
    assess,
    classify_rain,
    classify_snow,
    classify_wind,
)


class WindTableTests(SimpleTestCase):
    def test_boundaries(self):
        cases = [
            (0, R.LOW), (24.9, R.LOW),
            (25, R.MODERATE), (34, R.MODERATE), (34.9, R.MODERATE),
            (35, R.HIGH), (44, R.HIGH), (44.9, R.HIGH),
            (45, R.SEVERE), (54, R.SEVERE), (54.9, R.SEVERE),
            (55, R.NO_TRAVEL), (80, R.NO_TRAVEL),
        ]
        for mph, expected in cases:
            with self.subTest(mph=mph):
                self.assertEqual(classify_wind(mph), expected)


class RainTableTests(SimpleTestCase):
    def test_boundaries(self):
        cases = [
            (0, R.LOW), (0.099, R.LOW),
            (0.10, R.MODERATE), (0.249, R.MODERATE),
            (0.25, R.HIGH), (0.499, R.HIGH),
            (0.50, R.SEVERE), (1.00, R.SEVERE),
            (1.001, R.NO_TRAVEL), (2.0, R.NO_TRAVEL),
        ]
        for rate, expected in cases:
            with self.subTest(rate=rate):
                self.assertEqual(classify_rain(rate), expected)


class SnowTableTests(SimpleTestCase):
    def test_boundaries(self):
        cases = [
            (0, R.LOW), (0.49, R.LOW),
            (0.5, R.MODERATE), (0.99, R.MODERATE),
            (1.0, R.HIGH), (1.99, R.HIGH),
            (2.0, R.SEVERE), (3.0, R.SEVERE),
            (3.01, R.NO_TRAVEL),
        ]
        for rate, expected in cases:
            with self.subTest(rate=rate):
                self.assertEqual(classify_snow(rate), expected)


class LoadRuleTests(SimpleTestCase):
    def test_55_mph_is_no_travel_for_any_load(self):
        for load in (0, 10_000, 30_000, 80_000):
            with self.subTest(load=load):
                self.assertEqual(assess(55, 0, 0, load).level, R.NO_TRAVEL)

    def test_45_to_54_mph_over_30000_lb_is_no_travel(self):
        self.assertEqual(assess(45, 0, 0, 30_001).level, R.NO_TRAVEL)
        self.assertEqual(assess(54.9, 0, 0, 45_000).level, R.NO_TRAVEL)

    def test_45_to_54_mph_at_or_below_30000_lb_stays_severe(self):
        self.assertEqual(assess(50, 0, 0, 30_000).level, R.SEVERE)
        self.assertEqual(assess(50, 0, 0, 0).level, R.SEVERE)

    def test_35_to_44_mph_over_40000_lb_is_severe(self):
        self.assertEqual(assess(35, 0, 0, 40_001).level, R.SEVERE)
        self.assertEqual(assess(44.9, 0, 0, 45_000).level, R.SEVERE)

    def test_35_to_44_mph_at_or_below_40000_lb_stays_high(self):
        self.assertEqual(assess(40, 0, 0, 40_000).level, R.HIGH)
        # 35–44 mph between 30k and 40k lb: the 30k rule only applies to 45–54 mph.
        self.assertEqual(assess(40, 0, 0, 35_000).level, R.HIGH)

    def test_load_does_not_affect_lower_wind_levels(self):
        self.assertEqual(assess(30, 0, 0, 80_000).level, R.MODERATE)
        self.assertEqual(assess(10, 0, 0, 80_000).level, R.LOW)

    def test_load_rule_is_reported(self):
        level, rule = apply_load_rules(R.HIGH, 45_000)
        self.assertEqual(level, R.SEVERE)
        self.assertIn("40,000", rule)
        self.assertIsNone(apply_load_rules(R.HIGH, 20_000)[1])

    def test_load_rules_do_not_touch_rain_or_snow(self):
        self.assertEqual(assess(0, 0.3, 0, 80_000).level, R.HIGH)
        self.assertEqual(assess(0, 0, 1.5, 80_000).level, R.HIGH)


class CombinedAssessmentTests(SimpleTestCase):
    def test_worst_condition_wins(self):
        a = assess(wind_mph=10, rain_in_hr=0.3, snow_in_hr=0.6, load_lb=20_000)
        self.assertEqual(a.level, R.HIGH)
        self.assertEqual(a.drivers, ("rain",))

    def test_ties_report_all_drivers(self):
        a = assess(wind_mph=36, rain_in_hr=0.3, snow_in_hr=0, load_lb=20_000)
        self.assertEqual(a.level, R.HIGH)
        self.assertEqual(a.drivers, ("wind", "rain"))

    def test_calm_weather_is_low_with_no_drivers(self):
        a = assess(5, 0, 0, 45_000)
        self.assertEqual(a.level, R.LOW)
        self.assertEqual(a.drivers, ())

    def test_examples_from_walkthrough(self):
        self.assertEqual(assess(40, 0.05, 0, 45_000).level, R.SEVERE)
        self.assertEqual(assess(40, 0.05, 0, 20_000).level, R.HIGH)
        self.assertEqual(assess(50, 0, 0, 35_000).level, R.NO_TRAVEL)
        self.assertEqual(assess(50, 0, 0, 25_000).level, R.SEVERE)
        self.assertEqual(assess(10, 0.30, 0, 1_000).level, R.HIGH)

    def test_to_dict(self):
        d = assess(40, 0, 0, 45_000).to_dict()
        self.assertEqual(d["level"], "SEVERE")
        self.assertEqual(d["label"], "Severe")
        self.assertEqual(d["score"], 3)
        self.assertEqual(d["wind_level_base"], "HIGH")
