import { useState, useCallback } from 'react';
import { KanbanBoard } from './components/KanbanBoard';
import { ForgePanel } from './components/ForgePanel';
import { useHotkeys } from './hooks/useHotkeys';
import { T } from './styles/tokens';
import type { PmoCard, PmoSignal } from './api/types';

type View = 'kanban' | 'forge';

export default function App() {
  const [view, setView] = useState<View>('kanban');
  const [forgeSignal, setForgeSignal] = useState<PmoSignal | null>(null);
  const [showSignals, setShowSignals] = useState(false);

  function openForge(signal?: PmoSignal) {
    setForgeSignal(signal ?? null);
    setView('forge');
  }

  function backToBoard() {
    setView('kanban');
  }

  function handleCardForge(card: PmoCard) {
    const signal: PmoSignal = {
      signal_id: card.card_id,
      signal_type: 'reforge',
      title: card.title,
      description: `Re-forge plan for: ${card.title} (project: ${card.project_id})`,
      severity: card.risk_level || 'medium',
      status: 'open',
      created_at: card.updated_at,
      forge_task_id: card.card_id,
    };
    openForge(signal);
  }

  const toggleSignals = useCallback(() => setShowSignals(s => !s), []);
  const goForge = useCallback(() => openForge(), []); // eslint-disable-line react-hooks/exhaustive-deps
  const goKanban = useCallback(() => backToBoard(), []); // eslint-disable-line react-hooks/exhaustive-deps

  useHotkeys({
    n: goForge,
    s: toggleSignals,
    escape: goKanban,
  });

  return (
    <div style={{
      height: '100vh',
      display: 'flex',
      flexDirection: 'column',
      background: T.bg0,
      color: T.text0,
      overflow: 'hidden',
    }}>
      {/* Top nav bar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '6px 14px',
        borderBottom: `1px solid ${T.border}`,
        background: T.bg1,
        flexShrink: 0,
      }}>
        {/* Brand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{
            width: 22,
            height: 22,
            borderRadius: 4,
            background: 'linear-gradient(135deg, #1e40af, #7c3aed)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 10,
            fontWeight: 800,
            color: '#fff',
          }}>
            B
          </div>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: -0.3 }}>Baton PMO</div>
            <div style={{ fontSize: 9, color: T.text3, letterSpacing: 0.5, textTransform: 'uppercase' }}>
              Orchestration Board
            </div>
          </div>
        </div>

        {/* Nav tabs */}
        <div style={{ display: 'flex', gap: 2, marginLeft: 10 }}>
          {([
            { id: 'kanban' as const, label: 'AI Kanban', icon: '\u25AB' },
            { id: 'forge' as const, label: 'The Forge', icon: '\u2692' },
          ]).map(tab => (
            <button
              key={tab.id}
              onClick={() => {
                if (tab.id === 'kanban') backToBoard();
                else openForge();
              }}
              style={{
                padding: '3px 10px',
                borderRadius: 3,
                border: 'none',
                background: view === tab.id ? T.accent + '18' : 'transparent',
                color: view === tab.id ? T.accent : T.text3,
                fontSize: 9,
                fontWeight: view === tab.id ? 700 : 500,
                cursor: 'pointer',
              }}
            >
              {tab.icon} {tab.label}
            </button>
          ))}
        </div>

        <div style={{ flex: 1 }} />

        {/* Keyboard hint */}
        <span style={{ fontSize: 9, color: T.text4, fontFamily: 'monospace' }}>
          n=new&nbsp;&nbsp;s=signals&nbsp;&nbsp;esc=board
        </span>
        <div style={{ width: 1, height: 14, background: T.border }} />

        {/* Version / status */}
        <span style={{ fontSize: 9, color: T.text4, fontFamily: 'monospace' }}>
          agent-baton pmo
        </span>
      </div>

      {/* Main content — both views rendered simultaneously; CSS hides inactive one */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        <div style={{ display: view === 'kanban' ? 'block' : 'none', height: '100%' }}>
          <KanbanBoard
            onNewPlan={() => openForge()}
            onSignalToForge={(sig) => openForge(sig)}
            onCardForge={handleCardForge}
            showSignals={showSignals}
            onToggleSignals={toggleSignals}
          />
        </div>
        <div style={{ display: view === 'forge' ? 'block' : 'none', height: '100%' }}>
          <ForgePanel
            onBack={backToBoard}
            initialSignal={forgeSignal}
          />
        </div>
      </div>
    </div>
  );
}
