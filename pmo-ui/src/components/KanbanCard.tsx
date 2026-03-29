import { useState, useEffect, useRef } from 'react';
import type { PmoCard, ForgePlanResponse } from '../api/types';
import { T, PRIORITY_COLOR } from '../styles/tokens';
import { api } from '../api/client';
import { agentDisplayName } from '../utils/agent-names';
import { useToast } from '../contexts/ToastContext';

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
      fontSize: 8,
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
  const color = programDotColor(program);
  return (
    <div
      title={program}
      style={{ width: size, height: size, borderRadius: 2, background: color, flexShrink: 0 }}
    />
  );
}

export function KanbanCard({ card, columnColor, onForge, onEditPlan, onMutateCard }: KanbanCardProps) {
  const toast = useToast();
  const [expanded, setExpanded] = useState(false);
  const [showPlan, setShowPlan] = useState(false);
  const [planData, setPlanData] = useState<ForgePlanResponse | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [execLoading, setExecLoading] = useState(false);
  const [execResult, setExecResult] = useState<string | null>(null);
  const execTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isHuman = card.column === 'awaiting_human';
  const isQueued = card.column === 'queued';
  const priorityColor = PRIORITY_COLOR[card.priority] ?? T.text2;

  useEffect(() => {
    return () => {
      if (execTimerRef.current) clearTimeout(execTimerRef.current);
    };
  }, []);

  async function handleExecute(e: React.MouseEvent) {
    e.stopPropagation();
    if (execTimerRef.current) clearTimeout(execTimerRef.current);
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

  async function handleViewPlan(e: React.MouseEvent) {
    e.stopPropagation();
    if (showPlan) {
      setShowPlan(false);
      return;
    }
    setShowPlan(true);
    if (planData) return; // already fetched — use cache
    setPlanLoading(true);
    try {
      const result = await api.getCardDetail(card.card_id);
      setPlanData(result.plan);
    } catch {
      // silent — plan unavailable
    } finally {
      setPlanLoading(false);
    }
  }

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
            <Pips done={card.steps_completed} total={card.steps_total} color={columnColor} />
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
                onClick={e => { e.stopPropagation(); setExecResult(null); }}
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
                <div style={{ fontSize: 8, color: T.text3, fontStyle: 'italic', padding: 8 }}>
                  Loading plan…
                </div>
              )}
              {!planLoading && planData && (
                <InlinePlanView plan={planData} />
              )}
              {!planLoading && !planData && (
                <div style={{ fontSize: 8, color: T.text3, fontStyle: 'italic', padding: 8 }}>
                  No plan available for this card.
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InlinePlanView({ plan }: { plan: ForgePlanResponse }) {
  const [expandedPhase, setExpandedPhase] = useState<number | null>(0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {plan.task_summary && (
        <div style={{
          fontSize: 9,
          color: T.text2,
          padding: '4px 8px',
          background: T.bg2,
          borderRadius: 3,
          borderLeft: `2px solid ${T.accent}`,
          marginBottom: 2,
        }}>
          {plan.task_summary}
        </div>
      )}
      {plan.phases.map((phase, pi) => {
        const isOpen = expandedPhase === pi;
        return (
          <div key={String(phase.phase_id)} style={{
            border: `1px solid ${T.border}`,
            borderRadius: 3,
            overflow: 'hidden',
          }}>
            <div
              role="button"
              tabIndex={0}
              onClick={() => setExpandedPhase(isOpen ? null : pi)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  setExpandedPhase(isOpen ? null : pi);
                }
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 5,
                padding: '4px 8px',
                background: T.bg2,
                cursor: 'pointer',
                borderBottom: isOpen ? `1px solid ${T.border}` : 'none',
              }}
            >
              <span style={{ fontSize: 8, color: T.text3, minWidth: 10 }}>
                {isOpen ? '▾' : '▸'}
              </span>
              <span style={{ fontSize: 9, fontWeight: 600, color: T.text0, flex: 1 }}>
                {pi + 1}. {phase.name}
              </span>
              <span style={{ fontSize: 8, color: T.text3 }}>
                {phase.steps.length} steps
              </span>
              {phase.gate && (
                <span style={{ fontSize: 8, color: T.yellow }}>gate</span>
              )}
            </div>
            {isOpen && (
              <div>
                {phase.steps.map((step, si) => (
                  <div
                    key={step.step_id}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 6,
                      padding: '4px 8px',
                      borderBottom: si < phase.steps.length - 1 ? `1px solid ${T.border}` : 'none',
                    }}
                  >
                    <span style={{ fontSize: 8, color: T.text4, minWidth: 14, flexShrink: 0 }}>
                      {si + 1}.
                    </span>
                    <span style={{ fontSize: 9, color: T.text1, flex: 1, lineHeight: 1.4 }}>
                      {step.task_description}
                    </span>
                    {step.agent_name && (
                      <span style={{
                        fontSize: 8,
                        color: T.cyan,
                        background: T.cyan + '14',
                        border: `1px solid ${T.cyan}22`,
                        padding: '1px 4px',
                        borderRadius: 3,
                        whiteSpace: 'nowrap',
                        flexShrink: 0,
                      }}>
                        {agentDisplayName(step.agent_name)}
                      </span>
                    )}
                  </div>
                ))}
                {phase.steps.length === 0 && (
                  <div style={{ fontSize: 8, color: T.text3, fontStyle: 'italic', padding: '4px 8px' }}>
                    No steps.
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '—';
  }
}

const DOT_PALETTE = [
  '#3b82f6', '#a78bfa', '#34d399', '#f87171',
  '#38bdf8', '#fb923c', '#2dd4bf', '#c084fc',
];

function programDotColor(program: string): string {
  let hash = 0;
  for (let i = 0; i < program.length; i++) {
    hash = (hash * 31 + program.charCodeAt(i)) >>> 0;
  }
  return DOT_PALETTE[hash % DOT_PALETTE.length];
}
