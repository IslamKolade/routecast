import type { PlaceSuggestion, PlanRequest, PlanResponse, WeatherRequest } from './types';

// In development Vite proxies /api to Django. In production set VITE_API_BASE_URL
// to the backend origin, e.g. https://routecast-api.onrender.com
const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}${path}`, init);
  } catch {
    throw new ApiError('Cannot reach the server. Check your connection and try again.');
  }
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    throw new ApiError(body?.error?.message ?? `Request failed (${resp.status}).`);
  }
  return body as T;
}

export function checkHealth(signal?: AbortSignal): Promise<{ status: string }> {
  return request('/api/health', { signal });
}

export async function searchPlaces(query: string, signal?: AbortSignal): Promise<PlaceSuggestion[]> {
  const data = await request<{ results: PlaceSuggestion[] }>(
    `/api/geocode?q=${encodeURIComponent(query)}`,
    { signal },
  );
  return data.results;
}

type PlanStep = PlanResponse | { status: 'weather_required'; plan_id: string; weather_request: WeatherRequest };

const postJson = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/**
 * Plan a trip. Usually the server returns the finished plan. If the weather
 * service has rate-limited the server (free-tier limits are per IP address,
 * and shared hosting shares IPs), the server asks us to download the forecasts
 * instead: we fetch exactly the cells and query it lists, from this browser's
 * own IP, and post them back. All risk and ranking logic still runs on the server.
 */
export async function planTrip(body: PlanRequest): Promise<PlanResponse> {
  const step = await request<PlanStep>('/api/plan', postJson(body));
  if (step.status !== 'weather_required') return step;
  const forecasts = await downloadWeather(step.weather_request);
  return request<PlanResponse>(`/api/plan/${encodeURIComponent(step.plan_id)}/weather`, postJson({ forecasts }));
}

const MAX_PARALLEL = 3;

async function downloadWeather({ url, params, cells, chunk_size }: WeatherRequest): Promise<unknown[]> {
  const chunks: [number, number][][] = [];
  for (let i = 0; i < cells.length; i += chunk_size) chunks.push(cells.slice(i, i + chunk_size));

  const fetchChunk = async (chunk: [number, number][]): Promise<unknown[]> => {
    const query = new URLSearchParams({
      ...params,
      latitude: chunk.map((c) => c[0]).join(','),
      longitude: chunk.map((c) => c[1]).join(','),
    });
    let resp: Response;
    try {
      resp = await fetch(`${url}?${query}`);
    } catch {
      throw new ApiError('Cannot reach the weather service. Check your connection and try again.');
    }
    if (resp.status === 429) {
      throw new ApiError('The weather service is busy (free usage limit). Please wait a minute and try again.');
    }
    if (!resp.ok) throw new ApiError(`The weather service returned an error (${resp.status}).`);
    const data = await resp.json();
    return Array.isArray(data) ? data : [data]; // a single location comes back as an object
  };

  // Keep results in request order; the server matches them to cells by position.
  const results: unknown[][] = new Array(chunks.length);
  let next = 0;
  const worker = async () => {
    while (next < chunks.length) {
      const i = next++;
      results[i] = await fetchChunk(chunks[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(MAX_PARALLEL, chunks.length) }, worker));
  return results.flat();
}
