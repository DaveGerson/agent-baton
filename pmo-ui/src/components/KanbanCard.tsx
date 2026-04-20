import { memo, useState, useEffect, useRef } from 'react';
import type { ReactNode, MouseEvent } from 'react';
import type { PmoCard, ForgePlanResponse } from '../api/types';
import { T, PRIORITY_COLOR, programColor } from '../styles/tokens';
import { FONTS, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import { agentDisplayName } from '../utils/agent-names';
import { useToast } from '../contexts/ToastContext';
import { PlanPreview } from './PlanPreview';
import { ExecutionProgress } from './ExecutionProgress';
import { GateApprovalPanel } from './GateApprovalPanel';
import { ChangelistPanel } from './ChangelistPanel';
import { ReviewPanel } from './ReviewPanel';

interface KanbanCardProps {
  card: PmoCard;
  columnColor: string;
  onForge?: (card: PmoCard) => void;
  onEditPlan?: (card: PmoCard) => void;
  onMutateCard?: (cardId: string, updater: (card: PmoCard) => PmoCard) => void;
}

function Chip({ children, color = T.text2 }: { children: ReactNode; color?: string }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 3,
      padding: '1px 6px',
      borderRadius: 999,
      fontSize: 9,
      fontWeight: 600,
      color,
      background: color + '22',
      border: `1.5px solid ${color}`,
      boxShadow: `1.5px 1.5px 0 0 ${T.border}`,
      fontFamily: FONTS.body,
      whiteSpace: 'nowrap',
    }}>
      {children}
    </span>
  );
}

function Pips({ done, total, color }: { done: number; total: number; color: string }) {
  if (!total) return null;
  return (
    <div style={{ display: 'flex', gap: 2 }}>
      {Array.from({ length: total }).map((_, i) => (
        <div
          key={i}
          style={{
            width: 6,
            height: 6,
            borderRadius: 1,
            background: i < done ? color : T.bg3,
            border: `1px solid ${T.border}`,
          }}
        />
      ))}
    </div>
  );
}

function ProgramDot({ program, size = 7 }: { program: string; size?: number }) {
  const color = programColor(program);
  return (
    <div
      title={program}
      style={{ width: size, height: size, borderRadius: 2, background: color, flexShrink: 0 }}
    />
  );
}

function usePlanPreview(cardId: string) {
  const [showPlan, setShowPlan] = useState(false);
  const [planData, setPlanData] = useState<ForgePlanResponse | null>(null);
  const [planLoading, setPlanLoading] = useState(false);

  async function handleViewPlan(e: MouseEvent) {
    e.stopPropagation();
    if (showPlan) {
      setShowPlan(false);
      return;
    }
    setShowPlan(true);
    if (planData) return;
    setPlanLoading(true);
    try {
      const result = await api.getCardDetail(cardId);
      setPlanData(result.plan);
    } catch {
      // silent
    } finally {
      setPlanLoading(false);
    }
  }

  return { showPlan, planData, planLoading, handleViewPlan };
}

function useExecuteCard(
  card: PmoCard,
  toast: ReturnType<typeof useToast>,
  onMutateCard?: (cardId: string, updater: (card: PmoCard) => PmoCard) => void,
) {
  const [execLoading, setExecLoading] = useState(false);
  const [execResult, setExecResult] = useState<string | null>(null);
  const execTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => { if (execTimerRef.current) clearTimeout(execTimerRef.current); };
  }, []);

  async function handleExecute(e: MouseEvent) {
    e.stopPropagation();
    if (execTimerRef.current) clearTimeout(execTimerRef.current);
    // Stale-state guard: SSE may have moved the card out of 'queued' between
    // render and click. Bail early so we show a clear message instead of a
    // generic 409 from the backend.
    if (card.column !== 'queued') {
      setExecResult('Card is no longer queued — refresh to see its current state.');
      toast.error('Card is no longer queued');
      execTimerRef.current = setTimeout(() => setExecResult(null), 8000);
      return;
    }
    setExecLoading(true);
    setExecResult(null);
    try {
      const resp = await api.executeCard(card.card_id);
      setExecResult(`Launched (PID ${resp.pid})`);
      onMutateCard?.(card.card_id, c => ({ ...c, column: 'executing' }));
      toast.success('Execution launched');
    } catch (err) {
      setExecResult(err instanceof Error ? err.message : 'Launch failed');
      toast.error('Execution launch failed');
    } finally {
      setExecLoading(false);
      execTimerRef.current = setTimeout(() => setExecResult(null), 8000);
    }
  }

  function dismissExecResult(e: MouseEvent) {
    e.stopPropagation();
    setExecResult(null);
  }

  return { execLoading, execResult, handleExecute, dismissExecResult };
}

