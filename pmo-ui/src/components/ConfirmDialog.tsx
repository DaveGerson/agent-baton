import { useState } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';

interface ConfirmDialogProps {
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
  cancelLabel?: string;
}

export function ConfirmDialog({
  message,
  onConfirm,
  onCancel,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
}: ConfirmDialogProps) {
  const [cancelHover, setCancelHover] = useState(false);
  const [confirmHover, setConfirmHover] = useState(false);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Confirmation"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9998,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(42,26,16,.6)',
      }}
      onClick={onCancel}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: T.bg1,
          border: `3px solid ${T.border}`,
          borderRadius: 14,
          padding: '20px 22px',
          maxWidth: 420,
          boxShadow: SHADOWS.md,
          transform: 'rotate(-0.3deg)',
        }}
      >
        <div
          style={{
            fontFamily: FONTS.body,
            fontWeight: 600,
            fontSize: 14,
            color: T.text0,
            lineHeight: 1.55,
            marginBottom: 18,
          }}
        >
          {message}
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button
            onClick={onCancel}
            onMouseEnter={() => setCancelHover(true)}
            onMouseLeave={() => setCancelHover(false)}
            style={{
              padding: '7px 16px',
              borderRadius: 10,
              border: `2px dashed ${T.borderSoft}`,
              background: T.bg3,
              color: T.text1,
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 12,
              cursor: 'pointer',
              transform: cancelHover ? 'translate(-1px,-1px)' : 'none',
              boxShadow: cancelHover ? SHADOWS.md : 'none',
              transition: 'transform 0.1s, box-shadow 0.1s',
            }}
          >
            {cancelLabel}
          </button>

          <button
            autoFocus
            onClick={onConfirm}
            onMouseEnter={() => setConfirmHover(true)}
            onMouseLeave={() => setConfirmHover(false)}
            style={{
              padding: '7px 16px',
              borderRadius: 10,
              border: `2px solid ${T.border}`,
              background: T.cherry,
              color: T.cream,
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 12,
              cursor: 'pointer',
              boxShadow: confirmHover ? SHADOWS.md : SHADOWS.sm,
              transform: confirmHover ? 'translate(-1px,-1px)' : 'none',
              transition: 'transform 0.1s, box-shadow 0.1s',
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
