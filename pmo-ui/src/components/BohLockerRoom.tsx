import { useState, useEffect } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import type { Agent } from '../api/types';

// ---------------------------------------------------------------------------
// Category accent colours — map API category names to kitchen palette
// ---------------------------------------------------------------------------

const CATEGORY_ACCENT: Record<string, string> = {
  'Engineering':          T.blueberry,
  'Data & Analytics':     T.mint,
  'Review & Governance':  T.cherry,
  'Meta':                 T.butter,
  'Domain':               T.tangerine,
};

const CATEGORY_ACCENT_TEXT: Record<string, string> = {
  'Engineering':          T.cream,
  'Data & Analytics':     T.ink,
  'Review & Governance':  T.cream,
  'Meta':                 T.ink,
  'Domain':               T.ink,
};

function categoryAccent(cat: string): string {
  return CATEGORY_ACCENT[cat] ?? T.crust;
}
function categoryAccentText(cat: string): string {
  return CATEGORY_ACCENT_TEXT[cat] ?? T.ink;
}

// Model badge colours
const MODEL_BADGE: Record<string, { bg: string; text: string }> = {
  opus:   { bg: T.blueberry,  text: T.cream },
  sonnet: { bg: T.butter,     text: T.ink   },
  haiku:  { bg: T.mint,       text: T.ink   },
};
function modelBadge(model: string) {
  const key = model.toLowerCase().split('-')[0];
  return MODEL_BADGE[key] ?? { bg: T.crust, text: T.ink };
}

// ---------------------------------------------------------------------------
// Agent card
// ---------------------------------------------------------------------------

function AgentCard({ agent }: { agent: Agent }) {
  const badge = modelBadge(agent.model);
  const desc = agent.description.length > 90
    ? agent.description.slice(0, 87) + '…'
    : agent.description;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        padding: '10px 12px',
        background: T.bg2,
        border: `2px solid ${T.border}`,
        borderRadius: 10,
        boxShadow: SHADOWS.sm,
      }}
    >
      {/* Name + model badge row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
        <div style={{
          flex: 1,
          fontFamily: FONTS.mono,
          fontWeight: 700,
          fontSize: 11,
          color: T.ink,
          lineHeight: 1.3,
          wordBreak: 'break-word',
        }}>
          {agent.name}
        </div>
        <span style={{
          flexShrink: 0,
          background: badge.bg,
          color: badge.text,
          fontFamily: FONTS.body,
          fontWeight: 800,
          fontSize: 9,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          padding: '2px 6px',
          borderRadius: 999,
          border: `1.5px solid ${T.border}`,
        }}>
          {agent.model.split('-')[0]}
        </span>
      </div>

      {/* Description */}
      <div style={{
        fontFamily: FONTS.body,
        fontWeight: 400,
        fontSize: 11,
        color: T.text2,
        lineHeight: 1.45,
      }}>
        {desc}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Category section
// ---------------------------------------------------------------------------

