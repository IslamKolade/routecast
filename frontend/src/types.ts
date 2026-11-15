// Types mirroring the Django API's JSON (see backend/planner/services/planner.py).

export type RiskLevel = 'LOW' | 'MODERATE' | 'HIGH' | 'SEVERE' | 'NO_TRAVEL';

export interface Place {
  label: string;
  lat: number;
  lon: number;
}

export interface PlaceSuggestion extends Place {
  type: string;
}

export interface CheckpointWeather {
  wind_mph: number | null;
  gust_mph: number | null;
  rain_in_hr: number | null;
  snow_in_hr: number | null;
  temp_f: number | null;
  weather_code: number | null;
  wind_forecast_time: string;
  precip_forecast_hour_end: string;
  complete: boolean;
}

export interface CheckpointRisk {
  level: RiskLevel;
  label: string;
  score: number;
  wind_level: RiskLevel;
  wind_level_base: RiskLevel;
  rain_level: RiskLevel;
  snow_level: RiskLevel;
  load_rule: string | null;
  drivers: Array<'wind' | 'rain' | 'snow'>;
}

export interface Checkpoint {
  index: number;
  mile: number;
  lat: number;
  lon: number;
  eta: string;
  covers_miles: number;
  weather: CheckpointWeather;
  risk: CheckpointRisk;
}

export interface RouteSummary {
  total_miles: number;
  miles_by_level: Record<RiskLevel, number>;
  no_travel_miles: number;
  severe_miles: number;
  high_miles: number;
  avg_risk: number;
  max_level: RiskLevel;
}

export interface RouteOption {
  id: string;
  name: string;
  via: string[];
  distance_miles: number;
  travel_time_s: number;
  traffic_delay_s: number;
  departure_time: string;
  arrival_time: string;
  geometry: [number, number][]; // [lon, lat]
  timeline_s: number[]; // seconds from departure for each geometry vertex
  checkpoints: Checkpoint[];
  summary: RouteSummary;
  rank: number;
  recommended: boolean;
}

export interface HeatmapFrame {
  time: string;
  level: (number | null)[];
  wind_level: (number | null)[];
  rain_level: (number | null)[];
  snow_level: (number | null)[];
  wind: (number | null)[];
  rain: (number | null)[];
  snow: (number | null)[];
}

export interface Heatmap {
  start_time: string;
  hours: number;
  step_deg: number;
  cells: [number, number][]; // [lon, lat]
  frames: HeatmapFrame[];
}

export interface PlanResponse {
  status: 'complete';
  request: {
    origin: Place;
    destination: Place;
    departure_time: string;
    load_weight_lb: number;
    interval_miles: number;
  };
  routes: RouteOption[];
  recommendation: {
    route_id: string;
    ranking: string[];
    reason: string;
    warning: string | null;
  };
  notes: string[];
  heatmap: Heatmap;
}

/** A place picked from suggestions (with coordinates) or free text the server geocodes. */
export interface PlaceQuery {
  label: string;
  lat?: number;
  lon?: number;
}

export interface PlanRequest {
  origin: PlaceQuery;
  destination: PlaceQuery;
  departure_time: string;
  load_weight_lb: number;
  interval_miles: 10 | 25 | 50;
}

export type HeatmapMetric = 'level' | 'wind_level' | 'rain_level' | 'snow_level';

/** Sent by the server when it needs the browser to download forecasts (see api.ts). */
export interface WeatherRequest {
  url: string;
  params: Record<string, string>;
  cells: [number, number][]; // [lat, lon]
  chunk_size: number;
}
