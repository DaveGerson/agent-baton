import { memo, useState, useEffect, useRef } from 'react';
import type { PmoCard, ForgePlanResponse } from '../api/types';
import { T, PRIORITY_COLOR, programColor } from '../styles/tokens';
import { api } from '../api/client';
import { agentDisplayName } from '../utils/agent-names';
import { useToast } from '../contexts/ToastContext';
import { PlanPreview } from './PlanPreview';
import { ExecutionProgress } from './ExecutionProgress';
import { GateApprovalPanel } from './GateApprovalPanel';

interface KanbanCardProps {
  card: PmoCard;
  columnColor: string;
  onForge?: (card: PmoCard) => void;
  onEditPlan?: (card: PmoCard) => void;
  onMutateCard?: (cardId: string, updater: (card: PmoCard) => PmoCard) => void;
}

function Chip({ children, color = T.text2 }: { children: React.ReactNode; color?: string }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 3,
      padding: '1px 6px',
      borderRadius: 3,
      fontSize: 9,
      fontWeight: 600,
      color,
      background: color + '14',
      border: `1px solid ${color}22`,
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

  async function handleViewPlan(e: React.MouseEvent) {
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

  async function handleExecute(e: React.MouseEvent) {
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

  function dismissExecResult(e: React.MouseEvent) {
    e.stopPropagation();
    setExecResult(null);
  }

  return { execLoading, execResult, handleExecute, dismissExecResult };
}

function KanbanCardImpl({ card, columnColor, onForge, onEditPlan, onMutateCard }: KanbanCardProps) {
  const [expanded, setExpanded] = useState(false);
  const toast = useToast();
  const { showPlan, planData, planLoading, handleViewPlan } = usePlanPreview(card.card_id);
  const { execLoading, execResult, handleExecute, dismissExecResult } = useExecuteCard(card, toast, onMutateCard);
  const [showProgress, setShowProgress] = useState(false);
  const [gateResolved, setGateResolved] = useState(false);
  const isHuman = card.column === 'awaiting_human';
  const isQueued = card.column === 'queued';
  const isActive = card.column === 'executing' || card.column === 'validating' || card.column === 'awaiting_human';

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

  const borderColor = isHuman ? T.orange + '55' : expanded ? columnColor + '55' : T.border;

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
      style={{
        background: T.bg1,
        borderRadius: 4,
        border: `1px solid ${borderColor}`,
        cursor: 'pointer',
        overflow: 'hidden',
        transition: 'border-color 0.15s',
        boxShadow: isHuman ? `0 0 8px ${T.orange}10` : 'none',
      }}
      onMouseEnter={e => {
        if (!expanded) {
          (e.currentTarget as HTMLDivElement).style.borderColor = columnColor + '66';
        }
      }}
      onMouseLeave={e => {
        if (!expanded) {
          (e.currentTarget as HTMLDivElement).style.borderColor = borderColor;
        }
      }}
    >
      <div style={{ padding: '7px 8px 6px' }}>
        {/* Title row */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 4, marginBottom: 3 }}>
          <ProgramDot program={card.program} size={6} />
          <div style={{
            fontSize: 12,
            fontWeight: 600,
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
              style={{ fontSize: 9, color: T.text2, fontFamily: 'monospace', fontWeight: 600 }}
            >
              {card.external_id}
            </span>
          ) : (
            <span
              title={card.card_id}
              style={{ fontSize: 9, color: T.text4, fontFamily: 'monospace' }}
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
        </div>

        {/* Step progress pips */}
        {card.steps_total > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 3 }}>
            {card.steps_total <= 12 && (
              <Pips done={card.steps_completed} total={card.steps_total} color={columnColor} />
            )}
            <span style={{ fontSize: 9, color: T.text3 }}>
              {card.steps_completed}/{card.steps_total}
            </span>
          </div>
        )}

        {/* Current phase / error */}
        {card.current_phase && !card.error && (
          <div style={{
            fontSize: 9,
            color: isHuman ? T.orange : T.text2,
            lineHeight: 1.2,
            marginTop: 2,
            padding: '2px 4px',
            background: T.bg2,
            borderRadius: 2,
            borderLeft: `2px solid ${isHuman ? T.orange : columnColor}`,
          }}>
            {card.current_phase.length > 65
              ? card.current_phase.slice(0, 65) + '…'
              : card.current_phase}
          </div>
        )}
        {card.error && (
          <div style={{
            fontSize: 9,
            color: T.red,
            lineHeight: 1.2,
            marginTop: 2,
            padding: '2px 4px',
            background: T.bg2,
            borderRadius: 2,
            borderLeft: `2px solid ${T.red}`,
          }}>
            {card.error.length > 80 ? card.error.slice(0, 80) + '…' : card.error}
          </div>
        )}

        {/* Footer */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 5 }}>
          <span style={{ fontSize: 9, color: T.text3 }}>{card.project_id}</span>
          {card.agents.length > 0 && (
            <>
              <span style={{ fontSize: 9, color: T.text4 }}>·</span>
              <span style={{ fontSize: 9, color: T.text3 }}>
                {card.agents.slice(0, 2).map(agentDisplayName).join(', ')}
                {card.agents.length > 2 && ` +${card.agents.length - 2}`}
              </span>
            </>
          )}
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 9, color: T.text4 }}>{fmtTime(card.updated_at)}</span>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={{
          borderTop: `1px solid ${T.border}`,
          padding: '6px 8px',
          background: T.bg2,
        }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
            <div>
              <span style={{ fontSize: 9, color: T.text3 }}>Program: </span>
              <span style={{ fontSize: 9, color: T.text0, fontWeight: 600 }}>{card.program}</span>
            </div>
            <div>
              <span style={{ fontSize: 9, color: T.text3 }}>Gates passed: </span>
              <span style={{ fontSize: 9, color: T.text0, fontWeight: 600 }}>{card.gates_passed}</span>
            </div>
          </div>

          {/* Full untruncated phase/error text — only shown when expanded */}
          {card.current_phase && !card.error && card.current_phase.length > 65 && (
            <div style={{
              fontSize: 9,
              color: isHuman ? T.orange : T.text2,
              lineHeight: 1.4,
              marginBottom: 6,
              padding: '4px 6px',
              background: T.bg1,
              borderRadius: 2,
              borderLeft: `2px solid ${isHuman ? T.orange : T.accent}`,
              wordBreak: 'break-word',
            }}>
              {card.current_phase}
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
              borderLeft: `2px solid ${T.red}`,
              wordBreak: 'break-word',
            }}>
              {card.error}
            </div>
          )}
          {card.agents.length > 0 && (
            <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', marginBottom: 6 }}>
              {card.agents.map(a => (
                <Chip key={a} color={T.cyan}>{agentDisplayName(a)}</Chip>
              ))}
            </div>
          )}

          {/* Actions row */}
          <div style={{ display: 'flex', gap: 4, marginTop: 4, paddingTop: 4, borderTop: `1px solid ${T.border}` }}>
            {isQueued && (
              <button
                onClick={handleExecute}
                disabled={execLoading}
                style={{
                  padding: '3px 9px',
                  borderRadius: 3,
                  border: `1px solid ${T.green}44`,
                  background: `linear-gradient(135deg, ${T.green}18, ${T.green}0c)`,
                  color: T.green,
                  fontSize: 9,
                  fontWeight: 600,
                  cursor: execLoading ? 'not-allowed' : 'pointer',
                  opacity: execLoading ? 0.6 : 1,
                }}
                title="Launch autonomous execution for this card"
              >
                {execLoading ? 'Launching...' : '\u25B6 Execute'}
              </button>
            )}
            {isActive && (
              <button
                onClick={e => { e.stopPropagation(); setShowProgress(true); }}
                style={{
                  padding: '3px 9px',
                  borderRadius: 3,
                  border: `1px solid ${T.yellow}44`,
                  background: T.yellow + '12',
                  color: T.yellow,
                  fontSize: 9,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
                title="Monitor execution progress in real time"
              >
                Monitor
              </button>
            )}
            {onForge && (
              <button
                onClick={e => { e.stopPropagation(); onForge(card); }}
                style={{
                  padding: '3px 9px',
                  borderRadius: 3,
                  border: `1px solid ${T.accent}44`,
                  background: T.accent + '12',
                  color: T.accent,
                  fontSize: 9,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
                title="Open this card in the Forge for editing or re-planning"
              >
                Re-forge
              </button>
            )}
            {card.steps_total > 0 && onEditPlan && (
              <button
                onClick={e => { e.stopPropagation(); onEditPlan(card); }}
                style={{
                  padding: '3px 9px',
                  borderRadius: 3,
                  border: `1px solid ${T.accent}33`,
                  background: T.accent + '15',
                  color: T.accent,
                  fontSize: 9,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
                title="Jump directly to the Forge plan editor for this card"
              >
                {'\u270e'} Edit Plan
              </button>
            )}
            <button
              onClick={handleViewPlan}
              style={{
                padding: '3px 9px',
                borderRadius: 3,
                border: `1px solid ${T.green}44`,
                background: showPlan ? T.green + '18' : T.green + '0c',
                color: T.green,
                fontSize: 9,
                fontWeight: 600,
                cursor: 'pointer',
              }}
              title="View the execution plan for this card"
            >
              {showPlan ? 'Hide Plan' : 'View Plan'}
            </button>
          </div>

          {/* Gate approval panel — only visible when card is awaiting human input */}
          {isHuman && !gateResolved && (
            <GateApprovalPanel card={card} onResolved={handleGateResolved} />
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
                color: execResult.startsWith('Launched') ? T.green : T.red,
                padding: '3px 6px',
                marginTop: 4,
                background: T.bg1,
                borderRadius: 3,
              }}
            >
              <span style={{ flex: 1 }}>{execResult}</span>
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
                borderRadius: 4,
                border: `1px solid ${T.border}`,
                background: T.bg1,
                padding: 6,
              }}
            >
              {planLoading && (
                <div style={{ fontSize: 9, color: T.text3, fontStyle: 'italic', padding: 8 }}>
                  Loading plan…
                </div>
              )}
              {!planLoading && planData && (
                <PlanPreview plan={planData} collapsible />
              )}
              {!planLoading && !planData && (
                <div style={{ fontSize: 9, color: T.text3, fontStyle: 'italic', padding: 8 }}>
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
    </div>
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
