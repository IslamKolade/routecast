import type { ExpressionSpecification } from 'maplibre-gl';
import type { Heatmap, HeatmapMetric, PlanResponse } from '../types';
import { RISK_COLOR, ROUTE_COLOR } from './risk';

const METRICS: HeatmapMetric[] = ['level', 'wind_level', 'rain_level', 'snow_level'];

export const propKey = (metric: HeatmapMetric, hour: number) => `${metric}_${hour}`;

export function routesGeoJSON(plan: PlanResponse, selectedId: string): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: plan.routes.map((r) => ({
      type: 'Feature',
      properties: {
        id: r.id,
        color: ROUTE_COLOR[r.id] ?? '#334155',
        selected: r.id === selectedId,
        sort: r.id === selectedId ? 1 : 0,
      },
      geometry: { type: 'LineString', coordinates: r.geometry },
    })),
  };
}

export function checkpointsGeoJSON(plan: PlanResponse, selectedId: string): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: plan.routes.flatMap((r) =>
      r.checkpoints.map((cp) => ({
        type: 'Feature' as const,
        properties: {
          routeId: r.id,
          index: cp.index,
          color: RISK_COLOR[cp.risk.level],
          selected: r.id === selectedId,
          sort: (r.id === selectedId ? 10 : 0) + cp.risk.score,
        },
        geometry: { type: 'Point' as const, coordinates: [cp.lon, cp.lat] },
      })),
    ),
  };
}

/**
 * Every hour and metric is stored as a flat property on each cell
 * (e.g. `level_12`), so moving the slider only swaps a paint expression and
 * never re-uploads data.
 */
export function heatmapGeoJSON(heatmap: Heatmap): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: heatmap.cells.map((coords, cell) => {
      const properties: Record<string, number | null> = { cell };
      heatmap.frames.forEach((frame, hour) => {
        for (const metric of METRICS) properties[propKey(metric, hour)] = frame[metric][cell];
      });
      return { type: 'Feature', properties, geometry: { type: 'Point', coordinates: coords } };
    }),
  };
}

/**
 * Heatmap weight at `time` hours after departure. Risk score 0..4 maps to weight
 * 0.2..1.0 (missing data → 0, i.e. transparent). Between two whole hours the two
 * hourly frames are blended, so playback glides instead of jumping hour to hour.
 */
export function heatWeightExpression(metric: HeatmapMetric, time: number, maxHour: number): ExpressionSpecification {
  const weightAt = (hour: number): ExpressionSpecification => {
    const value: ExpressionSpecification = ['coalesce', ['get', propKey(metric, hour)], -1];
    return ['case', ['<', value, 0], 0, ['/', ['+', value, 1], 5]];
  };
  const t = Math.min(Math.max(time, 0), maxHour);
  const h0 = Math.floor(t);
  const f = t - h0;
  if (f < 0.01 || h0 >= maxHour) return weightAt(h0);
  return ['+', ['*', weightAt(h0), 1 - f], ['*', weightAt(h0 + 1), f]];
}

/** Density → colour. Stops sit at the weights produced for each risk level. */
export const HEAT_COLOR: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['heatmap-density'],
  0, 'rgba(22,163,74,0)',
  0.1, 'rgba(22,163,74,0.12)',
  0.2, 'rgba(22,163,74,0.38)',
  0.4, 'rgba(234,179,8,0.55)',
  0.6, 'rgba(249,115,22,0.65)',
  0.8, 'rgba(220,38,38,0.72)',
  1, 'rgba(127,29,29,0.82)',
];

/**
 * Heatmap calibration. MapLibre's heatmap kernel is a Gaussian with
 * σ = radius / 3 and peak 0.3989 × weight × intensity. Summed over a regular
 * grid that is (approximately) 0.3989·w·I·2πσ² / (sx·sy). With the radius set to
 * twice the north–south cell spacing, a uniform field of weight w then gives a
 * density ≈ w when I = 0.898·cos(lat) — independent of zoom — so colours match
 * the risk levels instead of the density of points.
 */
export function heatCalibration(heatmap: Heatmap) {
  const lats = heatmap.cells.map((c) => c[1]);
  const midLat = lats.length ? (Math.min(...lats) + Math.max(...lats)) / 2 : 0;
  const cos = Math.cos((midLat * Math.PI) / 180);
  // Cell spacing in pixels at zoom 0 (512 px world) along the north–south axis (Mercator).
  const spacingZ0 = ((heatmap.step_deg / 360) * 512) / cos;
  const radiusZ0 = 2 * spacingZ0;
  const radius: ExpressionSpecification = [
    'interpolate', ['exponential', 2], ['zoom'], 0, radiusZ0, 12, radiusZ0 * 2 ** 12,
  ];
  const hitRadius: ExpressionSpecification = [
    'interpolate', ['exponential', 2], ['zoom'], 0, spacingZ0 / 2, 12, (spacingZ0 / 2) * 2 ** 12,
  ];
  return { radius, hitRadius, intensity: 0.898 * cos };
}

export function boundsOf(plan: PlanResponse): [[number, number], [number, number]] {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const r of plan.routes) {
    for (const [x, y] of r.geometry) {
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
    }
  }
  return [[minX, minY], [maxX, maxY]];
}

/**
 * Where to put each route's summary label. For every route, candidate points
 * between 20% and 80% of the way along are scored by
 *   - distance from the other routes (prefer the stretch unique to this route), and
 *   - distance from labels already placed (so labels never pile up, even when
 *     two routes share most of their road),
 * both measured relative to the size of the trip.
 */
export function routeLabelAnchors(plan: PlanResponse): Record<string, [number, number]> {
  const [[minX, minY], [maxX, maxY]] = boundsOf(plan);
  const cos = Math.cos((((minY + maxY) / 2) * Math.PI) / 180);
  const dist = (a: [number, number], b: [number, number]) => Math.hypot((a[0] - b[0]) * cos, a[1] - b[1]);
  const diagonal = dist([minX, minY], [maxX, maxY]) || 1;
  const thin = (g: [number, number][]) => g.filter((_, i) => i % Math.max(1, Math.floor(g.length / 150)) === 0);
  const thinned = plan.routes.map((r) => thin(r.geometry));

  const anchors: Record<string, [number, number]> = {};
  const placed: [number, number][] = [];
  plan.routes.forEach((route, ri) => {
    const others = thinned.filter((_, i) => i !== ri).flat();
    const g = route.geometry;
    let best = g[Math.floor(g.length / 2)];
    let bestScore = -Infinity;
    for (let f = 0.2; f <= 0.801; f += 0.05) {
      const p = g[Math.floor((g.length - 1) * f)];
      const fromRoutes = Math.min(...others.map((o) => dist(p, o)), diagonal) / diagonal;
      const fromLabels = Math.min(...placed.map((l) => dist(p, l)), diagonal) / diagonal;
      // Label separation matters most up to a quarter of the trip's size; a slight
      // preference for the middle breaks ties.
      const score = fromRoutes + 2 * Math.min(fromLabels, 0.25) - 0.05 * Math.abs(f - 0.5);
      if (score > bestScore) {
        bestScore = score;
        best = p;
      }
    }
    anchors[route.id] = best;
    placed.push(best);
  });
  return anchors;
}
