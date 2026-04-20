import { T, FONTS, SHADOWS } from '../styles/tokens';

interface KeyboardShortcutsDialogProps {
  onClose: () => void;
}

const SHORTCUTS = [
  { key: 'N', description: 'Fire up a new recipe (The Forge)' },
  { key: 'S', description: 'Open the Kitchen Radio' },
  { key: 'Esc', description: 'Back to the rail' },
  { key: '?', description: 'Show this cheat sheet' },
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
        background: 'rgba(42,26,16,.6)',
      }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: T.bg1,
          border: `3px solid ${T.border}`,
          borderRadius: 16,
          minWidth: 320,
          boxShadow: SHADOWS.lg,
          transform: 'rotate(-0.5deg)',
          overflow: 'hidden',
        }}
      >
        {/* Header band */}
        <div
          style={{
            background: T.butter,
            borderBottom: `2px solid ${T.border}`,
            padding: '12px 16px',
          }}
        >
          <span
            style={{
              fontFamily: FONTS.display,
              fontWeight: 900,
              fontSize: 20,
              color: T.ink,
              letterSpacing: '-0.3px',
            }}
          >
            📋 Kitchen Cheat Sheet
          </span>
        </div>

        {/* Shortcut rows */}
        <div
          style={{
            padding: '14px 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          {SHORTCUTS.map(s => (
            <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <kbd
                style={{
                  display: 'inline-block',
                  minWidth: 40,
                  padding: '4px 10px',
                  borderRadius: 8,
                  border: `2px solid ${T.border}`,
                  background: T.bg3,
                  color: T.ink,
                  fontSize: 11,
                  fontWeight: 700,
                  fontFamily: FONTS.mono,
                  textAlign: 'center',
                  boxShadow: SHADOWS.sm,
                }}
              >
                {s.key}
              </kbd>
              <span
                style={{
                  fontFamily: FONTS.body,
                  fontWeight: 700,
                  fontSize: 13,
                  color: T.text0,
                }}
              >
                {s.description}
              </span>
            </div>
          ))}

          <button
            onClick={onClose}
            style={{
              marginTop: 14,
              padding: '8px 0',
              borderRadius: 10,
              border: `2px solid ${T.border}`,
              background: T.cherry,
              color: T.cream,
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 13,
              cursor: 'pointer',
              width: '100%',
              boxShadow: SHADOWS.sm,
            }}
          >
            close the book
          </button>
        </div>
      </div>
    </div>
  );
}
