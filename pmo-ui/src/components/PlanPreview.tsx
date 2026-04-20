import { useState } from 'react';
import type { ForgePlanResponse } from '../api/types';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { agentDisplayName } from '../utils/agent-names';

interface PlanPreviewProps {
  plan: ForgePlanResponse;
  collapsible?: boolean;
}

export function PlanPreview({ plan, collapsible = false }: PlanPreviewProps) {
  const [expandedPhase, setExpandedPhase] = useState<number | null>(0);
  const totalSteps = plan.phases.reduce((acc, ph) => acc + ph.steps.length, 0);

  if (collapsible) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {plan.task_summary && (
          <div style={{
            fontFamily: FONTS.hand,
            fontSize: 14,
            color: T.text1,
            padding: '4px 8px',
            background: T.bg3,
            borderRadius: 8,
            borderLeft: `3px solid ${T.cherry}`,
            marginBottom: 2,
            display: 'inline-block',
            transform: 'rotate(-0.3deg)',
          }}>
            "{plan.task_summary}"
          </div>
        )}
        {plan.phases.map((phase, pi) => {
          const isOpen = expandedPhase === pi;
          return (
            <div key={String(phase.phase_id)} style={{
              border: `1.5px solid ${T.border}`,
              borderRadius: 8,
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
                  background: isOpen ? T.butterSoft : T.bg2,
                  cursor: 'pointer',
                  borderBottom: isOpen ? `1.5px solid ${T.border}` : 'none',
                  borderRadius: isOpen ? '8px 8px 0 0' : 8,
                }}
              >
                {/* Phase number badge */}
                <span style={{
                  width: 18,
                  height: 18,
                  borderRadius: '50%',
                  background: T.ink,
                  color: T.cream,
                  fontFamily: FONTS.display,
                  fontWeight: 900,
                  fontSize: 10,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}>
                  {pi + 1}
                </span>
                <span style={{
                  fontFamily: FONTS.display,
                  fontWeight: 800,
                  fontSize: 12,
                  color: T.text0,
                  flex: 1,
                }}>
                  {phase.name}
                </span>
                <span style={{
                  fontFamily: FONTS.mono,
                  fontSize: 9,
                  color: T.text2,
                }}>
                  {phase.steps.length} steps
                </span>
                {phase.gate && (
                  <span style={{ fontSize: 9, color: T.cherry }}>
                    👅 gate
                  </span>
                )}
                <span style={{ fontSize: 9, color: T.text2, marginLeft: 2 }}>
                  {isOpen ? '▾' : '▸'}
                </span>
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
                        borderBottom: si < phase.steps.length - 1
                          ? `1px dashed ${T.borderSoft}`
                          : 'none',
                      }}
                    >
                      <span style={{
                        fontFamily: FONTS.mono,
                        fontSize: 9,
                        color: T.text3,
                        minWidth: 14,
                        flexShrink: 0,
                      }}>
                        {si + 1}.
                      </span>
                      <span style={{
                        fontFamily: FONTS.body,
                        fontSize: 11,
                        fontWeight: 600,
                        color: T.text0,
                        flex: 1,
                        lineHeight: 1.4,
                      }}>
                        {step.task_description}
                      </span>
                      {step.agent_name && (
                        <span style={{
                          fontFamily: FONTS.body,
                          fontWeight: 800,
                          fontSize: 9,
                          color: T.blueberry,
                          background: T.bg1,
                          border: `1.5px solid ${T.border}`,
                          padding: '1px 5px',
                          borderRadius: 999,
                          whiteSpace: 'nowrap',
                          flexShrink: 0,
                          boxShadow: SHADOWS.sm,
                        }}>
                          {agentDisplayName(step.agent_name)}
                        </span>
                      )}
                    </div>
                  ))}
                  {phase.steps.length === 0 && (
                    <div style={{
                      fontFamily: FONTS.body,
                      fontSize: 9,
                      color: T.text3,
                      fontStyle: 'italic',
                      padding: '4px 8px',
                    }}>
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* Summary stats */}
      <div style={{ display: 'flex', gap: 8 }}>
        <StatTile label="Task ID" value={plan.task_id} mono />
        <StatTile label="Phases" value={String(plan.phases.length)} />
        <StatTile label="Steps" value={String(totalSteps)} />
        {plan.risk_level && <StatTile label="Risk" value={plan.risk_level} />}
        {plan.budget_tier && <StatTile label="Budget" value={plan.budget_tier} />}
      </div>

      {/* Summary */}
      {plan.task_summary && (
        <div style={{
          padding: '8px 12px',
          background: T.bg3,
          borderRadius: 8,
          borderLeft: `3px solid ${T.cherry}`,
        }}>
          <div style={{
            fontFamily: FONTS.body,
            fontWeight: 800,
            fontSize: 9,
            color: T.text2,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            marginBottom: 3,
          }}>
            Summary
          </div>
          <div style={{
            fontFamily: FONTS.body,
            fontSize: 11,
            color: T.text0,
            lineHeight: 1.55,
          }}>
            {plan.task_summary}
          </div>
        </div>
      )}

      {/* Phases & steps */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {plan.phases.map((phase, pi) => (
          <div key={String(phase.phase_id)} style={{
            background: T.bg1,
            borderRadius: 12,
            border: `2px solid ${T.border}`,
            boxShadow: SHADOWS.sm,
            overflow: 'hidden',
          }}>
            {/* Phase header */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 10px',
              background: T.butter,
              borderBottom: `2px solid ${T.border}`,
            }}>
              <div style={{
                width: 20,
                height: 20,
                borderRadius: '50%',
                background: T.ink,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontFamily: FONTS.display,
                fontWeight: 900,
                fontSize: 9,
                color: T.butter,
                flexShrink: 0,
              }}>
                {pi + 1}
              </div>
              <div>
                <div style={{
                  fontFamily: FONTS.display,
                  fontWeight: 800,
                  fontSize: 14,
                  color: T.text0,
                }}>
                  {phase.name}
                </div>
              </div>
              <span style={{
                marginLeft: 'auto',
                fontFamily: FONTS.mono,
                fontSize: 9,
                color: T.text2,
                background: T.bg0,
                border: `1.5px solid ${T.border}`,
                padding: '1px 5px',
                borderRadius: 4,
              }}>
                {phase.steps.length} steps
              </span>
            </div>

            {/* Steps */}
            {phase.steps.map((step, si) => (
              <div
                key={step.step_id}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 7,
                  padding: '5px 10px',
                  borderBottom: si < phase.steps.length - 1
                    ? `1px dashed ${T.borderSoft}`
                    : 'none',
                }}
              >
                <div style={{
                  width: 16,
                  height: 16,
                  borderRadius: 3,
                  background: T.bg3,
                  border: `1px solid ${T.border}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontFamily: FONTS.display,
                  fontWeight: 900,
                  fontSize: 9,
                  color: T.text0,
                  flexShrink: 0,
                  marginTop: 1,
                }}>
                  {si + 1}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{
                    fontFamily: FONTS.body,
                    fontWeight: 600,
                    fontSize: 11,
                    color: T.text0,
                  }}>
                    {step.task_description}
                  </div>
                </div>
                {step.agent_name && (
                  <span style={{
                    fontFamily: FONTS.body,
                    fontWeight: 800,
                    fontSize: 9,
                    color: T.blueberry,
                    background: T.blueberrySoft,
                    border: `1.5px solid ${T.border}`,
                    padding: '1px 5px',
                    borderRadius: 999,
                    whiteSpace: 'nowrap',
                    flexShrink: 0,
                  }}>
                    {agentDisplayName(step.agent_name)}
                  </span>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function StatTile({
  label,
  value,
  mono = false,
  color,
}: {
  label: string;
  value: string;
  mono?: boolean;
  color?: string;
}) {
  return (
    <div
      aria-label={`${label}: ${value}`}
      style={{
        padding: '6px 10px',
        background: T.bg2,
        borderRadius: 10,
        border: `1.5px solid ${T.border}`,
        boxShadow: SHADOWS.sm,
        minWidth: 60,
      }}
    >
      <div style={{
        fontFamily: FONTS.body,
        fontSize: 9,
        color: T.text2,
        textTransform: 'uppercase',
        letterSpacing: 0.4,
      }}>
        {label}
      </div>
      <div
        title={String(value)}
        style={{
          fontFamily: mono ? FONTS.mono : FONTS.display,
          fontSize: 16,
          fontWeight: 900,
          color: color ?? T.text0,
          marginTop: 1,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          maxWidth: 120,
        }}
      >
        {value}
      </div>
    </div>
  );
}
