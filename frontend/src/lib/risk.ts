import type { HeatmapMetric, RiskLevel } from '../types';

export const RISK_LEVELS: RiskLevel[] = ['LOW', 'MODERATE', 'HIGH', 'SEVERE', 'NO_TRAVEL'];

export const RISK_LABEL: Record<RiskLevel, string> = {
  LOW: 'Low',
  MODERATE: 'Moderate',
  HIGH: 'High',
  SEVERE: 'Severe',
  NO_TRAVEL: 'No Travel',
};

export const RISK_COLOR: Record<RiskLevel, string> = {
  LOW: '#16a34a',
  MODERATE: '#eab308',
  HIGH: '#f97316',
  SEVERE: '#dc2626',
  NO_TRAVEL: '#7f1d1d',
};

/** Route line colours: deliberately outside the green→red risk palette. */
export const ROUTE_COLOR: Record<string, string> = {
  A: '#4f46e5',
  B: '#0284c7',
  C: '#c026d3',
};

export const METRIC_LABEL: Record<HeatmapMetric, string> = {
  level: 'Overall risk',
  wind_level: 'Wind',
  rain_level: 'Rain',
  snow_level: 'Snow',
};

/** Ranges from the assessment's risk table, for the legend. */
export const METRIC_RANGES: Record<Exclude<HeatmapMetric, 'level'>, string[]> = {
  wind_level: ['<25 mph', '25–34', '35–44', '45–54', '≥55'],
  rain_level: ['<0.10 in/hr', '0.10–0.25', '0.25–0.50', '0.50–1.00', '>1.00'],
  snow_level: ['<0.5 in/hr', '0.5–1.0', '1.0–2.0', '2.0–3.0', '>3.0'],
};

export function levelFromScore(score: number | null | undefined): RiskLevel | null {
  return score == null ? null : (RISK_LEVELS[score] ?? null);
}
