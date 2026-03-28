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
  const [newSignalType, setNewSignalType] = useState<'bug' | 'escalation' | 'blocker'>('bug');
  const [newSeverity, setNewSeverity] = useState('medium');
  const [submitting, setSubmitting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchResolving, setBatchResolving] = useState(false);
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
      setSelected(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    } catch {
      // silent — not critical
    }
  }

  async function handleBatchResolve() {
    if (selected.size === 0) return;
    const count = selected.size;
    const confirmed = window.confirm(
      `Resolve ${count} signal${count !== 1 ? 's' : ''}? This cannot be undone.`
    );
    if (!confirmed) return;
    setBatchResolving(true);
    try {
      const ids = Array.from(selected);
      const result = await api.batchResolveSignals(ids);
      const resolvedSet = new Set(result.resolved);
      setSignals(prev => {
        const next = prev.map(s =>
          resolvedSet.has(s.signal_id) ? { ...s, status: 'resolved' } : s
        );
        const openCount = next.filter(s => s.status !== 'resolved').length;
        onOpenCountChange?.(openCount);
        return next;
      });
      setSelected(new Set());
    } catch {
      // silent — not critical
    } finally {
      if (mountedRef.current) setBatchResolving(false);
    }
  }

  async function handleAddSignal() {
    if (!newTitle.trim()) return;
    setSubmitting(true);
    try {
      const sig = await api.createSignal({
        signal_id: `sig-${Date.now()}`,
        signal_type: newSignalType,
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

  function toggleSelect(id: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  const open = signals.filter(s => s.status !== 'resolved');
  const allSelected = open.length > 0 && selected.size === open.length;

  function toggleSelectAll() {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(open.map(s => s.signal_id)));
    }
  }

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
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {open.length > 0 && (
            <input
              type="checkbox"
              id="signal-select-all"
              aria-label="Select all open signals"
              checked={allSelected}
              onChange={toggleSelectAll}
              style={{ cursor: 'pointer', width: 11, height: 11, accentColor: T.accent }}
            />
          )}
          <span style={{
            fontSize: 9,
            fontWeight: 700,
            color: T.red,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
          }}>
            Signals — {open.length} open
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {selected.size > 0 && (
            <button
              onClick={handleBatchResolve}
              disabled={batchResolving}
              style={{
                padding: '2px 7px',
                borderRadius: 3,
                border: `1px solid ${T.green}44`,
                background: T.green + '15',
                color: T.green,
                fontSize: 9,
                fontWeight: 600,
                cursor: batchResolving ? 'not-allowed' : 'pointer',
                opacity: batchResolving ? 0.6 : 1,
              }}
            >
              {batchResolving ? 'Resolving…' : `Resolve Selected (${selected.size})`}
            </button>
          )}
          <button
            onClick={() => setShowAdd(!showAdd)}
            style={{
              padding: '2px 6px',
              borderRadius: 3,
              border: `1px solid ${T.red}44`,
              background: showAdd ? T.red + '15' : 'transparent',
              color: T.red,
              fontSize: 9,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {showAdd ? 'Cancel' : '+ Add Signal'}
          </button>
        </div>
      </div>

      {/* Add form */}
      {showAdd && (
        <div style={{ display: 'flex', gap: 4, marginBottom: 6, alignItems: 'center' }}>
          <input
            id="new-signal-title"
            aria-label="Signal title"
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
            id="new-signal-type"
            aria-label="Signal type"
            value={newSignalType}
            onChange={e => setNewSignalType(e.target.value as 'bug' | 'escalation' | 'blocker')}
            style={{
              padding: '4px 6px',
              borderRadius: 3,
              border: `1px solid ${T.border}`,
              background: T.bg1,
              color: T.text0,
              fontSize: 9,
            }}
          >
            <option value="bug">Bug</option>
            <option value="escalation">Escalation</option>
            <option value="blocker">Blocker</option>
          </select>
          <select
            id="new-signal-severity"
            aria-label="Severity"
            value={newSeverity}
            onChange={e => setNewSeverity(e.target.value)}
            style={{
              padding: '4px 6px',
              borderRadius: 3,
              border: `1px solid ${T.border}`,
              background: T.bg1,
              color: T.text0,
              fontSize: 9,
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
      <ul role="list" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
        {open.map(sig => (
          <li
            key={sig.signal_id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 8px',
              background: selected.has(sig.signal_id) ? T.accent + '0a' : T.bg1,
              borderRadius: 3,
              border: `1px solid ${selected.has(sig.signal_id) ? T.accent + '44' : T.border}`,
              borderLeft: `3px solid ${severityColor(sig.severity)}`,
              marginBottom: 3,
            }}
          >
            <input
              type="checkbox"
              id={`signal-select-${sig.signal_id}`}
              aria-label={`Select signal: ${sig.title}`}
              checked={selected.has(sig.signal_id)}
              onChange={() => toggleSelect(sig.signal_id)}
              onClick={e => e.stopPropagation()}
              style={{ cursor: 'pointer', width: 11, height: 11, accentColor: T.accent, flexShrink: 0 }}
            />
            <span style={{ fontSize: 9, color: T.text3, fontFamily: 'monospace' }}>
              {sig.signal_id.slice(0, 12)}
            </span>
            <span style={{ fontSize: 9, color: T.text0, flex: 1 }}>{sig.title}</span>
            {sig.description && (
              <span style={{ fontSize: 9, color: T.text3, maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {sig.description}
              </span>
            )}
            <span style={{
              display: 'inline-flex',
              alignItems: 'center',
              padding: '1px 5px',
              borderRadius: 3,
              fontSize: 9,
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
                fontSize: 9,
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
                fontSize: 9,
                cursor: 'pointer',
              }}
            >
              Resolve
            </button>
          </li>
        ))}
      </ul>

      {!loading && open.length === 0 && (
        <div style={{ fontSize: 8, color: T.text3, fontStyle: 'italic', padding: 6 }}>
          No open signals.
        </div>
      )}
    </div>
  );
}
