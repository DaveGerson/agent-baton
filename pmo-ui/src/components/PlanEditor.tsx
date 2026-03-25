import { useState } from 'react';
import { T } from '../styles/tokens';
import type { ForgePlanResponse, ForgePlanPhase, ForgePlanStep } from '../api/types';

const AGENT_LIST = [
  'backend-engineer',
  'frontend-engineer',
  'test-engineer',
  'architect',
  'security-reviewer',
  'devops-engineer',
  'data-engineer',
] as const;

interface PlanEditorProps {
  plan: ForgePlanResponse;
  onPlanChange: (plan: ForgePlanResponse) => void;
}

export function PlanEditor({ plan, onPlanChange }: PlanEditorProps) {
  const [expandedPhase, setExpandedPhase] = useState<number | null>(0);
  const [editingStep, setEditingStep] = useState<string | null>(null);

  const totalSteps = plan.phases.reduce((acc, ph) => acc + ph.steps.length, 0);
  const gateCount = plan.phases.filter(p => p.gate).length;

  function updatePhase(phaseIdx: number, updater: (phase: ForgePlanPhase) => ForgePlanPhase) {
    const newPhases = plan.phases.map((p, i) => i === phaseIdx ? updater({ ...p }) : p);
    onPlanChange({ ...plan, phases: newPhases });
  }

  function updateStep(phaseIdx: number, stepIdx: number, updater: (step: ForgePlanStep) => ForgePlanStep) {
    updatePhase(phaseIdx, phase => ({
      ...phase,
      steps: phase.steps.map((s, i) => i === stepIdx ? updater({ ...s }) : s),
    }));
  }

  function removeStep(phaseIdx: number, stepIdx: number) {
    updatePhase(phaseIdx, phase => ({
      ...phase,
      steps: phase.steps.filter((_, i) => i !== stepIdx),
    }));
  }

  function moveStep(phaseIdx: number, stepIdx: number, direction: -1 | 1) {
    updatePhase(phaseIdx, phase => {
      const steps = [...phase.steps];
      const newIdx = stepIdx + direction;
      if (newIdx < 0 || newIdx >= steps.length) return phase;
      [steps[stepIdx], steps[newIdx]] = [steps[newIdx], steps[stepIdx]];
      return { ...phase, steps };
    });
  }

  function addStep(phaseIdx: number) {
    updatePhase(phaseIdx, phase => ({
      ...phase,
      steps: [...phase.steps, {
        step_id: `${phase.phase_id + 1}.${phase.steps.length + 1}`,
        agent_name: 'backend-engineer',
        task_description: 'New step',
        model: 'sonnet',
        depends_on: [],
        deliverables: [],
        allowed_paths: [],
        blocked_paths: [],
        context_files: [],
      }],
    }));
  }

  function removePhase(phaseIdx: number) {
    onPlanChange({ ...plan, phases: plan.phases.filter((_, i) => i !== phaseIdx) });
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Stats bar */}
      <div style={{ display: 'flex', gap: 6 }}>
        <Stat label="Phases" value={String(plan.phases.length)} />
        <Stat label="Steps" value={String(totalSteps)} />
        <Stat label="Gates" value={String(gateCount)} color={T.yellow} />
        <Stat label="Risk" value={plan.risk_level} color={plan.risk_level === 'LOW' ? T.green : T.red} />
      </div>

      {/* Summary */}
      {plan.task_summary && (
        <div style={{
          padding: '8px 12px',
          background: T.bg2,
          borderRadius: 4,
          borderLeft: `3px solid ${T.accent}`,
        }}>
          <div style={{ fontSize: 7, color: T.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 3 }}>Summary</div>
          <div style={{ fontSize: 10, color: T.text1, lineHeight: 1.55 }}>{plan.task_summary}</div>
        </div>
      )}

      {/* Phases */}
      {plan.phases.map((phase, pi) => {
        const isExpanded = expandedPhase === pi;
        return (
          <div key={phase.phase_id} style={{
            background: T.bg1,
            borderRadius: 4,
            border: `1px solid ${T.border}`,
            overflow: 'hidden',
          }}>
            {/* Phase header */}
            <div
              onClick={() => setExpandedPhase(isExpanded ? null : pi)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '6px 10px',
                background: T.bg2,
                borderBottom: isExpanded ? `1px solid ${T.border}` : 'none',
                cursor: 'pointer',
              }}
            >
              <div style={{
                width: 16, height: 16, borderRadius: 3,
                background: T.accent + '20', border: `1px solid ${T.accent}33`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 8, fontWeight: 700, color: T.accent, flexShrink: 0,
              }}>
                {pi + 1}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 9, fontWeight: 700, color: T.text0 }}>{phase.name}</div>
              </div>
              <span style={{ fontSize: 7, color: T.text3, background: T.bg3, padding: '1px 4px', borderRadius: 3 }}>
                {phase.steps.length} steps
              </span>
              {phase.gate && (
                <span style={{ fontSize: 7, color: T.yellow, background: T.yellow + '14', border: `1px solid ${T.yellow}22`, padding: '1px 4px', borderRadius: 3 }}>
                  gate
                </span>
              )}
              <button
                onClick={e => { e.stopPropagation(); removePhase(pi); }}
                style={{ background: 'none', border: 'none', color: T.text3, fontSize: 10, cursor: 'pointer', padding: '0 4px' }}
                title="Remove phase"
              >
                {'\u00d7'}
              </button>
            </div>

            {/* Steps (when expanded) */}
            {isExpanded && (
              <>
                {phase.steps.map((step, si) => (
                  <div key={step.step_id} style={{
                    display: 'flex', alignItems: 'flex-start', gap: 6, padding: '5px 10px',
                    borderBottom: si < phase.steps.length - 1 ? `1px solid ${T.border}` : 'none',
                  }}>
                    {/* Reorder buttons */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 1, flexShrink: 0 }}>
                      <button
                        onClick={() => moveStep(pi, si, -1)}
                        disabled={si === 0}
                        style={{ background: 'none', border: 'none', color: si === 0 ? T.bg3 : T.text3, fontSize: 8, cursor: si === 0 ? 'default' : 'pointer', padding: 0, lineHeight: 1 }}
                      >{'\u25b2'}</button>
                      <button
                        onClick={() => moveStep(pi, si, 1)}
                        disabled={si === phase.steps.length - 1}
                        style={{ background: 'none', border: 'none', color: si === phase.steps.length - 1 ? T.bg3 : T.text3, fontSize: 8, cursor: si === phase.steps.length - 1 ? 'default' : 'pointer', padding: 0, lineHeight: 1 }}
                      >{'\u25bc'}</button>
                    </div>

                    {/* Step content */}
                    <div style={{ flex: 1 }}>
                      {editingStep === step.step_id ? (
                        <input
                          autoFocus
                          value={step.task_description}
                          onChange={e => updateStep(pi, si, s => ({ ...s, task_description: e.target.value }))}
                          onBlur={() => setEditingStep(null)}
                          onKeyDown={e => e.key === 'Enter' && setEditingStep(null)}
                          style={{
                            width: '100%', padding: '2px 4px', borderRadius: 3,
                            border: `1px solid ${T.accent}`, background: T.bg2,
                            color: T.text0, fontSize: 9, outline: 'none',
                          }}
                        />
                      ) : (
                        <div
                          onClick={() => setEditingStep(step.step_id)}
                          style={{ fontSize: 9, color: T.text0, fontWeight: 500, cursor: 'text' }}
                          title="Click to edit"
                        >
                          {step.task_description}
                        </div>
                      )}
                    </div>

                    {/* Agent chip — dropdown when editing, badge when not */}
                    {editingStep === step.step_id ? (
                      <select
                        value={step.agent_name}
                        onChange={e => updateStep(pi, si, s => ({ ...s, agent_name: e.target.value }))}
                        onClick={e => e.stopPropagation()}
                        style={{
                          fontSize: 9,
                          color: T.cyan,
                          background: T.bg3,
                          border: `1px solid ${T.cyan}44`,
                          borderRadius: 3,
                          padding: '1px 4px',
                          outline: 'none',
                          flexShrink: 0,
                          cursor: 'pointer',
                        }}
                      >
                        {AGENT_LIST.map(a => (
                          <option key={a} value={a}>{a}</option>
                        ))}
                        {/* Preserve current value if it's not in the standard list */}
                        {!AGENT_LIST.includes(step.agent_name as typeof AGENT_LIST[number]) && (
                          <option value={step.agent_name}>{step.agent_name}</option>
                        )}
                      </select>
                    ) : (
                      <span style={{
                        fontSize: 9, color: T.cyan, background: T.cyan + '14',
                        border: `1px solid ${T.cyan}22`, padding: '1px 5px',
                        borderRadius: 3, whiteSpace: 'nowrap', flexShrink: 0,
                      }}>
                        {step.agent_name}
                      </span>
                    )}

                    {/* Remove step */}
                    <button
                      onClick={() => removeStep(pi, si)}
                      style={{ background: 'none', border: 'none', color: T.text3, fontSize: 10, cursor: 'pointer', padding: '0 2px', flexShrink: 0 }}
                      title="Remove step"
                    >
                      {'\u00d7'}
                    </button>
                  </div>
                ))}

                {/* Add step button */}
                <div style={{ padding: '4px 10px' }}>
                  <button
                    onClick={() => addStep(pi)}
                    style={{
                      padding: '2px 8px', borderRadius: 3,
                      border: `1px dashed ${T.border}`, background: 'transparent',
                      color: T.text3, fontSize: 8, cursor: 'pointer',
                    }}
                  >
                    + Add step
                  </button>
                </div>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ padding: '4px 8px', background: T.bg2, borderRadius: 4 }}>
      <div style={{ fontSize: 7, color: T.text3, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 12, fontWeight: 700, color: color ?? T.text0, fontFamily: 'monospace' }}>{value}</div>
    </div>
  );
}
