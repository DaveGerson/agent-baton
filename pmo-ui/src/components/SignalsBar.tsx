import { useState, useEffect, useRef } from 'react';
import { api } from '../api/client';
import { ConfirmDialog } from './ConfirmDialog';
import { T, SEVERITY_COLOR, FONTS, SHADOWS } from '../styles/tokens';
import { useToast } from '../contexts/ToastContext';
import type { PmoSignal } from '../api/types';

const SIGNALS_POLL_MS = 15000;

const SIGNAL_TYPE_LABELS: Record<string, string> = {
  'stale_plan': 'Day-old recipe',
  'missing_gate': 'Skipped taste test',
  'budget_exceeded': 'Kitchen over budget',
  'execution_failed': 'Batch burned',
  'agent_error': 'Chef in the weeds',
  'manual': 'Head chef note',
  'bug': 'Found a hair in the soup',
  'escalation': 'Calling the boss',
  'blocker': 'Kitchen blocked',
  'reforge': 'Re-firing the recipe',
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

function severityBg(sev: string): string {
  const s = sev.toLowerCase();
  if (s === 'critical' || s === 'high') return T.cherrySoft;
  if (s === 'medium') return T.butterSoft;
  return T.bg3;
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
      borderBottom: `2px solid ${T.border}`,
      background: T.bg1,
      borderLeft: `2px solid ${T.border}`,
      maxHeight: 200,
      overflowY: 'auto',
    }}>
      {/* Kitchen Radio header */}
      <div style={{
        background: T.ink,
        padding: '8px 14px 6px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 16 }}>📻</span>
          <span style={{
            fontFamily: FONTS.display,
            fontWeight: 900,
            fontSize: 18,
            color: T.cream,
            lineHeight: 1,
          }}>
            Kitchen Radio
          </span>
          <span style={{
            fontFamily: FONTS.hand,
            fontSize: 15,
            color: T.butter,
            transform: 'rotate(-1deg)',
            display: 'inline-block',
          }}>
            tuned in
          </span>
          {/* LIVE badge */}
          <span style={{
            background: T.cherry,
            color: T.cream,
            fontSize: 10,
            fontWeight: 800,
            textTransform: 'uppercase',
            letterSpacing: 1,
            padding: '2px 6px',
            borderRadius: 4,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
          }}>
            <span style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: T.cream,
              display: 'inline-block',
              animation: 'pulse 1.4s ease-in-out infinite',
            }} />
            LIVE
          </span>
          {/* Open count chip */}
          {open.length > 0 && (
            <span style={{
              background: T.cherry,
              color: T.cream,
              fontSize: 10,
              fontWeight: 800,
              padding: '1px 7px',
              borderRadius: 10,
              fontFamily: FONTS.body,
            }}>
              {open.length} open
            </span>
          )}
        </div>

        {/* Header right controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          {open.length > 0 && (
            <input
              type="checkbox"
              id="signal-select-all"
              aria-label="Select all open signals"
              checked={allSelected}
              onChange={toggleSelectAll}
              style={{ cursor: 'pointer', width: 11, height: 11, accentColor: T.butter }}
            />
          )}
          {selected.size > 0 && (
            <button
              onClick={requestBatchResolve}
              disabled={batchResolving}
              style={{
                padding: '3px 9px',
                borderRadius: 6,
                border: `1.5px solid ${T.border}`,
                background: T.mint,
                color: T.ink,
                fontSize: 10,
                fontWeight: 800,
                fontFamily: FONTS.body,
                cursor: batchResolving ? 'not-allowed' : 'pointer',
                opacity: batchResolving ? 0.6 : 1,
                boxShadow: SHADOWS.sm,
              }}
            >
              {batchResolving ? 'Clearing…' : `Clear the board (${selected.size})`}
            </button>
          )}
          <button
            onClick={() => setShowResolved(s => !s)}
            style={{
              padding: '3px 8px',
              borderRadius: 6,
              border: `1px dashed ${T.borderSoft}`,
              background: showResolved ? T.bg3 : 'transparent',
              color: T.cream,
              fontSize: 9,
              fontWeight: 600,
              fontFamily: FONTS.body,
              cursor: 'pointer',
            }}
          >
            {showResolved ? 'Hide Resolved' : 'Show Resolved'}
          </button>
          <button
            onClick={() => setShowAdd(!showAdd)}
            style={{
              padding: '3px 9px',
              borderRadius: 6,
              border: `1.5px solid ${T.butter}`,
              background: showAdd ? T.bg3 : T.butter,
              color: T.ink,
              fontSize: 10,
              fontWeight: 800,
              fontFamily: FONTS.body,
              cursor: 'pointer',
              boxShadow: showAdd ? 'none' : SHADOWS.sm,
            }}
          >
            {showAdd ? 'Cancel' : '+ Ring the bell'}
          </button>
        </div>
      </div>

      {/* Resolve error banner */}
      {resolveError && (
        <div
          role="alert"
          style={{
            fontSize: 9,
            color: T.cherry,
            background: T.cherrySoft,
            border: `1px solid ${T.cherry}33`,
            borderRadius: 3,
            padding: '4px 8px',
            margin: '5px 10px 0',
            fontFamily: FONTS.body,
          }}
        >
          {resolveError}
        </div>
      )}

      {/* Add signal form */}
      {showAdd && (
        <div style={{
          margin: '8px 10px',
          padding: '10px 12px',
          background: T.bg3,
          border: `2px dashed ${T.border}`,
          borderRadius: 12,
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}>
          <label style={{
            fontSize: 9,
            fontWeight: 800,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            color: T.text0,
            fontFamily: FONTS.body,
          }}>
            New signal
          </label>
          <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
            <input
              id="new-signal-title"
              aria-label="Signal title"
              value={newTitle}
              onChange={e => setNewTitle(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleAddSignal(); }}
              placeholder="Signal description..."
              style={{
                flex: 1,
                padding: '5px 9px',
                borderRadius: 8,
                border: `2px solid ${T.border}`,
                background: T.bg1,
                color: T.text0,
                fontSize: 10,
                fontFamily: FONTS.body,
                outline: 'none',
              }}
            />
            <select
              id="new-signal-type"
              aria-label="Signal type"
              value={newSignalType}
              onChange={e => setNewSignalType(e.target.value as 'bug' | 'escalation' | 'blocker')}
              style={{
                padding: '5px 7px',
                borderRadius: 8,
                border: `2px solid ${T.border}`,
                background: T.bg1,
                color: T.text0,
                fontSize: 10,
                fontFamily: FONTS.body,
              }}
            >
              <option value="bug">Found a hair in the soup</option>
              <option value="escalation">Calling the boss</option>
              <option value="blocker">Kitchen blocked</option>
            </select>
            <select
              id="new-signal-severity"
              aria-label="Severity"
              value={newSeverity}
              onChange={e => setNewSeverity(e.target.value)}
              style={{
                padding: '5px 7px',
                borderRadius: 8,
                border: `2px solid ${T.border}`,
                background: T.bg1,
                color: T.text0,
                fontSize: 10,
                fontFamily: FONTS.body,
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
                padding: '5px 12px',
                borderRadius: 8,
                border: `2px solid ${T.border}`,
                background: T.cherry,
                color: T.cream,
                fontSize: 10,
                fontWeight: 800,
                fontFamily: FONTS.body,
                cursor: 'pointer',
                opacity: submitting || !newTitle.trim() ? 0.5 : 1,
                boxShadow: submitting || !newTitle.trim() ? 'none' : SHADOWS.sm,
              }}
            >
              Add
            </button>
            <button
              onClick={() => setShowAdd(false)}
              style={{
                padding: '5px 10px',
                borderRadius: 8,
                border: `2px dashed ${T.border}`,
                background: 'transparent',
                color: T.text1,
                fontSize: 10,
                fontWeight: 700,
                fontFamily: FONTS.body,
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {loading && (
        <div style={{ fontSize: 9, color: T.text3, fontStyle: 'italic', padding: '6px 14px', fontFamily: FONTS.body }}>
          Loading signals...
        </div>
      )}
      {error && (
        <div style={{ fontSize: 9, color: T.cherry, padding: '6px 14px', fontFamily: FONTS.body }}>{error}</div>
      )}

      {/* Signal rows */}
      <ul role="list" style={{ listStyle: 'none', padding: '6px 10px', margin: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
        {open.map((sig, index) => {
          const sev = sig.severity.toLowerCase();
          const leftColor = severityColor(sig.severity);
          return (
            <li
              key={sig.signal_id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '5px 9px',
                background: selected.has(sig.signal_id) ? T.butterSoft : severityBg(sev),
                borderRadius: 6,
                borderTop: `1px solid ${T.borderSoft}`,
                borderRight: `1px solid ${T.borderSoft}`,
                borderBottom: `1px solid ${T.borderSoft}`,
                borderLeft: `4px solid ${leftColor}`,
              }}
            >
              <input
                type="checkbox"
                id={`signal-select-${sig.signal_id}`}
                aria-label={`Select signal: ${sig.title}`}
                checked={selected.has(sig.signal_id)}
                onChange={() => toggleSelect(sig.signal_id)}
                onClick={e => e.stopPropagation()}
                style={{ cursor: 'pointer', width: 11, height: 11, accentColor: T.butter, flexShrink: 0 }}
              />
              <span
                title={sig.signal_id}
                style={{ fontSize: 10, color: T.text3, fontFamily: FONTS.mono }}
              >
                #{index + 1}
              </span>
              {/* Signal type label */}
              <span style={{
                fontSize: 10,
                fontWeight: 800,
                fontFamily: FONTS.body,
                textTransform: 'uppercase',
                letterSpacing: 0.4,
                color: leftColor,
                whiteSpace: 'nowrap',
              }}>
                {signalTypeLabel(sig.signal_type)}
              </span>
              <span style={{ fontSize: 13, fontWeight: 600, color: T.text0, fontFamily: FONTS.body, flex: 1 }}>
                {sig.title}
              </span>
              {sig.description && (
                <span
                  title={sig.description}
                  style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.body, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                >
                  {sig.description}
                </span>
              )}
              <span style={{
                display: 'inline-flex',
                alignItems: 'center',
                padding: '1px 6px',
                borderRadius: 4,
                fontSize: 9,
                fontWeight: 600,
                fontFamily: FONTS.body,
                color: leftColor,
                background: leftColor + '1a',
                border: `1px solid ${leftColor}33`,
                whiteSpace: 'nowrap',
              }}>
                {sig.severity}
              </span>
              <button
                onClick={() => onForge(sig)}
                style={{
                  padding: '2px 7px',
                  borderRadius: 5,
                  border: `1px solid ${T.blueberry}44`,
                  background: T.blueberry + '12',
                  color: T.blueberry,
                  fontSize: 9,
                  fontWeight: 700,
                  fontFamily: FONTS.body,
                  cursor: 'pointer',
                }}
              >
                Forge
              </button>
              <button
                onClick={() => handleResolve(sig.signal_id)}
                style={{
                  padding: '2px 8px',
                  borderRadius: 5,
                  border: `1.5px solid ${T.border}`,
                  background: T.mint,
                  color: T.ink,
                  fontSize: 9,
                  fontWeight: 800,
                  fontFamily: FONTS.body,
                  cursor: 'pointer',
                  boxShadow: SHADOWS.sm,
                  whiteSpace: 'nowrap',
                }}
              >
                Mop it up
              </button>
            </li>
          );
        })}
      </ul>

      {showResolved && (
        <ul role="list" style={{ listStyle: 'none', padding: '0 10px 6px', margin: 0, opacity: 0.6, display: 'flex', flexDirection: 'column', gap: 3 }}>
          {signals.filter(s => s.status === 'resolved').map((sig, index) => (
            <li key={sig.signal_id} style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 8px',
              background: T.bg1,
              borderRadius: 5,
              borderTop: `1px solid ${T.borderSoft}`,
              borderRight: `1px solid ${T.borderSoft}`,
              borderBottom: `1px solid ${T.borderSoft}`,
              borderLeft: `3px solid ${T.text3}`,
              textDecoration: 'line-through',
            }}>
              <span style={{ fontSize: 9, color: T.text4, fontFamily: FONTS.mono }}>#{index + 1}</span>
              <span style={{ fontSize: 9, color: T.text3, flex: 1, fontFamily: FONTS.body }}>{sig.title}</span>
              <span style={{
                display: 'inline-flex', alignItems: 'center', padding: '1px 5px',
                borderRadius: 3, fontSize: 9, fontWeight: 600, fontFamily: FONTS.body,
                color: T.mint, background: T.mint + '14',
              }}>resolved</span>
            </li>
          ))}
        </ul>
      )}

      {!loading && open.length === 0 && (
        <div style={{
          fontSize: 20,
          color: T.text2,
          fontFamily: FONTS.hand,
          textAlign: 'center',
          padding: '12px 6px',
        }}>
          nothin' on the radio 📻
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
