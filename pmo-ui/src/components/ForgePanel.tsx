import { useState, useEffect } from 'react';
import { api } from '../api/client';
import { PlanPreview } from './PlanPreview';
import { T } from '../styles/tokens';
import type { PmoProject, PmoSignal, PlanResponse } from '../api/types';

interface ForgePanelProps {
  onBack: () => void;
  initialSignal?: PmoSignal | null;
}

type Phase = 'intake' | 'generating' | 'preview' | 'saved';

const TASK_TYPES = [
  { value: '', label: 'Auto-detect' },
  { value: 'feature', label: 'New Feature' },
  { value: 'bugfix', label: 'Bug Fix' },
  { value: 'refactor', label: 'Refactor' },
  { value: 'analysis', label: 'Analysis' },
  { value: 'migration', label: 'Migration' },
];

const PRIORITIES = [
  { value: 'P0', label: 'P0 — Critical' },
  { value: 'P1', label: 'P1 — High' },
  { value: 'P2', label: 'P2 — Normal' },
  { value: 'P3', label: 'P3 — Low' },
];

export function ForgePanel({ onBack, initialSignal }: ForgePanelProps) {
  const [phase, setPhase] = useState<Phase>('intake');
  const [projects, setProjects] = useState<PmoProject[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);

  const [description, setDescription] = useState(
    initialSignal
      ? `Signal: ${initialSignal.title}\n\nSeverity: ${initialSignal.severity}\nType: ${initialSignal.signal_type}\n\n${initialSignal.description ?? ''}`
      : ''
  );
  const [projectId, setProjectId] = useState('');
  const [taskType, setTaskType] = useState('');
  const [priority, setPriority] = useState('P1');

  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savePath, setSavePath] = useState<string | null>(null);

  const selectedProject = projects.find(p => p.project_id === projectId);

  useEffect(() => {
    api.getProjects()
      .then(ps => {
        setProjects(ps);
        if (ps.length > 0 && !projectId) setProjectId(ps[0].project_id);
      })
      .catch(() => { /* non-fatal */ })
      .finally(() => setProjectsLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleGenerate() {
    if (!description.trim() || !projectId) return;
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
      setGenerateError(err instanceof Error ? err.message : 'Generation failed');
      setPhase('intake');
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

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '7px 14px',
        borderBottom: `1px solid ${T.border}`,
        background: T.bg1,
        flexShrink: 0,
      }}>
        <button
          onClick={onBack}
          style={{
            padding: '3px 8px',
            borderRadius: 3,
            border: `1px solid ${T.border}`,
            background: 'transparent',
            color: T.text2,
            fontSize: 9,
            cursor: 'pointer',
          }}
        >
          {'\u2190'} Board
        </button>
        <div style={{ width: 1, height: 14, background: T.border }} />
        <span style={{ fontSize: 11, fontWeight: 700, color: T.text0 }}>The Forge</span>
        <span style={{ fontSize: 8, color: T.text3 }}>
          {phase === 'intake' && 'Describe the work to generate a plan'}
          {phase === 'generating' && 'Generating plan...'}
          {phase === 'preview' && 'Review plan before approving'}
          {phase === 'saved' && 'Plan saved — ready to execute'}
        </span>
        {initialSignal && (
          <span style={{
            padding: '1px 6px',
            borderRadius: 3,
            fontSize: 7,
            fontWeight: 600,
            color: T.red,
            background: T.red + '14',
            border: `1px solid ${T.red}22`,
          }}>
            from signal: {initialSignal.signal_id}
          </span>
        )}
        <div style={{ flex: 1 }} />
        {phase === 'preview' && (
          <button
            onClick={() => setPhase('intake')}
            style={{
              padding: '3px 8px',
              borderRadius: 3,
              border: `1px solid ${T.border}`,
              background: 'transparent',
              color: T.text2,
              fontSize: 9,
              cursor: 'pointer',
            }}
          >
            {'\u2190'} Edit
          </button>
        )}
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>

        {/* ── INTAKE ── */}
        {(phase === 'intake' || phase === 'generating') && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 640 }}>
            <div style={{ fontSize: 9, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: 0.5 }}>
              Define the Work
            </div>

            {/* Project selector */}
            <FormField label="Project *">
              {projectsLoading ? (
                <div style={{ fontSize: 8, color: T.text3, padding: 4 }}>Loading projects...</div>
              ) : projects.length === 0 ? (
                <div style={{ fontSize: 8, color: T.yellow, padding: 4 }}>
                  No projects registered. Use <code style={{ fontFamily: 'monospace' }}>POST /api/v1/pmo/projects</code> to add one.
                </div>
              ) : (
                <select
                  value={projectId}
                  onChange={e => setProjectId(e.target.value)}
                  style={selectStyle}
                >
                  {projects.map(p => (
                    <option key={p.project_id} value={p.project_id}>
                      {p.name} ({p.program})
                    </option>
                  ))}
                </select>
              )}
            </FormField>

            {/* Row: task type + priority */}
            <div style={{ display: 'flex', gap: 8 }}>
              <FormField label="Task Type" style={{ flex: 1 }}>
                <select
                  value={taskType}
                  onChange={e => setTaskType(e.target.value)}
                  style={selectStyle}
                >
                  {TASK_TYPES.map(t => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </FormField>
              <FormField label="Priority" style={{ flex: 1 }}>
                <select
                  value={priority}
                  onChange={e => setPriority(e.target.value)}
                  style={selectStyle}
                >
                  {PRIORITIES.map(p => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              </FormField>
            </div>

            {/* Task description */}
            <FormField label="Task Description *">
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Describe the work: what needs to be built, fixed, or analyzed. The more detail you provide, the better the generated plan."
                rows={9}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  borderRadius: 4,
                  border: `1px solid ${T.border}`,
                  background: T.bg1,
                  color: T.text0,
                  fontSize: 10,
                  lineHeight: 1.55,
                  outline: 'none',
                  resize: 'vertical',
                  fontFamily: 'inherit',
                }}
              />
            </FormField>

            {generateError && (
              <div style={{ fontSize: 9, color: T.red, padding: '5px 8px', background: T.red + '12', borderRadius: 4 }}>
                {generateError}
              </div>
            )}

            <button
              onClick={handleGenerate}
              disabled={phase === 'generating' || !description.trim() || !projectId}
              style={{
                alignSelf: 'flex-start',
                padding: '7px 20px',
                borderRadius: 4,
                border: 'none',
                background: phase === 'generating' || !description.trim() || !projectId
                  ? T.bg3
                  : `linear-gradient(135deg, ${T.accent}, #2563eb)`,
                color: '#fff',
                fontSize: 10,
                fontWeight: 700,
                cursor: phase === 'generating' || !description.trim() || !projectId ? 'not-allowed' : 'pointer',
                opacity: phase === 'generating' || !description.trim() || !projectId ? 0.6 : 1,
              }}
            >
              {phase === 'generating' ? 'Generating...' : 'Generate Plan \u2192'}
            </button>
          </div>
        )}

        {/* ── PLAN PREVIEW ── */}
        {phase === 'preview' && plan && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* Action bar */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: T.text0 }}>
                Plan Ready
              </span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  onClick={handleApprove}
                  style={{
                    padding: '5px 16px',
                    borderRadius: 4,
                    border: 'none',
                    background: `linear-gradient(135deg, ${T.green}, #059669)`,
                    color: '#fff',
                    fontSize: 9,
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  Approve & Save
                </button>
              </div>
            </div>

            {saveError && (
              <div style={{ fontSize: 9, color: T.red, padding: '5px 8px', background: T.red + '12', borderRadius: 4 }}>
                {saveError}
              </div>
            )}

            <PlanPreview plan={plan} />
          </div>
        )}

        {/* ── SAVED ── */}
        {phase === 'saved' && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, paddingTop: 40 }}>
            <div style={{
              width: 48,
              height: 48,
              borderRadius: '50%',
              background: T.green + '20',
              border: `2px solid ${T.green}`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 22,
              color: T.green,
            }}>
              {'\u2713'}
            </div>
            <div style={{ fontSize: 14, fontWeight: 700, color: T.green }}>
              Plan Saved
            </div>
            {savePath && (
              <div style={{ fontSize: 9, color: T.text3, fontFamily: 'monospace' }}>
                {savePath}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={() => { setPhase('intake'); setDescription(''); setPlan(null); }}
                style={{
                  padding: '5px 14px',
                  borderRadius: 4,
                  border: `1px solid ${T.border}`,
                  background: 'transparent',
                  color: T.text2,
                  fontSize: 9,
                  cursor: 'pointer',
                }}
              >
                New Plan
              </button>
              <button
                onClick={onBack}
                style={{
                  padding: '5px 14px',
                  borderRadius: 4,
                  border: 'none',
                  background: T.accent,
                  color: '#fff',
                  fontSize: 9,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                Back to Board
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function FormField({
  label,
  children,
  style,
}: {
  label: string;
  children: React.ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div style={style}>
      <label style={{ fontSize: 8, color: T.text2, display: 'block', marginBottom: 4 }}>
        {label}
      </label>
      {children}
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  width: '100%',
  padding: '6px 8px',
  borderRadius: 4,
  border: `1px solid ${T.border}`,
  background: T.bg1,
  color: T.text0,
  fontSize: 10,
  outline: 'none',
};
