import { memo, useState } from 'react';
import { nextFullHour, toLocalInputValue } from '../lib/format';
import type { Place, PlanRequest } from '../types';
import { ClockIcon, SwapIcon, WeightIcon } from './Icons';
import { PlaceInput } from './PlaceInput';

interface Props {
  loading: boolean;
  onSubmit: (request: PlanRequest) => void;
}

const INTERVALS = [10, 25, 50] as const;
const MAX_LOAD_LB = 80_000;
const MAX_DAYS_AHEAD = 7;

export const TripForm = memo(function TripForm({ loading, onSubmit }: Props) {
  const [originText, setOriginText] = useState('');
  const [origin, setOrigin] = useState<Place | null>(null);
  const [destText, setDestText] = useState('');
  const [dest, setDest] = useState<Place | null>(null);
  const [departure, setDeparture] = useState(() => toLocalInputValue(nextFullHour()));
  const [load, setLoad] = useState('35000');
  const [interval, setIntervalMiles] = useState<(typeof INTERVALS)[number]>(25);
  const [error, setError] = useState<string | null>(null);

  const now = new Date();
  const minDeparture = toLocalInputValue(new Date(now.getTime() - 60 * 60 * 1000));
  const maxDeparture = toLocalInputValue(new Date(now.getTime() + MAX_DAYS_AHEAD * 24 * 3600 * 1000));

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const loadLb = Number(load);
    const departureDate = new Date(departure);
    if (!originText.trim() || !destText.trim()) return setError('Enter an origin and a destination.');
    if (load.trim() === '' || !Number.isFinite(loadLb) || loadLb < 0 || loadLb > MAX_LOAD_LB)
      return setError(`Load weight must be between 0 and ${MAX_LOAD_LB.toLocaleString()} lb.`);
    if (Number.isNaN(departureDate.getTime())) return setError('Choose a departure date and time.');
    setError(null);
    onSubmit({
      // Unselected free text is geocoded by the server.
      origin: origin ?? { label: originText.trim() },
      destination: dest ?? { label: destText.trim() },
      departure_time: departureDate.toISOString(),
      load_weight_lb: loadLb,
      interval_miles: interval,
    });
  };

  const swap = () => {
    setOriginText(destText);
    setDestText(originText);
    setOrigin(dest);
    setDest(origin);
  };

  return (
    <form className="trip-form" onSubmit={submit} noValidate>
      <div className="place-group">
        <PlaceInput
          label="Origin"
          marker="origin"
          placeholder="Origin — e.g. Dallas, TX"
          text={originText}
          value={origin}
          onTextChange={setOriginText}
          onSelect={setOrigin}
        />
        <PlaceInput
          label="Destination"
          marker="destination"
          placeholder="Destination — e.g. Denver, CO"
          text={destText}
          value={dest}
          onTextChange={setDestText}
          onSelect={setDest}
        />
        <button type="button" className="swap-button" onClick={swap} title="Swap origin and destination">
          <SwapIcon size={15} />
        </button>
      </div>

      <div className="field-row">
        <label className="field">
          <span className="field__label">
            <ClockIcon size={13} /> Departure
          </span>
          <input
            type="datetime-local"
            value={departure}
            min={minDeparture}
            max={maxDeparture}
            onChange={(e) => setDeparture(e.target.value)}
            required
          />
        </label>
        <label className="field field--load">
          <span className="field__label">
            <WeightIcon size={13} /> Load weight
          </span>
          <span className="input-suffix">
            <input
              type="number"
              inputMode="numeric"
              min={0}
              max={MAX_LOAD_LB}
              step={500}
              value={load}
              onChange={(e) => setLoad(e.target.value)}
              required
            />
            <span>lb</span>
          </span>
        </label>
      </div>

      <div className="field">
        <span className="field__label">Weather checkpoint every</span>
        <div className="segmented" role="radiogroup" aria-label="Checkpoint interval">
          {INTERVALS.map((miles) => (
            <button
              key={miles}
              type="button"
              role="radio"
              aria-checked={interval === miles}
              className={interval === miles ? 'selected' : undefined}
              onClick={() => setIntervalMiles(miles)}
            >
              {miles} mi
            </button>
          ))}
        </div>
      </div>

      {error && <p className="form-error">{error}</p>}
      <button className="primary-button" type="submit" disabled={loading}>
        {loading ? (
          <>
            <span className="spinner spinner--light" /> Planning…
          </>
        ) : (
          'Find safest route'
        )}
      </button>
      <p className="hint">Cargo weight only (not the truck). Times shown in your local time zone.</p>
    </form>
  );
});
