import type { ForgePlanResponse } from '../api/types';
import { T } from '../styles/tokens';
import { agentDisplayName } from '../utils/agent-names';

interface PlanPreviewProps {
  plan: ForgePlanResponse;
}

export function PlanPreview({ plan }: PlanPreviewProps) {
  const totalSteps = plan.phases.reduce((acc, ph) => acc + ph.steps.length, 0);

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
          background: T.bg2,
          borderRadius: 4,
          borderLeft: `3px solid ${T.accent}`,
        }}>
          <div style={{ fontSize: 9, color: T.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 3 }}>
            Summary
          </div>
          <div style={{ fontSize: 10, color: T.text1, lineHeight: 1.55 }}>
            {plan.task_summary}
          </div>
        </div>
      )}

      {/* Phases & steps */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {plan.phases.map((phase, pi) => (
          <div key={String(phase.phase_id)} style={{
            background: T.bg1,
            borderRadius: 4,
            border: `1px solid ${T.border}`,
            overflow: 'hidden',
          }}>
            {/* Phase header */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 10px',
              background: T.bg2,
              borderBottom: `1px solid ${T.border}`,
            }}>
              <div style={{
                width: 16,
                height: 16,
                borderRadius: 3,
                background: T.accent + '20',
                border: `1px solid ${T.accent}33`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 8,
                fontWeight: 700,
                color: T.accent,
                flexShrink: 0,
              }}>
                {pi + 1}
              </div>
              <div>
                <div style={{ fontSize: 9, fontWeight: 700, color: T.text0 }}>{phase.name}</div>
              </div>
              <span style={{
                marginLeft: 'auto',
                fontSize: 9,
                color: T.text3,
                background: T.bg3,
                padding: '1px 4px',
                borderRadius: 3,
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
                  borderBottom: si < phase.steps.length - 1 ? `1px solid ${T.border}` : 'none',
                }}
              >
                <div style={{
                  width: 14,
                  height: 14,
                  borderRadius: 2,
                  background: T.bg3,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 9,
                  color: T.text3,
                  flexShrink: 0,
                  marginTop: 1,
                }}>
                  {si + 1}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 9, color: T.text0, fontWeight: 500 }}>
                    {step.task_description}
                  </div>
                </div>
                {step.agent_name && (
                  <span style={{
                    fontSize: 9,
                    color: T.cyan,
                    background: T.cyan + '14',
                    border: `1px solid ${T.cyan}22`,
                    padding: '1px 5px',
                    borderRadius: 3,
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
      style={{ padding: '6px 10px', background: T.bg2, borderRadius: 4, minWidth: 60 }}
    >
      <div style={{ fontSize: 9, color: T.text3, textTransform: 'uppercase', letterSpacing: 0.4 }}>
        {label}
      </div>
      <div
        title={String(value)}
        style={{
          fontSize: 13,
          fontWeight: 700,
          color: color ?? T.text0,
          fontFamily: mono ? 'monospace' : 'inherit',
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
