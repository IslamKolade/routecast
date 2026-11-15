import maplibregl, { type GeoJSONSource, type MapMouseEvent } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useEffect, useRef, useState } from 'react';
import { formatDateTime, formatDuration, formatMiles, formatTime, formatValue } from '../lib/format';
import {
  HEAT_COLOR,
  boundsOf,
  checkpointsGeoJSON,
  heatCalibration,
  heatWeightExpression,
  heatmapGeoJSON,
  routeLabelAnchors,
  routesGeoJSON,
} from '../lib/mapData';
import { RISK_COLOR, RISK_LABEL, ROUTE_COLOR, levelFromScore } from '../lib/risk';
import { positionAt } from '../lib/timeline';
import type { Checkpoint, HeatmapMetric, PlanResponse, RouteOption } from '../types';

const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';
const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };
const US_CENTER: [number, number] = [-97, 38.5];
const TRUCK_SVG =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 17h4V5H2v12h3"/><path d="M20 17h2v-3.34a4 4 0 0 0-1.17-2.83L19 9h-5v8h1"/><circle cx="7.5" cy="17.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/></svg>';

export interface FocusedCheckpoint {
  routeId: string;
  index: number;
}

interface Props {
  plan: PlanResponse | null;
  selectedId: string | null;
  /** Hours after departure shown by the heatmap and truck marker (may be fractional during playback). */
  time: number;
  metric: HeatmapMetric;
  focused: FocusedCheckpoint | null;
  onSelectRoute: (id: string) => void;
  onFocusCheckpoint: (focus: FocusedCheckpoint) => void;
}

