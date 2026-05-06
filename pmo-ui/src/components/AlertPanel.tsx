import { useState } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import type { WorkforceAlert } from '../api/workforce';
import { ackAlert } from '../api/workforce';

// ===================================================================
// AlertPanel — right-column list of currently-open WARNING+ beads
// across all projects. Acknowledge optimistically; "Open" links to
// the bead drill-down (TODO when route exists).
// ===================================================================

interface Props {
  alerts: WorkforceAlert[];
  loading: boolean;
  onAcked?: (beadId: string) => void;
}

function relTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return 'now';
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function AlertPanel({ alerts, loading, onAcked }: Props) {
  const [ackedLocal, setAckedLocal] = useState<Set<string>>(new Set());
  const visible = alerts.filter(a => !a.ack && !ackedLocal.has(a.bead_id));

  async function handleAck(beadId: string) {
    setAckedLocal(prev => {
      const next = new Set(prev);
      next.add(beadId);
      return next;
    });
    void ackAlert(beadId).then(ok => {
      if (!ok) {
        // network / endpoint missing — keep optimistic state but log.
        console.info(`[workforce] ack ${beadId} not persisted (endpoint absent)`);
      }
      onAcked?.(beadId);
    });
  }

  return (
    <section
      aria-label="Open alerts"
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
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <div>
          <h3 style={{
            margin: 0,
            fontFamily: FONTS.display,
            fontSize: 16,
            fontWeight: 800,
            color: T.text0,
            letterSpacing: -0.4,
          }}>
            Alerts
          </h3>
          <div style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.body }}>
            open WARNING+ beads
          </div>
        </div>
        <span style={{
          fontFamily: FONTS.mono,
          fontSize: 11,
          padding: '2px 8px',
          borderRadius: 999,
          background: visible.length > 0 ? T.cherry : T.mint,
          color: T.cream,
          fontWeight: 800,
          border: `1.5px solid ${T.border}`,
        }}>
          {visible.length}
        </span>
      </div>
      <div
        role="list"
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}
      >
        {loading && alerts.length === 0 ? (
          <SkeletonRows />
        ) : visible.length === 0 ? (
          <Empty />
        ) : (
          visible.map(a => (
            <AlertRow key={a.bead_id} alert={a} onAck={() => handleAck(a.bead_id)} />
          ))
        )}
      </div>
    </section>
  );
}

interface RowProps {
  alert: WorkforceAlert;
  onAck: () => void;
}

function AlertRow({ alert, onAck }: RowProps) {
  const isError = alert.type === 'error';
  const accent = isError ? T.cherry : T.tangerine;
  return (
    <div
      role="listitem"
      style={{
        border: `2px solid ${T.border}`,
        borderLeft: `6px solid ${accent}`,
        borderRadius: 8,
        padding: '8px 10px',
        background: isError ? T.cherrySoft : T.tangerineSoft,
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <span style={{
          fontFamily: FONTS.mono,
          fontSize: 9,
          fontWeight: 800,
          textTransform: 'uppercase',
          letterSpacing: 0.5,
          color: T.text0,
        }}>
          {alert.type} · {alert.project_id}
        </span>
        <span style={{
          fontFamily: FONTS.mono,
          fontSize: 9,
          color: T.text2,
        }}>
          {relTime(alert.created_at)}
        </span>
      </div>
      <div style={{
        fontSize: 11,
        color: T.text0,
        fontFamily: FONTS.body,
        lineHeight: 1.35,
      }}>
        {alert.content}
      </div>
      <div style={{
        display: 'flex',
        gap: 6,
        justifyContent: 'flex-end',
      }}>
        <ActionButton kind="secondary" onClick={() => openBead(alert.bead_id)}>Open</ActionButton>
        <ActionButton kind="primary" onClick={onAck}>Acknowledge</ActionButton>
      </div>
    </div>
  );
}

function openBead(beadId: string) {
  // Future: route to a bead drill-down. For now log + copy-to-clipboard.
  console.info(`[workforce] open bead ${beadId}`);
  if (typeof navigator !== 'undefined' && navigator.clipboard) {
    void navigator.clipboard.writeText(beadId);
  }
}

interface BtnProps {
  kind: 'primary' | 'secondary';
  children: React.ReactNode;
  onClick: () => void;
}

function ActionButton({ kind, children, onClick }: BtnProps) {
  const primary = kind === 'primary';
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '4px 10px',
        borderRadius: 6,
        border: `1.5px solid ${T.border}`,
        background: primary ? T.butter : T.bg1,
        color: T.text0,
        fontFamily: FONTS.body,
        fontSize: 10,
        fontWeight: 800,
        cursor: 'pointer',
        letterSpacing: 0.3,
      }}
    >
      {children}
    </button>
  );
}

function SkeletonRows() {
  return (
    <div role="presentation" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} style={{
          height: 64,
          background: T.bg3,
          borderRadius: 8,
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
      All clear — no open alerts.
    </div>
  );
}
