import { useState, useEffect, useLayoutEffect } from 'react';
import type { ReactNode } from 'react';
import { KanbanCard } from './KanbanCard';
import { HealthBar } from './HealthBar';
import { SignalsBar } from './SignalsBar';
import { AnalyticsDashboard } from './AnalyticsDashboard';
import { DataExport } from './DataExport';
import { ExternalItemsPanel } from './ExternalItemsPanel';
import { usePmoBoard } from '../hooks/usePmoBoard';
import type { ConnectionMode } from '../hooks/usePmoBoard';
import { usePersistedState } from '../hooks/usePersistedState';
import { T, COLUMNS, SR_ONLY, FONTS, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import type { PmoCard, PmoSignal } from '../api/types';

interface KanbanBoardProps {
  onNewPlan: () => void;
  onSignalToForge: (signal: PmoSignal) => void;
  onCardForge: (card: PmoCard) => void;
  showSignals: boolean;
  onToggleSignals: () => void;
  /** Called once on mount with the board's refresh function so parent can trigger it. */
  onRefreshReady?: (refresh: () => void) => void;
}

export function KanbanBoard({ onNewPlan, onSignalToForge, onCardForge, showSignals, onToggleSignals, onRefreshReady }: KanbanBoardProps) {
  const { cards, health, loading, error, lastUpdated, connectionMode, mutateCard, refresh } = usePmoBoard();

  // Register the refresh callback with the parent once on mount.
  useLayoutEffect(() => {
    onRefreshReady?.(refresh);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [filter, setFilter] = usePersistedState<string>('pmo:board-filter', 'all');
  const [search, setSearch] = usePersistedState<string>('pmo:board-search', '');
  const [sortBy, setSortBy] = usePersistedState<string>('pmo:board-sort', 'priority');
  const [riskFilter, setRiskFilter] = usePersistedState<string>('pmo:board-risk', 'all');
  const [agentFilter, setAgentFilter] = usePersistedState<string>('pmo:board-agent', '');
  const [dateFrom, setDateFrom] = usePersistedState<string>('pmo:board-date-from', '');
  const [dateTo, setDateTo] = usePersistedState<string>('pmo:board-date-to', '');
  const [showAdvancedFilters, setShowAdvancedFilters] = usePersistedState<boolean>('pmo:board-adv-filters', false);
  const [openSignalCount, setOpenSignalCount] = useState(0);
  const [showAnalytics, setShowAnalytics] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [showExternalItems, setShowExternalItems] = useState(false);

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

  // Collect all agent names across all cards for the agent dropdown.
  const allAgents = Array.from(
    new Set(cards.flatMap(c => c.agents ?? []).filter(Boolean))
  ).sort();

  const hasAdvancedFilters =
    riskFilter !== 'all' || agentFilter !== '' || dateFrom !== '' || dateTo !== '';

  const filtered = cards
    .filter(c => filter === 'all' || c.program.toUpperCase() === filter.toUpperCase())
    .filter(c => {
      if (!search.trim()) return true;
      const q = search.toLowerCase();
      return c.title.toLowerCase().includes(q)
        || c.project_id.toLowerCase().includes(q)
        || (c.external_id ?? '').toLowerCase().includes(q)
        || (c.current_phase ?? '').toLowerCase().includes(q);
    })
    .filter(c => {
      if (riskFilter === 'all') return true;
      return (c.risk_level ?? '').toLowerCase() === riskFilter.toLowerCase();
    })
    .filter(c => {
      if (!agentFilter) return true;
      return (c.agents ?? []).some(a => a.toLowerCase() === agentFilter.toLowerCase());
    })
    .filter(c => {
      if (!dateFrom && !dateTo) return true;
      const created = new Date(c.created_at).getTime();
      if (dateFrom && created < new Date(dateFrom).getTime()) return false;
      // dateTo is an inclusive date — advance to end-of-day in local time.
      if (dateTo) {
        const end = new Date(dateTo);
        end.setHours(23, 59, 59, 999);
        if (created > end.getTime()) return false;
      }
      return true;
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
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: T.bg0, fontFamily: FONTS.body }}>
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
        padding: '8px 14px',
        borderBottom: `2px solid ${T.border}`,
        background: T.bg1,
        flexShrink: 0,
      }}>
        {/* Program filters */}
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
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
            fontSize: 11,
            padding: '4px 10px',
            borderRadius: 8,
            border: `2px solid ${T.border}`,
            background: T.bg3,
            color: T.text0,
            outline: 'none',
            width: 140,
            fontFamily: FONTS.body,
          }}
        />

        <select
          value={sortBy}
          onChange={e => setSortBy(e.target.value)}
          aria-label="Sort cards"
          style={{
            fontSize: 11,
            padding: '4px 6px',
            borderRadius: 8,
            border: `2px solid ${T.border}`,
            background: T.bg3,
            color: T.text0,
            fontFamily: FONTS.body,
          }}
        >
          <option value="priority">Priority</option>
          <option value="updated">Last Updated</option>
          <option value="risk">Risk</option>
          <option value="progress">Progress</option>
        </select>

        <div role="separator" aria-orientation="vertical" style={{ width: 1, height: 14, background: T.border }} />

        {/* Advanced filters toggle */}
        <button
          onClick={() => setShowAdvancedFilters(v => !v)}
          aria-pressed={showAdvancedFilters}
          aria-expanded={showAdvancedFilters}
          aria-controls="advanced-filter-bar"
          style={{
            padding: '3px 10px',
            borderRadius: 999,
            border: `1.5px solid ${T.border}`,
            background: (showAdvancedFilters || hasAdvancedFilters) ? T.butter : 'transparent',
            color: T.text0,
            fontSize: 11,
            fontWeight: 800,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            fontFamily: FONTS.body,
            boxShadow: SHADOWS.sm,
          }}
        >
          Filters
          {hasAdvancedFilters && (
            <span
              aria-hidden="true"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                minWidth: 16,
                height: 16,
                borderRadius: 8,
                background: T.cherry,
                color: T.cream,
                fontSize: 9,
                fontWeight: 700,
                padding: '0 3px',
              }}
            >
              {[riskFilter !== 'all', agentFilter !== '', dateFrom !== '' || dateTo !== ''].filter(Boolean).length}
            </span>
          )}
        </button>

        <div role="separator" aria-orientation="vertical" style={{ width: 1, height: 14, background: T.border }} />

        {/* Signals toggle */}
        <>
          <button
            onClick={onToggleSignals}
            aria-pressed={showSignals}
            style={{
              padding: '3px 10px',
              borderRadius: 999,
              border: `1.5px solid ${T.border}`,
              background: showSignals ? T.cherry : 'transparent',
              color: showSignals ? T.cream : T.text0,
              fontSize: 11,
              fontWeight: 800,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              fontFamily: FONTS.body,
              boxShadow: SHADOWS.sm,
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
                  minWidth: 16,
                  height: 16,
                  borderRadius: 8,
                  background: T.cherry,
                  color: T.cream,
                  fontSize: 9,
                  fontWeight: 700,
                  padding: '0 3px',
                  border: `1px solid ${T.border}`,
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

        {/* Analytics & Export */}
        <button
          onClick={() => setShowAnalytics(true)}
          style={{
            padding: '3px 10px',
            borderRadius: 10,
            border: `2px dashed ${T.border}`,
            background: 'transparent',
            color: T.text1,
            fontSize: 11,
            fontWeight: 800,
            cursor: 'pointer',
            fontFamily: FONTS.body,
          }}
        >
          Analytics
        </button>
        <button
          onClick={() => setShowExport(true)}
          style={{
            padding: '3px 10px',
            borderRadius: 10,
            border: `2px dashed ${T.border}`,
            background: 'transparent',
            color: T.text1,
            fontSize: 11,
            fontWeight: 800,
            cursor: 'pointer',
            fontFamily: FONTS.body,
          }}
        >
          Export
        </button>
        <button
          onClick={() => setShowExternalItems(true)}
          style={{
            padding: '3px 10px',
            borderRadius: 10,
            border: `2px dashed ${T.border}`,
            background: 'transparent',
            color: T.text1,
            fontSize: 11,
            fontWeight: 800,
            cursor: 'pointer',
            fontFamily: FONTS.body,
          }}
          title="View linked ADO, GitHub, Jira, and Linear items"
        >
          Linked Items
        </button>

        <div style={{ flex: 1 }} />

        {/* Status indicators */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, fontFamily: FONTS.body }}>
          {awaitingHuman > 0 && (
            <div
              role="status"
              aria-label={`${awaitingHuman} task${awaitingHuman !== 1 ? 's' : ''} awaiting human input`}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 3,
                padding: '2px 8px',
                borderRadius: 999,
                background: T.tangerineSoft,
                border: `1.5px solid ${T.border}`,
                boxShadow: SHADOWS.sm,
              }}
            >
              <div
                aria-hidden="true"
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: T.tangerine,
                  animation: 'pulse 1.5s infinite',
                }}
              />
              <span aria-hidden="true" style={{ color: T.text0, fontWeight: 700, fontSize: 11 }}>{awaitingHuman} awaiting</span>
            </div>
          )}
          {executing > 0 && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 3,
                padding: '2px 8px',
                borderRadius: 999,
                background: T.butterSoft,
                border: `1.5px solid ${T.border}`,
                boxShadow: SHADOWS.sm,
              }}
            >
              <span style={{ color: T.text0, fontWeight: 700, fontSize: 11 }}>{executing} baking</span>
            </div>
          )}
          <span style={{ color: T.text2, fontSize: 11 }}>
            {filtered.length < cards.length
              ? <><span style={{ color: T.cherry, fontWeight: 700 }}>{filtered.length}</span>{` / ${cards.length} plans`}</>
              : `${cards.length} plans`}
          </span>
          {lastUpdated && (
            <span style={{ color: T.text4, fontSize: 10, fontFamily: FONTS.mono }}>
              {fmtTime(lastUpdated.toISOString())}
            </span>
          )}
          <span
            role="status"
            aria-live="polite"
            aria-atomic="true"
            style={{ color: T.text3, fontSize: 10, fontFamily: FONTS.mono }}
          >
            {loading ? 'Refreshing board data…' : ''}
          </span>
          <ConnectionIndicator mode={connectionMode} />
        </div>

        <button
          onClick={onNewPlan}
          style={{
            padding: '5px 14px',
            borderRadius: 10,
            border: `2px solid ${T.border}`,
            background: T.cherry,
            color: T.cream,
            fontSize: 12,
            fontWeight: 800,
            cursor: 'pointer',
            fontFamily: FONTS.body,
            boxShadow: SHADOWS.sm,
          }}
        >
          + New Plan
        </button>
      </div>

      {/* Advanced filter bar */}
      {showAdvancedFilters && (
        <AdvancedFilterBar
          allAgents={allAgents}
          riskFilter={riskFilter}
          agentFilter={agentFilter}
          dateFrom={dateFrom}
          dateTo={dateTo}
          onRiskChange={setRiskFilter}
          onAgentChange={setAgentFilter}
          onDateFromChange={setDateFrom}
          onDateToChange={setDateTo}
          onClear={() => {
            setRiskFilter('all');
            setAgentFilter('');
            setDateFrom('');
            setDateTo('');
          }}
        />
      )}

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
            padding: '6px 14px',
            background: T.cherrySoft,
            borderBottom: `2px solid ${T.border}`,
            fontSize: 11,
            color: T.cherry,
            fontFamily: FONTS.body,
            fontWeight: 700,
          }}>
            {error} — retrying every {connectionMode === 'sse' ? '15' : '5'}s. Check that the backend is running (baton pmo serve).
          </div>
        )}
      </div>

      {/* Kanban columns */}
      <div style={{
        flex: 1,
        display: 'flex',
        overflow: 'auto',
        padding: '12px 8px',
        backgroundImage: `radial-gradient(${T.crust} 1.2px, transparent 1.4px)`,
        backgroundSize: '14px 14px',
        backgroundColor: T.bg0,
      }}>
        {COLUMNS.map((col) => {
          const colCards = sorted.filter(c => c.column === col.id);
          const isBlueberry = col.id === 'validating';
          const headerTextColor = isBlueberry ? T.cream : T.ink;
          const colEmoji = colStationEmoji(col.id);
          return (
            <section
              key={col.id}
              aria-labelledby={`col-${col.id}-heading`}
              style={{
                flex: 1,
                minWidth: 175,
                maxWidth: 245,
                display: 'flex',
                flexDirection: 'column',
                margin: '0 4px',
                background: T.bg1,
                borderRadius: 8,
                border: `1.5px solid ${T.borderSoft}`,
                overflow: 'hidden',
              }}
            >
              {/* Column header */}
              <div style={{
                height: 52,
                padding: '0 10px',
                background: col.color,
                borderBottom: `2px solid ${T.border}`,
                flexShrink: 0,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center',
                gap: 2,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span aria-hidden="true" style={{ fontSize: 16 }}>{colEmoji}</span>
                  <h2
                    id={`col-${col.id}-heading`}
                    style={{
                      fontSize: 18,
                      fontWeight: 900,
                      color: headerTextColor,
                      flex: 1,
                      margin: 0,
                      fontFamily: FONTS.display,
                      lineHeight: 1,
                    }}
                  >
                    {col.label}
                  </h2>
                  <span style={{
                    fontSize: 11,
                    fontWeight: 700,
                    color: T.ink,
                    background: T.creamSoft,
                    padding: '1px 6px',
                    borderRadius: 999,
                    border: `1.5px solid ${T.border}`,
                    fontFamily: FONTS.mono,
                  }}>
                    {colCards.length}
                  </span>
                </div>
                <div style={{
                  fontSize: 13,
                  color: headerTextColor,
                  fontFamily: FONTS.hand,
                  fontStyle: 'italic',
                  transform: 'rotate(-0.5deg)',
                  opacity: 0.85,
                  lineHeight: 1,
                }}>{col.desc}</div>
              </div>

              {/* Cards */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4, overflowY: 'auto', padding: '8px 6px 16px' }}>
                {colCards.map(card => (
                  <KanbanCard key={card.card_id} card={card} columnColor={col.color} onForge={onCardForge} onEditPlan={onCardForge} onMutateCard={mutateCard} />
                ))}
                {colCards.length === 0 && (
                  <div style={{
                    padding: '20px 8px',
                    textAlign: 'center',
                    fontSize: 18,
                    color: T.text2,
                    fontFamily: FONTS.hand,
                    lineHeight: 1.4,
                  }}>
                    nothin' cookin' 😴
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>

      {/* Modals */}
      {showAnalytics && (
        <AnalyticsDashboard cards={cards} health={health} onClose={() => setShowAnalytics(false)} />
      )}
      {showExport && (
        <DataExport cards={cards} health={health} filteredCards={filtered} onClose={() => setShowExport(false)} />
      )}
      {showExternalItems && (
        <ExternalItemsPanel onClose={() => setShowExternalItems(false)} />
      )}
    </div>
  );
}

function colStationEmoji(colId: string): string {
  switch (colId) {
    case 'queued': return '🥟';
    case 'executing': return '🔥';
    case 'awaiting_human': return '🛎';
    case 'validating': return '👅';
    case 'deployed': return '🍽';
    default: return '🍴';
  }
}

function FilterBtn({
  children,
  active,
  color,
  onClick,
}: {
  children: ReactNode;
  active: boolean;
  color: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '3px 10px',
        borderRadius: 999,
        border: `1.5px solid ${T.border}`,
        background: active ? color : 'transparent',
        color: active ? T.cream : T.text0,
        fontSize: 11,
        fontWeight: 800,
        cursor: 'pointer',
        fontFamily: FONTS.body,
        boxShadow: active ? SHADOWS.sm : undefined,
      }}
    >
      {children}
    </button>
  );
}

function ConnectionIndicator({ mode }: { mode: ConnectionMode }) {
  const isLive = mode === 'sse';
  const isConnecting = mode === 'connecting';

  const dotColor = isLive ? T.mint : isConnecting ? T.butter : T.text3;
  const label = isLive ? 'Live' : isConnecting ? 'Connecting' : 'Polling';
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
        gap: 4,
        padding: '2px 7px',
        borderRadius: 999,
        border: `1.5px solid ${T.border}`,
        background: T.bg3,
      }}
    >
      <div
        aria-hidden="true"
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: dotColor,
          animation: isLive ? 'none' : isConnecting ? 'pulse 1.5s infinite' : 'none',
          flexShrink: 0,
        }}
      />
      <span aria-hidden="true" style={{ fontSize: 10, color: T.text1, fontWeight: 700, fontFamily: FONTS.mono }}>{label}</span>
    </div>
  );
}