export function RouteMap({ plan, selectedId, time, metric, focused, onSelectRoute, onFocusCheckpoint }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const hoverPopupRef = useRef<maplibregl.Popup | null>(null);
  const endpointsRef = useRef<maplibregl.Marker[]>([]);
  const labelsRef = useRef<Map<string, maplibregl.Marker>>(new Map());
  const truckRef = useRef<maplibregl.Marker | null>(null);
  const [ready, setReady] = useState(false);

  // Latest values for map event handlers that are registered once.
  const latest = useRef({ plan, time, metric, onSelectRoute, onFocusCheckpoint });
  latest.current = { plan, time, metric, onSelectRoute, onFocusCheckpoint };

  // Heatmap paint updates are applied one at a time: a new expression is only
  // sent once the previous one has finished loading, so fast playback or
  // scrubbing never queues up work. `flushHeat` always applies the latest state.
  const appliedHeatKey = useRef('');
  const flushHeat = useRef(() => {});
  flushHeat.current = () => {
    const map = mapRef.current;
    const { plan: p, time: t, metric: m } = latest.current;
    if (!map || !p || !map.getLayer('heat')) return;
    const key = `${m}:${t.toFixed(2)}:${p.heatmap.start_time}`;
    if (key === appliedHeatKey.current || !map.isSourceLoaded('heat')) return;
    appliedHeatKey.current = key;
    map.setPaintProperty('heat', 'heatmap-weight', heatWeightExpression(m, t, p.heatmap.hours));
  };

  // ---- create the map once --------------------------------------------------
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current!,
      style: STYLE_URL,
      center: US_CENTER,
      zoom: window.innerWidth > 900 ? 3.6 : 2.6,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-right');

    map.on('load', () => {
      map.addSource('heat', { type: 'geojson', data: EMPTY });
      map.addSource('routes', { type: 'geojson', data: EMPTY });
      map.addSource('checkpoints', { type: 'geojson', data: EMPTY });

      map.addLayer({
        id: 'heat',
        type: 'heatmap',
        source: 'heat',
        maxzoom: 12,
        paint: { 'heatmap-color': HEAT_COLOR, 'heatmap-opacity': 0.85, 'heatmap-weight': 0 },
      });
      map.addLayer({
        id: 'heat-hit', // invisible circles, only used to hit-test heatmap cells for the tooltip
        type: 'circle',
        source: 'heat',
        maxzoom: 12,
        paint: { 'circle-opacity': 0, 'circle-radius': 6 },
      });
      map.addLayer({
        id: 'route-casing',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round', 'line-sort-key': ['get', 'sort'] },
        paint: {
          'line-color': '#ffffff',
          'line-width': ['case', ['get', 'selected'], 9, 6],
          'line-opacity': ['case', ['get', 'selected'], 1, 0.7],
        },
      });
      map.addLayer({
        id: 'route-line',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round', 'line-sort-key': ['get', 'sort'] },
        paint: {
          'line-color': ['get', 'color'],
          'line-width': ['case', ['get', 'selected'], 5, 3],
          'line-opacity': ['case', ['get', 'selected'], 1, 0.55],
        },
      });
      map.addLayer({
        id: 'checkpoint',
        type: 'circle',
        source: 'checkpoints',
        layout: { 'circle-sort-key': ['get', 'sort'] },
        paint: {
          'circle-color': ['get', 'color'],
          'circle-radius': ['case', ['get', 'selected'], 6.5, 4],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': ['case', ['get', 'selected'], 2, 1],
          'circle-opacity': ['case', ['get', 'selected'], 1, 0.75],
        },
      });

      map.on('click', (e) => {
        const hit = map.queryRenderedFeatures(e.point, { layers: ['checkpoint', 'route-line'] });
        const cp = hit.find((f) => f.layer.id === 'checkpoint');
        if (cp) {
          latest.current.onFocusCheckpoint({ routeId: cp.properties.routeId, index: cp.properties.index });
          return;
        }
        const route = hit.find((f) => f.layer.id === 'route-line');
        if (route) latest.current.onSelectRoute(route.properties.id);
      });
      map.on('mousemove', showHoverTooltip);
      map.on('mouseout', () => hoverPopupRef.current?.remove());
      map.on('sourcedata', (e) => e.sourceId === 'heat' && flushHeat.current());
      map.on('idle', () => flushHeat.current());
      setReady(true);
    });

    /** One hover tooltip: checkpoint details take priority over the heatmap cell underneath. */
    function showHoverTooltip(e: MapMouseEvent) {
      const p = latest.current.plan;
      const hit = p ? map.queryRenderedFeatures(e.point, { layers: ['checkpoint', 'route-line', 'heat-hit'] }) : [];
      const cpFeature = hit.find((f) => f.layer.id === 'checkpoint');
      const onRoute = hit.some((f) => f.layer.id === 'route-line');
      map.getCanvas().style.cursor = cpFeature || onRoute ? 'pointer' : '';

      let html: string | null = null;
      let at: maplibregl.LngLatLike = e.lngLat;
      if (p && cpFeature) {
        const route = p.routes.find((r) => r.id === cpFeature.properties.routeId);
        const cp = route?.checkpoints[cpFeature.properties.index];
        if (route && cp) {
          html = checkpointTipHtml(route, cp);
          at = [cp.lon, cp.lat];
        }
      } else if (p && !onRoute) {
        const cell = hit.find((f) => f.layer.id === 'heat-hit');
        if (cell) html = heatTipHtml(p, cell.properties.cell, latest.current.time);
      }
      if (!html) {
        hoverPopupRef.current?.remove();
        return;
      }
      if (!hoverPopupRef.current) {
        hoverPopupRef.current = new maplibregl.Popup({
          closeButton: false,
          closeOnClick: false,
          offset: 12,
          className: 'hover-popup',
        });
      }
      hoverPopupRef.current.setLngLat(at).setHTML(html).addTo(map);
    }

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // ---- new plan: data, heatmap calibration, endpoints, route labels, fit ------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    popupRef.current?.remove();
    hoverPopupRef.current?.remove();
    endpointsRef.current.forEach((m) => m.remove());
    endpointsRef.current = [];
    labelsRef.current.forEach((m) => m.remove());
    labelsRef.current.clear();
    appliedHeatKey.current = ''; // new data: the heatmap expression must be re-applied
    if (!plan) {
      for (const id of ['heat', 'routes', 'checkpoints']) (map.getSource(id) as GeoJSONSource).setData(EMPTY);
      return;
    }
    const calib = heatCalibration(plan.heatmap);
    map.setPaintProperty('heat', 'heatmap-radius', calib.radius);
    map.setPaintProperty('heat', 'heatmap-intensity', calib.intensity);
    map.setPaintProperty('heat-hit', 'circle-radius', calib.hitRadius);
    (map.getSource('heat') as GeoJSONSource).setData(heatmapGeoJSON(plan.heatmap));

    const { origin, destination } = plan.request;
    endpointsRef.current = [
      endpointMarker('start', `Origin: ${origin.label}`).setLngLat([origin.lon, origin.lat]).addTo(map),
      endpointMarker('end', `Destination: ${destination.label}`)
        .setLngLat([destination.lon, destination.lat])
        .addTo(map),
    ];
    const anchors = routeLabelAnchors(plan);
    plan.routes.forEach((route, i) => {
      // Alternate labels above / below the line so neighbouring labels stay apart.
      const marker = routeLabelMarker(route, i % 2 === 1, () => latest.current.onSelectRoute(route.id));
      labelsRef.current.set(route.id, marker.setLngLat(anchors[route.id]).addTo(map));
    });
    map.fitBounds(boundsOf(plan), { padding: fitPadding(), duration: 900 });
  }, [plan, ready]);

  // ---- selection changes ------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !plan || !selectedId) return;
    (map.getSource('routes') as GeoJSONSource).setData(routesGeoJSON(plan, selectedId));
    (map.getSource('checkpoints') as GeoJSONSource).setData(checkpointsGeoJSON(plan, selectedId));
    labelsRef.current.forEach((marker, id) =>
      marker.getElement().classList.toggle('route-label--selected', id === selectedId),
    );
  }, [plan, selectedId, ready]);

  // ---- slider / playback / layer: swap the heatmap paint expression only ------
  useEffect(() => {
    if (ready && plan) flushHeat.current();
  }, [plan, time, metric, ready]);

  // ---- truck position at the slider time on the selected route ---------------
  useEffect(() => {
    const map = mapRef.current;
    const route = plan?.routes.find((r) => r.id === selectedId);
    const pos = route ? positionAt(route.geometry, route.timeline_s, time * 3600) : null;
    if (!map || !ready || !pos) {
      truckRef.current?.remove();
      truckRef.current = null;
      return;
    }
    if (!truckRef.current) {
      const el = document.createElement('div');
      el.className = 'truck-marker';
      el.title = 'Truck position at the selected forecast time';
      el.innerHTML = TRUCK_SVG;
      truckRef.current = new maplibregl.Marker({ element: el }).setLngLat(pos).addTo(map);
    } else {
      truckRef.current.setLngLat(pos);
    }
  }, [plan, selectedId, time, ready]);

  // ---- focused checkpoint popup ----------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !plan || !focused) return;
    const route = plan.routes.find((r) => r.id === focused.routeId);
    const cp = route?.checkpoints[focused.index];
    if (!route || !cp) return;
    popupRef.current?.remove();
    hoverPopupRef.current?.remove();
    popupRef.current = new maplibregl.Popup({ offset: 10, maxWidth: '300px' })
      .setLngLat([cp.lon, cp.lat])
      .setHTML(checkpointHtml(route, cp, plan.request.load_weight_lb))
      .addTo(map);
    if (!map.getBounds().contains([cp.lon, cp.lat])) map.easeTo({ center: [cp.lon, cp.lat] });
  }, [focused, plan, ready]);

  return <div ref={containerRef} className="map" aria-label="Route map" />;
}

