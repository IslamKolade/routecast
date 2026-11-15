import { useEffect, useId, useRef, useState } from 'react';
import { searchPlaces } from '../api';
import type { Place, PlaceSuggestion } from '../types';

interface Props {
  label: string;
  marker: 'origin' | 'destination';
  placeholder: string;
  value: Place | null;
  text: string;
  onTextChange: (text: string) => void;
  onSelect: (place: Place | null) => void;
}

const DEBOUNCE_MS = 300;

/** Text input with debounced place suggestions (geocoding via the backend). */
export function PlaceInput({ label, marker, placeholder, value, text, onTextChange, onSelect }: Props) {
  const id = useId();
  const [suggestions, setSuggestions] = useState<PlaceSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [searching, setSearching] = useState(false);
  const skipNextSearch = useRef(false);

  useEffect(() => {
    if (skipNextSearch.current) {
      skipNextSearch.current = false;
      return;
    }
    const query = text.trim();
    if (query.length < 3) {
      setSuggestions([]);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const results = await searchPlaces(query, controller.signal);
        setSuggestions(results);
        setActive(results.length ? 0 : -1);
        setOpen(true);
      } catch {
        if (!controller.signal.aborted) setSuggestions([]);
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [text]);

  const choose = (s: PlaceSuggestion) => {
    skipNextSearch.current = true;
    onTextChange(s.label);
    onSelect({ label: s.label, lat: s.lat, lon: s.lon });
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || suggestions.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => (i + 1) % suggestions.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => (i - 1 + suggestions.length) % suggestions.length);
    } else if (e.key === 'Enter' && active >= 0) {
      e.preventDefault();
      choose(suggestions[active]);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  const listId = `${id}-list`;
  return (
    <div className={`place-input place-input--${marker}`}>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <div className="place-input__control">
        <span className="place-input__marker" aria-hidden />
        <input
          id={id}
          type="text"
          autoComplete="off"
          placeholder={placeholder}
          value={text}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          onChange={(e) => {
            onTextChange(e.target.value);
            if (value) onSelect(null);
          }}
          onFocus={() => suggestions.length && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={onKeyDown}
          required
        />
        {searching && <span className="place-input__spinner" aria-hidden />}
        {value && !searching && <span className="place-input__check" title="Location selected" />}
      </div>
      {open && suggestions.length > 0 && (
        <ul id={listId} role="listbox" className="place-input__list">
          {suggestions.map((s, i) => (
            <li
              key={`${s.lat},${s.lon},${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? 'active' : undefined}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(s);
              }}
              onMouseEnter={() => setActive(i)}
            >
              {s.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
