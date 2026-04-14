import { useState, useEffect, useRef } from 'react';
import { api } from '../api/client';
import { ConfirmDialog } from './ConfirmDialog';
import { T, SEVERITY_COLOR } from '../styles/tokens';
import { useToast } from '../contexts/ToastContext';
import type { PmoSignal } from '../api/types';

const SIGNALS_POLL_MS = 15000;

const SIGNAL_TYPE_LABELS: Record<string, string> = {
  'stale_plan': 'Stale Plan',
  'missing_gate': 'Missing Gate',
  'budget_exceeded': 'Budget Exceeded',
  'execution_failed': 'Execution Failed',
  'agent_error': 'Agent Error',
  'manual': 'Manual',
  'bug': 'Bug Report',
  'escalation': 'Escalation',
  'blocker': 'Blocker',
};

function signalTypeLabel(raw: string): string {
  return SIGNAL_TYPE_LABELS[raw] ?? raw.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

interface SignalsBarProps {
  onForge: (signal: PmoSignal) => void;
  onOpenCountChange?: (count: number) => void;
}

function severityColor(sev: string): string {
  return SEVERITY_COLOR[sev.toLowerCase()] ?? T.text2;
}

export function SignalsBar({ onForge, onOpenCountChange }: SignalsBarProps) {
  const toast = useToast();
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
  const [showBatchConfirm, setShowBatchConfirm] = useState(false);
  const [showResolved, setShowResolved] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const resolveErrorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // FB-02: propagate open count via effect, not inside state updaters
  const openCount = signals.filter(s => s.status !== 'resolved').length;
  useEffect(() => {
    onOpenCountChange?.(openCount);
  }, [openCount, onOpenCountChange]);

  function applySignals(data: PmoSignal[]) {
    if (!mountedRef.current) return;
    setSignals(data);
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

  function showResolveError(msg: string) {
    if (resolveErrorTimerRef.current) clearTimeout(resolveErrorTimerRef.current);
    setResolveError(msg);
    resolveErrorTimerRef.current = setTimeout(() => {
      if (mountedRef.current) setResolveError(null);
    }, 5000);
  }

  async function handleResolve(id: string) {
    try {
      await api.resolveSignal(id);
      // Filter the resolved signal out rather than replacing it with the partial
      // response object (which lacks title, severity, etc. and would corrupt the row).
      setSignals(prev => prev.filter(s => s.signal_id !== id));
      setSelected(prev => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    } catch {
      showResolveError('Failed to resolve signal. Please try again.');
    }
  }

  function requestBatchResolve() {
    if (selected.size === 0) return;
    setShowBatchConfirm(true);
  }

  async function doBatchResolve() {
    setShowBatchConfirm(false);
    setBatchResolving(true);
    try {
      const ids = Array.from(selected);
      const result = await api.batchResolveSignals(ids);
      const resolvedSet = new Set(result.resolved);
      setSignals(prev =>
        prev.map(s =>
          resolvedSet.has(s.signal_id) ? { ...s, status: 'resolved' } : s
        )
      );
      setSelected(new Set());
    } catch {
      showResolveError('Failed to resolve signals. Please try again.');
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
      setSignals(prev => [sig, ...prev]);
      setNewTitle('');
      setShowAdd(false);
    } catch {
      toast.error('Failed to add signal');
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
      {/* Resolve error banner */}
      {resolveError && (
        <div
          role="alert"
          style={{
            fontSize: 9,
            color: T.red,
            background: T.red + '14',
            border: `1px solid ${T.red}33`,
            borderRadius: 3,
            padding: '4px 8px',
            marginBottom: 5,
          }}
        >
          {resolveError}
        </div>
      )}

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
              onClick={requestBatchResolve}
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
            onClick={() => setShowResolved(s => !s)}
            style={{
              padding: '2px 6px',
              borderRadius: 3,
              border: `1px solid ${T.text3}44`,
              background: showResolved ? T.text3 + '15' : 'transparent',
              color: T.text3,
              fontSize: 9,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {showResolved ? 'Hide Resolved' : 'Show Resolved'}
          </button>
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
              fontSize: 9,
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
        <div style={{ fontSize: 9, color: T.text3, fontStyle: 'italic', padding: 4 }}>
          Loading signals...
        </div>
      )}
      {error && (
        <div style={{ fontSize: 9, color: T.red, padding: 4 }}>{error}</div>
      )}

      {/* Signal rows */}
      <ul role="list" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
        {open.map((sig, index) => {
          const sideBorderColor = selected.has(sig.signal_id) ? T.accent + '44' : T.border;
          return (
          <li
            key={sig.signal_id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 8px',
              background: selected.has(sig.signal_id) ? T.accent + '0a' : T.bg1,
              borderRadius: 3,
              // FB-03: avoid mixing border shorthand with borderLeft — use individual sides
              borderTop: `1px solid ${sideBorderColor}`,
              borderRight: `1px solid ${sideBorderColor}`,
              borderBottom: `1px solid ${sideBorderColor}`,
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
            <span
              title={sig.signal_id}
              style={{ fontSize: 9, color: T.text3, fontFamily: 'monospace' }}
            >
              #{index + 1}
            </span>
            <span style={{ fontSize: 9, color: T.text0, flex: 1 }}>{sig.title}</span>
            {sig.description && (
              <span
                title={sig.description}
                style={{ fontSize: 9, color: T.text3, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              >
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
              color: T.text3,
              background: T.bg3,
              whiteSpace: 'nowrap',
            }}>
              {signalTypeLabel(sig.signal_type)}
            </span>
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
          );
        })}
      </ul>

      {showResolved && (
        <ul role="list" style={{ listStyle: 'none', padding: 0, margin: 0, opacity: 0.6 }}>
          {signals.filter(s => s.status === 'resolved').map((sig, index) => (
            <li key={sig.signal_id} style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 8px',
              background: T.bg1,
              borderRadius: 3,
              borderTop: `1px solid ${T.border}`,
              borderRight: `1px solid ${T.border}`,
              borderBottom: `1px solid ${T.border}`,
              borderLeft: `3px solid ${T.text3}`,
              marginBottom: 3,
              textDecoration: 'line-through',
            }}>
              <span style={{ fontSize: 9, color: T.text4, fontFamily: 'monospace' }}>#{index + 1}</span>
              <span style={{ fontSize: 9, color: T.text3, flex: 1 }}>{sig.title}</span>
              <span style={{
                display: 'inline-flex', alignItems: 'center', padding: '1px 5px',
                borderRadius: 3, fontSize: 9, fontWeight: 600,
                color: T.green, background: T.green + '14',
              }}>resolved</span>
            </li>
          ))}
        </ul>
      )}

      {!loading && open.length === 0 && (
        <div style={{ fontSize: 9, color: T.text3, fontStyle: 'italic', padding: 6 }}>
          No open signals.
        </div>
      )}

      {showBatchConfirm && (
        <ConfirmDialog
          message={`Resolve ${selected.size} signal${selected.size !== 1 ? 's' : ''}? This cannot be undone.`}
          confirmLabel="Resolve"
          onConfirm={doBatchResolve}
          onCancel={() => setShowBatchConfirm(false)}
        />
      )}
    </div>
  );
}
