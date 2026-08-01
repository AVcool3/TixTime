import { createContext, useCallback, useContext, useState } from 'react';

/**
 * The simulated clock.
 *
 * The catalogue ends mid-2026, which wall-clock today has passed, so "now" is
 * an explicit value rather than Date.now(). Every price-bearing request passes
 * it, and moving it re-derives history, forecast, recommendation and alert
 * evaluation together -- which is also how the backtest replays a past date.
 */

const DEFAULT_AS_OF = '2026-02-15';
const STORAGE_KEY = 'tixtime.asOf';

export interface ClockState {
  asOf: string;
  setAsOf: (value: string) => void;
  reset: () => void;
  isDefault: boolean;
}

export const ClockContext = createContext<ClockState>({
  asOf: DEFAULT_AS_OF,
  setAsOf: () => {},
  reset: () => {},
  isDefault: true,
});

export function useClockState(): ClockState {
  const [asOf, setAsOfRaw] = useState<string>(
    () => window.localStorage.getItem(STORAGE_KEY) ?? DEFAULT_AS_OF,
  );
  const setAsOf = useCallback((value: string) => {
    setAsOfRaw(value);
    window.localStorage.setItem(STORAGE_KEY, value);
  }, []);
  const reset = useCallback(() => {
    setAsOfRaw(DEFAULT_AS_OF);
    window.localStorage.removeItem(STORAGE_KEY);
  }, []);
  return { asOf, setAsOf, reset, isDefault: asOf === DEFAULT_AS_OF };
}

export const useClock = () => useContext(ClockContext);
export { DEFAULT_AS_OF };
