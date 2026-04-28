import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { T, FONTS, FONT_SIZES, SHADOWS } from '../styles/tokens';
import type { HumanRole, ProgramHealth } from '../api/types';
import { api } from '../api/client';

/**
 * H3.2 — Role-Based Dashboard.
 *
 * Renders a different summary card layout depending on the active
 * `human_role`. The role is read in this priority order:
 *   1. The `?role=` query parameter on the current URL.
 *   2. The `role` prop passed in by the parent.
 *   3. Defaults to `senior`.
 *
 * Backend integration: pulls `/api/v1/pmo/health` for the program-level
 * counts that drive the rollups. The data already exists; this view is
 * pure presentation.
 */
export interface RoleBasedDashboardProps {
  role?: HumanRole;
}

const VALID_ROLES: HumanRole[] = [
  'junior',
  'senior',
  'tech_lead',
  'architect',
  'eng_manager',
  'qa',
];

function readRoleFromUrl(): HumanRole | null {
  if (typeof window === 'undefined') return null;
  try {
    const params = new URLSearchParams(window.location.search);
    const role = params.get('role') as HumanRole | null;
    if (role && (VALID_ROLES as string[]).includes(role)) return role;
  } catch {
    // ignore
  }
  return null;
}

const ROLE_DESCRIPTIONS: Record<HumanRole, string> = {
  junior: 'Your active assignments and recent feedback.',
  senior: 'Your in-flight work alongside the team backlog.',
  tech_lead: 'Team-level rollups across every program you lead.',
  architect: 'System-level metrics: risk, gates, and architectural beads.',
  eng_manager: 'Program health, completion rates, and incident counts.',
  qa: 'Gate pass-rates, failed reviews, and validation backlog.',
};

const cardStyle: CSSProperties = {
  background: T.cream,
  border: `2px solid ${T.border}`,
  borderRadius: 6,
  padding: 12,
  boxShadow: SHADOWS.sm,
  minWidth: 160,
  flex: 1,
};

const labelStyle: CSSProperties = {
  fontSize: FONT_SIZES.xs,
  color: T.text2,
  textTransform: 'uppercase',
  letterSpacing: 0.5,
};

const valueStyle: CSSProperties = {
  fontSize: 28,
  fontWeight: 800,
  fontFamily: FONTS.display,
  color: T.text0,
  marginTop: 4,
};

export function RoleBasedDashboard(props: RoleBasedDashboardProps) {
  const urlRole = readRoleFromUrl();
  const role = urlRole ?? props.role ?? 'senior';

  const [health, setHealth] = useState<Record<string, ProgramHealth>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getHealth()
      .then((data) => {
        if (!cancelled) setHealth(data);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err.message ?? err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Aggregate counts across all programs for system-level rollups.
  const totals = Object.values(health).reduce(
    (acc, h) => {
      acc.total_plans += h.total_plans;
      acc.active += h.active;
      acc.completed += h.completed;
      acc.blocked += h.blocked;
      acc.failed += h.failed;
      return acc;
    },
    { total_plans: 0, active: 0, completed: 0, blocked: 0, failed: 0 },
  );

  // Choose which cards to highlight based on role.
  let cards: Array<{ label: string; value: number | string }> = [];
  switch (role) {
    case 'junior':
      cards = [
        { label: 'My Active Tasks', value: totals.active },
        { label: 'Awaiting Review', value: totals.blocked },
      ];
      break;
    case 'senior':
      cards = [
        { label: 'My Active', value: totals.active },
        { label: 'Team Backlog', value: totals.total_plans },
        { label: 'Blocked', value: totals.blocked },
      ];
      break;
    case 'tech_lead':
      cards = [
        { label: 'Team Active', value: totals.active },
        { label: 'Completed (30d)', value: totals.completed },
        { label: 'Blocked', value: totals.blocked },
        { label: 'Failed', value: totals.failed },
      ];
      break;
    case 'architect':
      cards = [
        { label: 'System Plans', value: totals.total_plans },
        { label: 'Active Now', value: totals.active },
        { label: 'Failure Rate', value: totals.total_plans
          ? `${Math.round((totals.failed / totals.total_plans) * 100)}%`
          : '0%' },
      ];
      break;
    case 'eng_manager':
      cards = [
        { label: 'Total Plans', value: totals.total_plans },
        { label: 'Completed', value: totals.completed },
        { label: 'Active', value: totals.active },
        { label: 'Blocked / Failed', value: totals.blocked + totals.failed },
      ];
      break;
    case 'qa':
      cards = [
        { label: 'Gates Pending', value: totals.blocked },
        { label: 'Failed Validations', value: totals.failed },
        { label: 'In-Flight', value: totals.active },
      ];
      break;
  }

  return (
    <div
      style={{
        padding: 16,
        background: T.bg0,
        color: T.text0,
        fontFamily: FONTS.body,
        minHeight: '100%',
      }}
      data-testid="role-based-dashboard"
    >
      <h1
        style={{
          fontFamily: FONTS.display,
          fontSize: 24,
          margin: 0,
          color: T.text0,
        }}
      >
        Role View · {role.replace('_', ' ')}
      </h1>
      <div style={{ color: T.text2, fontSize: FONT_SIZES.sm, marginBottom: 16 }}>
        {ROLE_DESCRIPTIONS[role]}
      </div>

      {loading && <div style={{ color: T.text2 }}>Loading...</div>}
      {error && (
        <div
          role="alert"
          style={{
            color: T.cherry,
            border: `2px solid ${T.cherry}`,
            padding: 8,
            borderRadius: 4,
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      )}

      <div
        style={{
          display: 'flex',
          gap: 12,
          flexWrap: 'wrap',
        }}
        data-testid="role-cards"
      >
        {cards.map((c) => (
          <div key={c.label} style={cardStyle} data-testid="role-card">
            <div style={labelStyle}>{c.label}</div>
            <div style={valueStyle}>{c.value}</div>
          </div>
        ))}
      </div>

      {Object.keys(health).length > 0 && (
        <details style={{ marginTop: 24, color: T.text1 }}>
          <summary style={{ cursor: 'pointer' }}>Per-program breakdown</summary>
          <ul style={{ marginTop: 8 }}>
            {Object.entries(health).map(([prog, h]) => (
              <li key={prog} style={{ fontSize: FONT_SIZES.sm }}>
                <strong>{prog}</strong>: {h.active} active · {h.completed} done ·{' '}
                {h.blocked} blocked
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export default RoleBasedDashboard;