function KanbanCardImpl({ card, columnColor, onForge, onEditPlan, onMutateCard }: KanbanCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [pressed, setPressed] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const toast = useToast();
  const { showPlan, planData, planLoading, handleViewPlan } = usePlanPreview(card.card_id);
  const { execLoading, execResult, handleExecute, dismissExecResult } = useExecuteCard(card, toast, onMutateCard);
  const [showProgress, setShowProgress] = useState(false);
  const [showChangelist, setShowChangelist] = useState(false);
  const [gateResolved, setGateResolved] = useState(false);
  const [reviewResolved, setReviewResolved] = useState(false);
  const [sendReviewLoading, setSendReviewLoading] = useState(false);
  const isHuman = card.column === 'awaiting_human';
  const isQueued = card.column === 'queued';
  const isReview = card.column === 'review';
  const isAwaitingReview = card.column === 'awaiting_review' as string;
  const isActive = card.column === 'executing' || card.column === 'validating' || card.column === 'awaiting_human';

  // Approval mode: read from meta tag injected by the server, fallback to 'local'.
  // The meta tag <meta name="baton-approval-mode" content="team|local"> is set
  // by the backend template; if absent we default to 'local' (no team review required).
  const approvalMode = (
    document.querySelector('meta[name="baton-approval-mode"]')?.getAttribute('content') ?? 'local'
  ) as 'local' | 'team';

  async function handleSendForReview(e: MouseEvent) {
    e.stopPropagation();
    setSendReviewLoading(true);
    try {
      await api.requestReview(card.card_id, { notes: '' });
      toast.success('Sent for review');
      onMutateCard?.(card.card_id, c => ({ ...c, column: 'awaiting_review' as PmoCard['column'] }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to send for review');
    } finally {
      setSendReviewLoading(false);
    }
  }

  // Stable tilt derived from card ID
  const tilt = (card.card_id.charCodeAt(0) % 5) * 0.4 - 1;

  function handleGateResolved(result: 'approve' | 'reject') {
    setGateResolved(true);
    // On approve, optimistically move to executing so the card visibly leaves
    // awaiting_human. On reject, leave the column alone — the backend decides
    // the next state (typically back to queued for re-planning) and the SSE
    // update will deliver it. Hiding the gate panel is handled by gateResolved.
    if (result === 'approve' && onMutateCard) {
      onMutateCard(card.card_id, c => ({ ...c, column: 'executing' }));
    }
  }
  const priorityColor = PRIORITY_COLOR[card.priority] ?? T.text2;

  // Compute card transform and shadow based on interaction state
  const cardTransform = pressed
    ? `translate(2px,2px) rotate(${tilt}deg)`
    : hovered
      ? `translate(-1px,-1px) rotate(${tilt}deg)`
      : `rotate(${tilt}deg)`;
  const cardShadow = pressed ? 'none' : hovered ? SHADOWS.lg : SHADOWS.md;
  const cardBorder = isHuman
    ? `2px solid ${T.tangerine}`
    : `2px solid ${T.border}`;

  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      aria-label={`${card.title}. ${card.column.replace('_', ' ')}. ${card.steps_completed} of ${card.steps_total} steps complete. Press Enter to ${expanded ? 'collapse' : 'expand'} details.`}
      onClick={() => setExpanded(!expanded)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          setExpanded(!expanded);
        }
      }}
      onDoubleClick={(e) => { e.stopPropagation(); setShowDetail(true); }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => { setHovered(false); setPressed(false); }}
      onMouseDown={() => setPressed(true)}
      onMouseUp={() => setPressed(false)}
      style={{
        position: 'relative',
        background: T.bg1,
        borderRadius: 12,
        border: cardBorder,
        cursor: 'pointer',
        overflow: 'hidden',
        transition: 'transform 0.12s ease, box-shadow 0.12s ease',
        boxShadow: cardShadow,
        transform: cardTransform,
      }}
    >
      {/* Perforated top edge — ticket stub feel */}
      <div style={{
        position: 'absolute',
        top: 0,
        left: 8,
        right: 8,
        height: 3,
        backgroundImage: `radial-gradient(circle, ${T.bg3} 1.5px, transparent 2px)`,
        backgroundSize: '8px 3px',
        backgroundRepeat: 'repeat-x',
        pointerEvents: 'none',
      }} />

      <div style={{ padding: '10px 8px 6px' }}>
        {/* Title row */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 4, marginBottom: 3 }}>
          <ProgramDot program={card.program} size={6} />
          <div style={{
            fontSize: 16,
            fontWeight: 800,
            fontFamily: FONTS.display,
            color: T.text0,
            lineHeight: 1.25,
            flex: 1,
            overflow: 'hidden',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
          }}>
            {card.title}
          </div>
          <span
            aria-hidden="true"
            style={{
              fontSize: 10,
              color: T.text3,
              flexShrink: 0,
              marginTop: 1,
              transition: 'transform 0.15s',
              transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
              display: 'inline-block',
            }}
          >
            {'▾'}
          </span>
        </div>

        {/* Meta row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 3, flexWrap: 'wrap', marginBottom: 3 }}>
          {/* BO-01: show ADO external ID when available; fall back to abbreviated internal ID */}
          {card.external_id ? (
            <span
              title={`ADO: ${card.external_id} — internal: ${card.card_id}`}
              style={{ fontSize: 9, color: T.text2, fontFamily: FONTS.mono, fontWeight: 600 }}
            >
              {card.external_id}
            </span>
          ) : (
            <span
              title={card.card_id}
              style={{ fontSize: 9, color: T.text2, fontFamily: FONTS.mono }}
            >
              {card.card_id.slice(0, 8)}
            </span>
          )}
          {card.priority >= 1 && (
            <Chip color={priorityColor}>P{card.priority === 2 ? '0' : '1'}</Chip>
          )}
          {card.risk_level && card.risk_level !== 'low' && (
            <Chip color={card.risk_level === 'high' ? T.red : T.yellow}>
              {card.risk_level}
            </Chip>
          )}
          {isReview && card.consolidation_result && (
            <Chip color={T.crust}>
              {card.consolidation_result.files_changed.length} files
            </Chip>
          )}
        </div>

        {/* Step progress pips */}
        {card.steps_total > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 3 }}>
            {card.steps_total <= 12 && (
              <Pips done={card.steps_completed} total={card.steps_total} color={columnColor} />
            )}
            <span style={{ fontSize: 9, color: T.text3, fontFamily: FONTS.mono }}>
              {card.steps_completed}/{card.steps_total}
            </span>
          </div>
        )}

        {/* Current phase / error */}
        {card.current_phase && !card.error && (
          <div style={{
            fontSize: 14,
            color: isHuman ? T.tangerine : T.text1,
            lineHeight: 1.3,
            marginTop: 2,
            padding: '3px 6px',
            background: T.bg3,
            borderRadius: 2,
            borderLeft: `1.5px solid ${T.borderSoft}`,
            fontFamily: FONTS.hand,
            transform: 'rotate(-0.5deg)',
          }}>
            &ldquo;{card.current_phase.length > 65
              ? card.current_phase.slice(0, 65) + '…'
              : card.current_phase}&rdquo;
          </div>
        )}
        {card.error && (
          <div style={{
            fontSize: 9,
            color: T.red,
            lineHeight: 1.2,
            marginTop: 2,
            padding: '2px 4px',
            background: T.bg3,
            borderRadius: 2,
            borderLeft: `1.5px solid ${T.red}`,
          }}>
            {card.error.length > 80 ? card.error.slice(0, 80) + '…' : card.error}
          </div>
        )}

        {/* Footer */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          marginTop: 6,
          paddingTop: 5,
          borderTop: `1.5px dashed ${T.borderSoft}`,
        }}>
          <span style={{ fontSize: 9, color: T.text3, fontFamily: FONTS.mono }}>{card.project_id}</span>
          {card.agents.length > 0 && (
            <>
              <span style={{ fontSize: 9, color: T.text4 }}>·</span>
              <span style={{ fontSize: 9, color: T.text3, fontFamily: FONTS.body }}>
                {card.agents.slice(0, 2).map(agentDisplayName).join(', ')}
                {card.agents.length > 2 && ` +${card.agents.length - 2}`}
              </span>
            </>
          )}
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 9, color: T.text2, fontFamily: FONTS.mono }}>{fmtTime(card.updated_at)}</span>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={{
          borderTop: `1.5px dashed ${T.borderSoft}`,
          padding: '6px 8px',
          background: T.bg3,
        }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
            <div>
              <span style={{ fontSize: 9, color: T.text3, fontFamily: FONTS.body }}>Program: </span>
              <span style={{ fontSize: 9, color: T.text0, fontWeight: 600, fontFamily: FONTS.body }}>{card.program}</span>
            </div>
            <div>
              <span style={{ fontSize: 9, color: T.text3, fontFamily: FONTS.body }}>Gates passed: </span>
              <span style={{ fontSize: 9, color: T.text0, fontWeight: 600, fontFamily: FONTS.body }}>{card.gates_passed}</span>
            </div>
          </div>

          {/* Full untruncated phase/error text — only shown when expanded */}
          {card.current_phase && !card.error && card.current_phase.length > 65 && (
            <div style={{
              fontSize: 14,
              color: isHuman ? T.tangerine : T.text1,
              lineHeight: 1.4,
              marginBottom: 6,
              padding: '4px 6px',
              background: T.bg1,
              borderRadius: 2,
              borderLeft: `1.5px solid ${T.borderSoft}`,
              fontFamily: FONTS.hand,
              transform: 'rotate(-0.5deg)',
              wordBreak: 'break-word',
            }}>
              &ldquo;{card.current_phase}&rdquo;
            </div>
          )}
          {card.error && card.error.length > 80 && (
            <div style={{
              fontSize: 9,
              color: T.red,
              lineHeight: 1.4,
              marginBottom: 6,
              padding: '4px 6px',
              background: T.bg1,
              borderRadius: 2,
              borderLeft: `1.5px solid ${T.red}`,
              wordBreak: 'break-word',
            }}>
              {card.error}
            </div>
          )}
          {card.agents.length > 0 && (
            <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', marginBottom: 6 }}>
              {card.agents.map(a => (
                <Chip key={a} color={T.blueberry}>{agentDisplayName(a)}</Chip>
              ))}
            </div>
          )}

          {/* Actions row */}
          <div style={{
            display: 'flex',
            gap: 4,
            marginTop: 4,
            paddingTop: 4,
            borderTop: `1.5px dashed ${T.borderSoft}`,
            flexWrap: 'wrap',
          }}>
            {isQueued && (
              <ActionButton
                onClick={handleExecute}
                disabled={execLoading}
                title="Launch autonomous execution for this card"
                bg={T.mint + '22'}
                border={`1.5px solid ${T.mint}`}
                color={T.mint}
              >
                {execLoading ? 'Launching...' : '\u25B6 Execute'}
              </ActionButton>
            )}
            {isQueued && approvalMode === 'team' && (
              <ActionButton
                onClick={handleSendForReview}
                disabled={sendReviewLoading}
                title="Send this plan for peer review before execution"
                bg={T.blueberry + '18'}
                border={`1.5px solid ${T.blueberry}`}
                color={T.blueberry}
              >
                {sendReviewLoading ? 'Sending…' : 'Send for Review'}
              </ActionButton>
            )}
            {isReview && (
              <ActionButton
                onClick={e => { e.stopPropagation(); setShowChangelist(true); }}
                title="Review consolidated changes before merging"
                bg={T.crust + '33'}
                border={`1.5px solid ${T.crust}`}
                color={T.crustDark}
              >
                Review Changes
              </ActionButton>
            )}
            {isActive && (
              <ActionButton
                onClick={e => { e.stopPropagation(); setShowProgress(true); }}
                title="Monitor execution progress in real time"
                bg={T.butter + '33'}
                border={`1.5px solid ${T.butter}`}
                color={T.inkSoft}
              >
                Monitor
              </ActionButton>
            )}
            {onForge && (
              <ActionButton
                onClick={e => { e.stopPropagation(); onForge(card); }}
                title="Open this card in the Forge for editing or re-planning"
                bg={T.cherry + '18'}
                border={`1.5px solid ${T.cherry}`}
                color={T.cherry}
              >
                Re-forge
              </ActionButton>
            )}
            {card.steps_total > 0 && onEditPlan && (
              <ActionButton
                onClick={e => { e.stopPropagation(); onEditPlan(card); }}
                title="Jump directly to the Forge plan editor for this card"
                bg={T.blueberry + '18'}
                border={`1.5px solid ${T.blueberry}`}
                color={T.blueberry}
              >
                {'\u270e'} Edit Plan
              </ActionButton>
            )}
            <ActionButton
              onClick={handleViewPlan}
              title="View the execution plan for this card"
              bg={showPlan ? T.blueberry + '28' : T.blueberry + '18'}
              border={`1.5px solid ${T.blueberry}`}
              color={T.blueberry}
            >
              {showPlan ? 'Hide Plan' : 'View Plan'}
            </ActionButton>
          </div>

          {/* Gate approval panel — only visible when card is awaiting human input */}
          {isHuman && !gateResolved && (
            <GateApprovalPanel card={card} onResolved={handleGateResolved} />
          )}

          {/* Review panel — visible when card is awaiting peer review */}
          {isAwaitingReview && !reviewResolved && (
            <ReviewPanel
              cardId={card.card_id}
              card={card}
              onApproved={() => {
                setReviewResolved(true);
                onMutateCard?.(card.card_id, c => ({ ...c, column: 'queued' }));
              }}
              onRejected={() => setReviewResolved(true)}
              onClose={() => setReviewResolved(true)}
            />
          )}

          {/* Execution result */}
          {execResult && (
            <div
              role="status"
              aria-live="polite"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                fontSize: 9,
                color: execResult.startsWith('Launched') ? T.mint : T.cherry,
                padding: '4px 8px',
                marginTop: 4,
                background: execResult.startsWith('Launched') ? T.mintSoft : T.cherrySoft,
                borderRadius: 8,
                border: '1.5px solid currentColor',
              }}
            >
              <span style={{ flex: 1, fontFamily: FONTS.body }}>{execResult}</span>
              <button
                aria-label="Dismiss execution result"
                onClick={dismissExecResult}
                style={{
                  background: 'none',
                  border: 'none',
                  color: T.text3,
                  fontSize: 10,
                  cursor: 'pointer',
                  padding: '0 2px',
                  lineHeight: 1,
                  flexShrink: 0,
                }}
              >
                {'\u00d7'}
              </button>
            </div>
          )}

          {/* Plan preview — inline expandable list */}
          {showPlan && (
            <div
              onClick={e => e.stopPropagation()}
              style={{
                marginTop: 6,
                maxHeight: 300,
                overflowY: 'auto',
                borderRadius: 8,
                border: `1.5px dashed ${T.borderSoft}`,
                background: T.bg3,
                padding: 6,
              }}
            >
              {planLoading && (
                <div style={{ fontSize: 9, color: T.text3, fontStyle: 'italic', padding: 8, fontFamily: FONTS.body }}>
                  Loading plan…
                </div>
              )}
              {!planLoading && planData && (
                <PlanPreview plan={planData} collapsible />
              )}
              {!planLoading && !planData && (
                <div style={{ fontSize: 9, color: T.text3, fontStyle: 'italic', padding: 8, fontFamily: FONTS.body }}>
                  No plan available for this card.
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Execution progress modal */}
      {showProgress && (
        <ExecutionProgress card={card} onClose={() => setShowProgress(false)} />
      )}

      {/* Changelist review modal */}
      {showChangelist && (
        <ChangelistPanel
          cardId={card.card_id}
          onMerged={() => { setShowChangelist(false); onMutateCard?.(card.card_id, c => ({ ...c, column: 'deployed' })); }}
          onClose={() => setShowChangelist(false)}
        />
      )}

      {/* Full-screen detail modal */}
      {showDetail && (
        <CardDetailModal
          card={card}
          onClose={() => setShowDetail(false)}
          onForge={onForge}
          onEditPlan={onEditPlan}
          onMutateCard={onMutateCard}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------
// CardDetailModal — full-screen overlay opened on double-click
// ----------------------------------------------------------------
interface CardDetailModalProps {
  card: PmoCard;
  onClose: () => void;
  onForge?: (card: PmoCard) => void;
  onEditPlan?: (card: PmoCard) => void;
  onMutateCard?: (cardId: string, updater: (card: PmoCard) => PmoCard) => void;
}

function CardDetailModal({ card, onClose, onForge, onEditPlan, onMutateCard }: CardDetailModalProps) {
  const [planData, setPlanData] = useState<ForgePlanResponse | null>(null);
  const [planLoading, setPlanLoading] = useState(true);
  const [execLoading, setExecLoading] = useState(false);
  const [execResult, setExecResult] = useState<string | null>(null);
  const [sendReviewLoading, setSendReviewLoading] = useState(false);
  const [showProgress, setShowProgress] = useState(false);
  const [showChangelist, setShowChangelist] = useState(false);
  const toast = useToast();

  const isQueued = card.column === 'queued';
  const isActive = card.column === 'executing' || card.column === 'validating' || card.column === 'awaiting_human';
  const isReview = card.column === 'review';

  const approvalMode = (
    document.querySelector('meta[name="baton-approval-mode"]')?.getAttribute('content') ?? 'local'
  ) as 'local' | 'team';

  // Fetch plan on mount
  useEffect(() => {
    let cancelled = false;
    setPlanLoading(true);
    api.getCardDetail(card.card_id)
      .then(result => { if (!cancelled) setPlanData(result.plan); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setPlanLoading(false); });
    return () => { cancelled = true; };
  }, [card.card_id]);

  // Escape key closes modal
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  async function handleExecute(e: MouseEvent) {
    e.stopPropagation();
    if (card.column !== 'queued') {
      setExecResult('Card is no longer queued — refresh to see its current state.');
      toast.error('Card is no longer queued');
      return;
    }
    setExecLoading(true);
    setExecResult(null);
    try {
      const resp = await api.executeCard(card.card_id);
      setExecResult(`Launched (PID ${resp.pid})`);
      onMutateCard?.(card.card_id, c => ({ ...c, column: 'executing' }));
      toast.success('Execution launched');
    } catch (err) {
      setExecResult(err instanceof Error ? err.message : 'Launch failed');
      toast.error('Execution launch failed');
    } finally {
      setExecLoading(false);
    }
  }

  async function handleSendForReview(e: MouseEvent) {
    e.stopPropagation();
    setSendReviewLoading(true);
    try {
      await api.requestReview(card.card_id, { notes: '' });
      toast.success('Sent for review');
      onMutateCard?.(card.card_id, c => ({ ...c, column: 'awaiting_review' as PmoCard['column'] }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to send for review');
    } finally {
      setSendReviewLoading(false);
    }
  }

  const riskColor = card.risk_level === 'high' || card.risk_level === 'critical'
    ? T.red
    : card.risk_level === 'medium'
    ? T.yellow
    : T.text2;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Detail view: ${card.title}`}
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(42,26,16,0.55)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: T.bg1,
          borderRadius: 16,
          border: `2px solid ${T.border}`,
          boxShadow: SHADOWS.xl,
          width: '100%',
          maxWidth: 760,
          maxHeight: '88vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Modal header */}
        <div style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 10,
          padding: '14px 18px 12px',
          borderBottom: `2px solid ${T.border}`,
          background: T.bg3,
          flexShrink: 0,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontFamily: FONTS.display,
              fontWeight: 900,
              fontSize: 20,
              color: T.text0,
              lineHeight: 1.2,
              marginBottom: 6,
            }}>
              {card.title}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.mono }}>{card.project_id}</span>
              <span style={{ fontSize: 10, color: T.text4 }}>·</span>
              <span style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.body }}>{card.program}</span>
              {card.risk_level && card.risk_level !== 'low' && (
                <>
                  <span style={{ fontSize: 10, color: T.text4 }}>·</span>
                  <span style={{
                    fontSize: 10,
                    fontWeight: 700,
                    color: riskColor,
                    fontFamily: FONTS.body,
                    textTransform: 'uppercase',
                  }}>
                    {card.risk_level} risk
                  </span>
                </>
              )}
              <span style={{ fontSize: 10, color: T.text4 }}>·</span>
              <span style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.body }}>
                {card.column.replace('_', ' ')}
              </span>
              {card.steps_total > 0 && (
                <>
                  <span style={{ fontSize: 10, color: T.text4 }}>·</span>
                  <span style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.mono }}>
                    {card.steps_completed}/{card.steps_total} steps
                  </span>
                </>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close detail view"
            style={{
              background: 'transparent',
              border: `1.5px solid ${T.border}`,
              borderRadius: 8,
              color: T.text1,
              fontSize: 16,
              cursor: 'pointer',
              padding: '2px 8px',
              fontFamily: FONTS.body,
              flexShrink: 0,
            }}
          >
            {'\u00d7'}
          </button>
        </div>

        {/* Actions row */}
        <div style={{
          display: 'flex',
          gap: 6,
          padding: '10px 18px',
          borderBottom: `1.5px dashed ${T.borderSoft}`,
          flexWrap: 'wrap',
          flexShrink: 0,
          background: T.bg1,
        }}>
          {isQueued && (
            <ActionButton
              onClick={handleExecute}
              disabled={execLoading}
              title="Launch autonomous execution for this card"
              bg={T.mint + '22'}
              border={`1.5px solid ${T.mint}`}
              color={T.mint}
            >
              {execLoading ? 'Launching...' : '\u25B6 Execute'}
            </ActionButton>
          )}
          {isQueued && approvalMode === 'team' && (
            <ActionButton
              onClick={handleSendForReview}
              disabled={sendReviewLoading}
              title="Send this plan for peer review before execution"
              bg={T.blueberry + '18'}
              border={`1.5px solid ${T.blueberry}`}
              color={T.blueberry}
            >
              {sendReviewLoading ? 'Sending…' : 'Send for Review'}
            </ActionButton>
          )}
          {isActive && (
            <ActionButton
              onClick={e => { e.stopPropagation(); setShowProgress(true); }}
              title="Monitor execution progress in real time"
              bg={T.butter + '33'}
              border={`1.5px solid ${T.butter}`}
              color={T.inkSoft}
            >
              Monitor
            </ActionButton>
          )}
          {isReview && (
            <ActionButton
              onClick={e => { e.stopPropagation(); setShowChangelist(true); }}
              title="Review consolidated changes before merging"
              bg={T.crust + '33'}
              border={`1.5px solid ${T.crust}`}
              color={T.crustDark}
            >
              Review Changes
            </ActionButton>
          )}
          {onForge && (
            <ActionButton
              onClick={e => { e.stopPropagation(); onForge(card); onClose(); }}
              title="Open this card in the Forge for editing or re-planning"
              bg={T.cherry + '18'}
              border={`1.5px solid ${T.cherry}`}
              color={T.cherry}
            >
              Re-forge
            </ActionButton>
          )}
          {card.steps_total > 0 && onEditPlan && (
            <ActionButton
              onClick={e => { e.stopPropagation(); onEditPlan(card); onClose(); }}
              title="Jump directly to the Forge plan editor for this card"
              bg={T.blueberry + '18'}
              border={`1.5px solid ${T.blueberry}`}
              color={T.blueberry}
            >
              {'\u270e'} Edit Plan
            </ActionButton>
          )}
          {execResult && (
            <span style={{
              fontSize: 10,
              color: execResult.startsWith('Launched') ? T.mint : T.cherry,
              fontFamily: FONTS.body,
              alignSelf: 'center',
            }}>
              {execResult}
            </span>
          )}
        </div>

        {/* Plan body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '14px 18px' }}>
          {planLoading && (
            <div style={{
              fontSize: 12,
              color: T.text3,
              fontStyle: 'italic',
              fontFamily: FONTS.body,
              padding: '20px 0',
              textAlign: 'center',
            }}>
              Loading plan…
            </div>
          )}
          {!planLoading && planData && (
            <PlanPreview plan={planData} />
          )}
          {!planLoading && !planData && (
            <div style={{
              fontSize: 12,
              color: T.text3,
              fontStyle: 'italic',
              fontFamily: FONTS.body,
              padding: '20px 0',
              textAlign: 'center',
            }}>
              No plan available for this card.
            </div>
          )}
        </div>

        {/* Sub-modals spawned from the detail view */}
        {showProgress && (
          <ExecutionProgress card={card} onClose={() => setShowProgress(false)} />
        )}
        {showChangelist && (
          <ChangelistPanel
            cardId={card.card_id}
            onMerged={() => { setShowChangelist(false); onMutateCard?.(card.card_id, c => ({ ...c, column: 'deployed' })); }}
            onClose={() => setShowChangelist(false)}
          />
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------
// ActionButton — shared button style for the expanded actions row
// ----------------------------------------------------------------
interface ActionButtonProps {
  children: ReactNode;
  onClick: (e: MouseEvent) => void;
  title?: string;
  disabled?: boolean;
  bg: string;
  border: string;
  color: string;
}

function ActionButton({ children, onClick, title, disabled, bg, border, color }: ActionButtonProps) {
  const [hov, setHov] = useState(false);
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{
        padding: '3px 9px',
        borderRadius: 8,
        border,
        background: bg,
        color,
        fontSize: 9,
        fontWeight: 600,
        fontFamily: FONTS.body,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.6 : 1,
        boxShadow: hov && !disabled ? SHADOWS.md : SHADOWS.sm,
        transform: hov && !disabled ? 'translate(-1px,-1px)' : 'none',
        transition: 'transform 0.1s ease, box-shadow 0.1s ease',
      }}
    >
      {children}
    </button>
  );
}

// Memoized so that a mutation to one card (e.g. SSE update, gate approval)
// doesn't force all sibling cards on the board to reconcile. Cards are
// uniquely identified by card_id; when the parent creates a new `filtered`
// array each render, same-identity card objects still skip re-render.
export const KanbanCard = memo(KanbanCardImpl, (prev, next) => (
  prev.card === next.card
  && prev.columnColor === next.columnColor
  && prev.onForge === next.onForge
  && prev.onEditPlan === next.onEditPlan
  && prev.onMutateCard === next.onMutateCard
));

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '—';
  }
}
