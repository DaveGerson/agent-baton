import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api/client';
import type { PmoCard, ProgramHealth } from '../api/types';

interface UsePmoBoardResult {
  cards: PmoCard[];
  health: Record<string, ProgramHealth>;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  lastUpdated: Date | null;
}

const POLL_INTERVAL_MS = 5000;

export function usePmoBoard(program?: string): UsePmoBoardResult {
  const [cards, setCards] = useState<PmoCard[]>([]);
  const [health, setHealth] = useState<Record<string, ProgramHealth>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  const fetchBoard = useCallback(async () => {
    try {
      const data = program
        ? await api.getBoardByProgram(program)
        : await api.getBoard();
      if (!mountedRef.current) return;
      setCards(data.cards);
      setHealth(data.health);
      setError(null);
      setLastUpdated(new Date());
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load board');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [program]);

  useEffect(() => {
    mountedRef.current = true;
    setLoading(true);
    fetchBoard();

    intervalRef.current = setInterval(fetchBoard, POLL_INTERVAL_MS);

    return () => {
      mountedRef.current = false;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchBoard]);

  return { cards, health, loading, error, refresh: fetchBoard, lastUpdated };
}
