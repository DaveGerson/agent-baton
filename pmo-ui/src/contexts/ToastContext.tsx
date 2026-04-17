import { createContext, useContext, useState, useCallback, useRef } from 'react';
import { T } from '../styles/tokens';

type ToastType = 'success' | 'error' | 'info';

interface Toast {
  id: number;
  type: ToastType;
  message: string;
}

interface ToastContextValue {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const TOAST_COLORS: Record<ToastType, string> = {
  success: T.green,
  error: T.red,
  info: T.accent,
};

const MAX_TOASTS = 5;
const AUTO_DISMISS_MS = 5000;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);
  const timersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
  }, []);

  const addToast = useCallback((type: ToastType, message: string) => {
    const id = ++idRef.current;
    setToasts(prev => {
      const next = [...prev, { id, type, message }];
      return next.length > MAX_TOASTS ? next.slice(-MAX_TOASTS) : next;
    });
    const timer = setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    timersRef.current.set(id, timer);
  }, [dismiss]);

  const ctx: ToastContextValue = {
    success: useCallback((msg: string) => addToast('success', msg), [addToast]),
    error: useCallback((msg: string) => addToast('error', msg), [addToast]),
    info: useCallback((msg: string) => addToast('info', msg), [addToast]),
  };

  return (
    <ToastContext.Provider value={ctx}>
      {children}
      {toasts.length > 0 && (
        <div
          aria-live="polite"
          role="status"
          style={{
            position: 'fixed',
            bottom: 16,
            right: 16,
            zIndex: 9999,
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
            maxWidth: 340,
          }}
        >
          {toasts.map(toast => {
            const color = TOAST_COLORS[toast.type];
            return (
              <div
                key={toast.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '8px 12px',
                  background: T.bg1,
                  borderRadius: 4,
                  border: `1px solid ${T.border}`,
                  borderLeft: `3px solid ${color}`,
                  boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
                }}
              >
                <span style={{ fontSize: 10, color: T.text0, flex: 1 }}>{toast.message}</span>
                <button
                  aria-label="Dismiss notification"
                  onClick={() => dismiss(toast.id)}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: T.text3,
                    fontSize: 12,
                    cursor: 'pointer',
                    padding: '0 2px',
                    lineHeight: 1,
                    flexShrink: 0,
                  }}
                >
                  ×
                </button>
              </div>
            );
          })}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}
