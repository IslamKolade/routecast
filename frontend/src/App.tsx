import { useCallback, useEffect, useState } from 'react';
import { ApiError, checkHealth, planTrip } from './api';
import { CheckpointTable } from './components/CheckpointTable';
import { ForecastPanel } from './components/ForecastPanel';
import { AlertIcon, EditIcon, RouteIcon, TruckIcon } from './components/Icons';
import { RouteList } from './components/RouteList';
import { RouteMap, type FocusedCheckpoint } from './components/RouteMap';
import { TripForm } from './components/TripForm';
import { formatDateTime, formatLb } from './lib/format';
import type { HeatmapMetric, PlanRequest, PlanResponse } from './types';

type ServerState = 'checking' | 'waking' | 'ready' | 'down';
type Tab = 'routes' | 'checkpoints';

export default function App() {
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Hours after departure shown by the heatmap and truck marker (fractional during playback).
  const [time, setTime] = useState(0);
  const [metric, setMetric] = useState<HeatmapMetric>('level');
  const [focused, setFocused] = useState<FocusedCheckpoint | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(true);
  const [tab, setTab] = useState<Tab>('routes');
  const server = useServerWakeUp();

  const handleSubmit = useCallback(async (request: PlanRequest) => {
    setLoading(true);
    setError(null);
    try {
      const result = await planTrip(request);
      setPlan(result);
      setSelectedId(result.recommendation.route_id);
      setFocused(null);
      setTime(0);
      setTab('routes');
      setFormOpen(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Unexpected error while planning the trip.');
    } finally {
      setLoading(false);
    }
  }, []);

  const focusCheckpoint = useCallback(
    (focus: FocusedCheckpoint) => {
      setFocused(focus);
      setSelectedId(focus.routeId);
      setTab('checkpoints');
      // Move the forecast time to the moment the truck reaches this checkpoint, so the
      // heatmap shows the conditions it will actually meet there.
      const cp = plan?.routes.find((r) => r.id === focus.routeId)?.checkpoints[focus.index];
      if (plan && cp) {
        const hours = (new Date(cp.eta).getTime() - new Date(plan.heatmap.start_time).getTime()) / 3_600_000;
        setTime(Math.min(plan.heatmap.hours, Math.max(0, Math.round(hours * 100) / 100)));
      }
    },
    [plan],
  );

  const selectRoute = useCallback((id: string) => {
    setSelectedId(id);
    setFocused(null);
  }, []);

  const selectedRoute = plan?.routes.find((r) => r.id === selectedId) ?? null;
  const showResults = plan && selectedRoute && !formOpen;

  return (
    <div className="app">
      <main className="map-area">
        <RouteMap
          plan={plan}
          selectedId={selectedId}
          time={time}
          metric={metric}
          focused={focused}
          onSelectRoute={selectRoute}
          onFocusCheckpoint={focusCheckpoint}
        />
        {loading && (
          <div className="map-overlay glass">
            <span className="spinner" />
            Finding truck routes and checking forecasts along each one…
          </div>
        )}
        {plan && selectedId && (
          <ForecastPanel
            plan={plan}
            selectedId={selectedId}
            time={time}
            metric={metric}
            onTimeChange={setTime}
            onMetricChange={setMetric}
          />
        )}
      </main>

      <aside className="panel glass">
        <header className="brand">
          <span className="brand__mark">
            <TruckIcon size={18} />
          </span>
          <div>
            <div className="brand__name">RouteCast</div>
            <div className="brand__tagline">Weather-aware truck routing</div>
          </div>
        </header>

        {server === 'waking' && (
          <div className="banner banner--info">
            <span className="spinner" /> Waking up the server — free hosting can take up to a minute…
          </div>
        )}
        {server === 'down' && (
          <div className="banner banner--error">
            <AlertIcon size={14} /> The server is not responding. Please try again shortly.
          </div>
        )}

        {plan && !formOpen && (
          <button type="button" className="trip-chip" onClick={() => setFormOpen(true)}>
            <RouteIcon size={16} />
            <span className="trip-chip__text">
              <strong>
                {shortLabel(plan.request.origin.label)} → {shortLabel(plan.request.destination.label)}
              </strong>
              <span>
                {formatDateTime(plan.request.departure_time)} · {formatLb(plan.request.load_weight_lb)} ·{' '}
                {plan.request.interval_miles} mi checkpoints
              </span>
            </span>
            <EditIcon size={15} />
          </button>
        )}

        <div hidden={!formOpen}>
          <TripForm loading={loading} onSubmit={handleSubmit} />
          {plan && (
            <button type="button" className="link-button" onClick={() => setFormOpen(false)}>
              Back to results
            </button>
          )}
        </div>
        {error && (
          <div className="banner banner--error" role="alert">
            <AlertIcon size={14} /> {error}
          </div>
        )}

        {showResults && (
          <>
            <div className="tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'routes'}
                className={tab === 'routes' ? 'active' : undefined}
                onClick={() => setTab('routes')}
              >
                Routes <span className="tabs__count">{plan.routes.length}</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'checkpoints'}
                className={tab === 'checkpoints' ? 'active' : undefined}
                onClick={() => setTab('checkpoints')}
              >
                Checkpoints <span className="tabs__count">{selectedRoute.checkpoints.length}</span>
              </button>
            </div>
            {tab === 'routes' ? (
              <RouteList plan={plan} selectedId={selectedRoute.id} onSelect={selectRoute} />
            ) : (
              <CheckpointTable
                route={selectedRoute}
                intervalMiles={plan.request.interval_miles}
                focusedIndex={focused?.routeId === selectedRoute.id ? focused.index : null}
                onFocus={focusCheckpoint}
              />
            )}
          </>
        )}

        {!plan && !loading && (
          <div className="how-it-works">
            <h2>How it works</h2>
            <ol>
              <li>
                <b>3 truck routes</b> from a routing API, sized for your load.
              </li>
              <li>
                <b>Checkpoints</b> every 10, 25 or 50 miles with an ETA for each one.
              </li>
              <li>
                <b>Forecast at arrival time</b>: wind, rain and snow when the truck actually gets there.
              </li>
              <li>
                <b>Risk rating</b> with heavy-load wind rules, and a recommendation of the safest route.
              </li>
            </ol>
          </div>
        )}
      </aside>
    </div>
  );
}

