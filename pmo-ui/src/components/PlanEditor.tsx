import { useState, useRef, useEffect, useMemo } from 'react';
import type { DragEvent, KeyboardEvent } from 'react';
import { T, FONTS, SHADOWS, FONT_SIZES } from '../styles/tokens';
import type { ForgePlanResponse, ForgePlanPhase, ForgePlanStep, ForgePlanGate } from '../api/types';
import type { Agent } from '../api/types';
import { api } from '../api/client';
import { agentDisplayName } from '../utils/agent-names';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const AGENT_LIST_FALLBACK = [
  'backend-engineer',
  'frontend-engineer',
  'test-engineer',
  'architect',
  'security-reviewer',
  'devops-engineer',
  'data-engineer',
] as const;

const AGENT_DESCRIPTIONS_FALLBACK: Record<string, string> = {
  'backend-engineer': 'Server-side implementation, APIs, business logic',
  'frontend-engineer': 'Client-side UI, components, styling',
  'test-engineer': 'Test suites, coverage, quality assurance',
  'architect': 'System design, module boundaries, tech decisions',
  'security-reviewer': 'Security audit, OWASP, auth, secrets',
  'devops-engineer': 'Infrastructure, CI/CD, Docker, deployment',
  'data-engineer': 'Database schema, migrations, ETL pipelines',
};

const MODEL_LIST = ['sonnet', 'opus', 'haiku'] as const;
type ModelOption = typeof MODEL_LIST[number];

const GATE_TYPE_LIST = ['build', 'test', 'lint', 'custom'] as const;
type GateType = typeof GATE_TYPE_LIST[number];

const STEP_TYPE_LIST = [
  'developing',
  'planning',
  'testing',
  'reviewing',
  'consulting',
  'task',
  'automation',
] as const;
type StepType = typeof STEP_TYPE_LIST[number];

// Agent role colors — warm kitchen palette
const AGENT_COLORS: Record<string, string> = {
  'backend-engineer': T.blueberry,
  'frontend-engineer': T.tangerine,
  'test-engineer': T.mint,
  'architect': T.crust,
  'security-reviewer': T.cherry,
  'devops-engineer': T.butter,
  'data-engineer': T.mintDark,
};

const MODEL_COLORS: Record<ModelOption, string> = {
  sonnet: T.blueberry,
  opus: T.cherry,
  haiku: T.mint,
};

const STEP_TYPE_COLORS: Record<StepType, string> = {
  developing: T.blueberry,
  planning: T.crust,
  testing: T.mint,
  reviewing: T.tangerine,
  consulting: T.butter,
  task: T.text2,
  automation: T.cherry,
};

function agentColor(name: string): string {
  return AGENT_COLORS[name] ?? T.text2;
}

// ---------------------------------------------------------------------------
// TagInput — type + Enter to add, X to remove
// ---------------------------------------------------------------------------

interface TagInputProps {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  ariaLabel?: string;
}

