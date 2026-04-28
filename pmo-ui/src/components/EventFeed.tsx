import { useMemo } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { agentDisplayName } from '../utils/agent-names';
import type { WorkforceEvent, WorkforceEventType, WorkforceSeverity } from '../api/workforce';

// ===================================================================
// EventFeed — scrolling chronological feed of recent fleet events.
// Color-coded by severity. Top 30, newest first.
// ===================================================================

interface Props {
  events: WorkforceEvent[];
  loading: boolean;
}

const TYPE_GLYPH: Record<WorkforceEventType, string> = {
  step_started:      '▶',
  step_completed:    '✓',
  gate_passed:       '✓',
  gate_failed:       '✕',
  override_fired:    '!',
  escalation_opened: '⚠',
};

const TYPE_LABEL: Record<WorkforceEventType, string> = {
  step_started:      'step started',
  step_completed:    'step done',
  gate_passed:       'gate passed',
  gate_failed:       'gate failed',
  override_fired:    'override',
  escalation_opened: 'escalation',
};

function severityColor(sev: WorkforceSeverity): string {
  switch (sev) {
    case 'error': return T.cherry;
    case 'warn':  return T.tangerine;
    case 'info':
    default:      return T.mint;
  }
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return 'now';
  const s = Math.floor(ms / 1000);
  if (s < 60)   return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60)   return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24)   return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

export function EventFeed({ events, loading }: Props) {
  const sorted = useMemo(
    () => [...events].sort((a, b) => b.ts.localeCompare(a.ts)).slice(0, 30),
    [events],
  );

  return (
    <section
      aria-label="Recent fleet events"
      aria-busy={loading}
      style={{
        background: T.bg1,
        border: `2px solid ${T.border}`,
        borderRadius: 14,
        padding: 14,
        boxShadow: SHADOWS.sm,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        minHeight: 0,
      }}
    >
      <div>
        <h3 style={{
          margin: 0,
          fontFamily: FONTS.display,
          fontSize: 16,
          fontWeight: 800,
          color: T.text0,
          letterSpacing: -0.4,
        }}>
          Recent Events
        </h3>
        <div style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.body }}>
          live · top 30
        </div>
      </div>
      <div
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
        }}
      >
        {loading && sorted.length === 0 ? (
          <SkeletonRows />
        ) : sorted.length === 0 ? (
          <Empty />
        ) : (
          sorted.map(ev => <EventRow key={ev.event_id} ev={ev} />)
        )}
      </div>
    </section>
  );
}

function EventRow({ ev }: { ev: WorkforceEvent }) {
  const color = severityColor(ev.severity);
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '32px 96px 1fr 38px',
      alignItems: 'baseline',
      gap: 8,
      padding: '4px 6px',
      borderLeft: `3px solid ${color}`,
      background: ev.severity === 'error' ? T.cherrySoft
                : ev.severity === 'warn'  ? T.tangerineSoft
                : 'transparent',
      borderRadius: 4,
    }}>
      <span aria-hidden="true" style={{
        display: 'inline-flex', justifyContent: 'center', alignItems: 'center',
        width: 22, height: 22, borderRadius: '50%',
        background: color, color: T.cream,
        fontSize: 12, fontWeight: 900,
        fontFamily: FONTS.mono,
        border: `1.5px solid ${T.border}`,
      }}>
        {TYPE_GLYPH[ev.type]}
      </span>
      <span style={{
        fontFamily: FONTS.mono,
        fontSize: 9,
        color: T.text2,
        textTransform: 'uppercase',
        letterSpacing: 0.5,
      }}>
        {TYPE_LABEL[ev.type]}
      </span>
      <span style={{
        fontFamily: FONTS.body,
        fontSize: 11,
        color: T.text0,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}>
        {ev.agent && (
          <strong style={{ color: T.text0, marginRight: 6 }}>
            {agentDisplayName(ev.agent)}
          </strong>
        )}
        <span style={{ color: T.text1 }}>{ev.message}</span>
        {ev.project_id && (
          <span style={{ color: T.text3, marginLeft: 6, fontSize: 10 }}>
            · {ev.project_id}
          </span>
        )}
      </span>
      <span style={{
        fontFamily: FONTS.mono,
        fontSize: 10,
        color: T.text3,
        textAlign: 'right',
      }}>
        {relativeTime(ev.ts)}
      </span>
    </div>
  );
}

function SkeletonRows() {
  return (
    <div role="presentation" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} style={{
          height: 24,
          background: T.bg3,
          borderRadius: 4,
          animation: 'pulse 1.4s ease-in-out infinite',
          opacity: 0.4 + (i % 3) * 0.2,
        }} />
      ))}
    </div>
  );
}

function Empty() {
  return (
    <div style={{
      padding: 18,
      textAlign: 'center',
      color: T.text2,
      fontFamily: FONTS.hand,
      fontSize: 16,
    }}>
      Quiet shift — no recent events.
    </div>
  );
}
