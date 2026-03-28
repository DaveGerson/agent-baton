import { useState, useEffect, useRef } from 'react';
import { api } from '../api/client';
import { PlanEditor } from './PlanEditor';
import { InterviewPanel } from './InterviewPanel';
import { AdoCombobox } from './AdoCombobox';
import { usePersistedState } from '../hooks/usePersistedState';
import { T, SR_ONLY } from '../styles/tokens';
import type { PmoProject, PmoSignal, ForgePlanResponse, InterviewQuestion, InterviewAnswer } from '../api/types';

interface ForgePanelProps {
  onBack: () => void;
  initialSignal?: PmoSignal | null;
}

type Phase = 'intake' | 'generating' | 'preview' | 'regenerating' | 'saved';

const TASK_TYPES = [
  { value: '', label: 'Auto-detect' },
  { value: 'feature', label: 'New Feature' },
  { value: 'bugfix', label: 'Bug Fix' },
  { value: 'refactor', label: 'Refactor' },
  { value: 'analysis', label: 'Analysis' },
  { value: 'migration', label: 'Migration' },
];

const PRIORITIES = [
  { value: 2, label: 'P0 \u2014 Critical' },
  { value: 1, label: 'P1 \u2014 High' },
  { value: 0, label: 'P2 \u2014 Normal' },
];