// ------------------------------------------------------------------ markers
function endpointMarker(kind: 'start' | 'end', title: string) {
  const el = document.createElement('div');
  el.className = `endpoint-marker endpoint-marker--${kind}`;
  el.title = title;
  return new maplibregl.Marker({ element: el, anchor: kind === 'end' ? 'bottom' : 'center' });
}

/** Route summary shown on the map itself: id, drive time, distance and worst risk level. */
function routeLabelMarker(route: RouteOption, below: boolean, onClick: () => void) {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = `route-label${route.recommended ? ' route-label--best' : ''}${below ? ' route-label--below' : ''}`;
  el.style.setProperty('--route-color', ROUTE_COLOR[route.id] ?? '#334155');
  const worst = route.summary.max_level;
  el.title = `${route.name}${route.recommended ? ' (recommended)' : ''} — worst conditions: ${RISK_LABEL[worst]}`;

  const id = document.createElement('span');
  id.className = 'route-label__id';
  id.textContent = route.id;
  const text = document.createElement('span');
  text.className = 'route-label__text';
  const time = document.createElement('strong');
  time.textContent = formatDuration(route.travel_time_s);
  const sub = document.createElement('span');
  sub.textContent = `${formatMiles(route.distance_miles)} · ${RISK_LABEL[worst]}`;
  const dot = document.createElement('i');
  dot.style.background = RISK_COLOR[worst];
  sub.prepend(dot);
  text.append(time, sub);
  el.append(id, text);
  if (route.recommended) {
    const star = document.createElement('span');
    star.className = 'route-label__star';
    star.textContent = '★';
    el.append(star);
  }
  el.addEventListener('click', (e) => {
    e.stopPropagation();
    onClick();
  });
  return new maplibregl.Marker({ element: el, anchor: below ? 'top' : 'bottom', offset: [0, below ? 12 : -12] });
}

