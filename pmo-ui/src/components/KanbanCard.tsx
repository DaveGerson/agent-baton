import { memo, useState, useEffect } from 'react';
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

function KanbanCardImpl({ card, columnColor, onForge, onEditPlan, onMutateCard }: KanbanCardProps) {
  const [hovered, setHovered] = useState(false);
  const [pressed, setPressed] = useState(false);
  const [showDetail, setShowDetail] = useState(false);

  const isHuman = card.column === 'awaiting_human';
  const isReview = card.column === 'review';
  const priorityColor = PRIORITY_COLOR[card.priority] ?? T.text2;
  const tilt = (card.card_id.charCodeAt(0) % 5) * 0.4 - 1;

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
      aria-label={`${card.title}. ${card.column.replace('_', ' ')}. ${card.steps_completed} of ${card.steps_total} steps complete. Press Enter to open details.`}
      onClick={() => setShowDetail(true)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          setShowDetail(true);
        }
      }}
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
        </div>

        {/* Meta row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 3, flexWrap: 'wrap', marginBottom: 3 }}>
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

type DetailTab = 'overview' | 'plan' | 'execution' | 'changes';

function CardDetailModal({ card, onClose, onForge, onEditPlan, onMutateCard }: CardDetailModalProps) {
  const [planData, setPlanData] = useState<ForgePlanResponse | null>(null);
  const [planLoading, setPlanLoading] = useState(true);
  const [execLoading, setExecLoading] = useState(false);
  const [execResult, setExecResult] = useState<string | null>(null);
  const [sendReviewLoading, setSendReviewLoading] = useState(false);
  const [showProgress, setShowProgress] = useState(false);
  const [showChangelist, setShowChangelist] = useState(false);
  const [gateResolved, setGateResolved] = useState(false);
  const [activeTab, setActiveTab] = useState<DetailTab>('overview');
  const toast = useToast();

  const col = card.column;
  const isIntake = col === 'intake';
  const isQueued = col === 'queued';
  const isAwaitingHuman = col === 'awaiting_human';
  const isExecuting = col === 'executing';
  const isValidating = col === 'validating';
  const isReview = col === 'review';
  const isDeployed = col === 'deployed';
  const isActive = isExecuting || isValidating || isAwaitingHuman;

  // Determine which tabs are available for this column
  const availableTabs: DetailTab[] = ['overview', 'plan'];
  if (isActive || isValidating || isExecuting) availableTabs.push('execution');
  if (isReview || isDeployed) availableTabs.push('changes');

  // Auto-switch to a sensible default tab per column
  useEffect(() => {
    if (isActive) setActiveTab('execution');
    else if (isReview || isDeployed) setActiveTab('changes');
    else setActiveTab('overview');
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.card_id]);

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

  function handleGateResolved(result: 'approve' | 'reject') {
    setGateResolved(true);
    if (result === 'approve' && onMutateCard) {
      onMutateCard(card.card_id, c => ({ ...c, column: 'executing' }));
    }
  }

  const riskColor = card.risk_level === 'high' || card.risk_level === 'critical'
    ? T.red
    : card.risk_level === 'medium'
    ? T.yellow
    : T.text2;

  // Column label for display
  const colLabel: Record<string, string> = {
    intake: 'Tickets Up',
    queued: 'On Deck',
    awaiting_human: 'Pick Up',
    executing: 'In the Oven',
    validating: 'Taste Test',
    review: 'Plating Review',
    deployed: 'Served!',
    awaiting_review: 'Awaiting Review',
  };

  const TAB_LABELS: Record<DetailTab, string> = {
    overview: 'Overview',
    plan: 'Plan',
    execution: 'Execution',
    changes: 'Changes',
  };

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
        background: 'rgba(42,26,16,0.65)',
        display: 'flex',
        alignItems: 'stretch',
        justifyContent: 'center',
        padding: '20px 24px',
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
          maxWidth: 900,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* ── Modal header ── */}
        <div style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 12,
          padding: '16px 20px 14px',
          borderBottom: `2px solid ${T.border}`,
          background: T.bg3,
          flexShrink: 0,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontFamily: FONTS.display,
              fontWeight: 900,
              fontSize: 22,
              color: T.text0,
              lineHeight: 1.2,
              marginBottom: 8,
            }}>
              {card.title}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.mono }}>{card.card_id.slice(0, 8)}</span>
              {card.external_id && (
                <>
                  <span style={{ fontSize: 10, color: T.text4 }}>·</span>
                  <span style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.mono }}>{card.external_id}</span>
                </>
              )}
              <span style={{ fontSize: 10, color: T.text4 }}>·</span>
              <span style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.body }}>{card.project_id}</span>
              <span style={{ fontSize: 10, color: T.text4 }}>·</span>
              <span style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.body }}>{card.program}</span>
              {card.risk_level && card.risk_level !== 'low' && (
                <>
                  <span style={{ fontSize: 10, color: T.text4 }}>·</span>
                  <span style={{ fontSize: 10, fontWeight: 700, color: riskColor, fontFamily: FONTS.body, textTransform: 'uppercase' }}>
                    {card.risk_level} risk
                  </span>
                </>
              )}
              <span style={{ fontSize: 10, color: T.text4 }}>·</span>
              <span style={{
                fontSize: 10,
                fontWeight: 700,
                color: T.ink,
                background: T.creamSoft,
                border: `1.5px solid ${T.border}`,
                borderRadius: 999,
                padding: '1px 7px',
                fontFamily: FONTS.body,
              }}>
                {colLabel[col] ?? col.replace('_', ' ')}
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
              fontSize: 18,
              cursor: 'pointer',
              padding: '2px 10px',
              fontFamily: FONTS.body,
              flexShrink: 0,
              lineHeight: 1,
            }}
          >
            {'\u00d7'}
          </button>
        </div>

        {/* ── Contextual action bar ── */}
        <div style={{
          display: 'flex',
          gap: 6,
          padding: '10px 20px',
          borderBottom: `1.5px solid ${T.borderSoft}`,
          flexWrap: 'wrap',
          flexShrink: 0,
          background: T.bg1,
          alignItems: 'center',
        }}>
          {/* intake */}
          {isIntake && onForge && (
            <ActionButton
              onClick={e => { e.stopPropagation(); onForge(card); onClose(); }}
              title="Open the Forge to generate a plan for this item"
              bg={T.cherry + '18'}
              border={`1.5px solid ${T.cherry}`}
              color={T.cherry}
            >
              Forge Plan
            </ActionButton>
          )}

          {/* queued (On Deck) — send for manager review; edit plan */}
          {isQueued && (
            <>
              <ActionButton
                onClick={handleSendForReview}
                disabled={sendReviewLoading}
                title="Send this plan for manager review before execution"
                bg={T.tangerine + '18'}
                border={`1.5px solid ${T.tangerine}`}
                color={T.tangerine}
              >
                {sendReviewLoading ? 'Sending…' : 'Send for Review'}
              </ActionButton>
              {onEditPlan && (
                <ActionButton
                  onClick={e => { e.stopPropagation(); onEditPlan(card); onClose(); }}
                  title="Edit the plan before sending for review"
                  bg={T.blueberry + '18'}
                  border={`1.5px solid ${T.blueberry}`}
                  color={T.blueberry}
                >
                  {'\u270e'} Edit Plan
                </ActionButton>
              )}
            </>
          )}

          {/* awaiting_human (Pick Up) — approve & execute, or review details */}
          {isAwaitingHuman && !gateResolved && (
            <>
              <ActionButton
                onClick={handleExecute}
                disabled={execLoading}
                title="Approve this plan and launch execution"
                bg={T.mint + '22'}
                border={`1.5px solid ${T.mint}`}
                color={T.mint}
              >
                {execLoading ? 'Launching...' : '\u25B6 Approve & Execute'}
              </ActionButton>
              <ActionButton
                onClick={e => { e.stopPropagation(); setActiveTab('execution'); }}
                title="Review gate context before deciding"
                bg={T.tangerine + '22'}
                border={`1.5px solid ${T.tangerine}`}
                color={T.tangerine}
              >
                Review Details
              </ActionButton>
            </>
          )}

          {/* executing */}
          {isExecuting && (
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

          {/* validating */}
          {isValidating && (
            <ActionButton
              onClick={e => { e.stopPropagation(); setShowProgress(true); }}
              title="View execution log and retry or skip the failing step"
              bg={T.blueberry + '18'}
              border={`1.5px solid ${T.blueberry}`}
              color={T.blueberry}
            >
              Monitor
            </ActionButton>
          )}

          {/* review */}
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

          {/* deployed — read-only; no primary action, Plan tab is view-only */}

          {/* Re-forge always available (except deployed where it would be unusual) */}
          {onForge && !isIntake && !isDeployed && (
            <ActionButton
              onClick={e => { e.stopPropagation(); onForge(card); onClose(); }}
              title="Re-open in the Forge for editing or re-planning"
              bg={T.cherry + '18'}
              border={`1.5px solid ${T.cherry}`}
              color={T.cherry}
            >
              Re-forge
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

        {/* ── Tab bar ── */}
        <div style={{
          display: 'flex',
          gap: 0,
          borderBottom: `2px solid ${T.border}`,
          background: T.bg3,
          flexShrink: 0,
          paddingLeft: 20,
        }}>
          {availableTabs.map(tab => {
            const active = activeTab === tab;
            return (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                aria-selected={active}
                role="tab"
                style={{
                  padding: '9px 18px',
                  border: 'none',
                  borderBottom: active ? `3px solid ${T.cherry}` : '3px solid transparent',
                  background: 'transparent',
                  color: active ? T.cherry : T.text1,
                  fontFamily: FONTS.body,
                  fontWeight: active ? 800 : 600,
                  fontSize: 12,
                  cursor: 'pointer',
                  marginBottom: -2,
                  transition: 'color 0.1s',
                }}
              >
                {TAB_LABELS[tab]}
              </button>
            );
          })}
        </div>

        {/* ── Tab panels ── */}
        <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>

          {/* Overview tab */}
          {activeTab === 'overview' && (
            <div style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* Status / progress strip */}
              <div style={{
                display: 'flex',
                gap: 10,
                flexWrap: 'wrap',
              }}>
                <MetaTile label="Column" value={colLabel[col] ?? col} />
                <MetaTile label="Priority" value={card.priority >= 2 ? 'P0' : card.priority >= 1 ? 'P1' : 'P2'} />
                <MetaTile label="Risk" value={card.risk_level || 'low'} color={riskColor} />
                {card.steps_total > 0 && (
                  <MetaTile label="Progress" value={`${card.steps_completed} / ${card.steps_total} steps`} />
                )}
                <MetaTile label="Gates passed" value={String(card.gates_passed)} />
              </div>

              {/* Step progress bar */}
              {card.steps_total > 0 && (
                <div>
                  <div style={{ fontSize: 10, color: T.text3, fontFamily: FONTS.body, marginBottom: 5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4 }}>
                    Step progress
                  </div>
                  <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                    {Array.from({ length: card.steps_total }).map((_, i) => (
                      <div
                        key={i}
                        style={{
                          width: 14,
                          height: 14,
                          borderRadius: 3,
                          background: i < card.steps_completed ? T.mint : T.bg3,
                          border: `1.5px solid ${T.border}`,
                        }}
                      />
                    ))}
                  </div>
                </div>
              )}

              {/* Current phase / error */}
              {card.current_phase && !card.error && (
                <div style={{
                  padding: '10px 14px',
                  background: T.bg3,
                  borderRadius: 8,
                  borderLeft: `3px solid ${isAwaitingHuman ? T.tangerine : T.cherry}`,
                }}>
                  <div style={{ fontSize: 10, color: T.text3, fontFamily: FONTS.body, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 4 }}>
                    Current phase
                  </div>
                  <div style={{ fontFamily: FONTS.hand, fontSize: 15, color: isAwaitingHuman ? T.tangerine : T.text1, lineHeight: 1.4 }}>
                    &ldquo;{card.current_phase}&rdquo;
                  </div>
                </div>
              )}
              {card.error && (
                <div style={{
                  padding: '10px 14px',
                  background: T.cherrySoft,
                  borderRadius: 8,
                  borderLeft: `3px solid ${T.cherry}`,
                }}>
                  <div style={{ fontSize: 10, color: T.cherry, fontFamily: FONTS.body, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 4 }}>
                    Error
                  </div>
                  <div style={{ fontFamily: FONTS.mono, fontSize: 11, color: T.cherry, lineHeight: 1.5, wordBreak: 'break-word' }}>
                    {card.error}
                  </div>
                </div>
              )}

              {/* Agents */}
              {card.agents.length > 0 && (
                <div>
                  <div style={{ fontSize: 10, color: T.text3, fontFamily: FONTS.body, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 6 }}>
                    Agents
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {card.agents.map(a => (
                      <span key={a} style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        padding: '3px 10px',
                        borderRadius: 999,
                        fontSize: 11,
                        fontWeight: 600,
                        color: T.blueberry,
                        background: T.blueberrySoft,
                        border: `1.5px solid ${T.border}`,
                        fontFamily: FONTS.body,
                      }}>
                        {a}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Timestamps */}
              <div style={{ display: 'flex', gap: 16, fontSize: 10, color: T.text3, fontFamily: FONTS.mono }}>
                <span>Created: {new Date(card.created_at).toLocaleString()}</span>
                <span>Updated: {new Date(card.updated_at).toLocaleString()}</span>
              </div>
            </div>
          )}

          {/* Plan tab */}
          {activeTab === 'plan' && (
            <div style={{ padding: '18px 20px' }}>
              {planLoading && (
                <div style={{ fontSize: 12, color: T.text3, fontStyle: 'italic', fontFamily: FONTS.body, padding: '30px 0', textAlign: 'center' }}>
                  Loading plan…
                </div>
              )}
              {!planLoading && planData && (
                <PlanPreview plan={planData} />
              )}
              {!planLoading && !planData && (
                <div style={{ fontSize: 12, color: T.text3, fontStyle: 'italic', fontFamily: FONTS.body, padding: '30px 0', textAlign: 'center' }}>
                  No plan on file for this card.
                  {(isIntake || isQueued) && onForge && (
                    <div style={{ marginTop: 12 }}>
                      <button
                        onClick={() => { onForge(card); onClose(); }}
                        style={{
                          padding: '6px 16px',
                          borderRadius: 8,
                          border: `1.5px solid ${T.cherry}`,
                          background: T.cherry + '18',
                          color: T.cherry,
                          fontFamily: FONTS.body,
                          fontSize: 12,
                          fontWeight: 700,
                          cursor: 'pointer',
                        }}
                      >
                        Open Forge to create a plan
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Execution tab */}
          {activeTab === 'execution' && (
            <div style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* Gate approval panel for awaiting_human */}
              {isAwaitingHuman && !gateResolved && (
                <GateApprovalPanel card={card} onResolved={handleGateResolved} />
              )}
              {isAwaitingHuman && gateResolved && (
                <div style={{ fontSize: 12, color: T.mint, fontFamily: FONTS.body, padding: '10px 0', fontWeight: 700 }}>
                  Gate resolved. Waiting for engine to update…
                </div>
              )}
              {/* Live progress for executing/validating */}
              {(isExecuting || isValidating) && (
                <div>
                  <div style={{ fontSize: 12, color: T.text1, fontFamily: FONTS.body, marginBottom: 10 }}>
                    Click <strong>Monitor</strong> in the action bar to open the live execution log.
                  </div>
                  {card.current_phase && (
                    <div style={{
                      padding: '10px 14px',
                      background: T.butterSoft,
                      borderRadius: 8,
                      borderLeft: `3px solid ${T.butter}`,
                    }}>
                      <div style={{ fontSize: 10, color: T.text3, fontFamily: FONTS.body, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 4 }}>
                        Active phase
                      </div>
                      <div style={{ fontFamily: FONTS.hand, fontSize: 15, color: T.text0, lineHeight: 1.4 }}>
                        &ldquo;{card.current_phase}&rdquo;
                      </div>
                    </div>
                  )}
                  {isValidating && card.error && (
                    <div style={{
                      marginTop: 10,
                      padding: '10px 14px',
                      background: T.cherrySoft,
                      borderRadius: 8,
                      borderLeft: `3px solid ${T.cherry}`,
                    }}>
                      <div style={{ fontSize: 10, color: T.cherry, fontFamily: FONTS.body, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 4 }}>
                        Blocked — needs attention
                      </div>
                      <div style={{ fontFamily: FONTS.mono, fontSize: 11, color: T.cherry, lineHeight: 1.5, wordBreak: 'break-word' }}>
                        {card.error}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Changes tab */}
          {activeTab === 'changes' && (
            <div style={{ padding: '18px 20px' }}>
              {isReview && card.consolidation_result ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    <MetaTile label="Files changed" value={String(card.consolidation_result.files_changed.length)} />
                    <MetaTile label="Insertions" value={`+${card.consolidation_result.total_insertions}`} color={T.mint} />
                    <MetaTile label="Deletions" value={`-${card.consolidation_result.total_deletions}`} color={T.cherry} />
                    <MetaTile label="Status" value={card.consolidation_result.status} />
                  </div>
                  <div style={{ fontSize: 10, color: T.text3, fontFamily: FONTS.body, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, marginTop: 4 }}>
                    Changed files
                  </div>
                  <div style={{
                    background: T.bg3,
                    borderRadius: 8,
                    border: `1.5px solid ${T.borderSoft}`,
                    padding: '8px 12px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 3,
                    maxHeight: 260,
                    overflowY: 'auto',
                  }}>
                    {card.consolidation_result.files_changed.map(f => (
                      <div key={f} style={{ fontFamily: FONTS.mono, fontSize: 11, color: T.text0 }}>{f}</div>
                    ))}
                  </div>
                  <div style={{ marginTop: 4 }}>
                    <ActionButton
                      onClick={e => { e.stopPropagation(); setShowChangelist(true); }}
                      title="Open full changelist reviewer"
                      bg={T.crust + '33'}
                      border={`1.5px solid ${T.crust}`}
                      color={T.crustDark}
                    >
                      Open Full Changelist
                    </ActionButton>
                  </div>
                </div>
              ) : (
                <div style={{ fontSize: 12, color: T.text3, fontStyle: 'italic', fontFamily: FONTS.body, padding: '30px 0', textAlign: 'center' }}>
                  {isDeployed
                    ? 'Delivered — changelist no longer available inline.'
                    : 'No consolidation result yet. Run review to generate.'}
                </div>
              )}
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
// MetaTile — compact label+value tile used in the Overview tab
// ----------------------------------------------------------------
function MetaTile({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{
      padding: '7px 12px',
      background: T.bg2,
      borderRadius: 10,
      border: `1.5px solid ${T.border}`,
      boxShadow: SHADOWS.sm,
      minWidth: 80,
    }}>
      <div style={{ fontFamily: FONTS.body, fontSize: 9, color: T.text2, textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ fontFamily: FONTS.display, fontSize: 14, fontWeight: 900, color: color ?? T.text0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 130 }}>
        {value}
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
