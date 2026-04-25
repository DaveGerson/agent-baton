import { useCallback, useEffect, useRef, useState } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { KPIStrip } from '../components/KPIStrip';
import { AgentActivityStrip } from '../components/AgentActivityStrip';
import { ProjectHeatGrid } from '../components/ProjectHeatGrid';
import { EventFeed } from '../components/EventFeed';
import { AlertPanel } from '../components/AlertPanel';
import { fetchWorkforceSnapshot } from '../api/workforce';
import type { WorkforceSnapshot } from '../api/workforce';

// ===================================================================
// AgentWorkforceView — O1.6 NOC-style live dashboard.
//
// Layout (responsive grid):
//   ┌──────────── KPI Strip (4 cards) ────────────┐
//   │                                              │
//   ├── Agent Activity ──┬── Project Heat ────────┤
//   │                    │                        │
//   ├── Event Feed ──────┴── Alert Panel ─────────┤
//   └──────────────────────────────────────────────┘
//
// Auto-refresh every 10s; cancellable on unmount and via toolbar.
// ===================================================================

const REFRESH_MS = 10_000;

interface Props {
  onBack: () => void;
}

export function AgentWorkforceView({ onBack }: Props) {
  const [snapshot, setSnapshot] = useState<WorkforceSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [tickNow, setTickNow] = useState(Date.now());
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);

  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const aliveRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchWorkforceSnapshot();
      if (!aliveRef.current) return;
      setSnapshot(next);
      setError(null);
    } catch (err) {
      if (!aliveRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  // Initial fetch + auto-refresh interval.
  useEffect(() => {
    aliveRef.current = true;
    void refresh();

    if (autoRefresh) {
      refreshTimerRef.current = setInterval(() => { void refresh(); }, REFRESH_MS);
    }

    return () => {
      aliveRef.current = false;
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current);
      refreshTimerRef.current = null;
    };
  }, [refresh, autoRefresh]);

  // Independent 1s tick to keep "last updated Ns ago" + relative times
  // fresh between fetches.
  useEffect(() => {
    tickTimerRef.current = setInterval(() => setTickNow(Date.now()), 1000);
    return () => {
      if (tickTimerRef.current) clearInterval(tickTimerRef.current);
      tickTimerRef.current = null;
    };
  }, []);

  const lastUpdatedSec = snapshot
    ? Math.max(0, Math.floor((tickNow - new Date(snapshot.fetched_at).getTime()) / 1000))
    : null;

  return (
    <div style={{
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      background: T.bg0,
      overflow: 'hidden',
    }}>
      <Toolbar
        onBack={onBack}
        autoRefresh={autoRefresh}
        onToggleAuto={() => setAutoRefresh(v => !v)}
        onManualRefresh={() => { void refresh(); }}
        lastUpdatedSec={lastUpdatedSec}
        source={snapshot?.source ?? null}
        error={error}
      />

      <KPIStrip kpis={snapshot?.kpis ?? null} loading={loading} />

      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)',
          gridTemplateRows: 'minmax(0, 1fr) minmax(0, 1fr)',
          gap: 12,
          padding: '8px 16px 16px',
        }}
      >
        <div style={{ minHeight: 0, display: 'flex' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <AgentActivityStrip
              data={snapshot?.by_agent ?? []}
              loading={loading}
              onSelect={(a) => setSelectedAgent(prev => prev === a ? null : a)}
              selectedAgent={selectedAgent}
            />
          </div>
        </div>
        <div style={{ gridRow: '1 / span 2', minHeight: 0, display: 'flex' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <AlertPanel
              alerts={snapshot?.alerts ?? []}
              loading={loading}
            />
          </div>
        </div>
        <div style={{ minHeight: 0, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
          <ProjectHeatGrid
            data={snapshot?.by_project ?? []}
            loading={loading}
            onSelect={(p) => setSelectedProject(prev => prev === p ? null : p)}
            selectedProject={selectedProject}
          />
          <EventFeed
            events={snapshot?.events ?? []}
            loading={loading}
          />
        </div>
      </div>

      {(selectedAgent || selectedProject) && (
        <DrillBar
          agent={selectedAgent}
          project={selectedProject}
          onClear={() => { setSelectedAgent(null); setSelectedProject(null); }}
        />
      )}
    </div>
  );
}

interface ToolbarProps {
  onBack: () => void;
  autoRefresh: boolean;
  onToggleAuto: () => void;
  onManualRefresh: () => void;
  lastUpdatedSec: number | null;
  source: 'live' | 'fixture' | null;
  error: string | null;
}

function Toolbar({ onBack, autoRefresh, onToggleAuto, onManualRefresh, lastUpdatedSec, source, error }: ToolbarProps) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '8px 16px',
      borderBottom: `2px solid ${T.border}`,
      background: T.creamSoft,
      flexShrink: 0,
    }}>
      <button
        type="button"
        onClick={onBack}
        style={{
          padding: '4px 10px',
          borderRadius: 8,
          border: `2px solid ${T.border}`,
          background: T.bg1,
          color: T.text0,
          fontFamily: FONTS.body,
          fontSize: 11,
          fontWeight: 800,
          cursor: 'pointer',
          boxShadow: SHADOWS.sm,
        }}
      >
        ← Back
      </button>

      <div>
        <div style={{
          fontFamily: FONTS.display,
          fontSize: 18,
          fontWeight: 900,
          color: T.text0,
          letterSpacing: -0.6,
          lineHeight: 1,
        }}>
          Agent Workforce
        </div>
        <div style={{
          fontFamily: FONTS.hand,
          fontSize: 12,
          color: T.text1,
          lineHeight: 1,
        }}>
          fleet-wide NOC · O1.6
        </div>
      </div>

      <div style={{ flex: 1 }} />

      {source === 'fixture' && (
        <span title="Workforce aggregate endpoints not live — see bd-1bf1" style={{
          fontFamily: FONTS.mono,
          fontSize: 9,
          padding: '3px 8px',
          borderRadius: 999,
          background: T.tangerineSoft,
          color: T.text0,
          border: `1.5px solid ${T.border}`,
          fontWeight: 800,
          letterSpacing: 0.5,
        }}>
          FIXTURE DATA
        </span>
      )}
      {source === 'live' && (
        <span style={{
          fontFamily: FONTS.mono,
          fontSize: 9,
          padding: '3px 8px',
          borderRadius: 999,
          background: T.mintSoft,
          color: T.text0,
          border: `1.5px solid ${T.border}`,
          fontWeight: 800,
          letterSpacing: 0.5,
        }}>
          LIVE
        </span>
      )}
      {error && (
        <span title={error} style={{
          fontFamily: FONTS.mono,
          fontSize: 9,
          color: T.cherryDark,
          fontWeight: 800,
        }}>
          ERROR
        </span>
      )}

      <span style={{
        fontFamily: FONTS.mono,
        fontSize: 10,
        color: T.text2,
      }}>
        {lastUpdatedSec === null ? 'fetching…' : `last updated ${lastUpdatedSec}s ago`}
      </span>

      <button
        type="button"
        onClick={onManualRefresh}
        style={{
          padding: '4px 10px',
          borderRadius: 8,
          border: `2px solid ${T.border}`,
          background: T.bg1,
          color: T.text0,
          fontFamily: FONTS.body,
          fontSize: 11,
          fontWeight: 800,
          cursor: 'pointer',
        }}
      >
        ↻ Refresh
      </button>

      <label style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontFamily: FONTS.body,
        fontSize: 11,
        fontWeight: 700,
        color: T.text0,
        padding: '4px 10px',
        borderRadius: 8,
        border: `2px solid ${T.border}`,
        background: autoRefresh ? T.butter : T.bg1,
        cursor: 'pointer',
      }}>
        <input
          type="checkbox"
          checked={autoRefresh}
          onChange={onToggleAuto}
          style={{ margin: 0 }}
        />
        Auto · 10s
      </label>
    </div>
  );
}