function CategorySection({ category, agents }: { category: string; agents: Agent[] }) {
  const accent = categoryAccent(category);
  const accentText = categoryAccentText(category);

  return (
    <section aria-label={category}>
      {/* Category header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        marginBottom: 8,
      }}>
        <span style={{
          background: accent,
          color: accentText,
          fontFamily: FONTS.body,
          fontWeight: 800,
          fontSize: 10,
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
          padding: '3px 10px',
          borderRadius: 999,
          border: `1.5px solid ${T.border}`,
          boxShadow: SHADOWS.sm,
        }}>
          {category}
        </span>
        <span style={{
          fontFamily: FONTS.mono,
          fontSize: 10,
          color: T.text3,
        }}>
          {agents.length} agent{agents.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(2, 1fr)',
        gap: 8,
        marginBottom: 18,
      }}>
        {agents.map(a => (
          <AgentCard key={a.name} agent={a} />
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Loading / error states
// ---------------------------------------------------------------------------

function LoadingShimmer() {
  return (
    <div style={{ padding: '32px 0', textAlign: 'center' }}>
      <div style={{
        fontFamily: FONTS.hand,
        fontSize: 18,
        color: T.text2,
        transform: 'rotate(-1deg)',
        display: 'inline-block',
      }}>
        reading the crew board…
      </div>
    </div>
  );
}

function ErrorNote({ message }: { message: string }) {
  return (
    <div style={{
      background: T.cherrySoft,
      border: `2px solid ${T.cherry}`,
      borderRadius: 10,
      padding: '12px 16px',
      marginBottom: 16,
      fontFamily: FONTS.body,
      fontSize: 12,
      color: T.cherryDark,
      display: 'flex',
      gap: 8,
      alignItems: 'flex-start',
    }}>
      <span style={{ fontSize: 16, lineHeight: 1 }}>⚠</span>
      <div>
        <strong>Could not reach agent registry.</strong>
        <div style={{ marginTop: 4, color: T.text1 }}>{message} — showing cached or mock data below.</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BohLockerRoom
// ---------------------------------------------------------------------------

export function BohLockerRoom() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getAgents()
      .then(res => {
        if (!cancelled) {
          setAgents(res.agents);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  // Group by category, preserving a stable display order
  const categoryOrder = ['Engineering', 'Data & Analytics', 'Review & Governance', 'Meta', 'Domain'];
  const grouped = new Map<string, Agent[]>();
  for (const a of agents) {
    const bucket = grouped.get(a.category) ?? [];
    bucket.push(a);
    grouped.set(a.category, bucket);
  }
  // Any categories not in the known order go at the end
  const orderedCategories = [
    ...categoryOrder.filter(c => grouped.has(c)),
    ...[...grouped.keys()].filter(c => !categoryOrder.includes(c)),
  ];

  return (
    <div
      style={{
        fontFamily: FONTS.body,
        background: T.bg1,
        border: `2px solid ${T.border}`,
        borderRadius: 16,
        boxShadow: SHADOWS.md,
        overflow: 'hidden',
      }}
    >
      {/* Room banner */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '14px 18px',
          borderBottom: `2px solid ${T.border}`,
          background: T.cherrySoft,
        }}
      >
        <span aria-hidden="true" style={{ fontSize: 22, lineHeight: 1 }}>👥</span>
        <div style={{ flex: 1 }}>
          <div style={{
            fontFamily: FONTS.display,
            fontWeight: 900,
            fontSize: 20,
            color: T.cherry,
            lineHeight: 1.1,
          }}>
            The Locker Room
          </div>
          <div style={{
            fontFamily: FONTS.hand,
            fontSize: 14,
            color: T.text2,
            lineHeight: 1.2,
          }}>
            the crew — all registered agents
          </div>
        </div>
        {/* Count badge */}
        {!loading && !error && (
          <span style={{
            background: T.cherry,
            color: T.cream,
            fontFamily: FONTS.mono,
            fontWeight: 700,
            fontSize: 12,
            padding: '4px 10px',
            borderRadius: 999,
            border: `2px solid ${T.border}`,
            boxShadow: SHADOWS.sm,
          }}>
            {agents.length} on the line
          </span>
        )}
      </div>

      {/* Body */}
      <div style={{ padding: '16px 18px' }}>
        {loading && <LoadingShimmer />}
        {!loading && error && <ErrorNote message={error} />}
        {!loading && orderedCategories.map(cat => (
          <CategorySection
            key={cat}
            category={cat}
            agents={grouped.get(cat) ?? []}
          />
        ))}
        {!loading && agents.length === 0 && !error && (
          <div style={{
            padding: '32px 0',
            textAlign: 'center',
            fontFamily: FONTS.hand,
            fontSize: 16,
            color: T.text2,
          }}>
            No agents registered yet.
          </div>
        )}
      </div>
    </div>
  );
}
