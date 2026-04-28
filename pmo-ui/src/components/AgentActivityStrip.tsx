import { useMemo } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { agentDisplayName } from '../utils/agent-names';
import type { AgentActivity } from '../api/workforce';

// ===================================================================
// AgentActivityStrip — one stacked horizontal bar per agent.
// Bands: completed (mint), running (butter), failed (cherry).
// Width proportional to total step count (relative to top agent).
// Click → onSelect(agent) for drill-down.
// ===================================================================

interface Props {
  data: AgentActivity[];
  loading: boolean;
  onSelect?: (agent: string) => void;
  selectedAgent?: string | null;
}

export function AgentActivityStrip({ data, loading, onSelect, selectedAgent }: Props) {
  const sorted = useMemo(
    () => [...data].sort((a, b) => b.total - a.total),
    [data],
  );
  const max = sorted[0]?.total || 1;

  return (
    <section
      aria-label="Agent activity (last 24 hours)"
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
      }}
    >
      <Header />
      {loading && sorted.length === 0 ? <SkeletonRows /> : (
        <div role="list" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {sorted.map(row => (
            <AgentRow
              key={row.agent}
              row={row}
              maxTotal={max}
              selected={selectedAgent === row.agent}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
      <Legend />
    </section>
  );
}

function Header() {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
      <div>
        <h3 style={{
          margin: 0,
          fontFamily: FONTS.display,
          fontSize: 16,
          fontWeight: 800,
          color: T.text0,
          letterSpacing: -0.4,
        }}>
          Agent Activity
        </h3>
        <div style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.body }}>
          last 24 hours · click to drill in
        </div>
      </div>
    </div>
  );
}

interface RowProps {
  row: AgentActivity;
  maxTotal: number;
  selected: boolean;
  onSelect?: (agent: string) => void;
}

function AgentRow({ row, maxTotal, selected, onSelect }: RowProps) {
  const widthPct = maxTotal > 0 ? (row.total / maxTotal) * 100 : 0;
  const completedPct = row.total > 0 ? (row.completed / row.total) * 100 : 0;
  const runningPct   = row.total > 0 ? (row.running   / row.total) * 100 : 0;
  const failedPct    = row.total > 0 ? (row.failed    / row.total) * 100 : 0;

  const interactive = !!onSelect;
  const Tag = interactive ? 'button' : 'div';

  return (
    <Tag
      role="listitem"
      aria-label={`${agentDisplayName(row.agent)}: ${row.completed} completed, ${row.running} running, ${row.failed} failed`}
      onClick={interactive ? () => onSelect!(row.agent) : undefined}
      style={{
        display: 'grid',
        gridTemplateColumns: '140px 1fr 80px',
        alignItems: 'center',
        gap: 10,
        padding: '4px 6px',
        border: selected ? `2px solid ${T.cherry}` : '2px solid transparent',
        borderRadius: 8,
        background: selected ? T.cherrySoft : 'transparent',
        cursor: interactive ? 'pointer' : 'default',
        textAlign: 'left',
        font: 'inherit',
        color: 'inherit',
        width: '100%',
      }}
    >
      <div style={{
        fontSize: 11,
        fontWeight: 700,
        color: T.text0,
        fontFamily: FONTS.body,
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}>
        {agentDisplayName(row.agent)}
      </div>
      <div
        aria-hidden="true"
        style={{
          height: 14,
          width: `${widthPct}%`,
          minWidth: 6,
          borderRadius: 4,
          border: `1.5px solid ${T.border}`,
          display: 'flex',
          overflow: 'hidden',
          background: T.bg3,
        }}
      >
        {completedPct > 0 && <Band pct={completedPct} color={T.mint} />}
        {runningPct   > 0 && <Band pct={runningPct}   color={T.butter} />}
        {failedPct    > 0 && <Band pct={failedPct}    color={T.cherry} />}
      </div>
      <div style={{
        display: 'flex',
        gap: 6,
        fontSize: 10,
        color: T.text1,
        fontFamily: FONTS.mono,
        justifyContent: 'flex-end',
      }}>
        <span title="completed" style={{ color: T.mintDark }}>{row.completed}</span>
        <span style={{ color: T.text4 }}>·</span>
        <span title="running"   style={{ color: T.crustDark }}>{row.running}</span>
        <span style={{ color: T.text4 }}>·</span>
        <span title="failed"    style={{ color: T.cherryDark }}>{row.failed}</span>
      </div>
    </Tag>
  );
}

function Band({ pct, color }: { pct: number; color: string }) {
  return <div style={{ width: `${pct}%`, height: '100%', background: color }} />;
}

function SkeletonRows() {
  return (
    <div role="presentation" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} style={{
          height: 22,
          background: T.bg3,
          borderRadius: 6,
          animation: 'pulse 1.4s ease-in-out infinite',
          opacity: 0.4 + (i % 3) * 0.2,
        }} />
      ))}
    </div>
  );
}

function Legend() {
  return (
    <div style={{
      display: 'flex',
      gap: 10,
      paddingTop: 6,
      borderTop: `1px dashed ${T.borderSoft}`,
      fontSize: 10,
      color: T.text2,
      fontFamily: FONTS.body,
    }}>
      <Swatch color={T.mint}   label="completed" />
      <Swatch color={T.butter} label="running" />
      <Swatch color={T.cherry} label="failed" />
    </div>
  );
}

function Swatch({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span style={{
        display: 'inline-block',
        width: 10, height: 10, borderRadius: 2,
        border: `1px solid ${T.border}`,
        background: color,
      }} />
      {label}
    </span>
  );
}
