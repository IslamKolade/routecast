# RouteCast: Weather-Aware Truck Routing

RouteCast helps a truck driver pick the safest route. You enter an origin, a destination, a departure time and a load weight. The app then:

1. gets **3 truck route options** from a routing API,
2. places **weather checkpoints every 10, 25 or 50 miles** and estimates when the truck reaches each one,
3. reads the **forecast for that place at that arrival time**,
4. classifies the risk with the given **weather and load rules**,
5. **recommends** the best route (fewest Severe miles → fewest High miles → lowest average risk → shortest time),
6. shows the routes, checkpoints, risk levels, ETAs and summaries on a **map**, with a **weather heatmap** along the corridor and a **0–48 h forecast slider**.

- **Live app:** https://routecast-rose.vercel.app
- **API:** https://routecast-api-2xus.onrender.com/api/health
- **Walkthrough:** _add Loom URL_

> The API runs on Render's free tier, which sleeps after 15 idle minutes. The app pings the API as soon as the page opens (and shows a "waking up" message if that takes a while), then keeps it awake with a small ping every 10 minutes while the tab is open.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React 19 + TypeScript + Vite | Fast dev and build, typed API contract |
| Map | MapLibre GL JS + OpenFreeMap tiles | WebGL rendering with a native heatmap layer; no map key needed |
| Backend | Django 5 + Django REST Framework | Holds the API keys and all routing, weather and risk logic |
| Routing & geocoding | TomTom Routing + Search APIs | Real **truck** routing (vehicle weight, commercial vehicle) with alternative routes and traffic-aware travel times |
| Weather | Open-Meteo forecast API | Free hourly forecasts; returns mph and in/hr directly; many locations per request |

## How it works

```
React ──POST /api/plan──► Django
                           1. geocode origin/destination (if no coordinates)   TomTom Search
                           2. 3 truck route options                            TomTom Routing
                           3. checkpoints every N miles + ETA at each
                           4. corridor grid for the heatmap
                           5. hourly forecasts for all cells in one batch      Open-Meteo
                           6. risk at each checkpoint at its ETA
                           7. route summaries + recommendation
       ◄──── routes, checkpoints, recommendation, 49 heatmap frames (JSON, gzip)
```

### 1. Route options
TomTom is asked for the best truck route plus 2 alternatives (`travelMode=truck`, `vehicleWeight` = cargo + 35,000 lb typical empty tractor-trailer, `traffic=true`, `departAt` = departure). When the road network yields fewer than 3 alternatives, a different path is forced by asking TomTom to **avoid an area** around the middle of the routes already found. The router then chooses the best road around it, typically a parallel highway. (Forcing a via point instead was tried and rejected: it can produce dead-end out-and-back detours.) A forced route is kept only if it:
- is genuinely different (at least 10% of it is more than 2 km from the other routes),
- is no more than 60% slower than the best route, and
- does not drive the same road twice (out-and-back spur detection).

These attempts run in parallel batches.

### 2. Checkpoints and ETA
- Checkpoints sit at mile 0, N, 2N, … and always at the destination.
- **ETA**: TomTom's guidance instructions give the cumulative, traffic-aware travel time at specific polyline vertices. Time is interpolated by distance between those anchors, so a checkpoint after slow city driving gets a later ETA than a constant-speed estimate would give.
- Each checkpoint **represents the road from the midpoint with the previous checkpoint to the midpoint with the next**. These stretches add up exactly to the route length, which is what "Severe miles" and "High miles" are measured in.

