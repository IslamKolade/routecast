import { useEffect, useRef, useState } from 'react';
import { formatDateTime } from '../lib/format';
import { METRIC_LABEL, METRIC_RANGES, RISK_COLOR, RISK_LABEL, RISK_LEVELS } from '../lib/risk';
import type { Checkpoint, HeatmapMetric, PlanResponse } from '../types';
import { PauseIcon, PlayIcon, TruckIcon } from './Icons';

interface Props {
  plan: PlanResponse;
  selectedId: string;
  /** Hours after departure; whole hours when set by the user, fractional during playback. */
  time: number;
  metric: HeatmapMetric;
  onTimeChange: (time: number) => void;
  onMetricChange: (metric: HeatmapMetric) => void;
}

const PLAY_MS_PER_HOUR = 500; // 48 h plays in 24 s
const PLAY_FRAME_MS = 33; // cap React updates at ~30 fps

export function ForecastPanel({ plan, selectedId, time, metric, onTimeChange, onMetricChange }: Props) {
  const [playing, setPlaying] = useState(false);
  const maxHour = plan.heatmap.hours;
  const timeRef = useRef(time);
  timeRef.current = time;

  // Playback advances time continuously; the map blends neighbouring hourly frames.
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let lastEmit = last;
    let t = timeRef.current >= maxHour ? 0 : timeRef.current;
    let emitted = timeRef.current;
    const tick = (now: number) => {
      // If something else moved the time (e.g. a checkpoint was clicked), continue from there.
      if (timeRef.current !== emitted) t = timeRef.current;
      t += (now - last) / PLAY_MS_PER_HOUR;
      last = now;
      if (t > maxHour) t = 0;
      if (now - lastEmit >= PLAY_FRAME_MS) {
        lastEmit = now;
        emitted = t;
        timeRef.current = t;
        onTimeChange(t);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, maxHour, onTimeChange]);

  const togglePlay = () => {
    // Pausing settles on a whole hour, where the forecast data actually is.
    if (playing) onTimeChange(Math.round(time));
    setPlaying(!playing);
  };

  // Stop playback when a new plan arrives.
  useEffect(() => setPlaying(false), [plan]);

  const setHour = (hour: number) => {
    setPlaying(false);
    onTimeChange(Math.min(maxHour, Math.max(0, Math.round(hour))));
  };

  const shownTime = new Date(new Date(plan.heatmap.start_time).getTime() + time * 3_600_000);
  const route = plan.routes.find((r) => r.id === selectedId);
  const truckStatus = route ? describeTruck(route.checkpoints, time * 3600, route.travel_time_s, route.name) : '';
  const ranges = metric === 'level' ? null : METRIC_RANGES[metric];
  const hourLabel = Number.isInteger(time) ? String(time) : time.toFixed(1);

  return (
    <div className="forecast-panel glass">
      <div className="forecast-panel__top">
        <button
          type="button"
          className="play-button"
          onClick={togglePlay}
          aria-label={playing ? 'Pause forecast animation' : 'Play forecast animation'}
        >
          {playing ? <PauseIcon size={14} /> : <PlayIcon size={14} />}
        </button>
        <div className="forecast-panel__time">
          <div>
            <span className="forecast-panel__hour">+{hourLabel}h</span>
            <strong>{formatDateTime(shownTime)}</strong>
          </div>
          <div className="forecast-panel__truck">
            <TruckIcon size={13} /> {truckStatus}
          </div>
        </div>
        <div className="segmented segmented--small" role="radiogroup" aria-label="Heatmap layer">
          {(Object.keys(METRIC_LABEL) as HeatmapMetric[]).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={metric === m}
              className={metric === m ? 'selected' : undefined}
              onClick={() => onMetricChange(m)}
            >
              {METRIC_LABEL[m]}
            </button>
          ))}
        </div>
      </div>

      <input
        className="forecast-slider"
        type="range"
        min={0}
        max={maxHour}
        step={0.01} // fine steps so the thumb glides during playback; user input snaps to whole hours
        value={time}
        style={{ '--progress': `${(time / maxHour) * 100}%` } as React.CSSProperties}
        onChange={(e) => setHour(Number(e.target.value))}
        onKeyDown={(e) => {
          const delta = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -6, PageUp: 6 }[e.key];
          if (delta) {
            e.preventDefault();
            setHour(Math.round(time) + delta);
          }
        }}
        aria-label="Forecast hour after departure"
        aria-valuetext={`${hourLabel} hours after departure`}
      />
      <div className="forecast-ticks" aria-hidden>
        {[0, 12, 24, 36, 48].map((h) => (
          <span key={h}>{h === 0 ? 'Departure' : `+${h}h`}</span>
        ))}
      </div>

      <div className="legend">
        <span className="legend__title">{METRIC_LABEL[metric]}</span>
        {RISK_LEVELS.map((level, i) => (
          <span key={level} className="legend__item">
            <span className="legend__swatch" style={{ background: RISK_COLOR[level] }} />
            {RISK_LABEL[level]}
            {ranges && <span className="muted">{ranges[i]}</span>}
          </span>
        ))}
      </div>
    </div>
  );
}

function describeTruck(checkpoints: Checkpoint[], seconds: number, travelTime: number, name: string): string {
  if (seconds > travelTime) return `Arrived — ${name}`;
  const t = new Date(checkpoints[0].eta).getTime() + seconds * 1000;
  // Interpolate the mile marker between the two checkpoints around time t.
  for (let i = 1; i < checkpoints.length; i++) {
    const a = checkpoints[i - 1];
    const b = checkpoints[i];
    const ta = new Date(a.eta).getTime();
    const tb = new Date(b.eta).getTime();
    if (t <= tb) {
      const f = tb > ta ? (t - ta) / (tb - ta) : 0;
      const mile = Math.round(a.mile + (b.mile - a.mile) * f);
      return mile === 0 ? `Departing — ${name}` : `${name} · around mile ${mile}`;
    }
  }
  return `Arrived — ${name}`;
}