const RISK_LEVELS = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const;
const RISK_COLORS: Record<string, string> = {
  critical: T.red,
  high: T.red,
  medium: T.yellow,
  low: T.text2,
};

interface AdvancedFilterBarProps {
  allAgents: string[];
  riskFilter: string;
  agentFilter: string;
  dateFrom: string;
  dateTo: string;
  onRiskChange: (v: string) => void;
  onAgentChange: (v: string) => void;
  onDateFromChange: (v: string) => void;
  onDateToChange: (v: string) => void;
  onClear: () => void;
}

function AdvancedFilterBar({
  allAgents,
  riskFilter,
  agentFilter,
  dateFrom,
  dateTo,
  onRiskChange,
  onAgentChange,
  onDateFromChange,
  onDateToChange,
  onClear,
}: AdvancedFilterBarProps) {
  const hasAny = riskFilter !== 'all' || agentFilter !== '' || dateFrom !== '' || dateTo !== '';

  const inputStyle = {
    fontSize: 11,
    padding: '4px 8px',
    borderRadius: 8,
    border: `2px solid ${T.border}`,
    background: T.bg3,
    color: T.text0,
    outline: 'none',
    fontFamily: FONTS.body,
  } as const;

  const labelStyle = {
    fontSize: 11,
    color: T.text1,
    fontWeight: 700,
    whiteSpace: 'nowrap' as const,
    fontFamily: FONTS.body,
  };

  return (
    <div
      id="advanced-filter-bar"
      role="search"
      aria-label="Advanced card filters"
      style={{
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 8,
        padding: '8px 14px',
        borderBottom: `2px solid ${T.border}`,
        background: T.bg1,
        flexShrink: 0,
      }}
    >
      {/* Risk level */}
      <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={labelStyle}>Risk</span>
        <select
          value={riskFilter}
          onChange={e => onRiskChange(e.target.value)}
          aria-label="Filter by risk level"
          style={inputStyle}
        >
          <option value="all">All levels</option>
          {RISK_LEVELS.map(level => (
            <option key={level} value={level.toLowerCase()}>
              {level}
            </option>
          ))}
        </select>
        {riskFilter !== 'all' && (
          <span
            aria-hidden="true"
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: RISK_COLORS[riskFilter] ?? T.text2,
              flexShrink: 0,
            }}
          />
        )}
      </label>

      <div role="separator" aria-orientation="vertical" style={{ width: 1, height: 14, background: T.border }} />

      {/* Agent */}
      <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={labelStyle}>Agent</span>
        {allAgents.length > 0 ? (
          <select
            value={agentFilter}
            onChange={e => onAgentChange(e.target.value)}
            aria-label="Filter by agent name"
            style={{ ...inputStyle, maxWidth: 160 }}
          >
            <option value="">All agents</option>
            {allAgents.map(a => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        ) : (
          <input
            type="text"
            value={agentFilter}
            onChange={e => onAgentChange(e.target.value)}
            placeholder="Agent name…"
            aria-label="Filter by agent name"
            style={{ ...inputStyle, width: 120 }}
          />
        )}
      </label>

      <div role="separator" aria-orientation="vertical" style={{ width: 1, height: 14, background: T.border }} />

      {/* Date range */}
      <fieldset
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          border: 'none',
          margin: 0,
          padding: 0,
        }}
      >
        <legend style={{ ...labelStyle, float: 'left', marginRight: 4 }}>Created</legend>
        <label style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
          <span style={{ fontSize: 9, color: T.text4 }}>from</span>
          <input
            type="date"
            value={dateFrom}
            onChange={e => onDateFromChange(e.target.value)}
            max={dateTo || undefined}
            aria-label="Created from date"
            style={{ ...inputStyle, colorScheme: 'light' }}
          />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
          <span style={{ fontSize: 9, color: T.text4 }}>to</span>
          <input
            type="date"
            value={dateTo}
            onChange={e => onDateToChange(e.target.value)}
            min={dateFrom || undefined}
            aria-label="Created to date"
            style={{ ...inputStyle, colorScheme: 'light' }}
          />
        </label>
      </fieldset>

      {/* Clear */}
      {hasAny && (
        <>
          <div role="separator" aria-orientation="vertical" style={{ width: 1, height: 14, background: T.border }} />
          <button
            onClick={onClear}
            aria-label="Clear all advanced filters"
            style={{
              padding: '3px 10px',
              borderRadius: 999,
              border: `1.5px solid ${T.border}`,
              background: 'transparent',
              color: T.text1,
              fontSize: 11,
              fontWeight: 800,
              cursor: 'pointer',
              fontFamily: FONTS.body,
              boxShadow: SHADOWS.sm,
            }}
          >
            Clear filters
          </button>
        </>
      )}
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
