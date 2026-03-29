import { useState, useRef, useEffect, useMemo } from 'react';
import { T, FONT_SIZES } from '../styles/tokens';
import type { ForgePlanResponse, ForgePlanPhase, ForgePlanStep } from '../api/types';
import { agentDisplayName } from '../utils/agent-names';

const AGENT_LIST = [
  'backend-engineer',
  'frontend-engineer',
  'test-engineer',
  'architect',
  'security-reviewer',
  'devops-engineer',
  'data-engineer',
] as const;

const AGENT_DESCRIPTIONS: Record<string, string> = {
  'backend-engineer': 'Server-side implementation, APIs, business logic',
  'frontend-engineer': 'Client-side UI, components, styling',
  'test-engineer': 'Test suites, coverage, quality assurance',
  'architect': 'System design, module boundaries, tech decisions',
  'security-reviewer': 'Security audit, OWASP, auth, secrets',
  'devops-engineer': 'Infrastructure, CI/CD, Docker, deployment',
  'data-engineer': 'Database schema, migrations, ETL pipelines',
};

interface PlanEditorProps {
  plan: ForgePlanResponse;
  onPlanChange: (plan: ForgePlanResponse) => void;
  onDraftSave?: () => void;
  projectId: string;
}

export function PlanEditor({ plan, onPlanChange, onDraftSave, projectId }: PlanEditorProps) {
  const [expandedPhase, setExpandedPhase] = useState<number | null>(0);
  const [editingStep, setEditingStep] = useState<string | null>(null);
  const [draftSaved, setDraftSaved] = useState(false);
  const [lastSaveTime, setLastSaveTime] = useState<string | null>(null);
  const [dragState, setDragState] = useState<{ phaseIdx: number; stepIdx: number } | null>(null);
  const [dropTarget, setDropTarget] = useState<{ phaseIdx: number; stepIdx: number } | null>(null);
  const originalPlanRef = useRef<string>(JSON.stringify(plan));

  const isDirty = useMemo(() => JSON.stringify(plan) !== originalPlanRef.current, [plan]);

  // Reset the original snapshot when the plan prop is replaced wholesale
  // (e.g. after regeneration ForgePanel sets a brand-new plan).
  useEffect(() => {
    originalPlanRef.current = JSON.stringify(plan);
  // We intentionally only reset on task_id change — not on every edit.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan.task_id]);

  function handleSaveDraft() {
    try {
      localStorage.setItem('pmo:plan-draft', JSON.stringify({ plan, project_id: projectId }));
    } catch {
      // Storage unavailable — silently ignore.
    }
    setLastSaveTime(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    setDraftSaved(true);
    onDraftSave?.();
    setTimeout(() => setDraftSaved(false), 2000);
  }

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
    updatePhase(phaseIdx, phase => {
      const maxStepNum = phase.steps.reduce((max, s) => {
        const num = parseInt(s.step_id.split('.').pop() || '0', 10);
        return num > max ? num : max;
      }, 0);
      const newStepId = `${phaseIdx + 1}.${maxStepNum + 1}`;
      return {
        ...phase,
        steps: [...phase.steps, {
          step_id: newStepId,
          agent_name: 'backend-engineer',
          task_description: 'New step',
          model: 'sonnet',
          depends_on: [],
          deliverables: [],
          allowed_paths: [],
          blocked_paths: [],
          context_files: [],
        }],
      };
    });
  }

  function removePhase(phaseIdx: number) {
    onPlanChange({ ...plan, phases: plan.phases.filter((_, i) => i !== phaseIdx) });
  }

  function addPhase() {
    const newPhaseId = plan.phases.length;
    onPlanChange({
      ...plan,
      phases: [...plan.phases, {
        phase_id: newPhaseId,
        name: `Phase ${newPhaseId + 1}`,
        steps: [],
        gate: undefined,
      } as ForgePlanPhase],
    });
    setExpandedPhase(newPhaseId);
  }

  function handleDragStart(phaseIdx: number, stepIdx: number) {
    setDragState({ phaseIdx, stepIdx });
    setDropTarget(null);
  }

  function handleDragOver(e: React.DragEvent, phaseIdx: number, stepIdx: number) {
    e.preventDefault();
    if (!dragState || dragState.phaseIdx !== phaseIdx) return;
    if (dropTarget?.phaseIdx !== phaseIdx || dropTarget?.stepIdx !== stepIdx) {
      setDropTarget({ phaseIdx, stepIdx });
    }
  }

  function handleDrop(e: React.DragEvent, phaseIdx: number, targetIdx: number) {
    e.preventDefault();
    if (!dragState || dragState.phaseIdx !== phaseIdx) {
      setDragState(null);
      setDropTarget(null);
      return;
    }
    const fromIdx = dragState.stepIdx;
    if (fromIdx !== targetIdx) {
      updatePhase(phaseIdx, phase => {
        const steps = [...phase.steps];
        const [moved] = steps.splice(fromIdx, 1);
        steps.splice(targetIdx, 0, moved);
        return { ...phase, steps };
      });
    }
    setDragState(null);
    setDropTarget(null);
  }

  function handleDragEnd() {
    setDragState(null);
    setDropTarget(null);
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Stats bar */}
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <Stat label="Phases" value={String(plan.phases.length)} />
        <Stat label="Steps" value={String(totalSteps)} />
        <Stat label="Gates" value={String(gateCount)} color={T.yellow} />
        <Stat label="Risk" value={plan.risk_level} color={plan.risk_level === 'LOW' ? T.green : T.red} />
        <div style={{ flex: 1 }} />
        <button
          onClick={handleSaveDraft}
          aria-label="Save draft to local storage"
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            padding: '3px 10px', borderRadius: 4,
            background: T.green + '15',
            color: T.green,
            border: `1px solid ${T.green}33`,
            fontSize: FONT_SIZES.xs, fontWeight: 600, cursor: 'pointer',
          }}
        >
          {isDirty && (
            <span
              aria-label="unsaved changes"
              style={{
                width: 4, height: 4, borderRadius: '50%',
                background: '#f97316', flexShrink: 0,
                display: 'inline-block',
              }}
            />
          )}
          {draftSaved ? 'Saved \u2713' : 'Save Draft'}
        </button>
        {lastSaveTime && (
          <span style={{ fontSize: FONT_SIZES.xs, color: T.text3, fontStyle: 'italic' }}>
            Draft saved at {lastSaveTime}
          </span>
        )}
      </div>

      {/* Summary */}
      {plan.task_summary && (
        <div style={{
          padding: '8px 12px',
          background: T.bg2,
          borderRadius: 4,
          borderLeft: `3px solid ${T.accent}`,
        }}>
          <div style={{ fontSize: 9, color: T.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 3 }}>Summary</div>
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
            {/* Phase header — button separated from toggle to avoid nested-interactive */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              background: T.bg2,
              borderBottom: isExpanded ? `1px solid ${T.border}` : 'none',
            }}>
              <div
                role="button"
                tabIndex={0}
                aria-expanded={isExpanded}
                aria-controls={`phase-content-${pi}`}
                id={`phase-header-${pi}`}
                onClick={() => setExpandedPhase(isExpanded ? null : pi)}
                onKeyDown={e => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setExpandedPhase(isExpanded ? null : pi);
                  }
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '6px 10px',
                  flex: 1,
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
                <span style={{ fontSize: 9, color: T.text3, background: T.bg3, padding: '1px 4px', borderRadius: 3 }}>
                  {phase.steps.length} steps
                </span>
                {phase.gate && (
                  <span style={{ fontSize: 9, color: T.yellow, background: T.yellow + '14', border: `1px solid ${T.yellow}22`, padding: '1px 4px', borderRadius: 3 }}>
                    gate
                  </span>
                )}
              </div>
              <button
                aria-label={`Remove phase ${pi + 1}: ${phase.name}`}
                onClick={() => removePhase(pi)}
                style={{ background: 'none', border: 'none', color: T.text3, fontSize: 10, cursor: 'pointer', padding: '0 8px' }}
                title="Remove phase"
              >
                {'\u00d7'}
              </button>
            </div>

            {/* Steps region — always in DOM so aria-controls points to valid element */}
            <div
              id={`phase-content-${pi}`}
              role="region"
              aria-labelledby={`phase-header-${pi}`}
              hidden={!isExpanded}
            >
              {phase.steps.map((step, si) => {
                const isDragging = dragState?.phaseIdx === pi && dragState?.stepIdx === si;
                const isDropTarget = dropTarget?.phaseIdx === pi && dropTarget?.stepIdx === si && !isDragging;
                return (
                <div
                  key={step.step_id}
                  draggable
                  onDragStart={() => handleDragStart(pi, si)}
                  onDragOver={e => handleDragOver(e, pi, si)}
                  onDrop={e => handleDrop(e, pi, si)}
                  onDragEnd={handleDragEnd}
                  style={{
                    display: 'flex', alignItems: 'flex-start', gap: 6, padding: '5px 10px',
                    borderBottom: si < phase.steps.length - 1 ? `1px solid ${T.border}` : 'none',
                    borderTop: isDropTarget ? `2px solid ${T.accent}` : undefined,
                    opacity: isDragging ? 0.45 : 1,
                    transition: 'opacity 0.1s',
                  }}
                >
                  {/* Drag handle */}
                  <span
                    aria-hidden="true"
                    style={{ cursor: 'grab', color: T.text3, fontSize: 11, flexShrink: 0, lineHeight: 1, paddingTop: 4, userSelect: 'none' }}
                    title="Drag to reorder"
                  >
                    {'⠿'}
                  </span>

                  {/* Reorder buttons (keyboard fallback) */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 1, flexShrink: 0 }}>
                    <button
                      aria-label={`Move step ${si + 1} up`}
                      onClick={() => moveStep(pi, si, -1)}
                      disabled={si === 0}
                      style={{ background: 'none', border: 'none', color: si === 0 ? T.bg3 : T.text3, fontSize: 8, cursor: si === 0 ? 'default' : 'pointer', padding: 0, lineHeight: 1, minWidth: 24, minHeight: 24, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                    >{'\u25b2'}</button>
                    <button
                      aria-label={`Move step ${si + 1} down`}
                      onClick={() => moveStep(pi, si, 1)}
                      disabled={si === phase.steps.length - 1}
                      style={{ background: 'none', border: 'none', color: si === phase.steps.length - 1 ? T.bg3 : T.text3, fontSize: 8, cursor: si === phase.steps.length - 1 ? 'default' : 'pointer', padding: 0, lineHeight: 1, minWidth: 24, minHeight: 24, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
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
                        style={{ fontSize: 9, color: T.text0, fontWeight: 500, cursor: 'text', minHeight: 16 }}
                        title="Click to edit"
                      >
                        {step.task_description || (
                          <span style={{ color: T.text3, fontStyle: 'italic' }}>Click to add description</span>
                        )}
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
                        <option key={a} value={a} title={AGENT_DESCRIPTIONS[a] || ''}>{agentDisplayName(a)}</option>
                      ))}
                      {/* Preserve current value if it's not in the standard list */}
                      {!AGENT_LIST.includes(step.agent_name as typeof AGENT_LIST[number]) && (
                        <option value={step.agent_name} title={AGENT_DESCRIPTIONS[step.agent_name] || ''}>{agentDisplayName(step.agent_name)}</option>
                      )}
                    </select>
                  ) : (
                    <span style={{
                      fontSize: 9, color: T.cyan, background: T.cyan + '14',
                      border: `1px solid ${T.cyan}22`, padding: '1px 5px',
                      borderRadius: 3, whiteSpace: 'nowrap', flexShrink: 0,
                    }}>
                      {agentDisplayName(step.agent_name)}
                    </span>
                  )}

                  {/* Remove step */}
                  <button
                    aria-label={`Remove step ${si + 1}: ${step.task_description.slice(0, 40)}`}
                    onClick={() => removeStep(pi, si)}
                    style={{ background: 'none', border: 'none', color: T.text3, fontSize: 10, cursor: 'pointer', padding: '0 2px', flexShrink: 0 }}
                    title="Remove step"
                  >
                    {'\u00d7'}
                  </button>
                </div>
                );
              })}

              {/* CJ-12: empty phase placeholder */}
              {phase.steps.length === 0 && (
                <div style={{ padding: '8px 12px', fontSize: 9, color: T.text3, fontStyle: 'italic' }}>
                  No steps. Add a step or remove this phase.
                </div>
              )}

              {/* Add step button */}
              <div style={{ padding: '4px 10px' }}>
                <button
                  onClick={() => addStep(pi)}
                  style={{
                    padding: '2px 8px', borderRadius: 3,
                    border: `1px dashed ${T.border}`, background: 'transparent',
                    color: T.text3, fontSize: 9, cursor: 'pointer',
                  }}
                >
                  + Add step
                </button>
              </div>
            </div>
          </div>
        );
      })}
      <button
        onClick={addPhase}
        style={{
          padding: '4px 12px',
          borderRadius: 4,
          border: `1px dashed ${T.border}`,
          background: 'transparent',
          color: T.text3,
          fontSize: 9,
          cursor: 'pointer',
          width: '100%',
        }}
      >
        + Add Phase
      </button>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ padding: '4px 8px', background: T.bg2, borderRadius: 4 }}>
      <div style={{ fontSize: 9, color: T.text3, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: FONT_SIZES.md, fontWeight: 700, color: color ?? T.text0, fontFamily: 'monospace' }}>{value}</div>
    </div>
  );
}