/** Keep routes clear of the floating side panel and forecast panel. */
function fitPadding() {
  const desktop = window.innerWidth > 900;
  return desktop
    ? { top: 90, bottom: 240, left: 480, right: 70 }
    : { top: 70, bottom: 240, left: 30, right: 30 };
}

// ------------------------------------------------------------- popup content
const escapeHtml = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

function checkpointTipHtml(route: RouteOption, cp: Checkpoint): string {
  return `
    <div class="tip">
      <div class="tip__time">${escapeHtml(route.name)} · Mile ${cp.mile.toFixed(0)}</div>
      <div>ETA ${formatTime(cp.eta)} ·
        <b style="color:${RISK_COLOR[cp.risk.level]}">${RISK_LABEL[cp.risk.level]}</b></div>
      <div class="muted">Click for details</div>
    </div>`;
}

function heatTipHtml(plan: PlanResponse, cell: number, time: number): string {
  const hour = Math.min(plan.heatmap.hours, Math.max(0, Math.round(time)));
  const frame = plan.heatmap.frames[hour];
  const level = levelFromScore(frame.level[cell]);
  return `
    <div class="tip">
      <div class="tip__time">${formatDateTime(frame.time)} (+${hour}h)</div>
      <div><b style="color:${level ? RISK_COLOR[level] : '#64748b'}">${level ? RISK_LABEL[level] : 'No data'}</b>
        for this load</div>
      <div>Wind ${formatValue(frame.wind[cell], 'mph', 0)} · Rain ${formatValue(frame.rain[cell], 'in/hr')}
        · Snow ${formatValue(frame.snow[cell], 'in/hr')}</div>
    </div>`;
}

function checkpointHtml(route: RouteOption, cp: Checkpoint, loadLb: number): string {
  const w = cp.weather;
  const r = cp.risk;
  const row = (name: string, value: string, level: keyof typeof RISK_LABEL, driver: boolean) =>
    `<tr class="${driver ? 'driver' : ''}"><td>${name}</td><td>${value}</td>
      <td><span class="dot" style="background:${RISK_COLOR[level]}"></span>${RISK_LABEL[level]}</td></tr>`;
  const place = cp.index === 0 ? ' (origin)' : cp.index === route.checkpoints.length - 1 ? ' (destination)' : '';
  const gust = w.gust_mph != null ? ` <span class="muted">(gust ${w.gust_mph.toFixed(0)})</span>` : '';
  return `
    <div class="cp-popup">
      <div class="cp-popup__title">${escapeHtml(route.name)} · Mile ${cp.mile.toFixed(1)}${place}</div>
      <div class="cp-popup__eta">ETA ${formatDateTime(cp.eta)}</div>
      <div class="cp-popup__level" style="background:${RISK_COLOR[r.level]}">${RISK_LABEL[r.level]}</div>
      <table>
        ${row('Wind', `${formatValue(w.wind_mph, 'mph', 0)}${gust}`, r.wind_level, r.drivers.includes('wind'))}
        ${row('Rain', formatValue(w.rain_in_hr, 'in/hr'), r.rain_level, r.drivers.includes('rain'))}
        ${row('Snow', formatValue(w.snow_in_hr, 'in/hr'), r.snow_level, r.drivers.includes('snow'))}
      </table>
      ${r.load_rule ? `<div class="cp-popup__rule">Load rule (${loadLb.toLocaleString()} lb): ${escapeHtml(r.load_rule)}</div>` : ''}
      <div class="cp-popup__meta">
        ${w.temp_f != null ? `${w.temp_f.toFixed(0)}°F · ` : ''}Represents ${cp.covers_miles.toFixed(1)} mi of road
        ${w.complete ? '' : '<br/>⚠ Forecast incomplete for this time'}
      </div>
    </div>`;
}