/** "Dallas, TX 75201" → "Dallas, TX" — keeps the chip readable. */
function shortLabel(label: string): string {
  return label.split(',').slice(0, 2).join(',').replace(/\s+\d{5}(-\d{4})?$/, '');
}

const KEEP_ALIVE_MS = 10 * 60 * 1000;

/** Ping the API on load; free Render instances sleep and take a while to wake. */
function useServerWakeUp(): ServerState {
  const [state, setState] = useState<ServerState>('checking');
  useEffect(() => {
    const controller = new AbortController();
    const slow = setTimeout(() => setState((s) => (s === 'checking' ? 'waking' : s)), 2500);
    const attempt = async (tries: number): Promise<void> => {
      try {
        await checkHealth(controller.signal);
        setState('ready');
      } catch {
        if (controller.signal.aborted) return;
        if (tries > 1) {
          await new Promise((r) => setTimeout(r, 5000));
          return attempt(tries - 1);
        }
        setState('down');
      }
    };
    attempt(12);

    // Render's free tier sleeps after 15 idle minutes. While the page is open and
    // visible, ping every 10 minutes so the next search doesn't hit a cold start,
    // and ping straight away when the user returns to the tab.
    const keepAlive = () => {
      if (document.visibilityState === 'visible') checkHealth(controller.signal).catch(() => {});
    };
    const interval = setInterval(keepAlive, KEEP_ALIVE_MS);
    document.addEventListener('visibilitychange', keepAlive);

    return () => {
      clearTimeout(slow);
      clearInterval(interval);
      document.removeEventListener('visibilitychange', keepAlive);
      controller.abort();
    };
  }, []);
  return state;
}
