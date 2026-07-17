import { useCallback, useEffect, useState } from 'react';
import type { ButtonHTMLAttributes, CSSProperties, FormEvent, ReactNode } from 'react';
import { api } from '../api/client';
import { T, FONTS, FONT_SIZES, SHADOWS, SR_ONLY } from '../styles/tokens';
import { useToast } from '../contexts/ToastContext';
import { GateApprovalPanel } from '../components/GateApprovalPanel';
import { ExecutionProgress } from '../components/ExecutionProgress';
import { deriveExecutionStatus, STATUS_META } from '../utils/executionStatus';
import type { DisplayExecutionStatus } from '../utils/executionStatus';
import type {
  PmoCard,
  ForgePlanResponse,
  PendingGate,
  CardExecutionDetail,
  ExecutionDecision,
  ManagerCharterResponse,
  ManagerScopeMapResponse,
  ManagerWorkstreamsResponse,
  ManagerTeamBlueprintResponse,
  ManagerRoleCard,
  ManagerKnowledgePlanResponse,
  ManagerScopeContractSummary,
  ManagerScopeContractResponse,
  ManagerContextBundleSummary,
  ManagerContextBundleResponse,
  ManagerVersionResponse,
  ManagerValidationResponse,
  ManagerDecision,
} from '../api/types';

type CardDetail = PmoCard & { plan: ForgePlanResponse | null };

interface ManagerData {
  charter?: ManagerCharterResponse;
  scopeMap?: ManagerScopeMapResponse;
  workstreams?: ManagerWorkstreamsResponse;
  teamBlueprint?: ManagerTeamBlueprintResponse;
  roleCards?: ManagerRoleCard[];
  knowledgePlan?: ManagerKnowledgePlanResponse;
  scopeContracts?: ManagerScopeContractSummary[];
  contextBundles?: ManagerContextBundleSummary[];
  version?: ManagerVersionResponse;
  validation?: ManagerValidationResponse;
  decisions?: ManagerDecision[];
}

function msg(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

// ---------------------------------------------------------------------------
// Shared layout primitives
// ---------------------------------------------------------------------------

const sectionStyle: CSSProperties = {
  background: T.bg1,
  border: `2px solid ${T.border}`,
  borderRadius: 8,
  padding: '10px 14px',
  marginBottom: 12,
  boxShadow: SHADOWS.sm,
};

const summaryStyle: CSSProperties = {
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'baseline',
  gap: 8,
  outline: 'none',
};

function Section({
  id,
  title,
  subtitle,
  defaultOpen = true,
  children,
}: {
  id: string;
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <details open={defaultOpen} style={sectionStyle} data-testid={`section-${id}`}>
      <summary style={summaryStyle}>
        <span style={{ fontFamily: FONTS.display, fontWeight: 800, fontSize: FONT_SIZES.lg, color: T.text0 }}>
          {title}
        </span>
        {subtitle && (
          <span style={{ fontSize: FONT_SIZES.xs, color: T.text2, fontFamily: FONTS.body }}>{subtitle}</span>
        )}
      </summary>
      <div style={{ marginTop: 10 }}>{children}</div>
    </details>
  );
}

function SectionError({ message }: { message: string }) {
  return (
    <div
      role="alert"
      style={{
        color: T.cherry,
        border: `1.5px solid ${T.cherry}`,
        borderRadius: 6,
        padding: '5px 8px',
        fontSize: FONT_SIZES.sm,
        marginBottom: 8,
      }}
    >
      {message}
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <div style={{ color: T.text2, fontSize: FONT_SIZES.sm, fontStyle: 'italic' }}>{children}</div>;
}

function TagList({ items }: { items: string[] }) {
  if (items.length === 0) return <Empty>None recorded.</Empty>;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
      {items.map((item, i) => (
        <span
          key={`${item}-${i}`}
          style={{
            background: T.bg3,
            border: `1px solid ${T.borderSoft}`,
            borderRadius: 12,
            padding: '2px 9px',
            fontSize: FONT_SIZES.xs,
            color: T.text1,
            fontFamily: FONTS.body,
          }}
        >
          {item}
        </span>
      ))}
    </div>
  );
}

/** Pairs a human-readable reason with its path, so a path is never the only
 * label a screen reader announces or a sighted operator scans for meaning. */
function EvidenceRow({ reason, path, extra }: { reason: string; path: string; extra?: string }) {
  return (
    <div style={{ fontSize: FONT_SIZES.sm, marginBottom: 4 }}>
      <span style={{ color: T.text0 }}>{reason || 'Reference'}</span>
      {extra && <span style={{ color: T.text2 }}> · {extra}</span>}
      <div style={{ fontFamily: FONTS.mono, fontSize: FONT_SIZES.xs, color: T.text3, wordBreak: 'break-all' }}>
        {path}
      </div>
    </div>
  );
}

function MarkdownBlock({ text }: { text: string }) {
  return (
    <pre
      style={{
        fontFamily: FONTS.mono,
        fontSize: FONT_SIZES.sm,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        background: T.bg3,
        padding: 10,
        borderRadius: 6,
        border: `1px solid ${T.borderSoft}`,
        maxHeight: 320,
        overflowY: 'auto',
        margin: 0,
      }}
    >
      {text}
    </pre>
  );
}

function Provenance({ revision, publishedAt }: { revision: number | null | undefined; publishedAt: string | null | undefined }) {
  if (revision == null) {
    return <div style={{ fontSize: FONT_SIZES.xs, color: T.text3, marginTop: 6 }}>Unversioned — never published.</div>;
  }
  return (
    <div style={{ fontSize: FONT_SIZES.xs, color: T.text3, marginTop: 6 }}>
      Published revision {revision}
      {publishedAt ? ` · ${publishedAt}` : ''}
    </div>
  );
}

function StatusBadge({ status }: { status: DisplayExecutionStatus }) {
  const meta = STATUS_META[status];
  const color = T[meta.colorKey];
  return (
    <span
      role="status"
      aria-live="polite"
      data-testid="execution-status-badge"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        padding: '3px 10px',
        borderRadius: 12,
        border: `2px solid ${color}`,
        color,
        fontWeight: 800,
        fontSize: FONT_SIZES.sm,
        fontFamily: FONTS.body,
      }}
    >
      <span aria-hidden="true">{meta.symbol}</span>
      {meta.label}
    </span>
  );
}

