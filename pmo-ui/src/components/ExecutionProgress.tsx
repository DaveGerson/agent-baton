import { useState, useEffect, useRef, useCallback } from 'react';
import type { PmoCard } from '../api/types';
import { T, FONT_SIZES, SR_ONLY, FONTS, SHADOWS } from '../styles/tokens';
import { useBodyScrollLock } from '../hooks/useBodyScrollLock';

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
  useBodyScrollLock();
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
    if (status === 'complete') return T.mint;
    if (status === 'failed') return T.cherry;
    if (status === 'running') return T.butter;
    return T.text2;
  }

  function eventIcon(type: string): string {
    if (type === 'step.dispatched') return '🔥';
    if (type === 'step.completed') return '✓';
    if (type === 'step.failed') return '✗';
    if (type.startsWith('gate')) return '🛎';
    if (type.startsWith('phase')) return '●';
    return '•';
  }

  function eventIconColor(type: string, status: string | undefined): string {
    if (type === 'step.dispatched') return T.butter;
    if (type === 'step.completed') return T.mint;
    if (type === 'step.failed') return T.cherry;
    if (type.startsWith('gate')) return T.tangerine;
    if (type.startsWith('phase')) return T.crust;
    return statusColor(status);
  }

  function formatElapsed(seconds: number): string {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  }

  const progressPct = card.steps_total > 0
    ? `${(card.steps_completed / card.steps_total) * 100}%`
    : '0%';

  const progressBg =
    detail?.status === 'failed'
      ? T.cherry
      : detail?.status === 'complete' || isTerminal
        ? T.mint
        : `repeating-linear-gradient(45deg, ${T.butter} 0 8px, ${T.tangerine} 8px 16px)`;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(42,26,16,.6)',
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 560,
          maxHeight: '80vh',
          display: 'flex',
          flexDirection: 'column',
          background: T.bg1,
          border: `3px solid ${T.border}`,
          borderRadius: 18,
          boxShadow: SHADOWS.xl,
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '12px 16px',
          background: T.ink,
          flexShrink: 0,
        }}>
          <span style={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: isTerminal
              ? (detail?.status === 'failed' ? T.cherry : T.mint)
              : T.butter,
            animation: isTerminal ? undefined : 'pulse 2s ease-in-out infinite',
            flexShrink: 0,
          }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontFamily: FONTS.display,
              fontWeight: 900,
              fontSize: 17,
              color: T.cream,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}>
              {card.title}
            </div>
            <div style={{
              fontFamily: FONTS.mono,
              fontSize: 10,
              color: T.crust,
            }}>
              {card.card_id}
              {detail && ` · ${formatElapsed(detail.elapsed_seconds)}`}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close execution progress"
            style={{
              background: 'none',
              border: `1.5px solid ${T.cherry}`,
              color: T.cherry,
              fontSize: 14,
              cursor: 'pointer',
              padding: '2px 8px',
              borderRadius: 6,
              fontFamily: FONTS.body,
              fontWeight: 700,
              lineHeight: 1.4,
            }}
          >
            ×
          </button>
        </div>

        {/* Phase / progress section */}
        <div style={{
          padding: '10px 16px',
          background: T.bg3,
          borderBottom: `2px solid ${T.border}`,
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 10,
              color: T.text2,
              textTransform: 'uppercase',
              letterSpacing: 0.6,
            }}>
              Current course:{' '}
              <span style={{
                fontFamily: FONTS.hand,
                fontSize: 16,
                color: T.text0,
                display: 'inline-block',
                transform: 'rotate(-0.5deg)',
                textTransform: 'none',
                letterSpacing: 0,
                fontWeight: 400,
              }}>
                {detail?.current_phase ?? card.current_phase ?? 'N/A'}
              </span>
            </span>
            <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: T.text2 }}>
              {card.steps_completed}/{card.steps_total} steps
            </span>
          </div>

          {/* Progress bar */}
          <div style={{
            height: 6,
            borderRadius: 999,
            background: T.bg3,
            border: `1.5px solid ${T.border}`,
            overflow: 'hidden',
          }}>
            <div style={{
              width: progressPct,
              height: '100%',
              background: progressBg,
              transition: 'width 0.3s ease',
            }} />
          </div>

          {/* Stat chips */}
          <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
            <StatChip label="Gates" value={`${card.gates_passed}`} color={T.mint} />
            <StatChip label="Status" value={detail?.status ?? card.column} color={statusColor(detail?.status ?? card.column)} />
            {card.agents.length > 0 && (
              <StatChip label="Agents" value={card.agents.join(', ')} color={T.blueberry} />
            )}
          </div>
        </div>

        {/* Event log */}
        <div
          ref={logRef}
          style={{
            flex: 1,
            overflow: 'auto',
            padding: '4px 0',
            minHeight: 120,
            background: T.bg1,
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
            <div style={{
              padding: '8px 16px',
              fontFamily: FONTS.body,
              fontSize: FONT_SIZES.sm,
              color: T.cherry,
            }}>
              {error}
            </div>
          )}

          {!detail && !error && (
            <div style={{
              padding: '16px',
              fontFamily: FONTS.hand,
              fontSize: 16,
              color: T.text2,
              textAlign: 'center',
            }}>
              "Waiting for the oven to heat up..."
            </div>
          )}

          {detail && detail.steps.length === 0 && (
            <div style={{
              padding: '16px',
              fontFamily: FONTS.hand,
              fontSize: 16,
              color: T.text2,
              textAlign: 'center',
            }}>
              "No events yet — watching for the first step..."
            </div>
          )}

          {detail?.steps.map((step, i) => (
            <div
              key={`${step.step_id}-${i}`}
              style={{
                display: 'flex',
                gap: 8,
                padding: '5px 16px',
                fontSize: FONT_SIZES.sm,
                color: T.text1,
                lineHeight: 1.5,
              }}
            >
              <span style={{
                color: eventIconColor(step.event_type, step.status),
                flexShrink: 0,
                width: 16,
                textAlign: 'center',
              }}>
                {eventIcon(step.event_type)}
              </span>
              <span style={{
                color: T.text2,
                flexShrink: 0,
                width: 60,
                fontFamily: FONTS.mono,
                fontSize: 10,
              }}>
                {new Date(step.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{
                  fontFamily: FONTS.body,
                  fontWeight: 800,
                  color: T.blueberry,
                }}>
                  {step.agent ?? step.step_id}
                </span>
                {' '}
                <span style={{ fontFamily: FONTS.body, color: T.text2 }}>
                  {step.event_type.replace(/\./g, ' ')}
                </span>
                {step.message && (
                  <span style={{ color: T.text0 }}> — {step.message}</span>
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
          padding: '8px 16px',
          borderTop: `2px solid ${T.border}`,
          background: T.bg3,
          flexShrink: 0,
        }}>
          <span style={{
            fontFamily: FONTS.hand,
            fontStyle: isTerminal ? 'normal' : 'italic',
            fontSize: 14,
            color: isTerminal ? T.mint : T.text2,
          }}>
            {isTerminal ? 'Execution complete' : 'Polling every 3s'}
          </span>
          <button
            onClick={onClose}
            style={{
              padding: '4px 14px',
              borderRadius: 10,
              border: `2px solid ${T.border}`,
              background: T.bg1,
              color: T.text1,
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 12,
              cursor: 'pointer',
              boxShadow: SHADOWS.sm,
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
      border: `1.5px solid ${T.border}`,
      borderRadius: 999,
      padding: '2px 8px',
      background: T.bg1,
      boxShadow: SHADOWS.sm,
      fontFamily: FONTS.body,
      fontWeight: 800,
    }}>
      <span style={{ color: T.text2 }}>{label}:</span>
      <span style={{ color }}>{value}</span>
    </span>
  );
}
