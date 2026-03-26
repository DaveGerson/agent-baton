import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api/client';
import type { PmoCard, ProgramHealth } from '../api/types';

export type ConnectionMode = 'sse' | 'polling' | 'connecting';

interface UsePmoBoardResult {
  cards: PmoCard[];
  health: Record<string, ProgramHealth>;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  lastUpdated: Date | null;
  connectionMode: ConnectionMode;
}

// When SSE is live we poll rarely just as a safety net; when SSE is
// unavailable we fall back to a tighter interval so the board stays fresh.
const POLL_INTERVAL_SSE_MS = 15_000;
const POLL_INTERVAL_FALLBACK_MS = 5_000;

// Reconnection back-off: 1 s, 2 s, 4 s, … capped at 30 s.
const SSE_BACKOFF_INITIAL_MS = 1_000;
const SSE_BACKOFF_MAX_MS = 30_000;

const SSE_URL = '/api/v1/pmo/events';

export function usePmoBoard(program?: string): UsePmoBoardResult {
  const [cards, setCards] = useState<PmoCard[]>([]);
  const [health, setHealth] = useState<Record<string, ProgramHealth>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [connectionMode, setConnectionMode] = useState<ConnectionMode>('connecting');

  const mountedRef = useRef(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sseRef = useRef<EventSource | null>(null);
  const backoffRef = useRef(SSE_BACKOFF_INITIAL_MS);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  // Restart the polling interval at the given period, replacing any existing one.
  const restartPolling = useCallback(
    (intervalMs: number) => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = setInterval(fetchBoard, intervalMs);
    },
    [fetchBoard],
  );

  const closeSse = useCallback(() => {
    if (sseRef.current) {
      sseRef.current.close();
      sseRef.current = null;
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const connectSse = useCallback(() => {
    if (!mountedRef.current) return;
    closeSse();

    const es = new EventSource(SSE_URL);
    sseRef.current = es;

    es.addEventListener('card_update', () => {
      // A board-relevant event arrived — refresh the full board state.
      fetchBoard();
    });

    es.onopen = () => {
      if (!mountedRef.current) return;
      backoffRef.current = SSE_BACKOFF_INITIAL_MS; // reset back-off on success
      setConnectionMode('sse');
      // Slow down the safety-net poll while SSE is live.
      restartPolling(POLL_INTERVAL_SSE_MS);
    };

    es.onerror = () => {
      if (!mountedRef.current) return;
      es.close();
      sseRef.current = null;
      setConnectionMode('polling');
      // Fall back to tight polling immediately.
      restartPolling(POLL_INTERVAL_FALLBACK_MS);

      // Schedule a reconnection attempt with exponential back-off.
      const delay = backoffRef.current;
      backoffRef.current = Math.min(delay * 2, SSE_BACKOFF_MAX_MS);
      reconnectTimerRef.current = setTimeout(() => {
        if (mountedRef.current) connectSse();
      }, delay);
    };
  }, [closeSse, fetchBoard, restartPolling]);

  useEffect(() => {
    mountedRef.current = true;
    setLoading(true);
    setConnectionMode('connecting');

    // Kick off the first fetch immediately.
    fetchBoard();

    // Start polling at the fallback rate; SSE open handler will slow it down.
    restartPolling(POLL_INTERVAL_FALLBACK_MS);

    // Attempt SSE connection.
    connectSse();

    return () => {
      mountedRef.current = false;
      if (intervalRef.current) clearInterval(intervalRef.current);
      closeSse();
    };
    // connectSse, restartPolling, and closeSse are stable (useCallback with no
    // changing deps beyond fetchBoard, which changes only when program changes).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchBoard]);

  return {
    cards,
    health,
    loading,
    error,
    refresh: fetchBoard,
    lastUpdated,
    connectionMode,
  };
}
