import { useState, useCallback, useEffect } from 'react';

/**
 * A hook that wraps useState with sessionStorage persistence.
 * State survives page refreshes within the same browser tab/session.
 *
 * @param key - The sessionStorage key to persist under.
 * @param defaultValue - The initial value when no persisted state exists.
 * @returns A [value, setValue] tuple identical to useState.
 */
export function usePersistedState<T>(
  key: string,
  defaultValue: T,
): [T, (value: T | ((prev: T) => T)) => void] {
  const [state, setStateRaw] = useState<T>(() => {
    try {
      const stored = sessionStorage.getItem(key);
      if (stored !== null) {
        return JSON.parse(stored) as T;
      }
    } catch {
      // Corrupt or missing — fall through to default.
    }
    return defaultValue;
  });

  // Persist to sessionStorage whenever state changes.
  useEffect(() => {
    try {
      sessionStorage.setItem(key, JSON.stringify(state));
    } catch {
      // Storage full or unavailable — ignore silently.
    }
  }, [key, state]);

  const setState = useCallback(
    (value: T | ((prev: T) => T)) => {
      setStateRaw(value);
    },
    [],
  );

  return [state, setState];
}