function TagInput({ values, onChange, placeholder = 'Type and press Enter', ariaLabel }: TagInputProps) {
  const [draft, setDraft] = useState('');

  function commit() {
    const trimmed = draft.trim();
    if (trimmed && !values.includes(trimmed)) {
      onChange([...values, trimmed]);
    }
    setDraft('');
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault();
      commit();
    } else if (e.key === 'Backspace' && draft === '' && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  }

  function removeTag(idx: number) {
    onChange(values.filter((_, i) => i !== idx));
  }

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 6,
        padding: '8px 10px',
        borderRadius: 8,
        border: `1.5px solid ${T.borderSoft}`,
        background: T.bg0,
        minHeight: 38,
        alignItems: 'center',
        cursor: 'text',
      }}
      onClick={e => {
        const input = (e.currentTarget as HTMLElement).querySelector('input');
        input?.focus();
      }}
    >
      {values.map((v, i) => (
        <span
          key={i}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 3,
            background: T.bg3,
            border: `1px solid ${T.borderSoft}`,
            borderRadius: 6,
            padding: '3px 8px',
            fontSize: 13,
            fontFamily: FONTS.mono,
            color: T.text1,
            lineHeight: 1.4,
          }}
        >
          {v}
          <button
            type="button"
            aria-label={`Remove ${v}`}
            onClick={e => { e.stopPropagation(); removeTag(i); }}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: T.text3, padding: '0 2px', fontSize: 14, lineHeight: 1,
              fontFamily: FONTS.body,
            }}
          >
            {'\u00d7'}
          </button>
        </span>
      ))}
      <input
        aria-label={ariaLabel}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={commit}
        placeholder={values.length === 0 ? placeholder : ''}
        style={{
          border: 'none',
          outline: 'none',
          background: 'transparent',
          fontSize: 13,
          fontFamily: FONTS.mono,
          color: T.text0,
          flex: '1 1 80px',
          minWidth: 60,
          padding: '2px 0',
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// AutosizeTextarea — always-visible, grows with content
// ---------------------------------------------------------------------------

interface AutosizeTextareaProps {
  value: string;
  onChange: (value: string) => void;
  ariaLabel?: string;
  placeholder?: string;
}

function AutosizeTextarea({ value, onChange, ariaLabel, placeholder }: AutosizeTextareaProps) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Re-adjust height whenever value changes
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  return (
    <textarea
      ref={ref}
      rows={2}
      aria-label={ariaLabel}
      placeholder={placeholder}
      value={value}
      onChange={e => onChange(e.target.value)}
      style={{
        width: '100%',
        padding: '10px 12px',
        borderRadius: 8,
        border: `1.5px solid ${T.borderSoft}`,
        background: T.bg0,
        color: T.text0,
        fontSize: 14,
        fontWeight: 500,
        outline: 'none',
        fontFamily: FONTS.body,
        lineHeight: 1.5,
        resize: 'none',
        overflow: 'hidden',
        boxSizing: 'border-box',
        display: 'block',
        transition: 'border-color 0.15s',
      }}
      onFocus={e => { e.currentTarget.style.borderColor = T.cherry; }}
      onBlur={e => { e.currentTarget.style.borderColor = T.borderSoft; }}
    />
  );
}

// ---------------------------------------------------------------------------
// DependencySelect — multi-select checkboxes for step IDs
// ---------------------------------------------------------------------------

interface DependencySelectProps {
  allStepIds: string[];
  currentStepId: string;
  selected: string[];
  onChange: (ids: string[]) => void;
}

function DependencySelect({ allStepIds, currentStepId, selected, onChange }: DependencySelectProps) {
  const eligible = allStepIds.filter(id => id !== currentStepId);

  if (eligible.length === 0) {
    return (
      <span style={{ fontSize: FONT_SIZES.sm, color: T.text3, fontFamily: FONTS.body, fontStyle: 'italic' }}>
        No other steps in this phase
      </span>
    );
  }

  function toggle(id: string) {
    onChange(
      selected.includes(id) ? selected.filter(s => s !== id) : [...selected, id],
    );
  }

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {eligible.map(id => {
        const checked = selected.includes(id);
        return (
          <label
            key={id}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              cursor: 'pointer',
              fontSize: FONT_SIZES.sm,
              fontFamily: FONTS.mono,
              color: checked ? T.text0 : T.text2,
              background: checked ? T.bg3 : T.bg0,
              border: `1.5px solid ${checked ? T.border : T.borderSoft}`,
              borderRadius: 5,
              padding: '2px 7px',
              userSelect: 'none',
            }}
          >
            <input
              type="checkbox"
              checked={checked}
              onChange={() => toggle(id)}
              style={{ accentColor: T.cherry, width: 12, height: 12 }}
            />
            {id}
          </label>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AdvancedFieldRow — labelled row inside the advanced accordion
// ---------------------------------------------------------------------------

function AdvancedFieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{
        fontSize: 12,
        fontWeight: 800,
        fontFamily: FONTS.body,
        textTransform: 'uppercase',
        letterSpacing: '0.1em',
        color: T.text2,
      }}>
        {label}
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FieldRow — labelled row for main (non-advanced) step body fields
// ---------------------------------------------------------------------------

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{
        fontSize: 12,
        fontWeight: 800,
        fontFamily: FONTS.body,
        textTransform: 'uppercase',
        letterSpacing: '0.1em',
        color: T.text3,
      }}>
        {label}
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentSelect — populated from API, grouped by category
// ---------------------------------------------------------------------------

interface AgentSelectProps {
  value: string;
  onChange: (name: string) => void;
  agents: Agent[];
  color: string;
  ariaLabel?: string;
}

function AgentSelect({ value, onChange, agents, color, ariaLabel }: AgentSelectProps) {
  // Group by category
  const grouped = useMemo(() => {
    const map = new Map<string, Agent[]>();
    for (const a of agents) {
      const cat = a.category || 'other';
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(a);
    }
    return map;
  }, [agents]);

  const hasCurrentValue = agents.some(a => a.name === value);

  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      onClick={e => e.stopPropagation()}
      aria-label={ariaLabel}
      style={{
        fontSize: 11,
        color,
        background: T.bg3,
        border: `2px solid ${T.border}`,
        borderRadius: 6,
        padding: '3px 6px',
        outline: 'none',
        flexShrink: 0,
        cursor: 'pointer',
        fontFamily: FONTS.body,
        fontWeight: 700,
      }}
    >
      {grouped.size > 0 ? (
        Array.from(grouped.entries()).map(([cat, catAgents]) => (
          <optgroup key={cat} label={cat.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}>
            {catAgents.map(a => (
              <option key={a.name} value={a.name} title={a.description}>
                {agentDisplayName(a.name)}
              </option>
            ))}
          </optgroup>
        ))
      ) : (
        AGENT_LIST_FALLBACK.map(a => (
          <option key={a} value={a} title={AGENT_DESCRIPTIONS_FALLBACK[a] || ''}>
            {agentDisplayName(a)}
          </option>
        ))
      )}
      {/* Preserve current value if not present in the loaded list */}
      {!hasCurrentValue && (
        <option value={value}>{agentDisplayName(value)}</option>
      )}
    </select>
  );
}

// ---------------------------------------------------------------------------
// GateEditor — editable gate fields for a phase
// ---------------------------------------------------------------------------

interface GateEditorProps {
  gate: ForgePlanGate | undefined;
  onChange: (gate: ForgePlanGate | undefined) => void;
}

const DEFAULT_GATE: ForgePlanGate = {
  gate_type: 'test',
  command: '',
  description: '',
  fail_on: [],
  approval_required: false,
};

function GateEditor({ gate, onChange }: GateEditorProps) {
  const active = gate ?? DEFAULT_GATE;

  function patch(partial: Partial<ForgePlanGate>) {
    onChange({ ...active, ...partial });
  }

  const selectStyle = {
    fontSize: FONT_SIZES.sm,
    color: T.text0,
    background: T.bg0,
    border: `1.5px solid ${T.borderSoft}`,
    borderRadius: 6,
    padding: '4px 8px',
    outline: 'none',
    fontFamily: FONTS.body,
    fontWeight: 600,
    cursor: 'pointer',
    width: '100%',
  };

  const inputStyle = {
    width: '100%',
    padding: '4px 8px',
    borderRadius: 6,
    border: `1.5px solid ${T.borderSoft}`,
    background: T.bg0,
    color: T.text0,
    fontSize: FONT_SIZES.sm,
    fontWeight: 600,
    outline: 'none',
    fontFamily: FONTS.mono,
    boxSizing: 'border-box' as const,
  };

  return (
    <div style={{
      background: T.cherrySoft,
      border: `1.5px solid ${T.border}`,
      borderRadius: 10,
      padding: '10px 14px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      <div style={{
        fontSize: FONT_SIZES.xs,
        fontWeight: 800,
        fontFamily: FONTS.body,
        textTransform: 'uppercase',
        letterSpacing: '0.1em',
        color: T.cherry,
        marginBottom: 2,
      }}>
        Gate Configuration
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <AdvancedFieldRow label="Gate Type">
          <select
            value={active.gate_type}
            onChange={e => patch({ gate_type: e.target.value as GateType })}
            style={selectStyle}
            aria-label="Gate type"
          >
            {GATE_TYPE_LIST.map(gt => (
              <option key={gt} value={gt}>{gt}</option>
            ))}
          </select>
        </AdvancedFieldRow>

        <AdvancedFieldRow label="Command">
          <input
            value={active.command}
            onChange={e => patch({ command: e.target.value })}
            placeholder="e.g. npm test"
            style={inputStyle}
            aria-label="Gate command"
          />
        </AdvancedFieldRow>
      </div>

      <AdvancedFieldRow label="Description">
        <input
          value={active.description}
          onChange={e => patch({ description: e.target.value })}
          placeholder="What does this gate verify?"
          style={inputStyle}
          aria-label="Gate description"
        />
      </AdvancedFieldRow>

      <label style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 7,
        cursor: 'pointer',
        fontSize: FONT_SIZES.sm,
        fontFamily: FONTS.body,
        fontWeight: 700,
        color: T.text1,
        userSelect: 'none',
      }}>
        <input
          type="checkbox"
          checked={active.approval_required ?? false}
          onChange={e => patch({ approval_required: e.target.checked })}
          style={{ accentColor: T.cherry, width: 14, height: 14 }}
        />
        Require human approval before proceeding
      </label>

      {/* Remove gate */}
      <button
        type="button"
        onClick={() => onChange(undefined)}
        style={{
          alignSelf: 'flex-start',
          padding: '3px 10px',
          borderRadius: 6,
          border: `1.5px solid ${T.border}`,
          background: T.bg1,
          color: T.text2,
          fontSize: FONT_SIZES.sm,
          fontWeight: 700,
          cursor: 'pointer',
          fontFamily: FONTS.body,
        }}
      >
        Remove gate
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main PlanEditor
// ---------------------------------------------------------------------------

interface PlanEditorProps {
  plan: ForgePlanResponse;
  onPlanChange: (plan: ForgePlanResponse) => void;
  onDraftSave?: () => void;
  projectId: string;
  /** Passed from ForgePanel so the action bar can trigger back/approve/regen */
  onBack?: () => void;
  onApprove?: () => void;
  saving?: boolean;
  onStartRegenerate?: () => void;
  regenLoading?: boolean;
}

export function PlanEditor({
  plan, onPlanChange, onDraftSave, projectId,
  onBack, onApprove, saving, onStartRegenerate, regenLoading,
}: PlanEditorProps) {
  const [expandedPhase, setExpandedPhase] = useState<number | null>(0);
  const [expandedAdvanced, setExpandedAdvanced] = useState<Set<string>>(new Set());
  const [expandedGateEditor, setExpandedGateEditor] = useState<Set<number>>(new Set());
  const [draftSaved, setDraftSaved] = useState(false);
  const [lastSaveTime, setLastSaveTime] = useState<string | null>(null);
  const [dragState, setDragState] = useState<{ phaseIdx: number; stepIdx: number } | null>(null);
  const [dropTarget, setDropTarget] = useState<{ phaseIdx: number; stepIdx: number } | null>(null);

  // Agent registry state
  const [agents, setAgents] = useState<Agent[]>([]);

  const originalPlanRef = useRef<string>(JSON.stringify(plan));
  const isDirty = useMemo(() => JSON.stringify(plan) !== originalPlanRef.current, [plan]);

  // Fetch real agent list on mount; fall back gracefully on failure
  useEffect(() => {
    api.getAgents()
      .then(res => setAgents(res.agents))
      .catch(() => {
        // Fall back to empty — AgentSelect will render the hardcoded fallback list
        setAgents([]);
      });
  }, []);

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
          step_type: 'developing' as const,
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

  function toggleAdvanced(stepId: string) {
    setExpandedAdvanced(prev => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId);
      else next.add(stepId);
      return next;
    });
  }

  function toggleGateEditor(phaseIdx: number) {
    setExpandedGateEditor(prev => {
      const next = new Set(prev);
      if (next.has(phaseIdx)) next.delete(phaseIdx);
      else next.add(phaseIdx);
      return next;
    });
  }

  function handleDragStart(phaseIdx: number, stepIdx: number) {
    setDragState({ phaseIdx, stepIdx });
    setDropTarget(null);
  }

  function handleDragOver(e: DragEvent, phaseIdx: number, stepIdx: number) {
    e.preventDefault();
    if (!dragState || dragState.phaseIdx !== phaseIdx) return;
    if (dropTarget?.phaseIdx !== phaseIdx || dropTarget?.stepIdx !== stepIdx) {
      setDropTarget({ phaseIdx, stepIdx });
    }
  }

  function handleDrop(e: DragEvent, phaseIdx: number, targetIdx: number) {
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

  const riskColor = plan.risk_level === 'LOW' ? T.mint : plan.risk_level === 'HIGH' ? T.cherry : T.butter;

  const sharedSelectStyle = {
    fontSize: 13,
    background: T.bg3,
    border: `1.5px solid ${T.border}`,
    borderRadius: 8,
    padding: '6px 10px',
    outline: 'none',
    flexShrink: 0 as const,
    cursor: 'pointer',
    fontFamily: FONTS.body,
    fontWeight: 700,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.04em',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18, fontFamily: FONTS.body }}>

      {/* Recipe card header */}
      <div style={{
        position: 'relative',
        background: T.bg2,
        backgroundImage: `repeating-linear-gradient(0deg, transparent 0 28px, ${T.borderSoft}44 28px 29px)`,
        border: `3px solid ${T.border}`,
        borderRadius: 18,
        boxShadow: SHADOWS.lg,
        padding: '22px 22px 18px',
      }}>
        {/* Red ribbon tag */}
        <div style={{
          position: 'absolute', top: -10, left: 30,
          background: T.cherry, color: T.cream,
          fontSize: 11, fontWeight: 800, fontFamily: FONTS.body,
          textTransform: 'uppercase', letterSpacing: '0.08em',
          borderRadius: 6, padding: '4px 14px',
          boxShadow: SHADOWS.sm, transform: 'rotate(-2deg)',
          border: `2px solid ${T.border}`,
        }}>
          RECIPE CARD
        </div>

        {/* Back link — top right */}
        {onBack && (
          <button
            onClick={onBack}
            style={{
              position: 'absolute', top: 14, right: 16,
              background: 'none', border: 'none',
              color: T.text2, fontSize: 12, fontWeight: 700,
              cursor: 'pointer', fontFamily: FONTS.body,
            }}
          >
            {'\u2190'} Back
          </button>
        )}

        <div style={{ marginTop: 10 }}>
          <div style={{
            fontFamily: FONTS.body, fontSize: 10, fontWeight: 800,
            textTransform: 'uppercase', letterSpacing: '0.12em',
            color: T.cherry, marginBottom: 4,
          }}>
            FROM THE KITCHEN OF
          </div>
          <div style={{
            fontFamily: FONTS.display, fontWeight: 900, fontSize: 38,
            letterSpacing: '-0.02em', color: T.text0, lineHeight: 1.1,
          }}>
            {plan.task_summary || 'Untitled Recipe'}
          </div>
          {plan.task_summary && (
            <div style={{
              fontFamily: FONTS.hand, fontSize: 22, color: T.cherry,
              transform: 'rotate(-0.8deg)', display: 'inline-block', marginTop: 4,
            }}>
              "{plan.task_summary}"
            </div>
          )}
        </div>

        {/* Stats row */}
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 16 }}>
          <StatChip label="Phases" value={String(plan.phases.length)} valueColor={T.blueberry} />
          <StatChip label="Steps" value={String(totalSteps)} valueColor={T.text0} />
          <StatChip label="Gates" value={String(gateCount)} valueColor={T.butter} />
          <StatChip label="Risk" value={plan.risk_level} valueColor={riskColor} />
          <div style={{ flex: 1 }} />
          {/* Save draft button */}
          <button
            onClick={handleSaveDraft}
            aria-label="Save draft to local storage"
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '10px 14px', borderRadius: 10,
              background: draftSaved ? T.mintSoft : T.bg1,
              color: draftSaved ? T.mintDark : T.text1,
              border: `2px solid ${T.border}`,
              fontSize: FONT_SIZES.sm, fontWeight: 700, cursor: 'pointer',
              fontFamily: FONTS.body, boxShadow: SHADOWS.sm,
              transition: 'background 0.2s',
            }}
          >
            {isDirty && (
              <span
                aria-label="unsaved changes"
                style={{
                  width: 6, height: 6, borderRadius: '50%',
                  background: T.tangerine, flexShrink: 0,
                  display: 'inline-block',
                }}
              />
            )}
            {draftSaved ? 'Saved \u2713' : 'Save Draft'}
          </button>
          {lastSaveTime && (
            <span style={{ fontSize: FONT_SIZES.xs, color: T.text3, fontFamily: FONTS.hand, fontStyle: 'italic' }}>
              saved at {lastSaveTime}
            </span>
          )}
        </div>
      </div>

      {/* Phase / course cards */}
      {plan.phases.map((phase, pi) => {
        const isExpanded = expandedPhase === pi;
        const isGateEditorOpen = expandedGateEditor.has(pi);
        const allStepIdsInPhase = phase.steps.map(s => s.step_id);

        return (
          <div
            key={phase.phase_id}
            style={{
              background: T.bg1,
              borderRadius: 14,
              border: `2px solid ${T.border}`,
              boxShadow: SHADOWS.md,
              overflow: 'hidden',
              transform: `rotate(${pi % 2 === 0 ? -0.35 : 0.25}deg)`,
            }}
          >
            {/* Course header — acts as toggle */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              background: isExpanded ? T.butter : T.bg2,
              borderBottom: isExpanded ? `2px solid ${T.border}` : 'none',
              transition: 'background 0.15s',
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
                  gap: 10,
                  padding: '10px 14px',
                  flex: 1,
                  cursor: 'pointer',
                }}
              >
                {/* Course number badge */}
                <div style={{
                  width: 34, height: 34, borderRadius: '50%',
                  background: T.ink, color: T.butter,
                  fontFamily: FONTS.display, fontWeight: 900, fontSize: 16,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  flexShrink: 0, border: `2px solid ${T.border}`,
                }}>
                  {pi + 1}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{
                    fontFamily: FONTS.body, fontWeight: 800, fontSize: 10,
                    textTransform: 'uppercase', letterSpacing: '0.12em', color: T.cherry,
                    marginBottom: 1,
                  }}>
                    COURSE {String(pi + 1).padStart(2, '0')}
                  </div>
                  <div style={{
                    fontFamily: FONTS.display, fontWeight: 900, fontSize: 22, color: T.text0,
                  }}>
                    {phase.name}
                  </div>
                </div>
                {/* Step count chip */}
                <span style={{
                  padding: '3px 10px', borderRadius: 999,
                  background: T.bg0, border: `1.5px solid ${T.border}`,
                  fontFamily: FONTS.body, fontWeight: 800, fontSize: 11, color: T.text1,
                  whiteSpace: 'nowrap',
                }}>
                  {phase.steps.length} steps
                </span>
                {/* Gate chip */}
                {phase.gate && (
                  <span style={{
                    padding: '3px 10px', borderRadius: 999,
                    background: T.cherry, color: T.cream,
                    fontFamily: FONTS.body, fontWeight: 800, fontSize: 10,
                    textTransform: 'uppercase', letterSpacing: '0.06em',
                    border: `1.5px solid ${T.border}`,
                    whiteSpace: 'nowrap',
                  }}>
                    {'👅'} taste test
                  </span>
                )}
              </div>

              {/* Gate editor toggle */}
              <button
                aria-label={`${isGateEditorOpen ? 'Hide' : 'Edit'} gate for phase ${pi + 1}`}
                aria-expanded={isGateEditorOpen}
                onClick={() => toggleGateEditor(pi)}
                title="Edit gate"
                style={{
                  background: isGateEditorOpen ? T.cherrySoft : 'none',
                  border: `1px solid ${isGateEditorOpen ? T.border : 'transparent'}`,
                  color: isGateEditorOpen ? T.cherry : T.text3,
                  fontSize: 13, cursor: 'pointer', padding: '4px 8px',
                  borderRadius: 6, fontFamily: FONTS.body, fontWeight: 700,
                  transition: 'all 0.15s',
                }}
              >
                {phase.gate ? 'gate' : '+ gate'}
              </button>

              <button
                aria-label={`Remove phase ${pi + 1}: ${phase.name}`}
                onClick={() => removePhase(pi)}
                style={{
                  background: 'none', border: 'none', color: T.text3,
                  fontSize: 16, cursor: 'pointer', padding: '0 12px',
                  fontFamily: FONTS.body,
                }}
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
                const aColor = agentColor(step.agent_name);
                const isAdvancedOpen = expandedAdvanced.has(step.step_id);
                const model = (step.model ?? 'sonnet') as ModelOption;
                const stepType = (step.step_type ?? 'developing') as StepType;
                const isAutomation = stepType === 'automation';
                const isInteractive = step.interactive ?? false;

                return (
                  <div
                    key={step.step_id}
                    draggable
                    onDragStart={() => handleDragStart(pi, si)}
                    onDragOver={e => handleDragOver(e, pi, si)}
                    onDrop={e => handleDrop(e, pi, si)}
                    onDragEnd={handleDragEnd}
                    style={{
                      borderBottom: si < phase.steps.length - 1 ? `1.5px dashed ${T.borderSoft}` : 'none',
                      borderTop: isDropTarget ? `2px solid ${T.cherry}` : undefined,
                      opacity: isDragging ? 0.45 : 1,
                      transition: 'opacity 0.1s',
                    }}
                  >
                    {/* ── Step header row ── */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px 6px' }}>
                      {/* Drag handle */}
                      <span
                        aria-hidden="true"
                        style={{ cursor: 'grab', color: T.text3, fontSize: 14, flexShrink: 0, lineHeight: 1, userSelect: 'none' }}
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
                          style={{
                            background: 'none', border: 'none',
                            color: si === 0 ? T.bg3 : T.text3,
                            fontSize: 10, cursor: si === 0 ? 'default' : 'pointer',
                            padding: 0, lineHeight: 1, minWidth: 22, minHeight: 16,
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                          }}
                        >{'\u25b2'}</button>
                        <button
                          aria-label={`Move step ${si + 1} down`}
                          onClick={() => moveStep(pi, si, 1)}
                          disabled={si === phase.steps.length - 1}
                          style={{
                            background: 'none', border: 'none',
                            color: si === phase.steps.length - 1 ? T.bg3 : T.text3,
                            fontSize: 10, cursor: si === phase.steps.length - 1 ? 'default' : 'pointer',
                            padding: 0, lineHeight: 1, minWidth: 22, minHeight: 16,
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                          }}
                        >{'\u25bc'}</button>
                      </div>

                      {/* Step ID */}
                      <span style={{
                        fontFamily: FONTS.mono, fontSize: 14, color: T.text2,
                        flexShrink: 0, fontWeight: 600,
                      }}>
                        {step.step_id}
                      </span>

                      {/* Step Type dropdown */}
                      <select
                        value={stepType}
                        onChange={e => updateStep(pi, si, s => ({ ...s, step_type: e.target.value as StepType }))}
                        onClick={e => e.stopPropagation()}
                        aria-label={`Step type for step ${step.step_id}`}
                        style={{
                          ...sharedSelectStyle,
                          color: STEP_TYPE_COLORS[stepType] ?? T.text2,
                        }}
                      >
                        {STEP_TYPE_LIST.map(t => (
                          <option key={t} value={t}>{t}</option>
                        ))}
                      </select>

                      {/* Model dropdown */}
                      <select
                        value={model}
                        onChange={e => updateStep(pi, si, s => ({ ...s, model: e.target.value }))}
                        onClick={e => e.stopPropagation()}
                        aria-label={`Model for step ${step.step_id}`}
                        style={{
                          ...sharedSelectStyle,
                          color: MODEL_COLORS[model] ?? T.text2,
                        }}
                      >
                        {MODEL_LIST.map(m => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>

                      {/* Agent selector — always a dropdown, populated from API */}
                      <AgentSelect
                        value={step.agent_name}
                        onChange={name => updateStep(pi, si, s => ({ ...s, agent_name: name }))}
                        agents={agents}
                        color={aColor}
                        ariaLabel={`Agent for step ${step.step_id}`}
                      />

                      <div style={{ flex: 1 }} />

                      {/* Advanced toggle */}
                      <button
                        type="button"
                        aria-label={`${isAdvancedOpen ? 'Hide' : 'Show'} advanced fields for step ${step.step_id}`}
                        aria-expanded={isAdvancedOpen}
                        aria-controls={`advanced-${step.step_id}`}
                        onClick={() => toggleAdvanced(step.step_id)}
                        style={{
                          background: isAdvancedOpen ? T.bg3 : 'none',
                          border: `1px solid ${isAdvancedOpen ? T.border : T.borderSoft}`,
                          color: isAdvancedOpen ? T.text1 : T.text3,
                          fontSize: FONT_SIZES.xs,
                          fontWeight: 800,
                          fontFamily: FONTS.body,
                          textTransform: 'uppercase',
                          letterSpacing: '0.06em',
                          cursor: 'pointer',
                          padding: '2px 7px',
                          borderRadius: 5,
                          flexShrink: 0,
                          transition: 'all 0.15s',
                        }}
                      >
                        {isAdvancedOpen ? 'hide' : 'adv'}
                      </button>

                      {/* Remove step */}
                      <button
                        aria-label={`Remove step ${si + 1}: ${step.task_description.slice(0, 40)}`}
                        onClick={() => removeStep(pi, si)}
                        style={{
                          background: 'none', border: `1px solid ${T.borderSoft}`,
                          color: T.text3, fontSize: 12, cursor: 'pointer',
                          padding: '1px 6px', flexShrink: 0, borderRadius: 4,
                          fontFamily: FONTS.body,
                        }}
                        title="Remove step"
                      >
                        {'\u00d7'}
                      </button>
                    </div>

                    {/* ── Step body — always visible fields ── */}
                    <div style={{ padding: '4px 14px 10px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {/* Task description — always-visible textarea */}
                      <AutosizeTextarea
                        value={step.task_description}
                        onChange={val => updateStep(pi, si, s => ({ ...s, task_description: val }))}
                        ariaLabel={`Task description for step ${step.step_id}`}
                        placeholder="Describe what this step should accomplish..."
                      />

                      {/* Command — only shown for automation steps */}
                      {isAutomation && (
                        <FieldRow label="Command">
                          <input
                            value={step.command ?? ''}
                            onChange={e => updateStep(pi, si, s => ({ ...s, command: e.target.value }))}
                            placeholder="e.g. python scripts/run_pipeline.py"
                            aria-label={`Command for step ${step.step_id}`}
                            style={{
                              width: '100%',
                              padding: '5px 8px',
                              borderRadius: 6,
                              border: `1.5px solid ${T.borderSoft}`,
                              background: T.bg0,
                              color: T.text0,
                              fontSize: FONT_SIZES.sm,
                              fontFamily: FONTS.mono,
                              fontWeight: 600,
                              outline: 'none',
                              boxSizing: 'border-box',
                            }}
                          />
                        </FieldRow>
                      )}

                      {/* Deliverables — always visible */}
                      <FieldRow label="Deliverables">
                        <TagInput
                          values={step.deliverables ?? []}
                          onChange={vals => updateStep(pi, si, s => ({ ...s, deliverables: vals }))}
                          placeholder="e.g. README.md"
                          ariaLabel={`Deliverables for step ${step.step_id}`}
                        />
                      </FieldRow>

                      {/* Context Files — always visible */}
                      <FieldRow label="Context Files">
                        <TagInput
                          values={step.context_files ?? []}
                          onChange={vals => updateStep(pi, si, s => ({ ...s, context_files: vals }))}
                          placeholder="e.g. docs/architecture.md"
                          ariaLabel={`Context files for step ${step.step_id}`}
                        />
                      </FieldRow>
                    </div>

                    {/* ── Advanced accordion ── */}
                    <div
                      id={`advanced-${step.step_id}`}
                      hidden={!isAdvancedOpen}
                    >
                      <div style={{
                        margin: '0 14px 10px',
                        background: T.bg3,
                        border: `1.5px solid ${T.borderSoft}`,
                        borderRadius: 8,
                        padding: '10px 12px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 10,
                        boxShadow: 'inset 2px 2px 0 0 rgba(0,0,0,0.06)',
                      }}>
                        <div style={{
                          fontSize: FONT_SIZES.xs,
                          fontWeight: 800,
                          fontFamily: FONTS.body,
                          textTransform: 'uppercase',
                          letterSpacing: '0.1em',
                          color: T.text3,
                          marginBottom: 2,
                        }}>
                          Advanced
                        </div>

                        <AdvancedFieldRow label="Dependencies">
                          <DependencySelect
                            allStepIds={allStepIdsInPhase}
                            currentStepId={step.step_id}
                            selected={step.depends_on ?? []}
                            onChange={ids => updateStep(pi, si, s => ({ ...s, depends_on: ids }))}
                          />
                        </AdvancedFieldRow>

                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                          <AdvancedFieldRow label="Allowed Paths">
                            <TagInput
                              values={step.allowed_paths ?? []}
                              onChange={vals => updateStep(pi, si, s => ({ ...s, allowed_paths: vals }))}
                              placeholder="e.g. src/"
                              ariaLabel={`Allowed paths for step ${step.step_id}`}
                            />
                          </AdvancedFieldRow>

                          <AdvancedFieldRow label="Blocked Paths">
                            <TagInput
                              values={step.blocked_paths ?? []}
                              onChange={vals => updateStep(pi, si, s => ({ ...s, blocked_paths: vals }))}
                              placeholder="e.g. .env"
                              ariaLabel={`Blocked paths for step ${step.step_id}`}
                            />
                          </AdvancedFieldRow>
                        </div>

                        {/* Interactive / max_turns */}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                          <label style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 7,
                            cursor: 'pointer',
                            fontSize: FONT_SIZES.sm,
                            fontFamily: FONTS.body,
                            fontWeight: 700,
                            color: T.text1,
                            userSelect: 'none',
                          }}>
                            <input
                              type="checkbox"
                              checked={isInteractive}
                              onChange={e => updateStep(pi, si, s => ({ ...s, interactive: e.target.checked }))}
                              style={{ accentColor: T.cherry, width: 13, height: 13 }}
                            />
                            Interactive (multi-turn)
                          </label>

                          {isInteractive && (
                            <AdvancedFieldRow label="Max Turns">
                              <input
                                type="number"
                                min={1}
                                max={100}
                                value={step.max_turns ?? 10}
                                onChange={e => updateStep(pi, si, s => ({ ...s, max_turns: Math.max(1, parseInt(e.target.value, 10) || 10) }))}
                                aria-label={`Max turns for step ${step.step_id}`}
                                style={{
                                  width: 80,
                                  padding: '4px 7px',
                                  borderRadius: 6,
                                  border: `1.5px solid ${T.borderSoft}`,
                                  background: T.bg0,
                                  color: T.text0,
                                  fontSize: FONT_SIZES.sm,
                                  fontFamily: FONTS.mono,
                                  fontWeight: 700,
                                  outline: 'none',
                                }}
                              />
                            </AdvancedFieldRow>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}

              {/* Empty phase placeholder */}
              {phase.steps.length === 0 && (
                <div style={{
                  padding: '10px 14px', fontSize: 12, color: T.text3,
                  fontStyle: 'italic', fontFamily: FONTS.hand,
                }}>
                  No steps yet. Add a step or remove this course.
                </div>
              )}

              {/* Add step button */}
              <div style={{ padding: '6px 14px' }}>
                <button
                  onClick={() => addStep(pi)}
                  style={{
                    padding: '4px 12px', borderRadius: 6,
                    border: `2px dashed ${T.border}`, background: 'transparent',
                    color: T.text2, fontSize: 12, fontWeight: 700,
                    cursor: 'pointer', fontFamily: FONTS.body,
                  }}
                >
                  + Add step
                </button>
              </div>

              {/* Gate editor — shown when toggled */}
              {isGateEditorOpen && (
                <div style={{ padding: '6px 14px 10px' }}>
                  <GateEditor
                    gate={phase.gate}
                    onChange={gate => updatePhase(pi, p => ({ ...p, gate }))}
                  />
                  {!phase.gate && (
                    <button
                      type="button"
                      onClick={() => updatePhase(pi, p => ({ ...p, gate: { ...DEFAULT_GATE } }))}
                      style={{
                        marginTop: 8,
                        padding: '4px 12px', borderRadius: 6,
                        border: `2px dashed ${T.border}`, background: 'transparent',
                        color: T.cherry, fontSize: 12, fontWeight: 700,
                        cursor: 'pointer', fontFamily: FONTS.body,
                      }}
                    >
                      + Enable gate for this phase
                    </button>
                  )}
                </div>
              )}

              {/* Gate footer — read-only summary when gate is set and editor is closed */}
              {phase.gate && !isGateEditorOpen && (
                <div style={{
                  background: T.cherrySoft,
                  borderTop: `1.5px dashed ${T.border}`,
                  padding: '8px 14px',
                }}>
                  <div style={{
                    fontFamily: FONTS.hand, fontSize: 17, color: T.text0,
                    transform: 'rotate(-0.7deg)', display: 'inline-block',
                  }}>
                    "{phase.gate.description}"
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })}

      {/* Add phase button */}
      <button
        onClick={addPhase}
        style={{
          padding: '8px 16px',
          borderRadius: 10,
          border: `2px dashed ${T.border}`,
          background: 'transparent',
          color: T.text2,
          fontSize: 13,
          fontWeight: 700,
          cursor: 'pointer',
          width: '100%',
          fontFamily: FONTS.body,
        }}
      >
        + Add Course
      </button>

      {/* Action bar — sticky bottom */}
      {(onApprove || onStartRegenerate) && (
        <div style={{
          position: 'sticky', bottom: 0,
          background: T.bg1,
          border: `3px solid ${T.border}`,
          borderRadius: 14,
          boxShadow: SHADOWS.lg,
          padding: '12px 18px',
          display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
        }}>
          <div style={{
            fontFamily: FONTS.hand, fontSize: 20, color: T.mintDark,
            transform: 'rotate(-1deg)', display: 'inline-block', flex: 1,
          }}>
            looks good? fire it →
          </div>
          {onStartRegenerate && (
            <button
              onClick={onStartRegenerate}
              disabled={regenLoading}
              style={{
                padding: '10px 18px', borderRadius: 10,
                border: `2px solid ${T.border}`,
                background: T.butter, color: T.ink,
                fontSize: 13, fontWeight: 800,
                cursor: regenLoading ? 'not-allowed' : 'pointer',
                opacity: regenLoading ? 0.6 : 1,
                fontFamily: FONTS.body, boxShadow: SHADOWS.sm,
              }}
            >
              {regenLoading ? 'Loading...' : 'Ask me some questions'}
            </button>
          )}
          {onApprove && (
            <button
              onClick={onApprove}
              disabled={saving}
              style={{
                padding: '10px 22px', borderRadius: 10,
                border: `3px solid ${T.border}`,
                background: saving ? T.bg3 : T.cherry, color: saving ? T.text3 : T.cream,
                fontSize: 13, fontWeight: 800,
                cursor: saving ? 'not-allowed' : 'pointer',
                opacity: saving ? 0.6 : 1,
                fontFamily: FONTS.body,
                boxShadow: saving ? 'none' : SHADOWS.md,
              }}
            >
              {saving ? 'Queuing\u2026' : 'Approve & fire the pass'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// StatChip
// ---------------------------------------------------------------------------

function StatChip({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div style={{
      padding: '10px 12px',
      background: T.bg1,
      borderRadius: 10,
      border: `2px solid ${T.border}`,
      boxShadow: SHADOWS.sm,
    }}>
      <div style={{
        fontFamily: FONTS.body, fontWeight: 800, fontSize: 10,
        textTransform: 'uppercase', letterSpacing: '0.06em', color: T.text2,
        marginBottom: 2,
      }}>
        {label}
      </div>
      <div style={{
        fontFamily: FONTS.display, fontWeight: 900, fontSize: 22,
        color: valueColor ?? T.text0,
      }}>
        {value}
      </div>
    </div>
  );
}
