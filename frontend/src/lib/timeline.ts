/**
 * Where is the truck `seconds` after departure? Binary-search the route's
 * per-vertex timeline and interpolate between the two surrounding vertices.
 * Returns null before departure or after arrival.
 */
export function positionAt(
  geometry: [number, number][],
  timeline: number[],
  seconds: number,
): [number, number] | null {
  if (geometry.length === 0 || seconds < 0 || seconds > timeline[timeline.length - 1]) return null;
  let lo = 0;
  let hi = timeline.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (timeline[mid] <= seconds) lo = mid;
    else hi = mid;
  }
  const span = timeline[hi] - timeline[lo];
  const f = span > 0 ? (seconds - timeline[lo]) / span : 0;
  const [x0, y0] = geometry[lo];
  const [x1, y1] = geometry[hi];
  return [x0 + (x1 - x0) * f, y0 + (y1 - y0) * f];
}