interface DrillBarProps {
  agent: string | null;
  project: string | null;
  onClear: () => void;
}

function DrillBar({ agent, project, onClear }: DrillBarProps) {
  return (
    <div
      role="status"
      style={{
        position: 'absolute',
        bottom: 16,
        left: '50%',
        transform: 'translateX(-50%)',
        background: T.ink,
        color: T.cream,
        padding: '8px 14px',
        borderRadius: 10,
        boxShadow: SHADOWS.lg,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        fontFamily: FONTS.body,
        fontSize: 11,
        zIndex: 10,
      }}
    >
      <span style={{ fontWeight: 800 }}>Drill-in:</span>
      {agent && <span style={{ background: T.butter, color: T.ink, padding: '2px 8px', borderRadius: 999, fontWeight: 800 }}>agent · {agent}</span>}
      {project && <span style={{ background: T.mint, color: T.ink, padding: '2px 8px', borderRadius: 999, fontWeight: 800 }}>project · {project}</span>}
      <span style={{ color: T.butter, fontFamily: FONTS.hand, fontSize: 13 }}>
        history view coming soon
      </span>
      <button
        type="button"
        onClick={onClear}
        style={{
          background: 'transparent',
          border: `1.5px solid ${T.cream}`,
          color: T.cream,
          padding: '2px 8px',
          borderRadius: 6,
          cursor: 'pointer',
          fontSize: 10,
          fontWeight: 800,
        }}
      >
        Clear
      </button>
    </div>
  );
}