### 3. Weather at the ETA
- Forecasts come from Open-Meteo in **mph** and **inches**.
- **Wind** is an instantaneous value at the top of the hour, so the hour **nearest** to the ETA is used.
- **Rain and snow** are totals for the preceding hour (an in/hr rate), so the record for the hour **containing** the ETA is used. For example, an ETA of 14:20 uses the 15:00 record, which covers 14:00–15:00.
- Rain = `rain + showers`; snow = `snowfall` (inches of snow per hour).
- The **sustained** wind speed is classified; gusts are shown for information.
- Each checkpoint is looked up at its own position (rounded to 0.01°, about 1 km, so checkpoints on a stretch shared by two routes reuse one lookup). Heatmap cells sit on a 0.1° grid (~11 km, comparable to the weather models' resolution). Lookups are cached for 30 minutes.

### 4. Risk rules

| Condition | Low | Moderate | High | Severe | No Travel |
|---|---|---|---|---|---|
| Wind (mph) | < 25 | 25–34 | 35–44 | 45–54 | ≥ 55 |
| Rain (in/hr) | < 0.10 | 0.10–0.25 | 0.25–0.50 | 0.50–1.00 | > 1.00 |
| Snow (in/hr) | < 0.5 | 0.5–1.0 | 1.0–2.0 | 2.0–3.0 | > 3.0 |

Load rules, applied to the wind level:
- ≥ 55 mph → No Travel for any load
- 45–54 mph and load > 30,000 lb → No Travel
- 35–44 mph and load > 40,000 lb → Severe

A checkpoint's level is the **worst** of wind (after load rules), rain and snow.

**Boundaries.** The published ranges share end points (0.25 appears under both Moderate and High). The lower bound of each range is treated as inclusive, so a value exactly on a boundary falls into the more severe level. The explicit "> 1.00" and "> 3.0" No Travel cells stay strict. Integer wind ranges are read as half-open (34.9 mph is Moderate). The load comparisons are strict ("> 30,000 lb"). Every boundary is covered by unit tests ([test_risk.py](backend/planner/tests/test_risk.py)).

### 5. Recommendation
Routes are ranked **lexicographically**, so each criterion only matters when all earlier ones are tied:

0. fewest **No Travel** miles *(added: No Travel is worse than Severe, so it must rank first)*
1. fewest **Severe** miles
2. fewest **High** miles
3. lowest **average risk** (mile-weighted; Low = 0, Moderate = 1, High = 2, Severe = 3, No Travel = 4)
4. shortest **travel time**

Miles are compared to 0.1 mi and average risk to 0.001, so floating-point noise cannot decide the result. The UI names the criterion that decided the recommendation (for example, "Fewest Severe miles (0.0 mi vs 42.5 mi on Route B)"). It shows a warning if even the best route has Severe or No Travel miles.

### 6. Heatmap and slider
- The corridor is every grid cell within 25–100 km of any route. The width scales with trip length; the grid step grows so the corridor stays at or under 300 cells.
- For each hour 0–48 after departure, every cell gets the same risk classification used for checkpoints, with the user's load. The response contains all 49 frames.
- Each frame is stored as a property on the map features. Moving the slider only swaps a paint expression on the WebGL heatmap layer, with no network requests and no data re-upload.
- **Playback glides**: between two whole hours the two hourly frames are blended, and the truck marker and slider thumb move continuously. Paint updates are applied one at a time (a new one is sent only when the previous has loaded), so fast scrubbing never queues up work. The side panel is memoized so it does not re-render during playback.
- Measured on integrated graphics (Intel UHD, 1440×900): 60 fps while panning, scrubbing and playing, with no frame longer than 17 ms.
- Heatmap intensity is calibrated against MapLibre's Gaussian kernel. Colours then reflect the risk level, not how densely points are packed ([mapData.ts](frontend/src/lib/mapData.ts)).
- Layers: Overall risk, Wind, Rain, Snow. Hovering shows the values for a cell at the selected hour. A truck marker shows where the truck is at that time on the selected route.

### 7. On the map
- **Routes**: all three lines; click a line or its label to select it.
- **Route summaries**: a label on each route shows drive time, distance and its worst risk level (★ marks the recommended route). Labels are placed on the stretch unique to each route and kept apart from each other.
- **Checkpoints**: dots coloured by risk level. Hover for mile, ETA and risk; click for the full forecast (wind, gusts, rain, snow, which condition set the level, any load rule that applied).
- Selecting a checkpoint also moves the forecast time to the moment the truck reaches it, so the heatmap shows the conditions it will actually meet there.

### Rate limits
Open-Meteo's free tier allows about 600 location lookups per minute, so one plan is capped at about 500. Checkpoints take priority and the heatmap uses the remainder (minimum 100 cells). Only for very long trips at 10-mile sampling (e.g. coast to coast) are checkpoints snapped to a coarser grid (0.1° or more) instead of their exact position; the UI shows a note when that happens.

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness check |
| GET | `/api/geocode?q=dallas` | Place suggestions (TomTom Search; US/CA/MX) |
| POST | `/api/plan` | Full trip plan |

`POST /api/plan` body:
```json
{
  "origin": {"label": "Dallas, TX", "lat": 32.7767, "lon": -96.797},
  "destination": {"label": "Denver, CO"},
  "departure_time": "2026-11-20T14:00:00Z",
  "load_weight_lb": 42000,
  "interval_miles": 25
}
```
Places without coordinates are geocoded. Validation: load 0–80,000 lb; interval 10, 25 or 50; departure from 1 hour ago up to 7 days ahead (so the trip and the 48 h heatmap fall inside the forecast range). A departure slightly in the past is treated as "now". Errors return `{"error": {"code", "message"}}`.

## Project structure

```
backend/
  config/                 Django settings & URLs
  planner/
    services/
      risk.py             risk table + load rules (pure functions)
      ranking.py          route summaries + recommendation
      checkpoints.py      checkpoint placement, coverage, ETA
      routes.py           TomTom route parsing, vertex timing, 3-option logic
      weather.py          Open-Meteo client, hour selection, caching
      heatmap.py          corridor grid + 49 hourly frames
      planner.py          the pipeline
      tomtom.py, geo.py   API client, geometry helpers
    serializers.py, views.py, exceptions.py
    tests/                68 tests (rules, ranking, ETA, weather, routes, API)
frontend/
  src/
    components/           TripForm, PlaceInput, RouteList, CheckpointTable, RouteMap, ForecastPanel
    lib/                  mapData (GeoJSON, heatmap calibration, label placement), timeline, risk, format
    api.ts, types.ts
render.yaml               Render blueprint for the API
```

## Running locally

Requirements: Python 3.12+ and Node 20+, plus a free TomTom API key from developer.tomtom.com.

```bash
# Backend
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env              # then set TOMTOM_API_KEY
python manage.py runserver        # http://127.0.0.1:8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev                       # http://localhost:5173 (proxies /api to Django)
```

Tests:
```bash
cd backend && python manage.py test planner
cd frontend && npm test
```

## Deployment

**Backend (Render).** Create a Web Service from this repo (or use `render.yaml` as a Blueprint) with root directory `backend`, build command `pip install -r requirements.txt`, and start command `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`. Set these environment variables:
- `TOMTOM_API_KEY`
- `DJANGO_SECRET_KEY`
- `PYTHON_VERSION=3.13.3`
- `CORS_ALLOWED_ORIGINS=https://<your-app>.vercel.app`

**Frontend (Vercel).** Import the repo with root directory `frontend` (Vite preset). Set `VITE_API_BASE_URL=https://<your-api>.onrender.com`.

## Assumptions and limitations

- **Driver hours-of-service breaks are not modelled.** ETAs are continuous driving time from TomTom (traffic-aware). Real US truckers must stop for rest on long trips, which would push later ETAs back.
- Load weight is cargo only; 35,000 lb is added as the empty truck weight for TomTom's truck routing restrictions (configurable via `EMPTY_TRUCK_WEIGHT_LB`).
- Weather is sampled at checkpoints, not continuously, so a short storm between two checkpoints can be missed. A smaller interval reduces this.
- Forecast accuracy decreases with lead time; departures are limited to 7 days ahead.
- All times are displayed in the browser's local time zone.
- Geocoding is limited to the US, Canada and Mexico, because the rules use US units.
