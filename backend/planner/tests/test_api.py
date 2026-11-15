from datetime import timedelta
from unittest import mock

from django.core.cache import cache, caches
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from planner.services import planner as planner_module
from planner.services import weather

from .fakes import FakeTomTom, line, open_meteo_response, raw_route

ORIGIN, DEST = (35.2, -101.8), (35.5, -97.5)


def payload(**overrides):
    body = {
        "origin": {"label": "Amarillo, TX", "lat": ORIGIN[0], "lon": ORIGIN[1]},
        "destination": {"label": "Oklahoma City, OK", "lat": DEST[0], "lon": DEST[1]},
        "departure_time": (timezone.now() + timedelta(hours=2)).isoformat(),
        "load_weight_lb": 45_000,
        "interval_miles": 25,
    }
    body.update(overrides)
    return body


def fake_client():
    return FakeTomTom(
        [
            raw_route(line(ORIGIN, DEST), 4 * 3600),
            raw_route(line(ORIGIN, DEST, bulge=0.5), 4.5 * 3600),
            raw_route(line(ORIGIN, DEST, bulge=-0.5), 4.6 * 3600),
        ]
    )


@override_settings(TOMTOM_API_KEY="test")
class PlanEndpointTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        caches["shared"].clear()
        self.api = APIClient()

    def post(self, body, wind=10.0, rain=0.0):
        client = fake_client()
        with mock.patch.object(planner_module, "TomTomClient", return_value=client), mock.patch.object(
            weather.requests, "get", side_effect=lambda url, params, timeout: open_meteo_response(params, wind, rain)
        ):
            return self.api.post("/api/plan", body, format="json"), client

    def test_happy_path_shape(self):
        resp, client = self.post(payload())
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(len(data["routes"]), 3)
        self.assertEqual(client.calls[0]["gross_weight_lb"], 45_000 + 35_000)
        route = data["routes"][0]
        self.assertEqual(route["checkpoints"][0]["mile"], 0)
        self.assertEqual(route["checkpoints"][1]["mile"], 25)
        self.assertAlmostEqual(route["checkpoints"][-1]["mile"], route["distance_miles"], delta=0.1)
        covered = sum(cp["covers_miles"] for cp in route["checkpoints"])
        self.assertAlmostEqual(covered, route["distance_miles"], delta=0.1)
        self.assertEqual(len(route["geometry"]), len(route["timeline_s"]))
        self.assertEqual(sum(r["recommended"] for r in data["routes"]), 1)
        # Calm weather everywhere -> fastest route (A) wins on travel time.
        self.assertEqual(data["recommendation"]["route_id"], "A")
        self.assertIn("travel time", data["recommendation"]["reason"])
        heat = data["heatmap"]
        self.assertEqual(len(heat["frames"]), 49)
        self.assertEqual(len(heat["frames"][0]["level"]), len(heat["cells"]))
        self.assertLessEqual(len(heat["cells"]), 300)

    def test_load_rule_applied_end_to_end(self):
        resp, _ = self.post(payload(load_weight_lb=45_000), wind=40.0)
        cp = resp.json()["routes"][0]["checkpoints"][3]
        self.assertEqual(cp["weather"]["wind_mph"], 40.0)
        self.assertEqual(cp["risk"]["level"], "SEVERE")
        resp, _ = self.post(payload(load_weight_lb=20_000), wind=40.0)
        self.assertEqual(resp.json()["routes"][0]["checkpoints"][3]["risk"]["level"], "HIGH")

    def test_checkpoint_weather_is_looked_up_at_the_checkpoint(self):
        looked_up = set()

        def respond(url, params, timeout):
            looked_up.update(zip(params["latitude"].split(","), params["longitude"].split(",")))
            return open_meteo_response(params)

        with mock.patch.object(planner_module, "TomTomClient", return_value=fake_client()), mock.patch.object(
            weather.requests, "get", side_effect=respond
        ):
            data = self.api.post("/api/plan", payload(), format="json").json()
        self.assertEqual(data["notes"], [])
        for cp in data["routes"][1]["checkpoints"]:
            key = (f"{round(cp['lat'], 2):.4f}", f"{round(cp['lon'], 2):.4f}")
            self.assertIn(key, looked_up)

    def test_eta_increases_along_route(self):
        resp, _ = self.post(payload())
        etas = [cp["eta"] for cp in resp.json()["routes"][0]["checkpoints"]]
        self.assertEqual(etas, sorted(etas))

    def test_geocodes_text_only_places(self):
        body = payload(origin={"label": "Amarillo"}, destination={"label": "Tulsa"})
        resp, _ = self.post(body)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["request"]["origin"]["label"], "Amarillo (geocoded)")

    def test_validation_errors(self):
        cases = [
            payload(load_weight_lb=-1),
            payload(load_weight_lb=90_000),
            payload(interval_miles=30),
            payload(departure_time=(timezone.now() - timedelta(hours=3)).isoformat()),
            payload(departure_time=(timezone.now() + timedelta(days=9)).isoformat()),
            payload(destination={"label": "Same", "lat": ORIGIN[0], "lon": ORIGIN[1]}),
            payload(origin={"label": "x", "lat": 10}),
        ]
        for body in cases:
            with self.subTest(body=body):
                resp = self.api.post("/api/plan", body, format="json")
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["error"]["code"], "invalid_input")

    def test_cross_country_trip_stays_within_weather_budget(self):
        o, d = (40.7, -74.0), (34.05, -118.2)  # New York → Los Angeles, ~2,800 mi
        client = FakeTomTom(
            [raw_route(line(o, d, n=800, bulge=b), 42 * 3600 * (1 + abs(b) / 10)) for b in (0, 2, -2)]
        )
        looked_up = []

        def respond(url, params, timeout):
            looked_up.extend(params["latitude"].split(","))
            return open_meteo_response(params)

        body = payload(origin={"label": "NY", "lat": o[0], "lon": o[1]},
                       destination={"label": "LA", "lat": d[0], "lon": d[1]}, interval_miles=10)
        with mock.patch.object(planner_module, "TomTomClient", return_value=client), mock.patch.object(
            weather.requests, "get", side_effect=respond
        ):
            resp = self.api.post("/api/plan", body, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertLessEqual(len(looked_up), 550)
        self.assertGreaterEqual(len(resp.json()["heatmap"]["cells"]), 100)
        self.assertTrue(any("weather grid" in n for n in resp.json()["notes"]))
        # Every checkpoint still gets weather and a risk level.
        for route in resp.json()["routes"]:
            self.assertTrue(all(cp["weather"]["complete"] for cp in route["checkpoints"]))

    def test_departure_slightly_in_past_means_now(self):
        body = payload(departure_time=(timezone.now() - timedelta(minutes=20)).isoformat())
        resp, _ = self.post(body)
        self.assertEqual(resp.status_code, 200, resp.content)


def relay_items(weather_request, wind=10.0, rain=0.0):
    """What the browser does: fetch the requested cells with the given params, in order."""
    cells = weather_request["cells"]
    params = dict(weather_request["params"])
    params["latitude"] = ",".join(str(c[0]) for c in cells)
    params["longitude"] = ",".join(str(c[1]) for c in cells)
    data = open_meteo_response(params, wind, rain).json()
    return data if isinstance(data, list) else [data]


@override_settings(TOMTOM_API_KEY="test")
class WeatherRelayTests(SimpleTestCase):
    """When Open-Meteo rate-limits the server, the browser relays the weather download."""

    def setUp(self):
        cache.clear()
        caches["shared"].clear()
        self.api = APIClient()

    def start_plan(self, weather_status=429):
        limited = mock.Mock(status_code=weather_status)
        with mock.patch.object(planner_module, "TomTomClient", return_value=fake_client()), mock.patch.object(
            weather.requests, "get", return_value=limited
        ) as get:
            resp = self.api.post("/api/plan", payload(), format="json")
        return resp, get

    def test_rate_limited_server_asks_browser_for_weather(self):
        resp, _ = self.start_plan()
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["status"], "weather_required")
        req = data["weather_request"]
        self.assertEqual(req["url"], weather.API_URL)
        self.assertEqual(req["params"]["wind_speed_unit"], "mph")
        self.assertEqual(req["params"]["precipitation_unit"], "inch")
        self.assertGreater(len(req["cells"]), 0)

        done = self.api.post(f"/api/plan/{data['plan_id']}/weather",
                             {"forecasts": relay_items(req, wind=40.0)}, format="json")
        self.assertEqual(done.status_code, 200, done.content)
        plan = done.json()
        self.assertEqual(plan["status"], "complete")
        self.assertEqual(len(plan["routes"]), 3)
        # The relayed data goes through the same risk rules: 40 mph + 45,000 lb -> Severe.
        self.assertEqual(plan["routes"][0]["checkpoints"][1]["risk"]["level"], "SEVERE")
        self.assertEqual(len(plan["heatmap"]["frames"]), 49)

    def test_server_stops_trying_while_rate_limited(self):
        self.start_plan()
        _, get = self.start_plan(weather_status=200)
        get.assert_not_called()

    def test_relayed_weather_is_cached_for_the_next_plan(self):
        data = self.start_plan()[0].json()
        self.api.post(f"/api/plan/{data['plan_id']}/weather",
                      {"forecasts": relay_items(data["weather_request"])}, format="json")
        again = self.start_plan()[0].json()
        self.assertEqual(again["status"], "complete")

    def test_plan_can_only_be_completed_once(self):
        data = self.start_plan()[0].json()
        url = f"/api/plan/{data['plan_id']}/weather"
        items = relay_items(data["weather_request"])
        self.assertEqual(self.api.post(url, {"forecasts": items}, format="json").status_code, 200)
        self.assertEqual(self.api.post(url, {"forecasts": items}, format="json").status_code, 410)

    def test_unknown_plan_is_gone(self):
        resp = self.api.post("/api/plan/nope/weather", {"forecasts": []}, format="json")
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.json()["error"]["code"], "plan_expired")

    def test_rejects_wrong_or_malformed_weather(self):
        data = self.start_plan()[0].json()
        url = f"/api/plan/{data['plan_id']}/weather"
        items = relay_items(data["weather_request"])
        self.assertEqual(self.api.post(url, {"forecasts": items[:-1]}, format="json").status_code, 400)
        bad = [dict(i) for i in items]
        bad[0] = {"hourly": {**items[0]["hourly"], "wind_speed_10m": ["strong"] * 60}}
        resp = self.api.post(url, {"forecasts": bad}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("non-numeric", resp.json()["error"]["message"])
        wrong_start = [{"hourly": {**i["hourly"], "time": i["hourly"]["time"][1:]}} for i in items]
        self.assertEqual(self.api.post(url, {"forecasts": wrong_start}, format="json").status_code, 400)
        # A rejected attempt does not consume the plan: valid data still completes it.
        self.assertEqual(self.api.post(url, {"forecasts": items}, format="json").status_code, 200)


class HealthAndGeocodeTests(SimpleTestCase):
    def test_health(self):
        self.assertEqual(APIClient().get("/api/health").json(), {"status": "ok"})

    def test_short_query_returns_nothing(self):
        self.assertEqual(APIClient().get("/api/geocode?q=ab").json(), {"results": []})

    @override_settings(TOMTOM_API_KEY="")
    def test_missing_key_is_reported(self):
        cache.clear()
        resp = APIClient().get("/api/geocode?q=dallas")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["error"]["code"], "configuration_error")
