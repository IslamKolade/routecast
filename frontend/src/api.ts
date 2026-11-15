import type { PlaceSuggestion, PlanRequest, PlanResponse } from './types';

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

export function planTrip(body: PlanRequest): Promise<PlanResponse> {
  return request('/api/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
