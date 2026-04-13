import { useState, useEffect, useRef, useCallback } from 'react';
import type { PmoCard } from '../api/types';
import { T, FONT_SIZES, SR_ONLY } from '../styles/tokens';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StepEvent {
  event_type: string;
  step_id: string;
  agent?: string;
  status?: string;
  timestamp: string;
  message?: string;
}

interface ExecutionDetail {
  task_id: string;
  status: string;
  current_phase: string;
  steps: StepEvent[];
  started_at: string;
  elapsed_seconds: number;
}

interface Props {
  card: PmoCard;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ExecutionProgress({ card, onClose }: Props) {
  const [detail, setDetail] = useState<ExecutionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  const fetchDetail = useCallback(async () => {
    try {
      const res = await fetch(`/api/v1/pmo/cards/${encodeURIComponent(card.card_id)}/execution`);
      if (!res.ok) {
        if (res.status === 404) {
          setDetail(null);
          return;
        }
        throw new Error(`API ${res.status}`);
      }
      const data = await res.json();
      setDetail(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load execution details');
    }
  }, [card.card_id]);

  useEffect(() => {
    fetchDetail();
    intervalRef.current = setInterval(fetchDetail, 3000);
    return () => clearInterval(intervalRef.current);
  }, [fetchDetail]);

  useEffect(() => {
    if (autoScroll && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [detail?.steps.length, autoScroll]);

  const isTerminal = card.column === 'deployed' || (detail?.status === 'complete') || (detail?.status === 'failed');

  function statusColor(status: string | undefined): string {
    if (!status) return T.text2;
    if (status === 'complete') return T.green;
    if (status === 'failed') return T.red;
    if (status === 'running') return T.yellow;
    return T.text2;
  }

  function eventIcon(type: string): string {
    if (type === 'step.dispatched') return '\u25B6';
    if (type === 'step.completed') return '\u2713';
    if (type === 'step.failed') return '\u2717';
    if (type.startsWith('gate')) return '\u229A';
    if (type.startsWith('phase')) return '\u25CF';
    return '\u2022';
  }

  function formatElapsed(seconds: number): string {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  }

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      zIndex: 1000,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'rgba(0,0,0,0.6)',
    }} onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 540,
          maxHeight: '80vh',
          display: 'flex',
          flexDirection: 'column',
          background: T.bg1,
          border: `1px solid ${T.border}`,
          borderRadius: 8,
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 14px',
          borderBottom: `1px solid ${T.border}`,
          flexShrink: 0,
        }}>
          <span style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: isTerminal ? (detail?.status === 'failed' ? T.red : T.green) : T.yellow,
            animation: isTerminal ? undefined : 'pulse 2s ease-in-out infinite',
            flexShrink: 0,
          }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontSize: FONT_SIZES.md,
              fontWeight: 600,
              color: T.text0,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}>
              {card.title}
            </div>
            <div style={{ fontSize: FONT_SIZES.xs, color: T.text3 }}>
              {card.card_id}
              {detail && ` \u00B7 ${formatElapsed(detail.elapsed_seconds)}`}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close execution progress"
            style={{
              background: 'none',
              border: 'none',
              color: T.text3,
              fontSize: 16,
              cursor: 'pointer',
              padding: '0 4px',
            }}
          >
            \u2715
          </button>
        </div>

        {/* Progress bar */}
        <div style={{ padding: '8px 14px', borderBottom: `1px solid ${T.border}`, flexShrink: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ fontSize: FONT_SIZES.xs, color: T.text2 }}>
              Phase: {detail?.current_phase ?? card.current_phase ?? 'N/A'}
            </span>
            <span style={{ fontSize: FONT_SIZES.xs, color: T.text2 }}>
              {card.steps_completed}/{card.steps_total} steps
            </span>
          </div>
          <div style={{
            height: 4,
            borderRadius: 2,
            background: T.bg3,
            overflow: 'hidden',
          }}>
            <div style={{
              width: card.steps_total > 0 ? `${(card.steps_completed / card.steps_total) * 100}%` : '0%',
              height: '100%',
              borderRadius: 2,
              background: detail?.status === 'failed' ? T.red : T.accent,
              transition: 'width 0.3s ease',
            }} />
          </div>
          <div style={{ display: 'flex', gap: 12, marginTop: 6 }}>
            <StatChip label="Gates" value={`${card.gates_passed}`} color={T.green} />
            <StatChip label="Status" value={detail?.status ?? card.column} color={statusColor(detail?.status ?? card.column)} />
            {card.agents.length > 0 && (
              <StatChip label="Agents" value={card.agents.join(', ')} color={T.cyan} />
            )}
          </div>
        </div>

        {/* Event log */}
        <div
          ref={logRef}
          style={{
            flex: 1,
            overflow: 'auto',
            padding: '6px 0',
            minHeight: 120,
          }}
          onScroll={() => {
            if (logRef.current) {
              const { scrollTop, scrollHeight, clientHeight } = logRef.current;
              setAutoScroll(scrollHeight - scrollTop - clientHeight < 40);
            }
          }}
        >
          <span style={SR_ONLY} aria-live="polite">
            {detail ? `${detail.steps.length} execution events` : 'Loading execution details'}
          </span>

          {error && (
            <div style={{ padding: '8px 14px', fontSize: FONT_SIZES.sm, color: T.red }}>
              {error}
            </div>
          )}

          {!detail && !error && (
            <div style={{ padding: '12px 14px', fontSize: FONT_SIZES.sm, color: T.text3 }}>
              Loading execution events...
            </div>
          )}

          {detail && detail.steps.length === 0 && (
            <div style={{ padding: '12px 14px', fontSize: FONT_SIZES.sm, color: T.text3 }}>
              No execution events yet. Waiting for steps to dispatch...
            </div>
          )}

          {detail?.steps.map((step, i) => (
            <div
              key={`${step.step_id}-${i}`}
              style={{
                display: 'flex',
                gap: 8,
                padding: '3px 14px',
                fontSize: FONT_SIZES.sm,
                color: T.text1,
                lineHeight: 1.5,
              }}
            >
              <span style={{
                color: statusColor(step.status),
                flexShrink: 0,
                width: 14,
                textAlign: 'center',
              }}>
                {eventIcon(step.event_type)}
              </span>
              <span style={{ color: T.text3, flexShrink: 0, width: 52, fontFamily: 'monospace', fontSize: FONT_SIZES.xs }}>
                {new Date(step.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ color: T.cyan, fontWeight: 600 }}>{step.agent ?? step.step_id}</span>
                {' '}
                <span style={{ color: T.text2 }}>
                  {step.event_type.replace('step.', '').replace('gate.', 'gate: ').replace('phase.', 'phase: ')}
                </span>
                {step.message && (
                  <span style={{ color: T.text3 }}> — {step.message}</span>
                )}
              </span>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '6px 14px',
          borderTop: `1px solid ${T.border}`,
          flexShrink: 0,
        }}>
          <span style={{ fontSize: FONT_SIZES.xs, color: T.text4 }}>
            {isTerminal ? 'Execution complete' : 'Polling every 3s'}
          </span>
          <button
            onClick={onClose}
            style={{
              padding: '3px 12px',
              borderRadius: 4,
              border: `1px solid ${T.border}`,
              background: T.bg3,
              color: T.text1,
              fontSize: FONT_SIZES.sm,
              cursor: 'pointer',
            }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatChip({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 4,
      fontSize: FONT_SIZES.xs,
    }}>
      <span style={{ color: T.text3 }}>{label}:</span>
      <span style={{ color, fontWeight: 600 }}>{value}</span>
    </span>
  );
}
