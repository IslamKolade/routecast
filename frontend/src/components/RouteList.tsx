import { memo } from 'react';
import { formatDuration, formatMiles, formatTime } from '../lib/format';
import { RISK_COLOR, RISK_LABEL, RISK_LEVELS, ROUTE_COLOR } from '../lib/risk';
import type { PlanResponse, RouteOption } from '../types';
import { AlertIcon, SparkIcon } from './Icons';
import { RiskBadge } from './RiskBadge';

interface Props {
  plan: PlanResponse;
  selectedId: string;
  onSelect: (id: string) => void;
}

export const RouteList = memo(function RouteList({ plan, selectedId, onSelect }: Props) {
  const { recommendation } = plan;
  const ordered = [...plan.routes].sort((a, b) => a.rank - b.rank);
  const best = plan.routes.find((r) => r.id === recommendation.route_id)!;
  return (
    <section className="results" aria-label="Route options">
      <div className="hero" style={{ '--route-color': ROUTE_COLOR[best.id] } as React.CSSProperties}>
        <div className="hero__eyebrow">
          <SparkIcon size={13} /> Recommended
        </div>
        <div className="hero__title">
          {best.name}
          <span className="hero__meta">
            {formatMiles(best.distance_miles)} · {formatDuration(best.travel_time_s)}
          </span>
        </div>
        <p className="hero__reason">{recommendation.reason}</p>
        {recommendation.warning && (
          <p className="hero__warning">
            <AlertIcon size={14} /> {recommendation.warning}
          </p>
        )}
        {plan.notes.map((note) => (
          <p className="hero__note" key={note}>
            {note}
          </p>
        ))}
      </div>

      <ol className="route-list">
        {ordered.map((route) => (
          <li key={route.id}>
            <RouteCard route={route} selected={route.id === selectedId} onSelect={() => onSelect(route.id)} />
          </li>
        ))}
      </ol>
      <p className="hint">
        Ranked by fewest No Travel → Severe → High miles, then lowest average risk, then shortest drive time.
      </p>
    </section>
  );
});

function RouteCard({ route, selected, onSelect }: { route: RouteOption; selected: boolean; onSelect: () => void }) {
  const s = route.summary;
  return (
    <button
      type="button"
      className={`route-card${selected ? ' route-card--selected' : ''}`}
      onClick={onSelect}
      aria-pressed={selected}
      style={{ '--route-color': ROUTE_COLOR[route.id] ?? '#334155' } as React.CSSProperties}
    >
      <div className="route-card__header">
        <span className="route-card__id">{route.id}</span>
        <div className="route-card__title">
          <span className="route-card__name">
            {route.name}
            {route.recommended && <span className="pill pill--best">Best</span>}
          </span>
          <span className="route-card__via">{route.via.length ? `via ${route.via.join(' · ')}` : '—'}</span>
        </div>
        <span className="route-card__rank" title="Rank">
          #{route.rank}
        </span>
      </div>

      <div className="route-card__stats">
        <Stat label="Distance" value={formatMiles(route.distance_miles)} />
        <Stat label="Drive time" value={formatDuration(route.travel_time_s)} />
        <Stat label="Arrives" value={formatTime(route.arrival_time)} />
      </div>

      <RiskBar route={route} />

      <div className="route-card__risk">
        <Stat label="Severe" value={formatMiles(s.severe_miles)} strong={s.severe_miles > 0} />
        <Stat label="High" value={formatMiles(s.high_miles)} strong={s.high_miles > 0} />
        <Stat label="Avg risk" value={s.avg_risk.toFixed(2)} title="Mile-weighted: Low 0 · Moderate 1 · High 2 · Severe 3 · No Travel 4" />
        <div className="stat">
          <span className="stat__label">Worst</span>
          <RiskBadge level={s.max_level} />
        </div>
      </div>
      {s.no_travel_miles > 0 && (
        <div className="route-card__no-travel">
          <AlertIcon size={13} /> {formatMiles(s.no_travel_miles)} in No Travel conditions
        </div>
      )}
    </button>
  );
}

function Stat({ label, value, strong, title }: { label: string; value: string; strong?: boolean; title?: string }) {
  return (
    <div className="stat" title={title}>
      <span className="stat__label">{label}</span>
      <span className={`stat__value${strong ? ' stat__value--alert' : ''}`}>{value}</span>
    </div>
  );
}

/** Stacked bar: share of the route's miles at each risk level. */
function RiskBar({ route }: { route: RouteOption }) {
  const { miles_by_level, total_miles } = route.summary;
  const title = RISK_LEVELS.filter((l) => miles_by_level[l] > 0)
    .map((l) => `${RISK_LABEL[l]}: ${formatMiles(miles_by_level[l])}`)
    .join(' · ');
  return (
    <div className="risk-bar" title={title} aria-label={`Miles by risk level: ${title}`}>
      {RISK_LEVELS.map((level) => {
        const share = total_miles > 0 ? (miles_by_level[level] / total_miles) * 100 : 0;
        return share > 0 ? <span key={level} style={{ width: `${share}%`, background: RISK_COLOR[level] }} /> : null;
      })}
    </div>
  );
}
