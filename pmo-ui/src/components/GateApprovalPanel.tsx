import { useState } from 'react';
import type { MouseEvent, CSSProperties } from 'react';
import { api } from '../api/client';
import type { PmoCard } from '../api/types';
import { T, FONTS, SHADOWS } from '../styles/tokens';
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

  async function loadContext(e: MouseEvent) {
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

  async function handleApprove(e: MouseEvent) {
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

  async function handleReject(e: MouseEvent) {
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
    const isApproved = confirmed.startsWith('Approved');
    return (
      <div
        onClick={e => e.stopPropagation()}
        style={{
          marginTop: 6,
          padding: '7px 10px',
          borderRadius: 8,
          background: isApproved ? T.mintSoft : T.cherrySoft,
          border: `1.5px solid ${isApproved ? T.mint : T.cherry}`,
          fontSize: 13,
          color: isApproved ? T.mint : T.cherry,
          fontWeight: 800,
          fontFamily: FONTS.body,
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
      {/* Header row — "Ding! Pick up, chef!" */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        marginBottom: 5,
        padding: '5px 8px',
        borderRadius: 10,
        background: T.butter,
        border: `1.5px solid ${T.tangerine}`,
      }}>
        <span style={{
          fontFamily: FONTS.display,
          fontWeight: 900,
          fontSize: 15,
          color: T.ink,
        }}>
          🛎 Ding! Pick up, chef!
        </span>
        {card.current_phase && (
          <span style={{
            fontSize: 14,
            color: T.text1,
            fontFamily: FONTS.hand,
            transform: 'rotate(-.5deg)',
            display: 'inline-block',
          }}>
            "{card.current_phase}"
          </span>
        )}
        <div style={{ flex: 1 }} />
        <button
          onClick={loadContext}
          disabled={contextLoading || contextLoaded}
          style={{
            padding: '3px 9px',
            borderRadius: 6,
            border: `1.5px solid ${T.border}`,
            background: contextLoaded ? T.mintSoft : T.bg3,
            color: contextLoaded ? T.mint : T.text1,
            fontSize: 10,
            fontWeight: 800,
            fontFamily: FONTS.body,
            cursor: contextLoaded ? 'default' : 'pointer',
            opacity: contextLoading ? 0.6 : 1,
          }}
        >
          {contextLoading ? 'Loading…' : contextLoaded ? 'Context loaded' : 'Load review context'}
        </button>
      </div>

      {/* Approval context display — "Prep log" */}
      {contextLoaded && approvalContext && (
        <div style={{ marginBottom: 6 }}>
          <div style={{
            fontSize: 9,
            fontWeight: 800,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            color: T.text1,
            fontFamily: FONTS.body,
            marginBottom: 3,
          }}>
            Prep log
          </div>
          <div style={{
            maxHeight: 180,
            overflowY: 'auto',
            padding: '6px 8px',
            borderRadius: 8,
            background: T.bg3,
            border: `1.5px dashed ${T.borderSoft}`,
            fontSize: 9,
            color: T.text1,
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            fontFamily: FONTS.mono,
          }}>
            {approvalContext}
          </div>
        </div>
      )}

      {/* Action buttons */}
      {activeForm === 'none' && (
        <div style={{ display: 'flex', gap: 5 }}>
          {canApprove && (
            <button
              onClick={e => { e.stopPropagation(); setActiveForm('approve'); }}
              style={_approveBtn(false)}
              title="Approve this phase and allow execution to continue"
            >
              Fire it — approved 🔥
            </button>
          )}
          {canReject && (
            <button
              onClick={e => { e.stopPropagation(); setActiveForm('reject'); }}
              style={_rejectBtn(false)}
              title="Reject this phase and stop execution"
            >
              Send it back
            </button>
          )}
        </div>
      )}

      {/* Approve sub-form */}
      {activeForm === 'approve' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={{
            fontSize: 9,
            fontWeight: 800,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            color: T.text1,
            fontFamily: FONTS.body,
          }}>
            Notes (optional)
          </label>
          <textarea
            value={notes}
            onChange={e => setNotes(e.target.value)}
            onClick={e => e.stopPropagation()}
            rows={2}
            placeholder="any notes for the next course?"
            style={_textareaStyle()}
          />
          <div style={{ display: 'flex', gap: 5 }}>
            <button
              onClick={handleApprove}
              disabled={approveLoading}
              style={_approveBtn(approveLoading)}
            >
              {approveLoading ? 'Approving…' : 'Fire it — approved 🔥'}
            </button>
            <button
              onClick={e => { e.stopPropagation(); setActiveForm('none'); }}
              style={_cancelBtn()}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Reject sub-form */}
      {activeForm === 'reject' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <label style={{
            fontSize: 9,
            fontWeight: 800,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            color: T.cherry,
            fontFamily: FONTS.body,
          }}>
            Reason for rejection (required)
          </label>
          <textarea
            value={reason}
            onChange={e => setReason(e.target.value)}
            onClick={e => e.stopPropagation()}
            rows={2}
            placeholder="what went wrong in the kitchen?"
            style={_textareaStyle()}
          />
          <div style={{ display: 'flex', gap: 5 }}>
            <button
              onClick={handleReject}
              disabled={rejectLoading || !reason.trim()}
              style={_rejectBtn(rejectLoading || !reason.trim())}
            >
              {rejectLoading ? 'Rejecting…' : 'Send it back'}
            </button>
            <button
              onClick={e => { e.stopPropagation(); setActiveForm('none'); }}
              style={_cancelBtn()}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function _approveBtn(disabled = false): CSSProperties {
  return {
    padding: '4px 11px',
    borderRadius: 10,
    border: `2px solid ${T.border}`,
    background: T.mint,
    color: T.ink,
    fontSize: 12,
    fontWeight: 800,
    fontFamily: FONTS.body,
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.5 : 1,
    boxShadow: disabled ? 'none' : SHADOWS.sm,
  };
}

function _rejectBtn(disabled = false): CSSProperties {
  return {
    padding: '4px 11px',
    borderRadius: 10,
    border: `2px solid ${T.border}`,
    background: T.cherry,
    color: T.cream,
    fontSize: 12,
    fontWeight: 800,
    fontFamily: FONTS.body,
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.5 : 1,
    boxShadow: disabled ? 'none' : SHADOWS.sm,
  };
}

function _cancelBtn(): CSSProperties {
  return {
    padding: '4px 11px',
    borderRadius: 10,
    border: `2px dashed ${T.borderSoft}`,
    background: 'transparent',
    color: T.text2,
    fontSize: 12,
    fontWeight: 700,
    fontFamily: FONTS.body,
    cursor: 'pointer',
  };
}

function _textareaStyle(): CSSProperties {
  return {
    background: T.bg3,
    border: `2px solid ${T.border}`,
    borderRadius: 8,
    color: T.text0,
    fontSize: 10,
    padding: '5px 8px',
    resize: 'vertical',
    fontFamily: FONTS.body,
    outline: 'none',
  };
}
