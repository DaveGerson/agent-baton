import { useState, useEffect } from 'react';
import { KanbanCard } from './KanbanCard';
import { HealthBar } from './HealthBar';
import { SignalsBar } from './SignalsBar';
import { usePmoBoard } from '../hooks/usePmoBoard';
import type { ConnectionMode } from '../hooks/usePmoBoard';
import { usePersistedState } from '../hooks/usePersistedState';
import { T, COLUMNS, SR_ONLY } from '../styles/tokens';
import { api } from '../api/client';
import type { PmoCard, PmoSignal } from '../api/types';

interface KanbanBoardProps {
  onNewPlan: () => void;
  onSignalToForge: (signal: PmoSignal) => void;
  onCardForge: (card: PmoCard) => void;
  showSignals: boolean;
  onToggleSignals: () => void;
}

export function KanbanBoard({ onNewPlan, onSignalToForge, onCardForge, showSignals, onToggleSignals }: KanbanBoardProps) {
  const { cards, health, loading, error, lastUpdated, connectionMode, mutateCard } = usePmoBoard();
  const [filter, setFilter] = usePersistedState<string>('pmo:board-filter', 'all');
  const [search, setSearch] = usePersistedState<string>('pmo:board-search', '');
  const [sortBy, setSortBy] = usePersistedState<string>('pmo:board-sort', 'priority');
  const [openSignalCount, setOpenSignalCount] = useState(0);

  // Keep signal badge current regardless of whether SignalsBar is mounted.
  useEffect(() => {
    function fetchCount() {
      api.getSignals()
        .then(signals => setOpenSignalCount(signals.filter(s => s.status !== 'resolved').length))
        .catch(() => {});
    }
    fetchCount();
    const id = setInterval(fetchCount, 30_000);
    return () => clearInterval(id);
  }, []);

  const programs = Array.from(new Set(cards.map(c => c.program))).sort();

  const filtered = cards
    .filter(c => filter === 'all' || c.program.toUpperCase() === filter.toUpperCase())
    .filter(c => {
      if (!search.trim()) return true;
      const q = search.toLowerCase();
      return c.title.toLowerCase().includes(q)
        || c.project_id.toLowerCase().includes(q)
        || (c.external_id ?? '').toLowerCase().includes(q)
        || (c.current_phase ?? '').toLowerCase().includes(q);
    });

  const sorted = [...filtered].sort((a, b) => {
    switch (sortBy) {
      case 'priority': return b.priority - a.priority;
      case 'updated': return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
      case 'risk': {
        const riskOrder: Record<string, number> = { high: 3, medium: 2, low: 1 };
        return (riskOrder[b.risk_level ?? 'low'] ?? 0) - (riskOrder[a.risk_level ?? 'low'] ?? 0);
      }
      case 'progress': return (b.steps_completed / Math.max(b.steps_total, 1)) - (a.steps_completed / Math.max(a.steps_total, 1));
      default: return 0;
    }
  });

  const awaitingHuman = cards.filter(c => c.column === 'awaiting_human').length;
  const executing = cards.filter(c => c.column === 'executing').length;

  function handleProgramClick(program: string) {
    setFilter(prev => prev === program ? 'all' : program);
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <HealthBar
        health={health}
        activeProgram={filter === 'all' ? null : filter}
        onProgramClick={handleProgramClick}
      />

      {/* Toolbar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '6px 14px',
        borderBottom: `1px solid ${T.border}`,
        background: T.bg1,
        flexShrink: 0,
      }}>
        {/* Program filters */}
        <div style={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
          <FilterBtn
            active={filter === 'all'}
            color={T.accent}
            onClick={() => setFilter('all')}
          >
            All
          </FilterBtn>
          {programs.map(p => (
            <FilterBtn
              key={p}
              active={filter === p}
              color={T.accent}
              onClick={() => setFilter(p)}
            >
              {p}
            </FilterBtn>
          ))}
        </div>

        <input
          type="search"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search cards..."
          aria-label="Search cards"
          style={{
            fontSize: 9,
            padding: '3px 8px',
            borderRadius: 3,
            border: `1px solid ${T.border}`,
            background: T.bg1,
            color: T.text0,
            outline: 'none',
            width: 140,
          }}
        />

        <select
          value={sortBy}
          onChange={e => setSortBy(e.target.value)}
          aria-label="Sort cards"
          style={{
            fontSize: 9,
            padding: '3px 6px',
            borderRadius: 3,
            border: `1px solid ${T.border}`,
            background: T.bg1,
            color: T.text0,
          }}
        >
          <option value="priority">Priority</option>
          <option value="updated">Last Updated</option>
          <option value="risk">Risk</option>
          <option value="progress">Progress</option>
        </select>

        <div role="separator" aria-orientation="vertical" style={{ width: 1, height: 14, background: T.border }} />

        {/* Signals toggle */}
        <>
          <button
            onClick={onToggleSignals}
            aria-pressed={showSignals}
            style={{
              padding: '2px 7px',
              borderRadius: 3,
              border: `1px solid ${showSignals ? T.red + '66' : T.border}`,
              background: showSignals ? T.red + '15' : 'transparent',
              color: showSignals ? T.red : T.text3,
              fontSize: 9,
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            Signals
            {openSignalCount > 0 && (
              <span
                aria-hidden="true"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  minWidth: 14,
                  height: 14,
                  borderRadius: 7,
                  background: T.red,
                  color: '#fff',
                  fontSize: 9,
                  fontWeight: 700,
                  padding: '0 3px',
                }}
              >
                {openSignalCount}
              </span>
            )}
          </button>
          <span aria-live="polite" aria-atomic="true" style={SR_ONLY}>
            {openSignalCount > 0 ? `${openSignalCount} open signals` : 'No open signals'}
          </span>
        </>

        <div style={{ flex: 1 }} />

        {/* Status indicators */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 9 }}>
          {awaitingHuman > 0 && (
            <div
              role="status"
              aria-label={`${awaitingHuman} task${awaitingHuman !== 1 ? 's' : ''} awaiting human input`}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 3,
                padding: '2px 6px',
                borderRadius: 3,
                background: T.orange + '15',
                border: `1px solid ${T.orange}33`,
              }}
            >
              <div
                aria-hidden="true"
                style={{
                  width: 5,
                  height: 5,
                  borderRadius: '50%',
                  background: T.orange,
                  animation: 'pulse 1.5s infinite',
                }}
              />
              <span aria-hidden="true" style={{ color: T.orange, fontWeight: 600 }}>{awaitingHuman} awaiting</span>
            </div>
          )}
          <span style={{ color: T.text3 }}>
            {executing > 0 && `${executing} executing · `}{filtered.length} plans
          </span>
          {lastUpdated && (
            <span style={{ color: T.text4, fontSize: 9 }}>
              {fmtTime(lastUpdated.toISOString())}
            </span>
          )}
          <span
            role="status"
            aria-live="polite"
            aria-atomic="true"
            style={{ color: T.text3, fontSize: 9 }}
          >
            {loading ? 'Refreshing board data…' : ''}
          </span>
          <ConnectionIndicator mode={connectionMode} />
        </div>

        <button
          onClick={onNewPlan}
          style={{
            padding: '3px 11px',
            borderRadius: 3,
            border: 'none',
            background: `linear-gradient(135deg, ${T.accent}, #2563eb)`,
            color: '#fff',
            fontSize: 9,
            fontWeight: 700,
            cursor: 'pointer',
          }}
        >
          + New Plan
        </button>
      </div>

      {/* Signals panel */}
      {showSignals && (
        <SignalsBar
          onForge={(signal) => {
            onToggleSignals();
            onSignalToForge(signal);
          }}
          onOpenCountChange={setOpenSignalCount}
        />
      )}

      {/* Error banner */}
      <div role="alert" aria-live="assertive" aria-atomic="true">
        {error && (
          <div style={{
            padding: '5px 14px',
            background: T.red + '15',
            borderBottom: `1px solid ${T.red}33`,
            fontSize: 9,
            color: T.red,
          }}>
            {error} — retrying every {connectionMode === 'sse' ? '15' : '5'}s. Check that the backend is running (baton pmo serve).
          </div>
        )}
      </div>

      {/* Kanban columns */}
      <div style={{ flex: 1, display: 'flex', overflow: 'auto', padding: '10px 6px' }}>
        {COLUMNS.map(col => {
          const colCards = sorted.filter(c => c.column === col.id);
          return (
            <section
              key={col.id}
              aria-labelledby={`col-${col.id}-heading`}
              style={{
                flex: 1,
                minWidth: 170,
                maxWidth: 240,
                display: 'flex',
                flexDirection: 'column',
                margin: '0 3px',
              }}
            >
              {/* Column header */}
              <div style={{
                padding: '5px 8px',
                marginBottom: 5,
                borderRadius: 4,
                background: T.bg2,
                borderBottom: `2px solid ${col.color}30`,
                flexShrink: 0,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <div aria-hidden="true" style={{ width: 6, height: 6, borderRadius: 2, background: col.color }} />
                  <h2
                    id={`col-${col.id}-heading`}
                    style={{ fontSize: 11, fontWeight: 700, color: T.text0, flex: 1, margin: 0 }}
                  >
                    {col.label}
                  </h2>
                  <span style={{
                    fontSize: 9,
                    fontWeight: 700,
                    color: T.text3,
                    background: T.bg3,
                    padding: '1px 4px',
                    borderRadius: 3,
                  }}>
                    {colCards.length}
                  </span>
                </div>
                <div style={{ fontSize: 9, color: T.text3, marginTop: 1 }}>{col.desc}</div>
              </div>

              {/* Cards */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4, overflowY: 'auto', paddingBottom: 16 }}>
                {colCards.map(card => (
                  <KanbanCard key={card.card_id} card={card} columnColor={col.color} onForge={onCardForge} onEditPlan={onCardForge} onMutateCard={mutateCard} />
                ))}
                {colCards.length === 0 && (
                  <div style={{
                    padding: '14px 8px',
                    textAlign: 'center',
                    fontSize: 9,
                    color: T.text4,
                    fontStyle: 'italic',
                    border: `1px dashed ${T.border}`,
                    borderRadius: 4,
                    lineHeight: 1.4,
                  }}>
                    {columnEmptyText(col.id)}
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function columnEmptyText(colId: string): string {
  switch (colId) {
    case 'queued': return 'No plans ready to execute. Create one in The Forge.';
    case 'executing': return 'No active executions.';
    case 'awaiting_human': return 'No decisions required.';
    case 'validating': return 'No plans under validation.';
    case 'deployed': return 'No completed plans yet.';
    default: return 'Empty';
  }
}

function FilterBtn({
  children,
  active,
  color,
  onClick,
}: {
  children: React.ReactNode;
  active: boolean;
  color: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '2px 7px',
        borderRadius: 3,
        border: `1px solid ${active ? color + '66' : T.border}`,
        background: active ? color + '15' : 'transparent',
        color: active ? color : T.text3,
        fontSize: 9,
        fontWeight: 600,
        cursor: 'pointer',
      }}
    >
      {children}
    </button>
  );
}

function ConnectionIndicator({ mode }: { mode: ConnectionMode }) {
  const isLive = mode === 'sse';
  const isConnecting = mode === 'connecting';

  const dotColor = isLive ? T.green : isConnecting ? T.yellow : T.text3;
  const label = isLive ? 'Live' : isConnecting ? 'Connecting' : 'Reconnecting';
  const title = isLive
    ? 'Real-time updates via SSE'
    : isConnecting
    ? 'Establishing SSE connection…'
    : 'SSE unavailable — polling for updates';

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={`Connection: ${title}`}
      title={title}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 3,
        padding: '2px 5px',
        borderRadius: 3,
        border: `1px solid ${dotColor}33`,
        background: dotColor + '10',
      }}
    >
      <div
        aria-hidden="true"
        style={{
          width: 5,
          height: 5,
          borderRadius: '50%',
          background: dotColor,
          animation: isLive ? 'none' : isConnecting ? 'pulse 1.5s infinite' : 'none',
          flexShrink: 0,
        }}
      />
      <span aria-hidden="true" style={{ fontSize: 9, color: dotColor, fontWeight: 600 }}>{label}</span>
    </div>
  );
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '—';
  }
}
