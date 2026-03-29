import { useState, useCallback, useEffect } from 'react';

/**
 * A hook that wraps useState with Web Storage persistence.
 * Defaults to sessionStorage (survives page refreshes within the same tab).
 * Pass `storage: localStorage` to survive browser restarts.
 *
 * @param key - The storage key to persist under.
 * @param defaultValue - The initial value when no persisted state exists.
 * @param storage - The Storage object to use (default: sessionStorage).
 * @returns A [value, setValue] tuple identical to useState.
 */
export function usePersistedState<T>(
  key: string,
  defaultValue: T,
  storage: Storage = sessionStorage,
): [T, (value: T | ((prev: T) => T)) => void] {
  const [state, setStateRaw] = useState<T>(() => {
    try {
      const stored = storage.getItem(key);
      if (stored !== null) {
        return JSON.parse(stored) as T;
      }
    } catch {
      // Corrupt or missing — fall through to default.
    }
    return defaultValue;
  });

  // Persist to storage whenever state changes.
  useEffect(() => {
    try {
      storage.setItem(key, JSON.stringify(state));
    } catch {
      // Storage full or unavailable — ignore silently.
    }
  }, [key, state, storage]);

  const setState = useCallback(
    (value: T | ((prev: T) => T)) => {
      setStateRaw(value);
    },
    [],
  );

  return [state, setState];
}