export function ForgePanel({ onBack, initialSignal }: ForgePanelProps) {
  const [phase, setPhase] = useState<Phase>('intake');
  const [projects, setProjects] = useState<PmoProject[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);

  const signalDesc = initialSignal
    ? `Signal: ${initialSignal.title}\n\nSeverity: ${initialSignal.severity}\nType: ${initialSignal.signal_type}\n\n${initialSignal.description ?? ''}`
    : null;
  const [description, setDescription] = usePersistedState('pmo:forge-description', signalDesc ?? '');
  const [projectId, setProjectId] = useState('');
  const [taskType, setTaskType] = usePersistedState('pmo:forge-task-type', '');
  const [priority, setPriority] = usePersistedState<number>('pmo:forge-priority', 1);

  // When initialSignal changes (card reforge, signal triage), reset the form.
  useEffect(() => {
    if (initialSignal) {
      const desc = `Signal: ${initialSignal.title}\n\nSeverity: ${initialSignal.severity}\nType: ${initialSignal.signal_type}\n\n${initialSignal.description ?? ''}`;
      setDescription(desc);
      setPhase('intake');
      setPlan(null);
      setGenerateError(null);
      setSaveError(null);
      setSavePath(null);
      setInterviewQuestions([]);
      // Auto-select project if signal has source_project_id
      if (initialSignal.source_project_id) {
        setProjectId(initialSignal.source_project_id);
      }
    }
  }, [initialSignal]); // eslint-disable-line react-hooks/exhaustive-deps

  const [plan, setPlan] = useState<ForgePlanResponse | null>(null);
  const [interviewQuestions, setInterviewQuestions] = useState<InterviewQuestion[]>([]);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savePath, setSavePath] = useState<string | null>(null);
  const [regenLoading, setRegenLoading] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const panelBodyRef = useRef<HTMLDivElement>(null);
  const selectedProject = projects.find(p => p.project_id === projectId);

  // Derived dirty flag: user has an unsaved plan in review
  const isDirty = !!plan && phase === 'preview';

  useEffect(() => {
    api.getProjects()
      .then(ps => {
        setProjects(ps);
        if (ps.length > 0 && !projectId) setProjectId(ps[0].project_id);
      })
      .catch(() => {})
      .finally(() => setProjectsLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => { abortRef.current?.abort(); };
  }, []);

  // Shift focus to the panel body on every phase transition so keyboard
  // users land at the top of the new phase content.
  useEffect(() => {
    panelBodyRef.current?.focus();
  }, [phase]);

  async function handleGenerate() {
    if (!description.trim() || !projectId) return;
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setPhase('generating');
    setGenerateError(null);
    try {
      const result = await api.forgePlan({
        description: description.trim(),
        program: selectedProject?.program ?? '',
        project_id: projectId,
        task_type: taskType || undefined,
        priority,
      });
      setPlan(result);
      setPhase('preview');
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      setGenerateError(err instanceof Error ? err.message : 'Generation failed');
      setPhase('intake');
    }
  }

  async function handleStartRegenerate() {
    if (!plan) return;
    setRegenLoading(true);
    try {
      const resp = await api.forgeInterview({ plan });
      setInterviewQuestions(resp.questions);
      setPhase('regenerating');
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : 'Failed to generate questions');
    }
    setRegenLoading(false);
  }

  async function handleRegenerate(answers: InterviewAnswer[]) {
    if (!plan) return;
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setPhase('generating');
    setGenerateError(null);
    try {
      const result = await api.forgeRegenerate({
        project_id: projectId,
        description: description.trim(),
        task_type: taskType || undefined,
        priority,
        original_plan: plan,
        answers,
      });
      setPlan(result);
      setPhase('preview');
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      setGenerateError(err instanceof Error ? err.message : 'Re-generation failed');
      setPhase('preview');
    }
  }

  async function handleApprove() {
    if (!plan) return;
    setSaveError(null);
    try {
      const result = await api.forgeApprove({ plan, project_id: projectId });
      setSavePath(result.path);
      setPhase('saved');
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Save failed');
    }
  }

  function handleBack() {
    if (isDirty) {
      const confirmed = window.confirm(
        'You have an unsaved plan. Leave anyway? Your task description is saved but the generated plan will be lost.'
      );
      if (!confirmed) return;
    }
    onBack();
  }

  const phaseLabel: Record<Phase, string> = {
    intake: 'Describe the work to generate a plan',
    generating: 'Generating plan...',
    preview: 'Review, edit, or regenerate',
    regenerating: 'Answer refinement questions',
    saved: 'Plan saved \u2014 ready to execute',
  };

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '7px 14px', borderBottom: `1px solid ${T.border}`,
        background: T.bg1, flexShrink: 0,
      }}>
        <button onClick={handleBack} style={{
          padding: '3px 8px', borderRadius: 3, border: `1px solid ${T.border}`,
          background: 'transparent', color: T.text2, fontSize: 9, cursor: 'pointer',
        }}>{'\u2190'} Board</button>
        <div style={{ width: 1, height: 14, background: T.border }} />
        <span style={{ fontSize: 11, fontWeight: 700, color: T.text0 }}>The Forge</span>
        <span style={{ fontSize: 9, color: T.text3 }}>{phaseLabel[phase]}</span>
        {initialSignal && (
          <span style={{
            padding: '1px 6px', borderRadius: 3, fontSize: 7, fontWeight: 600,
            color: T.red, background: T.red + '14', border: `1px solid ${T.red}22`,
          }}>from signal: {initialSignal.signal_id}</span>
        )}
        <div style={{ flex: 1 }} />
        {phase === 'preview' && (
          <button onClick={() => setPhase('intake')} style={{
            padding: '3px 8px', borderRadius: 3, border: `1px solid ${T.border}`,
            background: 'transparent', color: T.text2, fontSize: 9, cursor: 'pointer',
          }}>{'\u2190'} Edit Intake</button>
        )}
      </div>

      {/* Body */}
      <div
        ref={panelBodyRef}
        tabIndex={-1}
        style={{ flex: 1, overflow: 'auto', padding: 16, outline: 'none' }}
      >
        {/* Generation status — always in DOM for screen reader announcements */}
        <div
          role="status"
          aria-live="polite"
          aria-atomic="true"
          style={SR_ONLY}
        >
          {phase === 'generating' || phase === 'regenerating'
            ? 'Generating plan, please wait\u2026'
            : phase === 'preview'
            ? 'Plan ready for review.'
            : phase === 'saved'
            ? 'Plan saved and queued successfully.'
            : ''}
        </div>

        {/* Global error banner — visible in any phase */}
        <div
          id="forge-generate-error"
          role="alert"
          aria-live="assertive"
          aria-atomic="true"
        >
        {generateError && (
          <div style={{
            fontSize: 9,
            color: T.red,
            padding: '5px 8px',
            background: T.red + '12',
            borderRadius: 4,
            marginBottom: 10,
            maxWidth: 640,
          }}>
            {generateError}
          </div>
        )}
        </div>

        {/* Cancel button during generation phases */}
        {(phase === 'generating' || phase === 'regenerating') && (
          <div style={{ marginBottom: 10, maxWidth: 640 }}>
            <button
              onClick={() => {
                abortRef.current?.abort();
                setPhase(phase === 'regenerating' ? 'preview' : 'intake');
              }}
              style={{
                padding: '4px 12px',
                borderRadius: 3,
                border: `1px solid ${T.border}`,
                background: 'transparent',
                color: T.text2,
                fontSize: 9,
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
          </div>
        )}

        {/* INTAKE */}
        {(phase === 'intake' || phase === 'generating') && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 640 }}>
            <div style={{ fontSize: 9, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: 0.5 }}>
              Define the Work
            </div>

            {/* ADO Import */}
            <FormField label="Import from ADO" htmlFor="forge-ado-search">
              <AdoCombobox
                inputId="forge-ado-search"
                onSelect={item => {
                  setDescription(item.description || item.title);
                }}
              />
            </FormField>

            {/* Project selector */}
            <FormField label="Project *" htmlFor="forge-project">
              {projectsLoading ? (
                <div style={{ fontSize: 9, color: T.text3, padding: 4 }}>Loading projects...</div>
              ) : projects.length === 0 ? (
                <div style={{ fontSize: 9, color: T.yellow, padding: 4 }}>
                  No projects registered. Use <code>baton pmo add</code> to register one.
                </div>
              ) : (
                <select
                  id="forge-project"
                  value={projectId}
                  onChange={e => setProjectId(e.target.value)}
                  style={selectStyle}
                >
                  {projects.map(p => (
                    <option key={p.project_id} value={p.project_id}>{p.name} ({p.program})</option>
                  ))}
                </select>
              )}
            </FormField>

            <div style={{ display: 'flex', gap: 8 }}>
              <FormField label="Task Type" htmlFor="forge-task-type" style={{ flex: 1 }}>
                <select
                  id="forge-task-type"
                  value={taskType}
                  onChange={e => setTaskType(e.target.value)}
                  style={selectStyle}
                >
                  {TASK_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </FormField>
              <FormField label="Priority" htmlFor="forge-priority" style={{ flex: 1 }}>
                <select
                  id="forge-priority"
                  value={priority}
                  onChange={e => setPriority(Number(e.target.value))}
                  style={selectStyle}
                >
                  {PRIORITIES.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </FormField>
            </div>

            <FormField label="Task Description *" htmlFor="forge-description">
              <textarea
                id="forge-description"
                aria-required="true"
                aria-describedby="forge-description-hint"
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Describe the work: what needs to be built, fixed, or analyzed."
                rows={9}
                style={{
                  width: '100%', padding: '8px 10px', borderRadius: 4,
                  border: `1px solid ${T.border}`, background: T.bg1,
                  color: T.text0, fontSize: 10, lineHeight: 1.55,
                  outline: 'none', resize: 'vertical', fontFamily: 'inherit',
                }}
              />
              <div
                id="forge-description-hint"
                style={{ fontSize: 9, color: T.text3, marginTop: 3 }}
              >
                Required. Describe the task in detail. This is used to generate the plan.
              </div>
            </FormField>

            <button
              onClick={handleGenerate}
              disabled={phase === 'generating' || !description.trim() || !projectId}
              aria-describedby={generateError ? 'forge-generate-error' : undefined}
              style={{
                alignSelf: 'flex-start', padding: '7px 20px', borderRadius: 4,
                border: 'none',
                background: phase === 'generating' || !description.trim() || !projectId ? T.bg3 : `linear-gradient(135deg, ${T.accent}, #2563eb)`,
                color: '#fff', fontSize: 10, fontWeight: 700,
                cursor: phase === 'generating' || !description.trim() || !projectId ? 'not-allowed' : 'pointer',
                opacity: phase === 'generating' || !description.trim() || !projectId ? 0.6 : 1,
              }}
            >
              {phase === 'generating' ? 'Generating...' : 'Generate Plan \u2192'}
            </button>
          </div>
        )}

        {/* PREVIEW */}
        {phase === 'preview' && plan && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: T.text0 }}>Plan Ready</span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  onClick={handleApprove}
                  aria-describedby={saveError ? 'forge-save-error' : undefined}
                  style={{
                    padding: '5px 16px', borderRadius: 4, border: 'none',
                    background: `linear-gradient(135deg, ${T.green}, #059669)`,
                    color: '#fff', fontSize: 9, fontWeight: 700, cursor: 'pointer',
                  }}
                >Approve & Queue</button>
                <button onClick={handleStartRegenerate} disabled={regenLoading} style={{
                  padding: '5px 14px', borderRadius: 4,
                  border: `1px solid ${T.yellow}44`, background: 'transparent',
                  color: T.yellow, fontSize: 9, fontWeight: 600,
                  cursor: regenLoading ? 'not-allowed' : 'pointer',
                  opacity: regenLoading ? 0.6 : 1,
                }}>{regenLoading ? 'Loading...' : 'Regenerate'}</button>
              </div>
            </div>

            <div
              id="forge-save-error"
              role="alert"
              aria-live="assertive"
              aria-atomic="true"
            >
              {saveError && (
                <div style={{ fontSize: 9, color: T.red, padding: '5px 8px', background: T.red + '12', borderRadius: 4 }}>
                  {saveError}
                </div>
              )}
            </div>

            <PlanEditor plan={plan} onPlanChange={setPlan} />
          </div>
        )}

        {/* REGENERATING */}
        {phase === 'regenerating' && (
          <InterviewPanel
            questions={interviewQuestions}
            onSubmit={handleRegenerate}
            onCancel={() => setPhase('preview')}
          />
        )}

        {/* SAVED */}
        {phase === 'saved' && (
          <SavedPhase
            savePath={savePath}
            plan={plan}
            onNewPlan={() => { setPhase('intake'); setDescription(''); setPlan(null); }}
            onBack={onBack}
          />
        )}
      </div>
    </div>
  );
}

