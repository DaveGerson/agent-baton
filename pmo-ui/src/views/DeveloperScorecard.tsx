import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { T, FONTS, FONT_SIZES, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import type { DeveloperScorecard as Scorecard } from '../api/types';

/**
 * H3.4 — Per-developer scorecard.
 *
 * Reads `userId` from `/pmo/scorecard/:userId` in the URL pathname or
 * accepts a `userId` prop. Calls `GET /api/v1/pmo/scorecard/{user_id}`
 * which gracefully returns zeros when the project's baton.db has no
 * matching rows yet.
 */
export interface DeveloperScorecardProps {
  userId?: string;
}

function userIdFromPathname(): string | null {
  if (typeof window === 'undefined') return null;
  const m = window.location.pathname.match(/\/pmo\/scorecard\/([^/?#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

const cardStyle: CSSProperties = {
  background: T.cream,
  border: `2px solid ${T.border}`,
  borderRadius: 6,
  padding: 12,
  minWidth: 160,
  boxShadow: SHADOWS.sm,
};

function MetricCard(props: { label: string; value: number | string; suffix?: string }) {
  return (
    <div style={cardStyle} data-testid="scorecard-metric">
      <div
        style={{
          fontSize: FONT_SIZES.xs,
          color: T.text2,
          textTransform: 'uppercase',
          letterSpacing: 0.5,
        }}
      >
        {props.label}
      </div>
      <div
        style={{
          fontSize: 28,
          fontWeight: 800,
          fontFamily: FONTS.display,
          color: T.text0,
          marginTop: 4,
        }}
      >
        {props.value}
        {props.suffix ? (
          <span style={{ fontSize: 14, color: T.text2, marginLeft: 4 }}>{props.suffix}</span>
        ) : null}
      </div>
    </div>
  );
}

export function DeveloperScorecard(props: DeveloperScorecardProps) {
  const pathUserId = userIdFromPathname();
  const userId = props.userId ?? pathUserId ?? '';

  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!userId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .getDeveloperScorecard(userId)
      .then((data) => {
        if (!cancelled) setScorecard(data);
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
  }, [userId]);

  if (!userId) {
    return (
      <div
        style={{ padding: 16, fontFamily: FONTS.body, color: T.text0 }}
        data-testid="scorecard-no-user"
      >
        No user specified. Visit <code>/pmo/scorecard/&lt;user-id&gt;</code> or
        pass a <code>userId</code> prop.
      </div>
    );
  }

  return (
    <div
      style={{
        padding: 16,
        background: T.bg0,
        color: T.text0,
        fontFamily: FONTS.body,
      }}
      data-testid="developer-scorecard"
    >
      <h1
        style={{
          fontFamily: FONTS.display,
          fontSize: 24,
          margin: 0,
          color: T.text0,
        }}
      >
        Scorecard · {userId}
      </h1>
      <div
        style={{
          color: T.text2,
          fontSize: FONT_SIZES.sm,
          marginBottom: 16,
        }}
      >
        Trailing 30-day window · sourced from baton.db
      </div>

      {loading && <div style={{ color: T.text2 }}>Loading scorecard...</div>}
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

      {scorecard && (
        <div
          style={{
            display: 'flex',
            gap: 12,
            flexWrap: 'wrap',
          }}
          data-testid="scorecard-metrics"
        >
          <MetricCard label="Tasks Completed" value={scorecard.tasks_completed} />
          <MetricCard
            label="Avg Cycle Time"
            value={scorecard.avg_cycle_time_minutes}
            suffix="min"
          />
          <MetricCard
            label="Gate Pass Rate"
            value={`${Math.round((scorecard.gate_pass_rate ?? 0) * 100)}`}
            suffix="%"
          />
          <MetricCard label="Incidents Authored" value={scorecard.incidents_authored} />
          <MetricCard label="Incidents Resolved" value={scorecard.incidents_resolved} />
          <MetricCard
            label="Knowledge Contributions"
            value={scorecard.knowledge_contributions}
          />
        </div>
      )}
    </div>
  );
}

export default DeveloperScorecard;