const buttonBase: CSSProperties = {
  padding: '5px 12px',
  borderRadius: 8,
  border: `2px solid ${T.border}`,
  fontFamily: FONTS.body,
  fontSize: FONT_SIZES.sm,
  fontWeight: 800,
  cursor: 'pointer',
};

function PrimaryButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props} style={{ ...buttonBase, background: T.mint, color: T.ink, ...props.style }} />;
}
function DangerButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props} style={{ ...buttonBase, background: T.cherry, color: T.cream, ...props.style }} />;
}
function GhostButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props} style={{ ...buttonBase, background: T.bg3, color: T.text1, ...props.style }} />;
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

/**
 * Manager Workspace — the Phase 7 "director console": a single, accessible
 * PMO surface for understanding a manager-mode plan's intent, scope,
 * ownership, evidence, provenance, execution state, and pending decisions,
 * and for resuming paused work through a durable decision.
 *
 * Reads exclusively through the read-only Manager PMO API
 * (`/pmo/manager/{card_id}/...`, agent_baton/api/routes/pmo_manager.py) plus
 * the existing card/execution/decision endpoints; the only mutations are
 * pause/resume, gate approval (delegated to `GateApprovalPanel`), and
 * decision resolution (generic + scope-expansion).
 */
export function ManagerWorkspaceView() {
  const toast = useToast();

  const [cards, setCards] = useState<PmoCard[]>([]);
  const [cardsError, setCardsError] = useState<string | null>(null);
  const [cardId, setCardId] = useState('');
  const [cardIdDraft, setCardIdDraft] = useState('');

  const [card, setCard] = useState<CardDetail | null>(null);
  const [notManagerMode, setNotManagerMode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [execution, setExecution] = useState<CardExecutionDetail | null>(null);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [controlStatus, setControlStatus] = useState<'paused' | 'running' | null>(null);
  const [controlPending, setControlPending] = useState(false);
  const [justResumed, setJustResumed] = useState(false);
  const [showTimeline, setShowTimeline] = useState(false);

  const [execDecisions, setExecDecisions] = useState<ExecutionDecision[]>([]);
  const [execDecisionsError, setExecDecisionsError] = useState<string | null>(null);

  const [pendingGate, setPendingGate] = useState<PendingGate | null>(null);

  const [managerData, setManagerData] = useState<ManagerData>({});
  const [managerErrors, setManagerErrors] = useState<Record<string, string>>({});

  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [decisionForms, setDecisionForms] = useState<Record<string, { option: string; rationale: string }>>({});
  const [additionalPathsInput, setAdditionalPathsInput] = useState<Record<string, string>>({});

  const [contractDetails, setContractDetails] = useState<Record<string, ManagerScopeContractResponse | 'loading' | 'error'>>({});
  const [bundleDetails, setBundleDetails] = useState<Record<string, ManagerContextBundleResponse | 'loading' | 'error'>>({});

  useEffect(() => {
    api.getBoard()
      .then(b => setCards(b.cards))
      .catch(err => setCardsError(msg(err)));
  }, []);

  const loadManagerData = useCallback(async (id: string): Promise<{ data: ManagerData; errors: Record<string, string> }> => {
    const errors: Record<string, string> = {};
    const [charterR, scopeMapR, workstreamsR, teamR, rolesR, knowR, contractsR, bundlesR, versionR, validationR, decisionsR] =
      await Promise.allSettled([
        api.getManagerCharter(id),
        api.getManagerScopeMap(id),
        api.getManagerWorkstreams(id),
        api.getManagerTeamBlueprint(id),
        api.listManagerRoleCards(id),
        api.getManagerKnowledgePlan(id),
        api.listManagerScopeContracts(id),
        api.listManagerContextBundles(id),
        api.getManagerVersion(id),
        api.getManagerValidation(id),
        api.listManagerDecisions(id),
      ]);

    const data: ManagerData = {};
    if (charterR.status === 'fulfilled') data.charter = charterR.value; else errors.charter = msg(charterR.reason);
    if (scopeMapR.status === 'fulfilled') data.scopeMap = scopeMapR.value; else errors.scopeMap = msg(scopeMapR.reason);
    if (workstreamsR.status === 'fulfilled') data.workstreams = workstreamsR.value; else errors.workstreams = msg(workstreamsR.reason);
    if (teamR.status === 'fulfilled') data.teamBlueprint = teamR.value; else errors.team = msg(teamR.reason);
    if (rolesR.status === 'fulfilled') data.roleCards = rolesR.value.role_cards; else errors.roles = msg(rolesR.reason);
    if (knowR.status === 'fulfilled') data.knowledgePlan = knowR.value; else errors.knowledge = msg(knowR.reason);
    if (contractsR.status === 'fulfilled') data.scopeContracts = contractsR.value.contracts; else errors.contracts = msg(contractsR.reason);
    if (bundlesR.status === 'fulfilled') data.contextBundles = bundlesR.value.bundles; else errors.bundles = msg(bundlesR.reason);
    if (versionR.status === 'fulfilled') data.version = versionR.value; else errors.version = msg(versionR.reason);
    if (validationR.status === 'fulfilled') data.validation = validationR.value; else errors.validation = msg(validationR.reason);
    if (decisionsR.status === 'fulfilled') data.decisions = decisionsR.value.decisions; else errors.decisions = msg(decisionsR.reason);
    return { data, errors };
  }, []);

  const refresh = useCallback(async (id: string) => {
    const trimmed = id.trim();
    if (!trimmed) return;
    setLoading(true);
    setLoadError(null);
    try {
      const detail = await api.getCardDetail(trimmed);
      setCard(detail);
      const isManagerMode = !!detail.plan?.manager_mode;
      setNotManagerMode(!isManagerMode);

      const [execR, execDecR, gatesR, managerR] = await Promise.allSettled([
        api.getCardExecution(trimmed),
        api.listExecutionDecisions(trimmed),
        api.listPendingGates(),
        isManagerMode ? loadManagerData(trimmed) : Promise.resolve({ data: {}, errors: {} }),
      ]);

      if (execR.status === 'fulfilled') {
        setExecution(execR.value);
        setExecutionError(null);
      } else {
        setExecution(null);
        setExecutionError(msg(execR.reason));
      }

      if (execDecR.status === 'fulfilled') {
        setExecDecisions(execDecR.value.decisions);
        setExecDecisionsError(null);
      } else {
        setExecDecisions([]);
        setExecDecisionsError(msg(execDecR.reason));
      }

      if (gatesR.status === 'fulfilled') {
        setPendingGate(gatesR.value.find(g => g.task_id === trimmed) ?? null);
      } else {
        setPendingGate(null);
      }

      if (managerR.status === 'fulfilled') {
        setManagerData(managerR.value.data);
        setManagerErrors(managerR.value.errors);
      } else {
        setManagerData({});
        setManagerErrors({});
      }
    } catch (err) {
      setLoadError(msg(err));
      setCard(null);
      setExecution(null);
      setExecDecisions([]);
      setPendingGate(null);
      setManagerData({});
    } finally {
      setLoading(false);
    }
  }, [loadManagerData]);

  function selectCard(id: string) {
    setCardId(id);
    setCardIdDraft(id);
    // A fresh card open starts from a clean local-action slate — any
    // paused/resuming badge belongs to whichever card was open before.
    // Refreshes of the SAME card (after a decision, gate, or the explicit
    // Refresh button) deliberately do NOT reset these: the execution
    // endpoint mirrors the kanban column, not the engine's richer status
    // vocabulary (see utils/executionStatus.ts), so a "just resumed" or
    // "paused" badge has no other server-side confirmation to wait for.
    setControlStatus(null);
    setJustResumed(false);
    setContractDetails({});
    setBundleDetails({});
    if (id) void refresh(id);
  }

  function handleManualLoad(e: FormEvent) {
    e.preventDefault();
    selectCard(cardIdDraft.trim());
  }

  async function handlePause() {
    if (!card) return;
    setControlPending(true);
    try {
      await api.pauseExecution(card.card_id);
      setControlStatus('paused');
      toast.success('Execution paused.');
    } catch (err) {
      toast.error(msg(err));
    } finally {
      setControlPending(false);
    }
  }

  async function handleResume() {
    if (!card) return;
    setControlPending(true);
    try {
      await api.resumeExecution(card.card_id);
      setControlStatus('running');
      toast.success('Execution resumed.');
    } catch (err) {
      toast.error(msg(err));
    } finally {
      setControlPending(false);
    }
  }

  async function handleResolveExecDecision(decision: ExecutionDecision) {
    const form = decisionForms[decision.request_id];
    if (!form?.option) {
      toast.error('Choose an option before resolving this decision.');
      return;
    }
    setResolvingId(decision.request_id);
    try {
      const result = await api.resolveExecutionDecision(cardId, decision.request_id, {
        option: form.option,
        rationale: form.rationale.trim() || undefined,
      });
      setJustResumed(result.execution_resumed);
      toast.success(result.execution_resumed ? 'Decision resolved — execution is resuming.' : 'Decision resolved.');
      await refresh(cardId);
    } catch (err) {
      toast.error(msg(err));
    } finally {
      setResolvingId(null);
    }
  }

  async function handleResolveManagerDecision(decision: ManagerDecision, resolution: 'approve' | 'reject') {
    setResolvingId(decision.decision_id);
    try {
      const rawPaths = additionalPathsInput[decision.decision_id] ?? '';
      const additional_paths = rawPaths
        .split(',')
        .map(p => p.trim())
        .filter(Boolean);
      await api.resolveManagerDecision(cardId, decision.decision_id, {
        resolution,
        additional_paths: additional_paths.length > 0 ? additional_paths : undefined,
      });
      setJustResumed(resolution === 'approve');
      toast.success(`Scope expansion ${resolution === 'approve' ? 'approved' : 'rejected'}.`);
      await refresh(cardId);
    } catch (err) {
      toast.error(msg(err));
    } finally {
      setResolvingId(null);
    }
  }

  async function loadContractDetail(stepId: string) {
    if (contractDetails[stepId]) return;
    setContractDetails(d => ({ ...d, [stepId]: 'loading' }));
    try {
      const data = await api.getManagerScopeContract(cardId, stepId);
      setContractDetails(d => ({ ...d, [stepId]: data }));
    } catch {
      setContractDetails(d => ({ ...d, [stepId]: 'error' }));
    }
  }

  async function loadBundleDetail(stepId: string) {
    if (bundleDetails[stepId]) return;
    setBundleDetails(d => ({ ...d, [stepId]: 'loading' }));
    try {
      const data = await api.getManagerContextBundle(cardId, stepId);
      setBundleDetails(d => ({ ...d, [stepId]: data }));
    } catch {
      setBundleDetails(d => ({ ...d, [stepId]: 'error' }));
    }
  }

  const pendingExecDecisions = execDecisions.filter(d => d.status === 'pending');
  const resolvedExecDecisions = execDecisions.filter(d => d.status !== 'pending');
  const managerDecisions = managerData.decisions ?? [];
  const pendingScopeExpansions = managerDecisions.filter(d => d.decision_type === 'scope_expansion' && !d.resolved_at);
  const otherManagerDecisions = managerDecisions.filter(d => !(d.decision_type === 'scope_expansion' && !d.resolved_at));

  const workstreamNameById = new Map<string, string>();
  for (const link of managerData.workstreams?.links ?? []) {
    if (link.workstream?.id) workstreamNameById.set(link.workstream.id, link.workstream.name || link.workstream.id);
  }

  const hasPendingDecisions = pendingExecDecisions.length > 0 || pendingScopeExpansions.length > 0;
  const displayStatus: DisplayExecutionStatus = card
    ? deriveExecutionStatus({
        column: card.column,
        error: card.error,
        controlStatus,
        justResumedViaDecision: justResumed,
        hasPendingDecisions,
      })
    : 'other';

  const containerStyle: CSSProperties = {
    padding: 16,
    background: T.bg0,
    color: T.text0,
    fontFamily: FONTS.body,
    height: '100%',
    overflowY: 'auto',
  };

  return (
    <div style={containerStyle} data-testid="manager-workspace">
      <h1 style={{ fontFamily: FONTS.display, fontSize: 24, margin: '0 0 4px' }}>Manager Workspace</h1>
      <p style={{ color: T.text2, fontSize: FONT_SIZES.sm, marginTop: 0, marginBottom: 14 }}>
        Intent, scope, ownership, evidence, execution state, and pending decisions for one plan, in one place.
      </p>

      {/* -------------------------------------------------------------- */}
      {/* Card picker                                                   */}
      {/* -------------------------------------------------------------- */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 16 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <label htmlFor="manager-card-select" style={{ fontSize: FONT_SIZES.xs, color: T.text1, fontWeight: 700 }}>
            Choose a plan
          </label>
          <select
            id="manager-card-select"
            value={cards.some(c => c.card_id === cardId) ? cardId : ''}
            onChange={e => selectCard(e.target.value)}
            style={{
              padding: '6px 8px',
              borderRadius: 6,
              border: `2px solid ${T.border}`,
              background: T.bg2,
              color: T.text0,
              fontFamily: FONTS.body,
              fontSize: FONT_SIZES.sm,
              minWidth: 280,
            }}
          >
            <option value="">Select a plan…</option>
            {cards.map(c => (
              <option key={c.card_id} value={c.card_id}>
                {c.title} — {c.project_id}
              </option>
            ))}
          </select>
        </div>

        <form onSubmit={handleManualLoad} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <label htmlFor="manager-card-id-input" style={{ fontSize: FONT_SIZES.xs, color: T.text1, fontWeight: 700 }}>
            Or open by task ID
          </label>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              id="manager-card-id-input"
              type="text"
              value={cardIdDraft}
              onChange={e => setCardIdDraft(e.target.value)}
              placeholder="task-id"
              style={{
                padding: '6px 8px',
                borderRadius: 6,
                border: `2px solid ${T.border}`,
                background: T.bg2,
                color: T.text0,
                fontFamily: FONTS.mono,
                fontSize: FONT_SIZES.sm,
              }}
            />
            <PrimaryButton type="submit">Open</PrimaryButton>
          </div>
        </form>

        {card && (
          <GhostButton type="button" onClick={() => void refresh(cardId)} disabled={loading} data-testid="refresh-button">
            {loading ? 'Refreshing…' : 'Refresh'}
          </GhostButton>
        )}
      </div>

      {cardsError && <SectionError message={`Could not load the board's plan list: ${cardsError}`} />}
      {loadError && <SectionError message={loadError} />}

      {!card && !loading && !loadError && (
        <Empty>Pick a plan above to open its manager workspace.</Empty>
      )}
      {loading && !card && <div style={{ color: T.text2 }}>Loading…</div>}

      {card && (
        <>
          {/* ---------------------------------------------------------- */}
          {/* Header                                                    */}
          {/* ---------------------------------------------------------- */}
          <div style={{ ...sectionStyle, background: T.bg2 }} data-testid="workspace-header">
            <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
              <div>
                <h2 style={{ fontFamily: FONTS.display, fontSize: 20, margin: 0 }}>{card.title}</h2>
                <div style={{ fontSize: FONT_SIZES.sm, color: T.text2 }}>
                  {card.project_id} / {card.program}
                  <span style={{ marginLeft: 8, fontFamily: FONTS.mono, fontSize: FONT_SIZES.xs, color: T.text3 }}>
                    {card.card_id}
                  </span>
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <StatusBadge status={displayStatus} />
              </div>
            </div>

            {notManagerMode && (
              <div
                role="status"
                data-testid="not-manager-mode-banner"
                style={{
                  marginTop: 8,
                  padding: '6px 9px',
                  borderRadius: 6,
                  background: T.butterSoft,
                  border: `1.5px solid ${T.butter}`,
                  fontSize: FONT_SIZES.sm,
                  color: T.text1,
                }}
              >
                This plan wasn't built in manager mode, so the charter, scope map, team blueprint, and other
                director-console artifacts aren't available — execution state and decisions below still apply.
              </div>
            )}

            {!notManagerMode && managerData.validation && !managerData.validation.valid && (
              <div
                role="alert"
                data-testid="stale-artifacts-banner"
                style={{
                  marginTop: 8,
                  padding: '6px 9px',
                  borderRadius: 6,
                  background: T.cherrySoft,
                  border: `1.5px solid ${T.cherry}`,
                  fontSize: FONT_SIZES.sm,
                  color: T.cherryDark,
                }}
              >
                {managerData.validation.published
                  ? `Published artifacts (revision ${managerData.validation.revision}) are stale relative to the current plan — the views below may not reflect the latest amendment.`
                  : 'No manager-mode artifacts have been published for this plan yet.'}
              </div>
            )}
          </div>

          {/* ---------------------------------------------------------- */}
          {/* Intent & success criteria                                 */}
          {/* ---------------------------------------------------------- */}
          {!notManagerMode && (
            <Section id="charter" title="Intent & success criteria" subtitle="Project charter">
              {managerErrors.charter && <SectionError message={managerErrors.charter} />}
              {managerData.charter ? (
                <>
                  <MarkdownBlock text={managerData.charter.markdown} />
                  <Provenance revision={managerData.charter.revision} publishedAt={managerData.charter.published_at} />
                </>
              ) : (
                !managerErrors.charter && <Empty>No charter published yet.</Empty>
              )}
            </Section>
          )}

          {/* ---------------------------------------------------------- */}
          {/* Phase / workstream health                                 */}
          {/* ---------------------------------------------------------- */}
          <Section id="phases" title="Phase & workstream health">
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 10, fontSize: FONT_SIZES.sm }}>
              <div>
                <strong>{card.steps_completed}</strong>/{card.steps_total} steps completed
              </div>
              <div><strong>{card.gates_passed}</strong> gates passed</div>
              <div>Current phase: <strong>{card.current_phase || '—'}</strong></div>
            </div>
            <div
              role="progressbar"
              aria-label="Plan step completion"
              aria-valuemin={0}
              aria-valuemax={card.steps_total || 0}
              aria-valuenow={card.steps_completed}
              style={{ height: 8, background: T.bg3, borderRadius: 4, overflow: 'hidden', marginBottom: 10 }}
            >
              <div
                style={{
                  height: '100%',
                  width: card.steps_total > 0 ? `${(card.steps_completed / card.steps_total) * 100}%` : '0%',
                  background: T.mint,
                }}
              />
            </div>
            {managerErrors.workstreams && <SectionError message={managerErrors.workstreams} />}
            {(managerData.workstreams?.links ?? []).length > 0 ? (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {managerData.workstreams!.links.map(link => (
                  <li key={link.phase_id} style={{ marginBottom: 6, fontSize: FONT_SIZES.sm }}>
                    <strong>Phase {link.phase_id}: {link.phase_name}</strong>
                    {card.current_phase === link.phase_name && (
                      <span style={{ marginLeft: 6, color: T.tangerine, fontWeight: 700 }}>(current)</span>
                    )}
                    {' — owned by '}
                    <span>{link.workstream.name || link.workstream.id || 'unassigned workstream'}</span>
                    {link.workstream.owner_role && <span> ({link.workstream.owner_role})</span>}
                  </li>
                ))}
              </ul>
            ) : (
              !managerErrors.workstreams && card.plan?.phases && (
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {card.plan.phases.map(p => (
                    <li key={p.phase_id} style={{ fontSize: FONT_SIZES.sm }}>
                      Phase {p.phase_id}: {p.name} ({p.steps.length} steps)
                    </li>
                  ))}
                </ul>
              )
            )}
          </Section>

          {/* ---------------------------------------------------------- */}
          {/* Scope boundaries & pending expansions                     */}
          {/* ---------------------------------------------------------- */}
          {!notManagerMode && (
            <Section id="scope" title="Scope boundaries & pending expansions">
              {managerErrors.scopeMap && <SectionError message={managerErrors.scopeMap} />}
              {managerData.scopeMap && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, marginBottom: 3 }}>Out of scope</div>
                  <TagList items={managerData.scopeMap.scope_map.out_of_scope} />
                  <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, margin: '8px 0 3px' }}>Cross-cutting concerns</div>
                  <TagList items={managerData.scopeMap.scope_map.cross_cutting_concerns} />
                  <div style={{ fontSize: FONT_SIZES.xs, color: T.text2, marginTop: 6 }}>
                    Scope-expansion policy: <strong>{managerData.scopeMap.scope_map.scope_expansion_policy}</strong>
                  </div>
                </div>
              )}

              {managerErrors.contracts && <SectionError message={managerErrors.contracts} />}
              <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, marginBottom: 4 }}>Step scope contracts</div>
              {(managerData.scopeContracts ?? []).length === 0 && !managerErrors.contracts && (
                <Empty>No step scope contracts recorded.</Empty>
              )}
              {(managerData.scopeContracts ?? []).map(c => {
                const detail = contractDetails[c.step_id];
                return (
                  <details
                    key={c.step_id}
                    style={{ marginBottom: 6 }}
                    onToggle={e => { if ((e.target as HTMLDetailsElement).open) void loadContractDetail(c.step_id); }}
                  >
                    <summary style={{ cursor: 'pointer', fontSize: FONT_SIZES.sm }}>
                      <strong>{c.step_id}</strong> — {c.agent_name || 'unassigned agent'}
                      {c.workstream_id && ` (${workstreamNameById.get(c.workstream_id) ?? c.workstream_id})`}
                      {' — '}{c.allowed_paths.length} allowed path{c.allowed_paths.length === 1 ? '' : 's'}
                    </summary>
                    <div style={{ paddingLeft: 12, marginTop: 4 }}>
                      {detail === 'loading' && <Empty>Loading contract…</Empty>}
                      {detail === 'error' && <SectionError message="Could not load this step's contract." />}
                      {detail && detail !== 'loading' && detail !== 'error' && (
                        <MarkdownBlock text={detail.markdown || JSON.stringify(detail.contract, null, 2)} />
                      )}
                    </div>
                  </details>
                );
              })}

              <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, margin: '10px 0 4px' }}>Pending scope expansions</div>
              {pendingScopeExpansions.length === 0 ? (
                <Empty>No scope expansions awaiting a decision.</Empty>
              ) : (
                pendingScopeExpansions.map(d => (
                  <div
                    key={d.decision_id}
                    data-testid="scope-expansion-decision"
                    style={{
                      border: `1.5px solid ${T.tangerine}`,
                      borderRadius: 6,
                      padding: 8,
                      marginBottom: 8,
                      background: T.tangerineSoft,
                    }}
                  >
                    <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm }}>{d.summary}</div>
                    <div style={{ fontSize: FONT_SIZES.xs, color: T.text1, marginBottom: 6 }}>{d.context}</div>
                    <label
                      htmlFor={`additional-paths-${d.decision_id}`}
                      style={{ fontSize: FONT_SIZES.xs, fontWeight: 700, display: 'block', marginBottom: 3 }}
                    >
                      Additional allowed paths to grant (comma-separated, optional)
                    </label>
                    <input
                      id={`additional-paths-${d.decision_id}`}
                      type="text"
                      value={additionalPathsInput[d.decision_id] ?? ''}
                      onChange={e => setAdditionalPathsInput(v => ({ ...v, [d.decision_id]: e.target.value }))}
                      style={{
                        width: '100%',
                        boxSizing: 'border-box',
                        padding: '4px 7px',
                        borderRadius: 5,
                        border: `1.5px solid ${T.border}`,
                        fontFamily: FONTS.mono,
                        fontSize: FONT_SIZES.xs,
                        marginBottom: 6,
                      }}
                    />
                    <div style={{ display: 'flex', gap: 6 }}>
                      <PrimaryButton
                        type="button"
                        disabled={resolvingId === d.decision_id}
                        onClick={() => void handleResolveManagerDecision(d, 'approve')}
                      >
                        Approve expansion
                      </PrimaryButton>
                      <DangerButton
                        type="button"
                        disabled={resolvingId === d.decision_id}
                        onClick={() => void handleResolveManagerDecision(d, 'reject')}
                      >
                        Deny expansion
                      </DangerButton>
                    </div>
                  </div>
                ))
              )}
            </Section>
          )}

          {/* ---------------------------------------------------------- */}
          {/* Team & role cards                                         */}
          {/* ---------------------------------------------------------- */}
          {!notManagerMode && (
            <Section id="team" title="Assigned team & role cards" defaultOpen={false}>
              {managerErrors.team && <SectionError message={managerErrors.team} />}
              {managerData.teamBlueprint && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontWeight: 700, fontSize: FONT_SIZES.md }}>
                    {managerData.teamBlueprint.team_blueprint.team_name || 'Team'}
                  </div>
                  <div style={{ fontSize: FONT_SIZES.sm, color: T.text2, marginBottom: 6 }}>
                    {managerData.teamBlueprint.team_blueprint.mission}
                  </div>
                  {managerData.teamBlueprint.team_blueprint.roles.map(role => (
                    <div key={role.role} style={{ border: `1px solid ${T.borderSoft}`, borderRadius: 6, padding: 7, marginBottom: 6 }}>
                      <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm }}>
                        {role.role} <span style={{ color: T.text2, fontWeight: 400 }}>· {role.agent_name}</span>
                      </div>
                      <div style={{ fontSize: FONT_SIZES.xs, color: T.text1, marginBottom: 4 }}>{role.mission}</div>
                      {role.owns.length > 0 && (
                        <div style={{ fontSize: FONT_SIZES.xs, color: T.text2 }}>Owns: {role.owns.join(', ')}</div>
                      )}
                      {role.does_not_own.length > 0 && (
                        <div style={{ fontSize: FONT_SIZES.xs, color: T.text2 }}>Does not own: {role.does_not_own.join(', ')}</div>
                      )}
                    </div>
                  ))}
                  {Object.keys(managerData.teamBlueprint.team_blueprint.workstream_assignments).length > 0 && (
                    <div style={{ fontSize: FONT_SIZES.xs, color: T.text2, marginTop: 4 }}>
                      {Object.entries(managerData.teamBlueprint.team_blueprint.workstream_assignments).map(([wsId, role]) => (
                        <div key={wsId}>{workstreamNameById.get(wsId) ?? wsId} → {role}</div>
                      ))}
                    </div>
                  )}
                  <Provenance revision={managerData.teamBlueprint.revision} publishedAt={managerData.teamBlueprint.published_at} />
                </div>
              )}

              {managerErrors.roles && <SectionError message={managerErrors.roles} />}
              <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, marginBottom: 4 }}>Role cards</div>
              {(managerData.roleCards ?? []).length === 0 && !managerErrors.roles && <Empty>No role cards published.</Empty>}
              {(managerData.roleCards ?? []).map(rc => (
                <details key={rc.role} style={{ marginBottom: 5 }}>
                  <summary style={{ cursor: 'pointer', fontSize: FONT_SIZES.sm, fontWeight: 700 }}>{rc.role}</summary>
                  <MarkdownBlock text={rc.markdown} />
                </details>
              ))}
            </Section>
          )}

          {/* ---------------------------------------------------------- */}
          {/* Knowledge & context provenance                            */}
          {/* ---------------------------------------------------------- */}
          {!notManagerMode && (
            <Section id="knowledge" title="Knowledge & context provenance" defaultOpen={false}>
              {managerErrors.knowledge && <SectionError message={managerErrors.knowledge} />}
              {managerData.knowledgePlan && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, marginBottom: 3 }}>Selected knowledge packs</div>
                  {managerData.knowledgePlan.knowledge_plan.selected_packs.length === 0 ? (
                    <Empty>None selected.</Empty>
                  ) : (
                    managerData.knowledgePlan.knowledge_plan.selected_packs.map(p => (
                      <EvidenceRow key={p.name} reason={`${p.name} (${p.confidence} confidence)`} path={p.path} extra={p.reason} />
                    ))
                  )}
                  {managerData.knowledgePlan.knowledge_plan.missing_packs.length > 0 && (
                    <>
                      <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, margin: '8px 0 3px' }}>Missing knowledge packs</div>
                      {managerData.knowledgePlan.knowledge_plan.missing_packs.map(p => (
                        <div key={p.name} style={{ fontSize: FONT_SIZES.sm, marginBottom: 3 }}>
                          <strong>{p.name}</strong> — {p.reason}
                        </div>
                      ))}
                    </>
                  )}
                  <Provenance revision={managerData.knowledgePlan.revision} publishedAt={managerData.knowledgePlan.published_at} />
                </div>
              )}

              {managerErrors.bundles && <SectionError message={managerErrors.bundles} />}
              <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, marginBottom: 4 }}>Per-step context bundles</div>
              {(managerData.contextBundles ?? []).length === 0 && !managerErrors.bundles && (
                <Empty>No context bundles recorded.</Empty>
              )}
              {(managerData.contextBundles ?? []).map(b => {
                const detail = bundleDetails[b.step_id];
                return (
                  <details
                    key={b.step_id}
                    style={{ marginBottom: 6 }}
                    onToggle={e => { if ((e.target as HTMLDetailsElement).open) void loadBundleDetail(b.step_id); }}
                  >
                    <summary style={{ cursor: 'pointer', fontSize: FONT_SIZES.sm }}>
                      <strong>{b.step_id}</strong> — {b.agent_name || 'unassigned agent'} — {b.must_read_count} must-read,{' '}
                      {b.knowledge_pack_count} knowledge pack{b.knowledge_pack_count === 1 ? '' : 's'},{' '}
                      {b.estimated_tokens}/{b.token_budget} tokens
                      {b.truncation_warnings.length > 0 && (
                        <span style={{ color: T.cherry, fontWeight: 700 }}> — truncated</span>
                      )}
                    </summary>
                    <div style={{ paddingLeft: 12, marginTop: 4 }}>
                      {detail === 'loading' && <Empty>Loading bundle…</Empty>}
                      {detail === 'error' && <SectionError message="Could not load this step's context bundle." />}
                      {detail && detail !== 'loading' && detail !== 'error' && (
                        <>
                          {detail.bundle.must_read.map((ref, i) => (
                            <EvidenceRow key={`mr-${i}`} reason={ref.reason || 'Must read'} path={ref.path} extra={ref.kind} />
                          ))}
                          {detail.bundle.reference_only.map((ref, i) => (
                            <EvidenceRow key={`ro-${i}`} reason={ref.reason || 'Reference only'} path={ref.path} extra={ref.kind} />
                          ))}
                        </>
                      )}
                    </div>
                  </details>
                );
              })}
            </Section>
          )}

          {/* ---------------------------------------------------------- */}
          {/* Artifact validation & version                             */}
          {/* ---------------------------------------------------------- */}
          {!notManagerMode && (
            <Section id="version" title="Artifact validation & version" defaultOpen={false}>
              {managerErrors.version && <SectionError message={managerErrors.version} />}
              {managerErrors.validation && <SectionError message={managerErrors.validation} />}
              {managerData.version && (
                <div style={{ fontSize: FONT_SIZES.sm, marginBottom: 8 }}>
                  {managerData.version.published ? (
                    <>
                      Revision <strong>{managerData.version.revision}</strong> (from {managerData.version.prior_revision}),
                      triggered by <strong>{managerData.version.trigger || 'unknown'}</strong> at {managerData.version.created_at}.
                      <div style={{ color: T.text2, fontSize: FONT_SIZES.xs }}>
                        {managerData.version.phase_count} phases · {managerData.version.step_count} steps ·{' '}
                        {managerData.version.published_paths.length} published files
                      </div>
                    </>
                  ) : (
                    <Empty>No revision has ever been published for this plan.</Empty>
                  )}
                </div>
              )}
              {managerData.validation && (
                <div
                  role="status"
                  data-testid="validation-status"
                  style={{
                    fontSize: FONT_SIZES.sm,
                    color: managerData.validation.valid ? T.mintDark : T.cherryDark,
                    fontWeight: 700,
                  }}
                >
                  {managerData.validation.valid ? 'Artifacts are version-consistent with the current plan.' : 'Artifacts are stale.'}
                  {managerData.validation.errors.map((e, i) => (
                    <div key={i} style={{ fontWeight: 400, color: T.text1 }}>{e}</div>
                  ))}
                </div>
              )}
            </Section>
          )}

          {/* ---------------------------------------------------------- */}
          {/* Execution progress                                        */}
          {/* ---------------------------------------------------------- */}
          <Section id="execution" title="Execution progress">
            {executionError && <SectionError message={executionError} />}
            {execution && (
              <div style={{ fontSize: FONT_SIZES.sm, marginBottom: 8 }}>
                <div>Started {execution.started_at} · elapsed {Math.round(execution.elapsed_seconds)}s · {execution.turn_count} turns</div>
                {execution.goal.completion_condition && (
                  <div style={{ color: T.text2, fontSize: FONT_SIZES.xs }}>
                    Goal: {execution.goal.completion_condition} — status {execution.goal.goal_status || 'active'}
                    {' '}({execution.goal.amend_cycles_used}/{execution.goal.max_amend_cycles} amend cycles)
                  </div>
                )}
              </div>
            )}
            {card.error && (
              <div role="alert" style={{ color: T.cherryDark, fontSize: FONT_SIZES.sm, marginBottom: 8 }}>
                Last error: {card.error}
              </div>
            )}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <PrimaryButton type="button" disabled={controlPending} onClick={() => void handlePause()} data-testid="pause-button">
                Pause
              </PrimaryButton>
              <PrimaryButton type="button" disabled={controlPending} onClick={() => void handleResume()} data-testid="resume-button">
                Resume
              </PrimaryButton>
              <GhostButton type="button" onClick={() => setShowTimeline(true)} data-testid="open-timeline-button">
                Open full execution timeline
              </GhostButton>
            </div>

            {pendingGate && (
              <div style={{ marginTop: 10 }}>
                <GateApprovalPanel card={card} onResolved={() => void refresh(cardId)} />
              </div>
            )}
          </Section>

          {/* ---------------------------------------------------------- */}
          {/* Decision inbox                                            */}
          {/* ---------------------------------------------------------- */}
          <Section id="decisions" title="Decision inbox" subtitle={hasPendingDecisions ? `${pendingExecDecisions.length + pendingScopeExpansions.length} pending` : 'up to date'}>
            {execDecisionsError && <SectionError message={execDecisionsError} />}
            {managerErrors.decisions && <SectionError message={managerErrors.decisions} />}

            <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, marginBottom: 6 }}>Pending</div>
            {pendingExecDecisions.length === 0 && pendingScopeExpansions.length === 0 && (
              <Empty>Nothing waiting on a human decision.</Empty>
            )}

            {pendingExecDecisions.map(d => {
              const form = decisionForms[d.request_id] ?? { option: '', rationale: '' };
              return (
                <form
                  key={d.request_id}
                  data-testid="execution-decision-form"
                  onSubmit={e => { e.preventDefault(); void handleResolveExecDecision(d); }}
                  style={{
                    border: `1.5px solid ${T.blueberry}`,
                    background: T.blueberrySoft,
                    borderRadius: 6,
                    padding: 8,
                    marginBottom: 8,
                  }}
                >
                  <fieldset style={{ border: 'none', padding: 0, margin: 0 }}>
                    <legend style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, padding: 0 }}>{d.summary}</legend>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginTop: 4 }}>
                      {d.options.map(opt => {
                        const inputId = `decision-${d.request_id}-${opt}`;
                        return (
                          <label key={opt} htmlFor={inputId} style={{ fontSize: FONT_SIZES.sm, display: 'flex', alignItems: 'center', gap: 5 }}>
                            <input
                              id={inputId}
                              type="radio"
                              name={`decision-${d.request_id}`}
                              value={opt}
                              checked={form.option === opt}
                              onChange={() => setDecisionForms(v => ({ ...v, [d.request_id]: { ...form, option: opt } }))}
                            />
                            {opt}
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>
                  <label htmlFor={`rationale-${d.request_id}`} style={{ fontSize: FONT_SIZES.xs, fontWeight: 700, display: 'block', marginTop: 6 }}>
                    Rationale (optional)
                  </label>
                  <textarea
                    id={`rationale-${d.request_id}`}
                    value={form.rationale}
                    onChange={e => setDecisionForms(v => ({ ...v, [d.request_id]: { ...form, rationale: e.target.value } }))}
                    rows={2}
                    style={{ width: '100%', boxSizing: 'border-box', fontFamily: FONTS.body, fontSize: FONT_SIZES.sm }}
                  />
                  <PrimaryButton type="submit" disabled={resolvingId === d.request_id} style={{ marginTop: 6 }}>
                    {resolvingId === d.request_id ? 'Resolving…' : 'Resolve decision'}
                  </PrimaryButton>
                </form>
              );
            })}

            {otherManagerDecisions.length > 0 && (
              <>
                <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, margin: '10px 0 6px' }}>Other decision packets</div>
                {otherManagerDecisions.map(d => (
                  <details key={d.decision_id} style={{ marginBottom: 6 }}>
                    <summary style={{ cursor: 'pointer', fontSize: FONT_SIZES.sm }}>
                      [{d.decision_type}] {d.summary}
                      {d.resolved_at ? ` — resolved (${d.resolution})` : ' — no in-app resolution path yet'}
                    </summary>
                    <MarkdownBlock text={d.markdown || d.context} />
                  </details>
                ))}
              </>
            )}

            {resolvedExecDecisions.length > 0 && (
              <>
                <div style={{ fontWeight: 700, fontSize: FONT_SIZES.sm, margin: '10px 0 6px' }}>Resolved</div>
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {resolvedExecDecisions.map(d => (
                    <li key={d.request_id} style={{ fontSize: FONT_SIZES.xs, color: T.text2 }}>
                      {d.summary} — <span style={{ fontWeight: 700 }}>{d.status}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Section>
        </>
      )}

      <span style={SR_ONLY} aria-live="polite">
        {card ? `${card.title} status: ${STATUS_META[displayStatus].label}` : ''}
      </span>

      {showTimeline && card && (
        <ExecutionProgress card={card} onClose={() => { setShowTimeline(false); void refresh(cardId); }} />
      )}
    </div>
  );
}

export default ManagerWorkspaceView;
