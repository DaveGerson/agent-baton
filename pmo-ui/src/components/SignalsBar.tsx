import { useState, useEffect, useRef } from 'react';
import { api } from '../api/client';
import { T, SEVERITY_COLOR } from '../styles/tokens';
import type { PmoSignal } from '../api/types';

const SIGNALS_POLL_MS = 15000;

interface SignalsBarProps {
  onForge: (signal: PmoSignal) => void;
  onOpenCountChange?: (count: number) => void;
}

function severityColor(sev: string): string {
  return SEVERITY_COLOR[sev.toLowerCase()] ?? T.text2;
}

export function SignalsBar({ onForge, onOpenCountChange }: SignalsBarProps) {
  const [signals, setSignals] = useState<PmoSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newSeverity, setNewSeverity] = useState('medium');
  const [submitting, setSubmitting] = useState(false);
  const mountedRef = useRef(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function applySignals(data: PmoSignal[]) {
    if (!mountedRef.current) return;
    setSignals(data);
    const openCount = data.filter(s => s.status !== 'resolved').length;
    onOpenCountChange?.(openCount);
  }

  useEffect(() => {
    mountedRef.current = true;

    async function fetchSignals() {
      try {
        const data = await api.getSignals();
        applySignals(data);
        setError(null);
      } catch (e) {
        if (mountedRef.current) setError(e instanceof Error ? e.message : 'Failed to load signals');
      } finally {
        if (mountedRef.current) setLoading(false);
      }
    }

    fetchSignals();
    intervalRef.current = setInterval(fetchSignals, SIGNALS_POLL_MS);

    return () => {
      mountedRef.current = false;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleResolve(id: string) {
    try {
      const updated = await api.resolveSignal(id);
      setSignals(prev => {
        const next = prev.map(s => s.signal_id === id ? updated : s);
        const openCount = next.filter(s => s.status !== 'resolved').length;
        onOpenCountChange?.(openCount);
        return next;
      });
    } catch {
      // silent — not critical
    }
  }

  async function handleAddSignal() {
    if (!newTitle.trim()) return;
    setSubmitting(true);
    try {
      const sig = await api.createSignal({
        signal_id: `sig-${Date.now()}`,
        signal_type: 'bug',
        title: newTitle.trim(),
        severity: newSeverity,
        status: 'open',
      });
      setSignals(prev => {
        const next = [sig, ...prev];
        const openCount = next.filter(s => s.status !== 'resolved').length;
        onOpenCountChange?.(openCount);
        return next;
      });
      setNewTitle('');
      setShowAdd(false);
    } catch {
      // silent
    } finally {
      setSubmitting(false);
    }
  }

  const open = signals.filter(s => s.status !== 'resolved');

  return (
    <div style={{
      padding: '7px 14px',
      borderBottom: `1px solid ${T.border}`,
      background: T.bg2,
      maxHeight: 160,
      overflowY: 'auto',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 5 }}>
        <span style={{
          fontSize: 9,
          fontWeight: 700,
          color: T.red,
          textTransform: 'uppercase',
          letterSpacing: 0.5,
        }}>
          Signals — {open.length} open
        </span>
        <button
          onClick={() => setShowAdd(!showAdd)}
          style={{
            padding: '2px 6px',
            borderRadius: 3,
            border: `1px solid ${T.red}44`,
            background: showAdd ? T.red + '15' : 'transparent',
            color: T.red,
            fontSize: 7,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          {showAdd ? 'Cancel' : '+ Add Signal'}
        </button>
      </div>

      {/* Add form */}
      {showAdd && (
        <div style={{ display: 'flex', gap: 4, marginBottom: 6, alignItems: 'center' }}>
          <input
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleAddSignal(); }}
            placeholder="Signal description..."
            style={{
              flex: 1,
              padding: '4px 8px',
              borderRadius: 3,
              border: `1px solid ${T.border}`,
              background: T.bg1,
              color: T.text0,
              fontSize: 9,
              outline: 'none',
            }}
          />
          <select
            value={newSeverity}
            onChange={e => setNewSeverity(e.target.value)}
            style={{
              padding: '4px 6px',
              borderRadius: 3,
              border: `1px solid ${T.border}`,
              background: T.bg1,
              color: T.text0,
              fontSize: 8,
            }}
          >
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <button
            onClick={handleAddSignal}
            disabled={submitting || !newTitle.trim()}
            style={{
              padding: '4px 10px',
              borderRadius: 3,
              border: 'none',
              background: T.green,
              color: '#fff',
              fontSize: 8,
              fontWeight: 600,
              cursor: 'pointer',
              opacity: submitting || !newTitle.trim() ? 0.5 : 1,
            }}
          >
            Add
          </button>
        </div>
      )}

      {loading && (
        <div style={{ fontSize: 8, color: T.text3, fontStyle: 'italic', padding: 4 }}>
          Loading signals...
        </div>
      )}
      {error && (
        <div style={{ fontSize: 8, color: T.red, padding: 4 }}>{error}</div>
      )}

      {/* Signal rows */}
      {open.map(sig => (
        <div
          key={sig.signal_id}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '4px 8px',
            background: T.bg1,
            borderRadius: 3,
            border: `1px solid ${T.border}`,
            borderLeft: `3px solid ${severityColor(sig.severity)}`,
            marginBottom: 3,
          }}
        >
          <span style={{ fontSize: 7, color: T.text3, fontFamily: 'monospace' }}>
            {sig.signal_id.slice(0, 12)}
          </span>
          <span style={{ fontSize: 9, color: T.text0, flex: 1 }}>{sig.title}</span>
          {sig.description && (
            <span style={{ fontSize: 7, color: T.text3, maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {sig.description}
            </span>
          )}
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            padding: '1px 5px',
            borderRadius: 3,
            fontSize: 7,
            fontWeight: 600,
            color: severityColor(sig.severity),
            background: severityColor(sig.severity) + '14',
            border: `1px solid ${severityColor(sig.severity)}22`,
            whiteSpace: 'nowrap',
          }}>
            {sig.severity}
          </span>
          <button
            onClick={() => onForge(sig)}
            style={{
              padding: '2px 6px',
              borderRadius: 3,
              border: `1px solid ${T.accent}44`,
              background: T.accent + '12',
              color: T.accent,
              fontSize: 7,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Forge
          </button>
          <button
            onClick={() => handleResolve(sig.signal_id)}
            style={{
              padding: '2px 6px',
              borderRadius: 3,
              border: `1px solid ${T.border}`,
              background: 'transparent',
              color: T.text3,
              fontSize: 7,
              cursor: 'pointer',
            }}
          >
            Resolve
          </button>
        </div>
      ))}

      {!loading && open.length === 0 && (
        <div style={{ fontSize: 8, color: T.text3, fontStyle: 'italic', padding: 6 }}>
          No open signals.
        </div>
      )}
    </div>
  );
}
