import { describe, expect, it } from 'vitest';
import { formatDuration, nextFullHour } from './format';
import { positionAt } from './timeline';

describe('positionAt', () => {
  const geometry: [number, number][] = [
    [0, 0],
    [1, 0],
    [1, 2],
  ];
  const timeline = [0, 100, 300];

  it('interpolates between vertices', () => {
    expect(positionAt(geometry, timeline, 50)).toEqual([0.5, 0]);
    expect(positionAt(geometry, timeline, 200)).toEqual([1, 1]);
  });

  it('returns the ends exactly', () => {
    expect(positionAt(geometry, timeline, 0)).toEqual([0, 0]);
    expect(positionAt(geometry, timeline, 300)).toEqual([1, 2]);
  });

  it('is null outside the trip', () => {
    expect(positionAt(geometry, timeline, -1)).toBeNull();
    expect(positionAt(geometry, timeline, 301)).toBeNull();
  });
});

describe('format helpers', () => {
  it('formats durations', () => {
    expect(formatDuration(59 * 60)).toBe('59m');
    expect(formatDuration(3 * 3600 + 5 * 60)).toBe('3h 05m');
  });

  it('rounds up to the next full hour', () => {
    const d = nextFullHour(new Date(2026, 10, 14, 9, 25));
    expect([d.getHours(), d.getMinutes()]).toEqual([10, 0]);
  });
});
