import { useState, useEffect, useRef } from 'react';
import type { ReactNode, CSSProperties } from 'react';
import { api } from '../api/client';
import { PlanEditor } from './PlanEditor';
import { InterviewPanel } from './InterviewPanel';
import { AdoCombobox } from './AdoCombobox';
import { ConfirmDialog } from './ConfirmDialog';
import { usePersistedState } from '../hooks/usePersistedState';
import { T, SR_ONLY, FONTS, SHADOWS } from '../styles/tokens';
import { useToast } from '../contexts/ToastContext';
import type { PmoProject, PmoSignal, ForgePlanResponse, InterviewQuestion, InterviewAnswer } from '../api/types';

interface ForgePanelProps {
  onBack: () => void;
  initialSignal?: PmoSignal | null;
  /** Called after a plan is successfully approved and queued, so the board can refresh. */
  onApproved?: () => void;
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

export function ForgePanel({ onBack, initialSignal, onApproved }: ForgePanelProps) {
  const toast = useToast();
  const [phase, setPhase] = useState<Phase>('intake');
  const [projects, setProjects] = useState<PmoProject[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);

  const signalDesc = initialSignal
    ? `Signal: ${initialSignal.title}\n\nSeverity: ${initialSignal.severity}\nType: ${initialSignal.signal_type}\n\n${initialSignal.description ?? ''}`
    : null;
  const [description, setDescription] = usePersistedState('pmo:forge-description', signalDesc ?? '', localStorage);
  const [projectId, setProjectId] = usePersistedState('pmo:forge-project-id', '');
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
  const [saving, setSaving] = useState(false);
  const [savePath, setSavePath] = useState<string | null>(null);
  const [regenLoading, setRegenLoading] = useState(false);
  const [showDraftBanner, setShowDraftBanner] = useState(false);
  const [showLeaveConfirm, setShowLeaveConfirm] = useState(false);

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

  // Show the draft restore banner when entering preview if a draft exists for this project.
  useEffect(() => {
    if (phase === 'preview') {
      try {
        const raw = localStorage.getItem('pmo:plan-draft');
        if (raw) {
          const parsed = JSON.parse(raw) as { plan: ForgePlanResponse; project_id: string };
          setShowDraftBanner(parsed.project_id === projectId);
        } else {
          setShowDraftBanner(false);
        }
      } catch {
        // localStorage unavailable or corrupt — ignore.
        setShowDraftBanner(false);
      }
    } else {
      setShowDraftBanner(false);
    }
  }, [phase, projectId]);

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

  function handleRestoreDraft() {
    try {
      const raw = localStorage.getItem('pmo:plan-draft');
      if (raw) {
        const parsed = JSON.parse(raw) as { plan: ForgePlanResponse; project_id: string };
        if (parsed.project_id === projectId) {
          setPlan(parsed.plan);
        }
      }
    } catch {
      // Corrupt draft — ignore.
    }
    setShowDraftBanner(false);
  }

  function handleDismissDraft() {
    try {
      localStorage.removeItem('pmo:plan-draft');
    } catch {
      // ignore.
    }
    setShowDraftBanner(false);
  }

  async function handleApprove() {
    if (!plan) return;
    setSaving(true);
    setSaveError(null);
    try {
      const result = await api.forgeApprove({ plan, project_id: projectId });
      // Clear any saved draft once the plan is officially queued.
      try { localStorage.removeItem('pmo:plan-draft'); } catch { /* ignore */ }

      // PMO-UX-007: if this plan was forged from a signal, resolve that signal
      // so it is cleared from the Signals Bar and linked to this plan.
      if (initialSignal?.signal_id) {
        api.resolveSignal(initialSignal.signal_id).catch(() => {
          // Non-fatal — the plan is saved; signal will be resolved on next poll.
        });
      }

      setSavePath(result.path);
      setPhase('saved');
      toast.success('Plan approved & queued');

      // PMO-UX-006: trigger an immediate board refresh so the new card appears
      // without waiting for the next poll cycle.
      onApproved?.();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  function handleBack() {
    if (isDirty) {
      setShowLeaveConfirm(true);
      return;
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
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: T.bg0, fontFamily: FONTS.body }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '8px 16px', borderBottom: `2px solid ${T.border}`,
        background: T.bg1, flexShrink: 0,
      }}>
        <button onClick={handleBack} style={{
          padding: '4px 10px', borderRadius: 6, border: `2px solid ${T.border}`,
          background: 'transparent', color: T.text1, fontSize: 11, fontWeight: 700,
          cursor: 'pointer', fontFamily: FONTS.body, boxShadow: SHADOWS.sm,
        }}>{'\u2190'} Board</button>
        <div style={{ width: 2, height: 16, background: T.borderSoft }} />
        <span style={{ fontSize: 13, fontWeight: 900, color: T.text0, fontFamily: FONTS.display }}>The Forge</span>
        <span style={{
          fontSize: 12, color: T.text2, fontFamily: FONTS.hand,
          transform: 'rotate(-0.5deg)', display: 'inline-block',
        }}>{phaseLabel[phase]}</span>
        {initialSignal && (
          <span
            title={`Signal: ${initialSignal.title} (${initialSignal.signal_id})`}
            style={{
              padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700,
              color: T.cherry, background: T.cherrySoft, border: `2px solid ${T.cherry}`,
              maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              fontFamily: FONTS.body,
            }}
          >
            from signal: {initialSignal.title.length > 40 ? initialSignal.title.slice(0, 40) + '\u2026' : initialSignal.title}
          </span>
        )}
        <div style={{ flex: 1 }} />
        {phase === 'preview' && (
          <button onClick={() => setPhase('intake')} style={{
            padding: '4px 10px', borderRadius: 6, border: `2px solid ${T.border}`,
            background: 'transparent', color: T.text1, fontSize: 11, fontWeight: 700,
            cursor: 'pointer', fontFamily: FONTS.body, boxShadow: SHADOWS.sm,
          }}>{'\u2190'} Edit Intake</button>
        )}
      </div>

      {/* Body */}
      <div
        ref={panelBodyRef}
        tabIndex={-1}
        style={{ flex: 1, overflow: 'auto', padding: 20, outline: 'none', background: T.bg0 }}
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
            fontSize: 12,
            color: T.cherry,
            padding: '8px 12px',
            background: T.cherrySoft,
            borderRadius: 8,
            marginBottom: 14,
            maxWidth: 660,
            border: `2px solid ${T.cherry}`,
            fontFamily: FONTS.body,
            fontWeight: 600,
          }}>
            {generateError}
          </div>
        )}
        </div>

        {/* Cancel button during generation phases */}
        {(phase === 'generating' || phase === 'regenerating') && (
          <div style={{ marginBottom: 14, maxWidth: 660 }}>
            <button
              onClick={() => {
                abortRef.current?.abort();
                setPhase(phase === 'regenerating' ? 'preview' : 'intake');
              }}
              style={{
                padding: '5px 14px',
                borderRadius: 8,
                border: `2px solid ${T.cherry}`,
                background: 'transparent',
                color: T.cherry,
                fontSize: 12,
                fontWeight: 700,
                cursor: 'pointer',
                fontFamily: FONTS.body,
              }}
            >
              Cancel
            </button>
          </div>
        )}

        {/* INTAKE */}
        {(phase === 'intake' || phase === 'generating') && (
          <div style={{ maxWidth: 660 }}>
            {/* Recipe card with slight tilt */}
            <div style={{
              background: T.bg1,
              border: `3px solid ${T.border}`,
              borderRadius: 18,
              boxShadow: SHADOWS.lg,
              transform: 'rotate(-0.4deg)',
              overflow: 'hidden',
            }}>
              {/* Card header */}
              <div style={{
                background: T.cherry,
                padding: '18px 22px',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ fontSize: 32, lineHeight: 1 }}>{'👨\u200d🍳'}</span>
                  <div>
                    <div style={{
                      fontFamily: FONTS.hand,
                      fontSize: 20,
                      color: T.cherrySoft,
                      transform: 'rotate(-2deg)',
                      display: 'inline-block',
                      marginBottom: 2,
                    }}>
                      what're we cookin', chef?
                    </div>
                    <div style={{
                      fontFamily: FONTS.display,
                      fontWeight: 900,
                      fontSize: 30,
                      color: T.cream,
                      lineHeight: 1.1,
                    }}>
                      Fire up a new recipe
                    </div>
                  </div>
                </div>
              </div>

              {/* Form body */}
              <div style={{
                padding: 22,
                display: 'flex',
                flexDirection: 'column',
                gap: 14,
                background: T.bg1,
              }}>
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
                    <div style={{ fontSize: 12, color: T.text3, padding: 4, fontFamily: FONTS.body }}>Loading projects...</div>
                  ) : projects.length === 0 ? (
                    <div style={{ fontSize: 12, color: T.butter, padding: 4, fontFamily: FONTS.body }}>
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

                <div style={{ display: 'flex', gap: 10 }}>
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
                      width: '100%', padding: '9px 11px', borderRadius: 8,
                      border: `2px solid ${T.border}`, background: T.bg3,
                      color: T.text0, fontSize: 13, fontWeight: 600, lineHeight: 1.55,
                      outline: 'none', resize: 'vertical', fontFamily: FONTS.body,
                      boxShadow: 'inset 2px 2px 0 0 rgba(0,0,0,.06)',
                      boxSizing: 'border-box',
                    }}
                  />
                  <div
                    id="forge-description-hint"
                    style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 4 }}
                  >
                    <span style={{ fontFamily: FONTS.hand, fontSize: 14, color: T.text2, transform: 'rotate(-0.5deg)', display: 'inline-block' }}>
                      Required. Describe the task in detail.
                    </span>
                    <span style={{
                      fontFamily: FONTS.mono,
                      fontSize: 12,
                      color: description.length > 4000 ? T.cherry : description.length > 3000 ? T.butter : T.text3,
                    }}>
                      {description.length} / 4000
                    </span>
                  </div>
                </FormField>

                {/* Footer row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                  <button
                    onClick={handleGenerate}
                    disabled={phase === 'generating' || !description.trim() || description.trim().length < 20 || !projectId}
                    aria-describedby={generateError ? 'forge-generate-error' : undefined}
                    style={{
                      padding: '14px 22px', borderRadius: 12,
                      border: `3px solid ${T.border}`,
                      background: (phase === 'generating' || !description.trim() || description.trim().length < 20 || !projectId) ? T.bg3 : T.cherry,
                      color: (phase === 'generating' || !description.trim() || description.trim().length < 20 || !projectId) ? T.text3 : T.cream,
                      fontSize: 14, fontWeight: 800,
                      cursor: (phase === 'generating' || !description.trim() || description.trim().length < 20 || !projectId) ? 'not-allowed' : 'pointer',
                      opacity: (phase === 'generating' || !description.trim() || description.trim().length < 20 || !projectId) ? 0.6 : 1,
                      fontFamily: FONTS.body,
                      boxShadow: (phase === 'generating' || !description.trim() || description.trim().length < 20 || !projectId) ? 'none' : SHADOWS.md,
                    }}
                  >
                    {phase === 'generating' ? 'Drafting...' : 'Draft the recipe \u2192'}
                  </button>
                  <span style={{
                    fontFamily: FONTS.hand, fontSize: 15, color: T.text2,
                    transform: 'rotate(-0.5deg)', display: 'inline-block', maxWidth: 300,
                  }}>
                    we'll draft the recipe — you can tweak before it hits the rail
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* GENERATING spinner */}
        {phase === 'generating' && (
          <div style={{
            maxWidth: 500, margin: '20px auto 0',
            background: T.bg1,
            border: `3px solid ${T.border}`,
            borderRadius: 18,
            boxShadow: SHADOWS.lg,
            padding: '36px 24px',
            textAlign: 'center',
            display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14,
          }}>
            <span style={{ fontSize: 64, lineHeight: 1 }}>🏃</span>
            <div style={{
              fontFamily: FONTS.display, fontWeight: 900, fontSize: 30, color: T.text0,
            }}>
              heating up the pans…
            </div>
            <div style={{
              fontFamily: FONTS.hand, fontSize: 22, color: T.cherry,
              transform: 'rotate(-1.5deg)', display: 'inline-block',
            }}>
              drafting your recipe, chef
            </div>
            {/* Progress bar */}
            <div style={{
              background: T.bg3, border: `2px solid ${T.border}`, borderRadius: 999,
              height: 12, width: '80%', overflow: 'hidden',
            }}>
              <div style={{
                height: '100%',
                backgroundImage: `repeating-linear-gradient(45deg, ${T.butter} 0 10px, ${T.tangerine} 10px 20px)`,
                animation: 'forge-bar 1.4s linear infinite',
                width: '100%',
              }} />
            </div>
          </div>
        )}

        {/* PREVIEW */}
        {phase === 'preview' && plan && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 900, margin: '0 auto' }}>

            {/* Draft restore banner */}
            {showDraftBanner && (
              <div
                role="status"
                aria-label="Draft available"
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '8px 14px', borderRadius: 8,
                  background: T.butterSoft,
                  border: `2px solid ${T.butter}`,
                  fontSize: 12, fontFamily: FONTS.body,
                }}
              >
                <span style={{ color: T.text0, flex: 1, fontWeight: 600 }}>Draft available from a previous session.</span>
                <button
                  onClick={handleRestoreDraft}
                  style={{
                    padding: '3px 10px', borderRadius: 6, border: `2px solid ${T.border}`,
                    background: T.butter, color: T.ink,
                    fontSize: 11, fontWeight: 700, cursor: 'pointer',
                    fontFamily: FONTS.body, boxShadow: SHADOWS.sm,
                  }}
                >Restore</button>
                <button
                  onClick={handleDismissDraft}
                  style={{
                    padding: '3px 10px', borderRadius: 6, border: `2px solid ${T.border}`,
                    background: 'transparent', color: T.text2,
                    fontSize: 11, fontWeight: 600, cursor: 'pointer', fontFamily: FONTS.body,
                  }}
                >Dismiss</button>
              </div>
            )}

            <div
              id="forge-save-error"
              role="alert"
              aria-live="assertive"
              aria-atomic="true"
            >
              {saveError && (
                <div style={{
                  fontSize: 12, color: T.cherry, padding: '8px 12px',
                  background: T.cherrySoft, borderRadius: 8,
                  border: `2px solid ${T.cherry}`, fontFamily: FONTS.body, fontWeight: 600,
                }}>
                  {saveError}
                </div>
              )}
            </div>

            <PlanEditor
              plan={plan}
              onPlanChange={(p) => setPlan(p)}
              onDraftSave={() => setShowDraftBanner(false)}
              projectId={projectId}
              onBack={() => setPhase('intake')}
              onApprove={handleApprove}
              saving={saving}
              onStartRegenerate={handleStartRegenerate}
              regenLoading={regenLoading}
            />
          </div>
        )}

        {/* REGENERATING */}
        {phase === 'regenerating' && (
          <div style={{ maxWidth: 700, margin: '0 auto' }}>
            <button
              onClick={() => setPhase('preview')}
              style={{
                background: 'none',
                border: 'none',
                color: T.text2,
                fontSize: 12,
                cursor: 'pointer',
                padding: '4px 0',
                marginBottom: 10,
                fontFamily: FONTS.body,
                fontWeight: 700,
              }}
            >
              {'\u2190'} Back to Plan
            </button>
            <InterviewPanel
              questions={interviewQuestions}
              onSubmit={handleRegenerate}
              onCancel={() => setPhase('preview')}
            />
          </div>
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

      {showLeaveConfirm && (
        <ConfirmDialog
          message="You have an unsaved plan. Leave anyway? Your task description is saved but the generated plan will be lost."
          confirmLabel="Leave"
          cancelLabel="Stay"
          onConfirm={() => { setShowLeaveConfirm(false); onBack(); }}
          onCancel={() => setShowLeaveConfirm(false)}
        />
      )}
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
    <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 40 }}>
      <div style={{
        background: T.bg1,
        border: `3px solid ${T.border}`,
        borderRadius: 18,
        boxShadow: SHADOWS.lg,
        padding: '30px 24px',
        textAlign: 'center',
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14,
        maxWidth: 480, width: '100%',
      }}>
        <span style={{ fontSize: 64, lineHeight: 1 }}>😄</span>
        <div style={{
          fontFamily: FONTS.display, fontWeight: 900, fontSize: 32, color: T.mint,
        }}>
          Clipped to the rail!
        </div>
        <div style={{
          fontFamily: FONTS.hand, fontSize: 20, color: T.cherry,
          transform: 'rotate(-1deg)', display: 'inline-block',
        }}>
          recipe's filed, chefs got it, dinner's on
        </div>

        {savePath && (
          <div style={{
            fontFamily: FONTS.mono, fontSize: 11, color: T.text2,
            background: T.bg3,
            border: `1.5px dashed ${T.borderSoft}`,
            borderRadius: 6,
            padding: '6px 10px',
            display: 'inline-block',
            wordBreak: 'break-all',
          }} title={savePath}>
            {savePath.includes('/') ? savePath.split('/').pop() : savePath}
          </div>
        )}

        {/* Execution launch */}
        <button
          onClick={handleExecute}
          disabled={execLoading || !plan || execResult?.startsWith('Execution launched')}
          style={{
            padding: '10px 26px', borderRadius: 12,
            border: `3px solid ${T.border}`,
            background: (execLoading || execResult?.startsWith('Execution launched')) ? T.bg3 : T.cherry,
            color: (execLoading || execResult?.startsWith('Execution launched')) ? T.text3 : T.cream,
            fontSize: 14, fontWeight: 800,
            cursor: execLoading ? 'not-allowed' : 'pointer',
            opacity: execLoading ? 0.6 : 1,
            fontFamily: FONTS.body,
            boxShadow: (execLoading || execResult?.startsWith('Execution launched')) ? 'none' : SHADOWS.md,
          }}
        >
          {execLoading ? 'Launching...' : 'Start Cookin\' \u25B6'}
        </button>

        <div
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          {execResult && (
            <div style={{
              fontSize: 12,
              color: execResult.startsWith('Execution launched') ? T.mint : T.cherry,
              padding: '6px 12px',
              background: execResult.startsWith('Execution launched') ? T.mintSoft : T.cherrySoft,
              borderRadius: 6, border: `2px solid ${execResult.startsWith('Execution launched') ? T.mint : T.cherry}`,
              fontFamily: FONTS.body, fontWeight: 600,
            }}>
              {execResult}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={onNewPlan} style={{
            padding: '8px 18px', borderRadius: 10,
            border: `2px solid ${T.border}`,
            background: T.bg1, color: T.text1, fontSize: 12, fontWeight: 700,
            cursor: 'pointer', fontFamily: FONTS.body, boxShadow: SHADOWS.sm,
          }}>Another recipe</button>
          <button onClick={onBack} style={{
            padding: '8px 18px', borderRadius: 10,
            border: `3px solid ${T.border}`,
            background: T.cherry, color: T.cream, fontSize: 12, fontWeight: 800,
            cursor: 'pointer', fontFamily: FONTS.body, boxShadow: SHADOWS.md,
          }}>Back to Board</button>
        </div>
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
  children: ReactNode;
  style?: CSSProperties;
  htmlFor?: string;
}) {
  return (
    <div style={style}>
      <label
        htmlFor={htmlFor}
        style={{
          fontFamily: FONTS.body, fontSize: 11, fontWeight: 800,
          textTransform: 'uppercase', letterSpacing: '0.08em',
          color: T.text1, display: 'block', marginBottom: 5,
        }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}

const selectStyle: CSSProperties = {
  width: '100%', padding: '9px 11px', borderRadius: 8,
  border: `2px solid ${T.border}`, background: T.bg3,
  color: T.text0, fontSize: 13, fontWeight: 600, outline: 'none',
  fontFamily: FONTS.body,
  boxShadow: 'inset 2px 2px 0 0 rgba(0,0,0,.06)',
};
