import { useMemo } from 'react';
import type { PmoCard, ProgramHealth } from '../api/types';
import { T, FONT_SIZES, programColor, SR_ONLY } from '../styles/tokens';
import { useBodyScrollLock } from '../hooks/useBodyScrollLock';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  cards: PmoCard[];
  health: Record<string, ProgramHealth>;
  onClose: () => void;
}

interface AgentMetric {
  agent: string;
  dispatched: number;
  success: number;
  failed: number;
  rate: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AnalyticsDashboard({ cards, health, onClose }: Props) {
  useBodyScrollLock();
  const stats = useMemo(() => computeStats(cards, health), [cards, health]);

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
          width: 680,
          maxHeight: '85vh',
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
          justifyContent: 'space-between',
          padding: '10px 16px',
          borderBottom: `1px solid ${T.border}`,
          flexShrink: 0,
        }}>
          <h2 style={{ fontSize: FONT_SIZES.lg, fontWeight: 700, color: T.text0, margin: 0 }}>
            Portfolio Analytics
          </h2>
          <button
            onClick={onClose}
            aria-label="Close analytics"
            style={{
              background: 'none',
              border: 'none',
              color: T.text3,
              fontSize: 16,
              cursor: 'pointer',
            }}
          >
            \u2715
          </button>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <span style={SR_ONLY} aria-live="polite">Analytics dashboard loaded with {cards.length} cards</span>

          {/* Summary Cards Row */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
            <MetricCard label="Total Plans" value={stats.totalPlans} color={T.accent} />
            <MetricCard label="Success Rate" value={`${stats.successRate}%`} color={T.green} />
            <MetricCard label="Active" value={stats.activePlans} color={T.yellow} />
            <MetricCard label="Blocked / Failed" value={stats.blockedOrFailed} color={T.red} />
          </div>

          {/* Column Distribution */}
          <Section title="Pipeline Distribution">
            <div style={{ display: 'flex', gap: 2, height: 24, borderRadius: 4, overflow: 'hidden' }}>
              {stats.columnDistribution.map(({ column, count, color, pct }) => (
                <div
                  key={column}
                  title={`${column}: ${count} (${pct}%)`}
                  style={{
                    flex: pct,
                    background: color,
                    minWidth: count > 0 ? 2 : 0,
                    transition: 'flex 0.3s ease',
                  }}
                />
              ))}
            </div>
            <div style={{ display: 'flex', gap: 12, marginTop: 6, flexWrap: 'wrap' }}>
              {stats.columnDistribution.map(({ column, count, color }) => (
                <span key={column} style={{ fontSize: FONT_SIZES.xs, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: color, display: 'inline-block' }} />
                  <span style={{ color: T.text2 }}>{column}</span>
                  <span style={{ color: T.text0, fontWeight: 600 }}>{count}</span>
                </span>
              ))}
            </div>
          </Section>

          {/* Program Health */}
          {stats.programs.length > 0 && (
            <Section title="Program Health">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {stats.programs.map((p) => (
                  <div key={p.program} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: programColor(p.program),
                      flexShrink: 0,
                    }} />
                    <span style={{ fontSize: FONT_SIZES.sm, color: T.text1, width: 100, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {p.program}
                    </span>
                    <div style={{ flex: 1, height: 6, borderRadius: 3, background: T.bg3, overflow: 'hidden' }}>
                      <div style={{
                        width: `${p.completion_pct}%`,
                        height: '100%',
                        borderRadius: 3,
                        background: programColor(p.program),
                        transition: 'width 0.3s ease',
                      }} />
                    </div>
                    <span style={{ fontSize: FONT_SIZES.xs, color: T.text2, width: 35, textAlign: 'right', flexShrink: 0 }}>
                      {p.completion_pct}%
                    </span>
                    <span style={{ fontSize: FONT_SIZES.xs, color: T.text3, width: 80, textAlign: 'right', flexShrink: 0 }}>
                      {p.completed}/{p.total_plans} done
                    </span>
                  </div>
                ))}
              </div>
            </Section>
          )}

          {/* Agent Utilization */}
          {stats.agents.length > 0 && (
            <Section title="Agent Utilization">
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 60px 60px 60px 60px', gap: '2px 8px', fontSize: FONT_SIZES.xs }}>
                {/* Header */}
                <span style={{ color: T.text3, fontWeight: 600 }}>Agent</span>
                <span style={{ color: T.text3, fontWeight: 600, textAlign: 'right' }}>Tasks</span>
                <span style={{ color: T.text3, fontWeight: 600, textAlign: 'right' }}>Success</span>
                <span style={{ color: T.text3, fontWeight: 600, textAlign: 'right' }}>Failed</span>
                <span style={{ color: T.text3, fontWeight: 600, textAlign: 'right' }}>Rate</span>
                {/* Rows */}
                {stats.agents.map((a) => (
                  <AgentRow key={a.agent} metric={a} />
                ))}
              </div>
            </Section>
          )}

          {/* Risk Distribution */}
          <Section title="Risk Distribution">
            <div style={{ display: 'flex', gap: 16 }}>
              {stats.riskDistribution.map(({ level, count, color }) => (
                <div key={level} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{
                    width: 28,
                    height: 28,
                    borderRadius: '50%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: color + '20',
                    border: `1px solid ${color}44`,
                    color,
                    fontSize: FONT_SIZES.sm,
                    fontWeight: 700,
                  }}>
                    {count}
                  </span>
                  <span style={{ fontSize: FONT_SIZES.xs, color: T.text2, textTransform: 'capitalize' }}>{level}</span>
                </div>
              ))}
            </div>
          </Section>
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          padding: '8px 16px',
          borderTop: `1px solid ${T.border}`,
          flexShrink: 0,
        }}>
          <button
            onClick={onClose}
            style={{
              padding: '4px 14px',
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

function MetricCard({ label, value, color }: { label: string; value: string | number; color: string }) {
  return (
    <div style={{
      padding: '10px 12px',
      borderRadius: 6,
      background: T.bg2,
      border: `1px solid ${T.border}`,
    }}>
      <div style={{ fontSize: 20, fontWeight: 700, color, letterSpacing: -0.5 }}>{value}</div>
      <div style={{ fontSize: FONT_SIZES.xs, color: T.text3, marginTop: 2 }}>{label}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{
      padding: 12,
      borderRadius: 6,
      background: T.bg2,
      border: `1px solid ${T.border}`,
    }}>
      <h3 style={{ fontSize: FONT_SIZES.sm, fontWeight: 600, color: T.text1, margin: '0 0 8px' }}>{title}</h3>
      {children}
    </div>
  );
}

function AgentRow({ metric }: { metric: AgentMetric }) {
  return (
    <>
      <span style={{ color: T.cyan, fontWeight: 500 }}>{metric.agent}</span>
      <span style={{ color: T.text1, textAlign: 'right' }}>{metric.dispatched}</span>
      <span style={{ color: T.green, textAlign: 'right' }}>{metric.success}</span>
      <span style={{ color: metric.failed > 0 ? T.red : T.text3, textAlign: 'right' }}>{metric.failed}</span>
      <span style={{ color: metric.rate >= 80 ? T.green : metric.rate >= 50 ? T.yellow : T.red, textAlign: 'right', fontWeight: 600 }}>
        {metric.rate}%
      </span>
    </>
  );
}

// ---------------------------------------------------------------------------
// Analytics computation
// ---------------------------------------------------------------------------

const COLUMN_COLORS: Record<string, string> = {
  queued: T.text2,
  executing: T.yellow,
  awaiting_human: T.orange,
  validating: T.purple,
  deployed: T.green,
};

function computeStats(cards: PmoCard[], health: Record<string, ProgramHealth>) {
  const totalPlans = cards.length;
  const deployed = cards.filter(c => c.column === 'deployed').length;
  const activePlans = cards.filter(c => c.column === 'executing' || c.column === 'validating').length;
  const blockedOrFailed = cards.filter(c => c.column === 'awaiting_human' || c.error).length;
  const successRate = totalPlans > 0 ? Math.round((deployed / totalPlans) * 100) : 0;

  // Column distribution
  const columnCounts: Record<string, number> = {};
  for (const c of cards) {
    columnCounts[c.column] = (columnCounts[c.column] ?? 0) + 1;
  }
  const columnDistribution = ['queued', 'executing', 'awaiting_human', 'validating', 'deployed'].map(col => ({
    column: col.replace('_', ' '),
    count: columnCounts[col] ?? 0,
    color: COLUMN_COLORS[col] ?? T.text2,
    pct: totalPlans > 0 ? Math.round(((columnCounts[col] ?? 0) / totalPlans) * 100) : 0,
  }));

  // Programs
  const programs = Object.values(health).sort((a, b) => b.total_plans - a.total_plans);

  // Agent utilization
  const agentMap = new Map<string, { dispatched: number; success: number; failed: number }>();
  for (const c of cards) {
    for (const agent of c.agents) {
      const entry = agentMap.get(agent) ?? { dispatched: 0, success: 0, failed: 0 };
      entry.dispatched++;
      if (c.column === 'deployed') entry.success++;
      if (c.error) entry.failed++;
      agentMap.set(agent, entry);
    }
  }
  const agents: AgentMetric[] = [...agentMap.entries()]
    .map(([agent, m]) => ({
      agent,
      ...m,
      rate: m.dispatched > 0 ? Math.round((m.success / m.dispatched) * 100) : 0,
    }))
    .sort((a, b) => b.dispatched - a.dispatched)
    .slice(0, 15);

  // Risk distribution
  const riskCounts: Record<string, number> = {};
  for (const c of cards) {
    const level = (c.risk_level || 'low').toLowerCase();
    riskCounts[level] = (riskCounts[level] ?? 0) + 1;
  }
  const RISK_COLORS: Record<string, string> = {
    critical: T.red,
    high: T.orange,
    medium: T.yellow,
    low: T.green,
  };
  const riskDistribution = ['critical', 'high', 'medium', 'low']
    .filter(level => (riskCounts[level] ?? 0) > 0)
    .map(level => ({
      level,
      count: riskCounts[level] ?? 0,
      color: RISK_COLORS[level] ?? T.text2,
    }));

  return {
    totalPlans,
    successRate,
    activePlans,
    blockedOrFailed,
    columnDistribution,
    programs,
    agents,
    riskDistribution,
  };
}
