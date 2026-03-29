import { T } from '../styles/tokens';

interface KeyboardShortcutsDialogProps {
  onClose: () => void;
}

const SHORTCUTS = [
  { key: 'N', description: 'Open The Forge (new plan)' },
  { key: 'S', description: 'Toggle signals panel' },
  { key: 'Esc', description: 'Return to Kanban board' },
  { key: '?', description: 'Show this help' },
];

export function KeyboardShortcutsDialog({ onClose }: KeyboardShortcutsDialogProps) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9998,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0,0,0,0.5)',
      }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: T.bg1,
          border: `1px solid ${T.border}`,
          borderRadius: 6,
          padding: '16px 20px',
          minWidth: 280,
          boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 700, color: T.text0, marginBottom: 12 }}>
          Keyboard Shortcuts
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {SHORTCUTS.map(s => (
            <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <kbd style={{
                display: 'inline-block',
                minWidth: 32,
                padding: '2px 6px',
                borderRadius: 3,
                border: `1px solid ${T.border}`,
                background: T.bg3,
                color: T.text0,
                fontSize: 10,
                fontWeight: 600,
                fontFamily: 'monospace',
                textAlign: 'center',
              }}>
                {s.key}
              </kbd>
              <span style={{ fontSize: 10, color: T.text1 }}>{s.description}</span>
            </div>
          ))}
        </div>
        <button
          onClick={onClose}
          style={{
            marginTop: 14,
            padding: '4px 12px',
            borderRadius: 4,
            border: `1px solid ${T.border}`,
            background: 'transparent',
            color: T.text2,
            fontSize: 9,
            cursor: 'pointer',
            width: '100%',
          }}
        >
          Close
        </button>
      </div>
    </div>
  );
}
