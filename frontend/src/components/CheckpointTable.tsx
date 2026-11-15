import { memo } from 'react';
import { formatTime } from '../lib/format';
import { ROUTE_COLOR } from '../lib/risk';
import type { RouteOption } from '../types';
import { RiskBadge } from './RiskBadge';

interface Props {
  route: RouteOption;
  intervalMiles: number;
  focusedIndex: number | null;
  onFocus: (focus: { routeId: string; index: number }) => void;
}

const num = (v: number | null, digits: number) => (v == null ? '—' : v.toFixed(digits));

export const CheckpointTable = memo(function CheckpointTable({ route, intervalMiles, focusedIndex, onFocus }: Props) {
  const focus = (index: number) => onFocus({ routeId: route.id, index });
  return (
    <section className="checkpoints" aria-label={`Checkpoints for ${route.name}`}>
      <div className="section-head">
        <span className="dot" style={{ background: ROUTE_COLOR[route.id] }} />
        <h2>{route.name}</h2>
        <span className="muted">
          {route.checkpoints.length} checkpoints · every {intervalMiles} mi
        </span>
      </div>
      <p className="hint">Forecast at each point for the time the truck gets there. Select a row to see it on the map.</p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Mile</th>
              <th>ETA</th>
              <th title="Sustained wind, mph">
                Wind<small>mph</small>
              </th>
              <th title="Rain rate, inches per hour">
                Rain<small>in/h</small>
              </th>
              <th title="Snowfall rate, inches per hour">
                Snow<small>in/h</small>
              </th>
              <th>Risk</th>
            </tr>
          </thead>
          <tbody>
            {route.checkpoints.map((cp) => (
              <tr
                key={cp.index}
                className={cp.index === focusedIndex ? 'focused' : undefined}
                onClick={() => focus(cp.index)}
                tabIndex={0}
                onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && focus(cp.index)}
              >
                <td className="num">{cp.mile.toFixed(0)}</td>
                <td>{formatTime(cp.eta)}</td>
                <td className={`num${cp.risk.drivers.includes('wind') ? ' driver' : ''}`}>
                  {num(cp.weather.wind_mph, 0)}
                </td>
                <td className={`num${cp.risk.drivers.includes('rain') ? ' driver' : ''}`}>
                  {num(cp.weather.rain_in_hr, 2)}
                </td>
                <td className={`num${cp.risk.drivers.includes('snow') ? ' driver' : ''}`}>
                  {num(cp.weather.snow_in_hr, 2)}
                </td>
                <td>
                  <RiskBadge level={cp.risk.level} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
});
