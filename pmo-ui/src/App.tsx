import { useState, useCallback, useMemo, useRef } from 'react';
import { KanbanBoard } from './components/KanbanBoard';
import { ForgePanel } from './components/ForgePanel';
import { BackOfHousePanel } from './components/BackOfHousePanel';
import { SpecsPanel } from './components/SpecsPanel';
import { KeyboardShortcutsDialog } from './components/KeyboardShortcutsDialog';
import { useHotkeys } from './hooks/useHotkeys';
import { usePersistedState } from './hooks/usePersistedState';
import { T, FONTS, SHADOWS } from './styles/tokens';
import { ToastProvider } from './contexts/ToastContext';
import type { PmoCard, PmoSignal } from './api/types';

type View = 'kanban' | 'forge' | 'boh' | 'specs';

export default function App() {
  const [view, setView] = usePersistedState<View>('pmo:active-view', 'kanban');
  const [forgeSignal, setForgeSignal] = useState<PmoSignal | null>(null);
  const [showSignals, setShowSignals] = usePersistedState('pmo:show-signals', false);
  const [showShortcuts, setShowShortcuts] = useState(false);

  // KanbanBoard registers its refresh function here so ForgePanel can trigger
  // an immediate board refresh after plan approval (PMO-UX-006).
  const boardRefreshRef = useRef<(() => void) | null>(null);
  function handleBoardRefreshReady(fn: () => void) {
    boardRefreshRef.current = fn;
  }
  function refreshBoard() {
    boardRefreshRef.current?.();
  }

  function openForge(signal?: PmoSignal) {
    setForgeSignal(signal ?? null);
    setView('forge');
  }

  function backToBoard() {
    setView('kanban');
  }

  const handleCardForge = useCallback((card: PmoCard) => {
    const signal: PmoSignal = {
      signal_id: card.card_id,
      signal_type: 'reforge',
      title: card.title,
      description: `Re-forge plan for: ${card.title} (project: ${card.project_id})`,
      severity: card.risk_level || 'medium',
      status: 'open',
      created_at: card.updated_at,
      forge_task_id: card.card_id,
      source_project_id: card.project_id,
    };
    // openForge is referenced via closure; it's a plain function but only
    // calls setState, so it's safe to exclude from deps.
  // eslint-disable-next-line react-hooks/exhaustive-deps
    openForge(signal);
  }, []);

  const toggleSignals = useCallback(() => setShowSignals(s => !s), []);
  const goForge = useCallback(() => openForge(), []); // eslint-disable-line react-hooks/exhaustive-deps
  const goKanban = useCallback(() => backToBoard(), []); // eslint-disable-line react-hooks/exhaustive-deps
  const toggleShortcuts = useCallback(() => setShowShortcuts(s => !s), []);

  const hotkeyBindings = useMemo(() => ({
    n: goForge,
    s: toggleSignals,
    escape: goKanban,
    '?': toggleShortcuts,
  }), [goForge, toggleSignals, goKanban, toggleShortcuts]);

  useHotkeys(hotkeyBindings);

  const NAV_TABS = [
    { id: 'kanban' as const, label: 'The Rail',       emoji: '🥟' },
    { id: 'forge'  as const, label: 'The Forge',      emoji: '🍳' },
    { id: 'specs'  as const, label: 'Specs',          emoji: '📋' },
    { id: 'boh'    as const, label: 'Back of House',  emoji: '🚪' },
  ];

  return (
    <ToastProvider>
    <style>{`
      @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
      @keyframes forge-bar { 0% { width: 15%; } 50% { width: 75%; } 100% { width: 95%; } }
    `}</style>
    <div style={{
      height: '100vh',
      display: 'flex',
      flexDirection: 'column',
      background: T.bg0,
      color: T.text0,
      overflow: 'hidden',
    }}>
      {/* Top nav bar — kitchen style */}
      <nav
        aria-label="Main"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '0 14px',
          borderBottom: `2px solid ${T.border}`,
          background: T.ink,
          flexShrink: 0,
          height: 46,
        }}
      >
        {/* Brand mark — pie emoji + name */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <div style={{
            width: 30, height: 30, borderRadius: '50%',
            background: T.butter, border: `2px solid ${T.cream}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 15, boxShadow: SHADOWS.sm,
          }}>🥧</div>
          <div>
            <div style={{
              fontFamily: FONTS.display,
              fontWeight: 900, fontSize: 15, letterSpacing: -0.5,
              color: T.cream, lineHeight: 1,
            }}>
              Baton PMO
            </div>
            <div style={{
              fontFamily: FONTS.hand,
              fontSize: 11, color: T.butter,
              lineHeight: 1, transform: 'rotate(-1deg)', display: 'inline-block',
            }}>
              the kitchen's open
            </div>
          </div>
        </div>

        {/* Nav tabs */}
        <div
          role="tablist"
          aria-label="Views"
          style={{ display: 'flex', gap: 4, marginLeft: 6 }}
        >
          {NAV_TABS.map(tab => {
            const active = view === tab.id;
            return (
              <button
                key={tab.id}
                role="tab"
                aria-selected={active}
                aria-controls={`panel-${tab.id}`}
                id={`tab-${tab.id}`}
                onClick={() => {
                  if (tab.id === 'kanban') backToBoard();
                  else if (tab.id === 'forge') openForge();
                  else setView(tab.id);
                }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '5px 12px',
                  borderRadius: 8,
                  border: active ? `2px solid ${T.butter}` : '2px solid transparent',
                  background: active ? T.butter : 'transparent',
                  color: active ? T.ink : T.text4,
                  fontFamily: FONTS.body,
                  fontSize: 12, fontWeight: 800,
                  cursor: 'pointer',
                  letterSpacing: '.02em',
                  transition: 'all 120ms',
                }}
              >
                <span style={{ fontSize: 13 }}>{tab.emoji}</span>
                {tab.label}
              </button>
            );
          })}
        </div>

        <div style={{ flex: 1 }} />

        {/* Keyboard hint */}
        <span style={{
          fontFamily: FONTS.mono, fontSize: 9,
          color: T.text4, letterSpacing: '.08em',
        }}>
          n=new · s=signals · esc=board · ?=help
        </span>
        <div role="separator" aria-orientation="vertical" style={{ width: 1, height: 18, background: T.inkSoft }} />
        <span style={{ fontFamily: FONTS.mono, fontSize: 9, color: T.text2 }}>
          agent-baton
        </span>
      </nav>

      {/* Main content — both views rendered simultaneously; CSS hides inactive one */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        <div
          id="panel-kanban"
          role="tabpanel"
          aria-labelledby="tab-kanban"
          aria-hidden={view !== 'kanban'}
          style={{ display: view === 'kanban' ? 'block' : 'none', height: '100%' }}
        >
          <KanbanBoard
            onNewPlan={() => openForge()}
            onSignalToForge={(sig) => openForge(sig)}
            onCardForge={handleCardForge}
            showSignals={showSignals}
            onToggleSignals={toggleSignals}
            onRefreshReady={handleBoardRefreshReady}
          />
        </div>
        <div
          id="panel-forge"
          role="tabpanel"
          aria-labelledby="tab-forge"
          aria-hidden={view !== 'forge'}
          style={{ display: view === 'forge' ? 'block' : 'none', height: '100%' }}
        >
          <ForgePanel
            onBack={backToBoard}
            initialSignal={forgeSignal}
            onApproved={refreshBoard}
          />
        </div>
        <div
          id="panel-specs"
          role="tabpanel"
          aria-labelledby="tab-specs"
          aria-hidden={view !== 'specs'}
          style={{ display: view === 'specs' ? 'block' : 'none', height: '100%' }}
        >
          <SpecsPanel onBack={backToBoard} />
        </div>
        <div
          id="panel-boh"
          role="tabpanel"
          aria-labelledby="tab-boh"
          aria-hidden={view !== 'boh'}
          style={{ display: view === 'boh' ? 'block' : 'none', height: '100%' }}
        >
          <BackOfHousePanel onBack={backToBoard} />
        </div>
      </div>
    </div>

    {showShortcuts && <KeyboardShortcutsDialog onClose={() => setShowShortcuts(false)} />}
    </ToastProvider>
  );
}
