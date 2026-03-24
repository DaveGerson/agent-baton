import { useState } from 'react';
import { KanbanBoard } from './components/KanbanBoard';
import { ForgePanel } from './components/ForgePanel';
import { T } from './styles/tokens';
import type { PmoSignal } from './api/types';

type View = 'kanban' | 'forge';

export default function App() {
  const [view, setView] = useState<View>('kanban');
  const [forgeSignal, setForgeSignal] = useState<PmoSignal | null>(null);

  function openForge(signal?: PmoSignal) {
    setForgeSignal(signal ?? null);
    setView('forge');
  }

  function backToBoard() {
    setView('kanban');
    setForgeSignal(null);
  }

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
            <div style={{ fontSize: 7, color: T.text3, letterSpacing: 0.5, textTransform: 'uppercase' }}>
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

        {/* Version / status */}
        <span style={{ fontSize: 7, color: T.text4, fontFamily: 'monospace' }}>
          agent-baton pmo
        </span>
      </div>

      {/* Main content */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        {view === 'kanban' && (
          <KanbanBoard
            onNewPlan={() => openForge()}
            onSignalToForge={(sig) => openForge(sig)}
          />
        )}
        {view === 'forge' && (
          <ForgePanel
            onBack={backToBoard}
            initialSignal={forgeSignal}
          />
        )}
      </div>
    </div>
  );
}