function SavedPhase({
  savePath, plan, onNewPlan, onBack,
}: {
  savePath: string | null;
  plan: ForgePlanResponse | null;
  onNewPlan: () => void;
  onBack: () => void;
}) {
  const [execLoading, setExecLoading] = useState(false);
  const [execResult, setExecResult] = useState<string | null>(null);

  async function handleExecute() {
    if (!plan) return;
    setExecLoading(true);
    setExecResult(null);
    try {
      const resp = await api.executeCard(plan.task_id);
      setExecResult(`Execution launched (PID ${resp.pid})`);
    } catch (err) {
      setExecResult(err instanceof Error ? err.message : 'Launch failed');
    } finally {
      setExecLoading(false);
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, paddingTop: 40 }}>
      <div style={{
        width: 48, height: 48, borderRadius: '50%',
        background: T.green + '20', border: `2px solid ${T.green}`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 22, color: T.green,
      }}>{'\u2713'}</div>
      <div style={{ fontSize: 14, fontWeight: 700, color: T.green }}>Plan Saved & Queued</div>
      {savePath && (
        <div style={{ fontSize: 9, color: T.text3, fontFamily: 'monospace' }} title={savePath}>
          {savePath.includes('/') ? savePath.split('/').pop() : savePath}
        </div>
      )}

      {/* Execution launch */}
      <button
        onClick={handleExecute}
        disabled={execLoading || !plan || execResult?.startsWith('Execution launched')}
        style={{
          padding: '7px 24px', borderRadius: 4, border: 'none',
          background: (execLoading || execResult?.startsWith('Execution launched')) ? T.bg3 : `linear-gradient(135deg, ${T.green}, #059669)`,
          color: '#fff', fontSize: 11, fontWeight: 700,
          cursor: execLoading ? 'not-allowed' : 'pointer',
          opacity: execLoading ? 0.6 : 1,
        }}
      >
        {execLoading ? 'Launching...' : '\u25B6 Start Execution'}
      </button>

      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {execResult && (
          <div style={{
            fontSize: 9,
            color: execResult.startsWith('Execution launched') ? T.green : T.red,
            padding: '4px 10px',
            background: execResult.startsWith('Execution launched') ? T.green + '12' : T.red + '12',
            borderRadius: 4,
          }}>
            {execResult}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={onNewPlan} style={{
          padding: '5px 14px', borderRadius: 4, border: `1px solid ${T.border}`,
          background: 'transparent', color: T.text2, fontSize: 9, cursor: 'pointer',
        }}>New Plan</button>
        <button onClick={onBack} style={{
          padding: '5px 14px', borderRadius: 4, border: 'none',
          background: T.accent, color: '#fff', fontSize: 9, fontWeight: 600, cursor: 'pointer',
        }}>Back to Board</button>
      </div>
    </div>
  );
}

function FormField({
  label,
  children,
  style,
  htmlFor,
}: {
  label: string;
  children: React.ReactNode;
  style?: React.CSSProperties;
  htmlFor?: string;
}) {
  return (
    <div style={style}>
      <label
        htmlFor={htmlFor}
        style={{ fontSize: 9, color: T.text2, display: 'block', marginBottom: 4 }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  width: '100%', padding: '6px 8px', borderRadius: 4,
  border: `1px solid ${T.border}`, background: T.bg1,
  color: T.text0, fontSize: 10, outline: 'none',
};
