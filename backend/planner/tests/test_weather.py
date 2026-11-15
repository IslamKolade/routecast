from datetime import datetime, timedelta, timezone
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase

from planner.exceptions import UpstreamServiceError
from planner.services import weather
from planner.services.weather import Forecast, fetch_forecasts, snap

START = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)


def make_forecast(hours=6):
    return Forecast(
        start=START,
        wind=[float(10 + i) for i in range(hours)],  # 10, 11, 12 ...
        gust=[float(20 + i) for i in range(hours)],
        rain=[i / 100 for i in range(hours)],  # 0.00, 0.01, 0.02 ...
        snow=[0.0] * hours,
        temp=[40.0] * hours,
        code=[0] * hours,
    )


class SampleAtTests(SimpleTestCase):
    def test_wind_uses_nearest_hour(self):
        f = make_forecast()
        self.assertEqual(f.sample_at(START + timedelta(hours=2, minutes=20)).wind_mph, 12)
        self.assertEqual(f.sample_at(START + timedelta(hours=2, minutes=40)).wind_mph, 13)

    def test_precip_uses_hour_containing_eta(self):
        f = make_forecast()
        # 02:20 lies in 02:00–03:00, which Open-Meteo reports at 03:00 (index 3).
        s = f.sample_at(START + timedelta(hours=2, minutes=20))
        self.assertEqual(s.rain_in_hr, 0.03)
        self.assertEqual(s.precip_time, START + timedelta(hours=3))
        self.assertEqual(s.wind_time, START + timedelta(hours=2))

    def test_on_the_hour(self):
        s = make_forecast().sample_at(START + timedelta(hours=2))
        self.assertEqual(s.wind_mph, 12)
        self.assertEqual(s.rain_in_hr, 0.03)

    def test_out_of_range_is_incomplete(self):
        s = make_forecast(hours=3).sample_at(START + timedelta(hours=5))
        self.assertIsNone(s.wind_mph)
        self.assertFalse(s.complete)


class SnapTests(SimpleTestCase):
    def test_snap(self):
        self.assertEqual(snap((35.149, -101.861)), (35.1, -101.9))
        self.assertEqual(snap((-0.04, 0.06)), (-0.0, 0.1))

    def test_checkpoint_precision(self):
        self.assertEqual(snap((35.14937, -101.86144), 0.01), (35.15, -101.86))


def fake_response(cells, start, hours, status=200):
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    item = {
        "hourly": {
            "time": times,
            "wind_speed_10m": [30.0] * hours,
            "wind_gusts_10m": [40.0] * hours,
            "rain": [0.05] * hours,
            "showers": [0.10] * hours,
            "snowfall": [0.0] * hours,
            "temperature_2m": [50.0] * hours,
            "weather_code": [61] * hours,
        }
    }
    resp = mock.Mock(status_code=status)
    resp.json.return_value = [item] * len(cells) if len(cells) > 1 else item
    return resp


class FetchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_fetch_parses_sums_rain_and_showers_and_caches(self):
        cells = [(35.1, -101.9), (35.2, -101.9)]
        end = START + timedelta(hours=3)
        with mock.patch.object(weather.requests, "get", return_value=fake_response(cells, START, 4)) as get:
            result = fetch_forecasts(cells, START, end)
            self.assertEqual(get.call_count, 1)
            params = get.call_args.kwargs["params"]
            self.assertEqual(params["wind_speed_unit"], "mph")
            self.assertEqual(params["precipitation_unit"], "inch")
            self.assertEqual(params["start_hour"], "2026-11-20T00:00")
            fetch_forecasts(cells, START, end)  # second call served from cache
            self.assertEqual(get.call_count, 1)
        self.assertEqual(result[cells[0]].rain[0], 0.15)

    def test_chunks_large_requests(self):
        cells = [(30 + i * 0.1, -100.0) for i in range(250)]
        end = START + timedelta(hours=1)

        def respond(url, params, timeout):
            n = len(params["latitude"].split(","))
            return fake_response([None] * n, START, 2)

        with mock.patch.object(weather.requests, "get", side_effect=respond) as get:
            result = fetch_forecasts(cells, START, end)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(len(result), 250)

    def test_rate_limit_error(self):
        resp = mock.Mock(status_code=429)
        with mock.patch.object(weather.requests, "get", return_value=resp):
            with self.assertRaises(UpstreamServiceError):
                fetch_forecasts([(1.0, 1.0)], START, START)

    def test_unexpected_time_range_rejected(self):
        resp = fake_response([(1.0, 1.0)], START + timedelta(hours=1), 2)
        with mock.patch.object(weather.requests, "get", return_value=resp):
            with self.assertRaises(UpstreamServiceError):
                fetch_forecasts([(1.0, 1.0)], START, START + timedelta(hours=1))
