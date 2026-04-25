import type { CSSProperties } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import type { WorkforceKpis } from '../api/workforce';

// ===================================================================
// KPIStrip — four large KPI cards across the top of the NOC view.
// Pure presentational component; data is owned by the parent view.
// ===================================================================

interface Props {
  kpis: WorkforceKpis | null;
  loading: boolean;
}

interface CardSpec {
  label: string;
  value: string;
  sub: string;
  accent: string;
}

function formatUsd(n: number): string {
  if (n < 1)    return `$${n.toFixed(2)}`;
  if (n < 100)  return `$${n.toFixed(2)}`;
  if (n < 10_000) return `$${n.toFixed(0)}`;
  return `$${(n / 1000).toFixed(1)}k`;
}

function formatInt(n: number): string {
  if (n < 1000) return n.toString();
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`;
  return `${Math.round(n / 1000)}k`;
}

export function KPIStrip({ kpis, loading }: Props) {
  const cards: CardSpec[] = kpis
    ? [
        { label: 'Active Executions', value: formatInt(kpis.active_executions), sub: 'across all projects', accent: T.butter },
        { label: 'Completed · 24h',   value: formatInt(kpis.completed_24h),     sub: 'tasks finished',      accent: T.mint },
        { label: 'Token Spend · 24h', value: formatUsd(kpis.token_spend_24h_usd), sub: 'estimated USD',     accent: T.blueberry },
        { label: 'Open Warnings',     value: formatInt(kpis.open_warnings),     sub: 'WARNING+ beads',      accent: kpis.open_warnings > 0 ? T.cherry : T.text2 },
      ]
    : [
        { label: 'Active Executions', value: '—', sub: 'across all projects', accent: T.bg4 },
        { label: 'Completed · 24h',   value: '—', sub: 'tasks finished',      accent: T.bg4 },
        { label: 'Token Spend · 24h', value: '—', sub: 'estimated USD',       accent: T.bg4 },
        { label: 'Open Warnings',     value: '—', sub: 'WARNING+ beads',      accent: T.bg4 },
      ];

  return (
    <div
      role="group"
      aria-label="Fleet KPIs"
      aria-busy={loading}
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: 12,
        padding: '12px 16px 4px',
      }}
    >
      {cards.map((c, i) => (
        <KPICard key={i} {...c} loading={loading && !kpis} />
      ))}
    </div>
  );
}

function KPICard({ label, value, sub, accent, loading }: CardSpec & { loading: boolean }) {
  const cardStyle: CSSProperties = {
    background: T.bg1,
    border: `2px solid ${T.border}`,
    borderTop: `6px solid ${accent}`,
    borderRadius: 14,
    padding: '12px 14px',
    boxShadow: SHADOWS.sm,
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    minHeight: 92,
  };
  return (
    <div style={cardStyle}>
      <div style={{
        fontSize: 9,
        textTransform: 'uppercase',
        letterSpacing: 1.5,
        color: T.text2,
        fontFamily: FONTS.body,
        fontWeight: 800,
      }}>
        {label}
      </div>
      <div style={{
        fontFamily: FONTS.display,
        fontSize: 32,
        fontWeight: 900,
        lineHeight: 1.05,
        color: T.text0,
        letterSpacing: -1,
        ...(loading ? { opacity: 0.4 } : {}),
      }}>
        {loading ? <Skeleton width={80} height={32} /> : value}
      </div>
      <div style={{
        fontSize: 10,
        color: T.text2,
        fontFamily: FONTS.body,
      }}>
        {sub}
      </div>
    </div>
  );
}

function Skeleton({ width, height }: { width: number; height: number }) {
  return (
    <div
      aria-hidden="true"
      style={{
        width,
        height,
        background: T.bg3,
        borderRadius: 6,
        animation: 'pulse 1.4s ease-in-out infinite',
      }}
    />
  );
}
