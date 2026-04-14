import { useState } from 'react';
import { api } from '../api/client';
import type { PmoCard } from '../api/types';
import { T } from '../styles/tokens';
import { useToast } from '../contexts/ToastContext';

interface GateApprovalPanelProps {
  card: PmoCard;
  /** Called after a successful approve or reject so the parent can update state. */
  onResolved: (result: 'approve' | 'reject') => void;
}

/**
 * GateApprovalPanel renders approve / reject controls for a card that is
 * sitting in the ``awaiting_human`` column.  It fetches gate context from
 * ``GET /api/v1/pmo/gates/pending`` so the reviewer can read the engine's
 * review summary before acting.
 *
 * Wired into KanbanCard's expanded detail section when ``card.column === 'awaiting_human'``.
 */
export function GateApprovalPanel({ card, onResolved }: GateApprovalPanelProps) {
  const toast = useToast();

  // Gate context loaded from the pending-gates endpoint.
  const [phaseId, setPhaseId] = useState<number | null>(null);
  const [approvalContext, setApprovalContext] = useState<string>('');
  const [approvalOptions, setApprovalOptions] = useState<string[]>([
    'approve',
    'reject',
    'approve-with-feedback',
  ]);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextLoaded, setContextLoaded] = useState(false);

  // Approve form state.
  const [notes, setNotes] = useState('');
  const [approveLoading, setApproveLoading] = useState(false);

  // Reject form state.
  const [reason, setReason] = useState('');
  const [rejectLoading, setRejectLoading] = useState(false);

  // Which sub-form is open: none | 'approve' | 'reject'
  const [activeForm, setActiveForm] = useState<'none' | 'approve' | 'reject'>('none');

  // Result confirmation message shown after action.
  const [confirmed, setConfirmed] = useState<string | null>(null);

  async function loadContext(e: React.MouseEvent) {
    e.stopPropagation();
    if (contextLoaded) return;
    setContextLoading(true);
    try {
      const gates = await api.listPendingGates();
      const gate = gates.find(g => g.task_id === card.card_id);
      if (gate) {
        setPhaseId(gate.phase_id);
        setApprovalContext(gate.approval_context);
        setApprovalOptions(
          gate.approval_options.length > 0
            ? gate.approval_options
            : ['approve', 'reject', 'approve-with-feedback'],
        );
      }
      setContextLoaded(true);
    } catch {
      toast.error('Failed to load gate context');
    } finally {
      setContextLoading(false);
    }
  }

  async function handleApprove(e: React.MouseEvent) {
    e.stopPropagation();
    if (phaseId === null) {
      toast.error('Gate context not loaded — expand review first');
      return;
    }
    setApproveLoading(true);
    try {
      await api.approveGate(card.card_id, {
        phase_id: phaseId,
        notes: notes.trim() || undefined,
      });
      setConfirmed('Approved — execution will continue.');
      toast.success('Gate approved');
      onResolved('approve');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Approval failed');
    } finally {
      setApproveLoading(false);
    }
  }

  async function handleReject(e: React.MouseEvent) {
    e.stopPropagation();
    if (!reason.trim()) {
      toast.error('A rejection reason is required');
      return;
    }
    if (phaseId === null) {
      toast.error('Gate context not loaded — expand review first');
      return;
    }
    setRejectLoading(true);
    try {
      await api.rejectGate(card.card_id, {
        phase_id: phaseId,
        reason: reason.trim(),
      });
      setConfirmed('Rejected — execution has been stopped.');
      toast.success('Gate rejected');
      onResolved('reject');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Rejection failed');
    } finally {
      setRejectLoading(false);
    }
  }

  // After confirmation, show a read-only banner instead of the forms.
  if (confirmed) {
    return (
      <div
        onClick={e => e.stopPropagation()}
        style={{
          marginTop: 6,
          padding: '6px 8px',
          borderRadius: 3,
          background: T.bg3,
          border: `1px solid ${T.border}`,
          fontSize: 9,
          color: confirmed.startsWith('Approved') ? T.green : T.red,
          fontWeight: 600,
        }}
      >
        {confirmed}
      </div>
    );
  }

  const canApprove = approvalOptions.includes('approve') || approvalOptions.includes('approve-with-feedback');
  const canReject = approvalOptions.includes('reject');

  return (
    <div onClick={e => e.stopPropagation()} style={{ marginTop: 6 }}>
      {/* Header row */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        marginBottom: 5,
        padding: '4px 6px',
        borderRadius: 3,
        background: T.orange + '14',
        border: `1px solid ${T.orange}33`,
      }}>
        <span style={{ fontSize: 9, fontWeight: 700, color: T.orange }}>
          Awaiting Approval
        </span>
        {card.current_phase && (
          <span style={{ fontSize: 9, color: T.text2 }}>
            — {card.current_phase}
          </span>
        )}
        <div style={{ flex: 1 }} />
        <button
          onClick={loadContext}
          disabled={contextLoading || contextLoaded}
          style={{
            padding: '2px 7px',
            borderRadius: 3,
            border: `1px solid ${T.border}`,
            background: T.bg3,
            color: contextLoaded ? T.text3 : T.text1,
            fontSize: 9,
            cursor: contextLoaded ? 'default' : 'pointer',
            opacity: contextLoading ? 0.6 : 1,
          }}
        >
          {contextLoading ? 'Loading…' : contextLoaded ? 'Context loaded' : 'Load review context'}
        </button>
      </div>

      {/* Approval context markdown (read-only display) */}
      {contextLoaded && approvalContext && (
        <div style={{
          marginBottom: 6,
          maxHeight: 180,
          overflowY: 'auto',
          padding: '5px 7px',
          borderRadius: 3,
          background: T.bg1,
          border: `1px solid ${T.border}`,
          fontSize: 9,
          color: T.text1,
          lineHeight: 1.5,
          whiteSpace: 'pre-wrap',
          fontFamily: 'monospace',
        }}>
          {approvalContext}
        </div>
      )}

      {/* Action buttons */}
      {activeForm === 'none' && (
        <div style={{ display: 'flex', gap: 4 }}>
          {canApprove && (
            <button
              onClick={e => { e.stopPropagation(); setActiveForm('approve'); }}
              style={_btnStyle(T.green)}
              title="Approve this phase and allow execution to continue"
            >
              Approve
            </button>
          )}
          {canReject && (
            <button
              onClick={e => { e.stopPropagation(); setActiveForm('reject'); }}
              style={_btnStyle(T.red)}
              title="Reject this phase and stop execution"
            >
              Reject
            </button>
          )}
        </div>
      )}

      {/* Approve sub-form */}
      {activeForm === 'approve' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 9, color: T.text2 }}>
            Notes (optional)
          </label>
          <textarea
            value={notes}
            onChange={e => setNotes(e.target.value)}
            onClick={e => e.stopPropagation()}
            rows={2}
            placeholder="Optional reviewer notes…"
            style={_textareaStyle()}
          />
          <div style={{ display: 'flex', gap: 4 }}>
            <button
              onClick={handleApprove}
              disabled={approveLoading}
              style={_btnStyle(T.green, approveLoading)}
            >
              {approveLoading ? 'Approving…' : 'Confirm Approve'}
            </button>
            <button
              onClick={e => { e.stopPropagation(); setActiveForm('none'); }}
              style={_btnStyle(T.text3)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Reject sub-form */}
      {activeForm === 'reject' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 9, color: T.red }}>
            Reason for rejection (required)
          </label>
          <textarea
            value={reason}
            onChange={e => setReason(e.target.value)}
            onClick={e => e.stopPropagation()}
            rows={2}
            placeholder="Explain why this phase is being rejected…"
            style={_textareaStyle()}
          />
          <div style={{ display: 'flex', gap: 4 }}>
            <button
              onClick={handleReject}
              disabled={rejectLoading || !reason.trim()}
              style={_btnStyle(T.red, rejectLoading || !reason.trim())}
            >
              {rejectLoading ? 'Rejecting…' : 'Confirm Reject'}
            </button>
            <button
              onClick={e => { e.stopPropagation(); setActiveForm('none'); }}
              style={_btnStyle(T.text3)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function _btnStyle(color: string, disabled = false): React.CSSProperties {
  return {
    padding: '3px 9px',
    borderRadius: 3,
    border: `1px solid ${color}44`,
    background: color + '14',
    color,
    fontSize: 9,
    fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.5 : 1,
  };
}

function _textareaStyle(): React.CSSProperties {
  return {
    background: T.bg1,
    border: `1px solid ${T.border}`,
    borderRadius: 3,
    color: T.text0,
    fontSize: 9,
    padding: '4px 6px',
    resize: 'vertical',
    fontFamily: 'inherit',
    outline: 'none',
  };
}
