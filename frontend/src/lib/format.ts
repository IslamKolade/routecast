// All times are shown in the browser's local time zone.

const timeFmt = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  hour: 'numeric',
  minute: '2-digit',
});
const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  timeZoneName: 'short',
});

export const formatTime = (iso: string | Date) => timeFmt.format(new Date(iso));
export const formatDateTime = (iso: string | Date) => dateTimeFmt.format(new Date(iso));

export function formatDuration(seconds: number): string {
  const totalMinutes = Math.round(seconds / 60);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return h > 0 ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`;
}

export const formatMiles = (miles: number) =>
  `${miles.toLocaleString(undefined, { maximumFractionDigits: miles < 10 ? 1 : 0 })} mi`;

export const formatLb = (lb: number) => `${lb.toLocaleString()} lb`;

export function formatValue(value: number | null | undefined, unit: string, digits = 2): string {
  return value == null ? '—' : `${value.toFixed(digits)} ${unit}`;
}

/** Value for <input type="datetime-local"> in local time. */
export function toLocalInputValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Next full hour from now — a sensible default departure. */
export function nextFullHour(now = new Date()): Date {
  const d = new Date(now);
  d.setMinutes(0, 0, 0);
  d.setHours(d.getHours() + 1);
  return d;
}
