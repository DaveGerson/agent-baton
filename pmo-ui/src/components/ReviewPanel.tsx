import { useState, useEffect } from 'react';
import type { MouseEvent, CSSProperties } from 'react';
import { api } from '../api/client';
import type { PmoCard, ApprovalLogEntry } from '../api/types';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { useToast } from '../contexts/ToastContext';

interface ReviewPanelProps {
  cardId: string;
  card: PmoCard;
  onApproved?: () => void;
  onRejected?: () => void;
  onClose?: () => void;
}

/**
 * ReviewPanel — the "tasting panel" for a card in the awaiting_review column.
 *
 * Shows:
 * - Plan summary (task description, phase/step counts, risk level, agents)
 * - Approve / Request Changes / Reject actions (with notes/feedback textareas)
 * - Approval history timeline fetched from GET /api/v1/pmo/cards/{card_id}/approval-log
 */
export function ReviewPanel({ cardId, card, onApproved, onRejected, onClose }: ReviewPanelProps) {
  const toast = useToast();

  // Approval log
  const [logEntries, setLogEntries] = useState<ApprovalLogEntry[]>([]);
  const [logLoading, setLogLoading] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);

  // Active action form: none | approve | changes | reject
  const [activeForm, setActiveForm] = useState<'none' | 'approve' | 'changes' | 'reject'>('none');

  // Approve
  const [approveNotes, setApproveNotes] = useState('');
  const [approveLoading, setApproveLoading] = useState(false);

  // Request changes
  const [changesFeedback, setChangesFeedback] = useState('');
  const [changesLoading, setChangesLoading] = useState(false);

  // Reject
  const [rejectReason, setRejectReason] = useState('');
  const [rejectLoading, setRejectLoading] = useState(false);

  // Post-action confirmation
  const [confirmed, setConfirmed] = useState<{ text: string; kind: 'approve' | 'reject' | 'changes' } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLogLoading(true);
    setLogError(null);
    api.getApprovalLog(cardId)
      .then(resp => {
        if (!cancelled) setLogEntries(resp.entries);
      })
      .catch(() => {
        if (!cancelled) setLogError('Could not load approval history.');
      })
      .finally(() => {
        if (!cancelled) setLogLoading(false);
      });
    return () => { cancelled = true; };
  }, [cardId]);

  const phaseCount = card.steps_total > 0 ? card.steps_total : null;
  const agentList = card.agents.slice(0, 4);
  const moreAgents = card.agents.length > 4 ? card.agents.length - 4 : 0;

  // ---- handlers ----

  async function handleApprove(e: MouseEvent) {
    e.stopPropagation();
    setApproveLoading(true);
    try {
      await api.approveGate(cardId, { phase_id: 0, notes: approveNotes.trim() || undefined });
      setConfirmed({ text: 'Tasted and approved — send it out!', kind: 'approve' });
      toast.success('Approved');
      onApproved?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Approval failed');
    } finally {
      setApproveLoading(false);
    }
  }

  async function handleRequestChanges(e: MouseEvent) {
    e.stopPropagation();
    if (!changesFeedback.trim()) {
      toast.error('Feedback is required when requesting changes');
      return;
    }
    setChangesLoading(true);
    try {
      await api.requestReview(cardId, { notes: changesFeedback.trim() });
      setConfirmed({ text: 'Sent back to the kitchen with notes.', kind: 'changes' });
      toast.success('Changes requested');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setChangesLoading(false);
    }
  }

  async function handleReject(e: MouseEvent) {
    e.stopPropagation();
    if (!rejectReason.trim()) {
      toast.error('A rejection reason is required');
      return;
    }
    setRejectLoading(true);
    try {
      await api.rejectGate(cardId, { phase_id: 0, reason: rejectReason.trim() });
      setConfirmed({ text: 'Rejected — dish pulled from service.', kind: 'reject' });
      toast.success('Rejected');
      onRejected?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Rejection failed');
    } finally {
      setRejectLoading(false);
    }
  }

  // ---- confirmed banner ----
  if (confirmed) {
    const colorMap = {
      approve: { bg: T.mintSoft, border: T.mint, color: T.mintDark },
      changes: { bg: T.butterSoft, border: T.butter, color: T.inkSoft },
      reject:  { bg: T.cherrySoft, border: T.cherry, color: T.cherryDark },
    };
    const c = colorMap[confirmed.kind];
    return (
      <div onClick={e => e.stopPropagation()} style={{
        marginTop: 6,
        padding: '8px 12px',
        borderRadius: 8,
        background: c.bg,
        border: `2px solid ${c.border}`,
        color: c.color,
        fontSize: 13,
        fontWeight: 800,
        fontFamily: FONTS.body,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
      }}>
        <span style={{ flex: 1 }}>{confirmed.text}</span>
        {onClose && (
          <button onClick={e => { e.stopPropagation(); onClose(); }} style={_closeBtn()}>
            Close
          </button>
        )}
      </div>
    );
  }

  return (
    <div onClick={e => e.stopPropagation()} style={{ marginTop: 6 }}>

      {/* Tasting panel header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        marginBottom: 6,
        padding: '6px 10px',
        borderRadius: 10,
        background: T.blueberrySoft,
        border: `2px solid ${T.blueberry}`,
      }}>
        <span style={{
          fontFamily: FONTS.display,
          fontWeight: 900,
          fontSize: 14,
          color: T.blueberry,
        }}>
          Tasting Panel
        </span>
        <span style={{
          fontFamily: FONTS.hand,
          fontSize: 13,
          color: T.inkSoft,
          transform: 'rotate(-0.5deg)',
          display: 'inline-block',
        }}>
          — quality check before it goes out
        </span>
        <div style={{ flex: 1 }} />
        {onClose && (
          <button
            onClick={e => { e.stopPropagation(); onClose(); }}
            style={_closeBtn()}
            title="Close review panel"
          >
            {'\u00d7'}
          </button>
        )}
      </div>

      {/* Plan summary */}
      <div style={{
        marginBottom: 8,
        padding: '8px 10px',
        borderRadius: 8,
        background: T.bg3,
        border: `1.5px dashed ${T.borderSoft}`,
      }}>
        <div style={_sectionLabel()}>Plan Summary</div>

        {/* Task description */}
        <div style={{
          fontSize: 11,
          color: T.text0,
          fontFamily: FONTS.body,
          fontWeight: 600,
          lineHeight: 1.4,
          marginBottom: 6,
        }}>
          {card.current_phase || card.title}
        </div>

        {/* Metadata chips row */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {phaseCount !== null && (
            <_SummaryChip label="Steps" value={String(phaseCount)} color={T.blueberry} />
          )}
          {card.gates_passed > 0 && (
            <_SummaryChip label="Gates passed" value={String(card.gates_passed)} color={T.mint} />
          )}
          {card.risk_level && (
            <_SummaryChip
              label="Risk"
              value={card.risk_level}
              color={card.risk_level === 'high' ? T.cherry : card.risk_level === 'medium' ? T.butter : T.mint}
            />
          )}
        </div>

        {/* Agents */}
        {card.agents.length > 0 && (
          <div style={{ marginTop: 6, display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: 9, color: T.text3, fontFamily: FONTS.body, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4 }}>
              Agents:
            </span>
            {agentList.map(a => (
              <span key={a} style={{
                fontSize: 9,
                fontFamily: FONTS.body,
                fontWeight: 600,
                color: T.blueberry,
                background: T.blueberry + '18',
                border: `1px solid ${T.blueberry}`,
                borderRadius: 999,
                padding: '1px 6px',
              }}>
                {a}
              </span>
            ))}
            {moreAgents > 0 && (
              <span style={{ fontSize: 9, color: T.text3, fontFamily: FONTS.mono }}>+{moreAgents} more</span>
            )}
          </div>
        )}
      </div>

      {/* Action buttons */}
      {activeForm === 'none' && (
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 6 }}>
          <button
            onClick={e => { e.stopPropagation(); setActiveForm('approve'); }}
            style={_approveBtn(false)}
            title="Approve this plan"
          >
            Approve
          </button>
          <button
            onClick={e => { e.stopPropagation(); setActiveForm('changes'); }}
            style={_changesBtn(false)}
            title="Request changes before approving"
          >
            Request Changes
          </button>
          <button
            onClick={e => { e.stopPropagation(); setActiveForm('reject'); }}
            style={_rejectBtn(false)}
            title="Reject and pull from service"
          >
            Reject
          </button>
        </div>
      )}

      {/* Approve sub-form */}
      {activeForm === 'approve' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 6 }}>
          <label style={_fieldLabel(T.mint)}>Notes (optional)</label>
          <textarea
            value={approveNotes}
            onChange={e => setApproveNotes(e.target.value)}
            onClick={e => e.stopPropagation()}
            rows={2}
            placeholder="any finishing notes for the next chef?"
            style={_textareaStyle()}
          />
          <div style={{ display: 'flex', gap: 5 }}>
            <button onClick={handleApprove} disabled={approveLoading} style={_approveBtn(approveLoading)}>
              {approveLoading ? 'Approving…' : 'Approve'}
            </button>
            <button onClick={e => { e.stopPropagation(); setActiveForm('none'); }} style={_cancelBtn()}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Request changes sub-form */}
      {activeForm === 'changes' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 6 }}>
          <label style={_fieldLabel(T.butter)}>Feedback (required)</label>
          <textarea
            value={changesFeedback}
            onChange={e => setChangesFeedback(e.target.value)}
            onClick={e => e.stopPropagation()}
            rows={3}
            placeholder="what needs to change before this is ready to serve?"
            style={_textareaStyle()}
          />
          <div style={{ display: 'flex', gap: 5 }}>
            <button
              onClick={handleRequestChanges}
              disabled={changesLoading || !changesFeedback.trim()}
              style={_changesBtn(changesLoading || !changesFeedback.trim())}
            >
              {changesLoading ? 'Sending…' : 'Send Back'}
            </button>
            <button onClick={e => { e.stopPropagation(); setActiveForm('none'); }} style={_cancelBtn()}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Reject sub-form */}
      {activeForm === 'reject' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 6 }}>
          <label style={_fieldLabel(T.cherry)}>Rejection reason (required)</label>
          <textarea
            value={rejectReason}
            onChange={e => setRejectReason(e.target.value)}
            onClick={e => e.stopPropagation()}
            rows={2}
            placeholder="what went wrong in the kitchen?"
            style={_textareaStyle()}
          />
          <div style={{ display: 'flex', gap: 5 }}>
            <button
              onClick={handleReject}
              disabled={rejectLoading || !rejectReason.trim()}
              style={_rejectBtn(rejectLoading || !rejectReason.trim())}
            >
              {rejectLoading ? 'Rejecting…' : 'Reject'}
            </button>
            <button onClick={e => { e.stopPropagation(); setActiveForm('none'); }} style={_cancelBtn()}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Approval history timeline */}
      <div>
        <div style={_sectionLabel()}>Approval History</div>
        {logLoading && (
          <div style={{ fontSize: 9, color: T.text3, fontStyle: 'italic', fontFamily: FONTS.body, padding: '4px 0' }}>
            Loading history…
          </div>
        )}
        {logError && (
          <div style={{ fontSize: 9, color: T.cherry, fontFamily: FONTS.body, padding: '4px 0' }}>
            {logError}
          </div>
        )}
        {!logLoading && !logError && logEntries.length === 0 && (
          <div style={{ fontSize: 9, color: T.text3, fontStyle: 'italic', fontFamily: FONTS.body, padding: '4px 0' }}>
            No approval activity yet.
          </div>
        )}
        {!logLoading && logEntries.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {logEntries.map(entry => (
              <ApprovalLogRow key={entry.log_id} entry={entry} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- sub-components ----

function ApprovalLogRow({ entry }: { entry: ApprovalLogEntry }) {
  const actionMeta: Record<ApprovalLogEntry['action'], { icon: string; color: string; label: string }> = {
    approve:        { icon: '\u2713', color: T.mint,      label: 'Approved' },
    reject:         { icon: '\u00d7', color: T.cherry,    label: 'Rejected' },
    request_review: { icon: '\u27a4', color: T.blueberry, label: 'Review requested' },
    feedback:       { icon: '\u270e', color: T.butter,    label: 'Feedback' },
  };
  const meta = actionMeta[entry.action] ?? { icon: '?', color: T.text3, label: entry.action };

  return (
    <div style={{
      display: 'flex',
      gap: 7,
      alignItems: 'flex-start',
      padding: '5px 8px',
      borderRadius: 6,
      background: T.bg3,
      border: `1px solid ${T.borderSoft}`,
    }}>
      {/* Icon dot */}
      <div style={{
        width: 18,
        height: 18,
        borderRadius: '50%',
        background: meta.color + '28',
        border: `1.5px solid ${meta.color}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 10,
        color: meta.color,
        fontWeight: 900,
        flexShrink: 0,
        marginTop: 1,
      }}>
        {meta.icon}
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 10, fontWeight: 700, color: meta.color, fontFamily: FONTS.body }}>
            {meta.label}
          </span>
          {entry.user_id && (
            <span style={{ fontSize: 9, color: T.text2, fontFamily: FONTS.mono }}>
              by {entry.user_id}
            </span>
          )}
          <span style={{ fontSize: 9, color: T.text4, fontFamily: FONTS.mono, marginLeft: 'auto' }}>
            {fmtTimestamp(entry.created_at)}
          </span>
        </div>
        {entry.notes && (
          <div style={{
            fontSize: 9,
            color: T.text1,
            fontFamily: FONTS.body,
            marginTop: 2,
            lineHeight: 1.4,
            wordBreak: 'break-word',
          }}>
            {entry.notes}
          </div>
        )}
      </div>
    </div>
  );
}

function _SummaryChip({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 3,
      fontSize: 9,
      fontFamily: FONTS.body,
      fontWeight: 700,
      color,
      background: color + '18',
      border: `1.5px solid ${color}`,
      borderRadius: 999,
      padding: '1px 7px',
      boxShadow: `1.5px 1.5px 0 0 ${T.border}`,
    }}>
      <span style={{ color: T.text3, fontWeight: 600 }}>{label}:</span>
      {value}
    </span>
  );
}

function _sectionLabel(): CSSProperties {
  return {
    fontSize: 9,
    fontWeight: 800,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    color: T.text2,
    fontFamily: FONTS.body,
    marginBottom: 4,
  };
}

function _fieldLabel(color: string): CSSProperties {
  return {
    fontSize: 9,
    fontWeight: 800,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    color,
    fontFamily: FONTS.body,
  };
}

function _approveBtn(disabled = false): CSSProperties {
  return {
    padding: '4px 11px',
    borderRadius: 10,
    border: `2px solid ${T.border}`,
    background: T.mint,
    color: T.ink,
    fontSize: 11,
    fontWeight: 800,
    fontFamily: FONTS.body,
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.5 : 1,
    boxShadow: disabled ? 'none' : SHADOWS.sm,
  };
}

function _changesBtn(disabled = false): CSSProperties {
  return {
    padding: '4px 11px',
    borderRadius: 10,
    border: `2px solid ${T.butter}`,
    background: T.butterSoft,
    color: T.ink,
    fontSize: 11,
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
    fontSize: 11,
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
    fontSize: 11,
    fontWeight: 700,
    fontFamily: FONTS.body,
    cursor: 'pointer',
  };
}

function _closeBtn(): CSSProperties {
  return {
    padding: '2px 7px',
    borderRadius: 6,
    border: `1.5px solid ${T.borderSoft}`,
    background: 'transparent',
    color: T.text2,
    fontSize: 10,
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

function fmtTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}
