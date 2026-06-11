import { useState, useEffect, useCallback } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import type {
  SpecDraft,
  SpecQualityReport,
  SpecQueueStatus,
  SubmitSpecDraftBody,
  ImportSpecDraftBody,
} from '../api/types';

// ---------------------------------------------------------------------------
// Status badge colours — kitchen palette mapping
// submitted grey/enriched butter/approved mint/bounced cherry/fired blueberry
// ---------------------------------------------------------------------------

const STATUS_BADGE: Record<SpecQueueStatus, { bg: string; text: string; label: string }> = {
  submitted: { bg: T.bg4,       text: T.ink,   label: 'Submitted' },
  enriched:  { bg: T.butter,    text: T.ink,   label: 'Enriched'  },
  approved:  { bg: T.mint,      text: T.ink,   label: 'Approved'  },
  bounced:   { bg: T.cherry,    text: T.cream, label: 'Bounced'   },
  fired:     { bg: T.blueberry, text: T.cream, label: 'Fired'     },
};

const ALL_STATUSES: SpecQueueStatus[] = ['submitted', 'enriched', 'approved', 'bounced', 'fired'];

const RISK_BADGE: Record<string, { bg: string; text: string }> = {
  CRITICAL: { bg: T.cherry,    text: T.cream },
  HIGH:     { bg: T.tangerine, text: T.ink   },
  MEDIUM:   { bg: T.butter,    text: T.ink   },
  LOW:      { bg: T.mint,      text: T.ink   },
};

const SOURCE_BADGE: Record<string, { label: string; bg: string }> = {
  manual: { label: 'Manual',     bg: T.bg4       },
  github: { label: 'GitHub',     bg: T.blueberry },
  ado:    { label: 'Azure DevOps', bg: T.tangerine },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDate(iso: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function fmtUsd(n: number | undefined): string {
  if (n === undefined || n === null) return '—';
  return `$${n.toFixed(3)}`;
}

// ---------------------------------------------------------------------------
// Sub-components: badges
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: SpecQueueStatus }) {
  const cfg = STATUS_BADGE[status] ?? STATUS_BADGE.submitted;
  return (
    <span style={{
      display: 'inline-block',
      background: cfg.bg,
      color: cfg.text,
      fontFamily: FONTS.body,
      fontWeight: 800,
      fontSize: 9,
      textTransform: 'uppercase',
      letterSpacing: '0.07em',
      padding: '2px 8px',
      borderRadius: 999,
      border: `1.5px solid ${T.border}`,
      boxShadow: SHADOWS.sm,
      whiteSpace: 'nowrap',
    }}>
      {cfg.label}
    </span>
  );
}

function RiskBadge({ risk }: { risk: string }) {
  const cfg = RISK_BADGE[risk.toUpperCase()] ?? { bg: T.bg4, text: T.ink };
  return (
    <span style={{
      display: 'inline-block',
      background: cfg.bg,
      color: cfg.text,
      fontFamily: FONTS.mono,
      fontWeight: 700,
      fontSize: 9,
      padding: '2px 7px',
      borderRadius: 6,
      border: `1.5px solid ${T.border}`,
      whiteSpace: 'nowrap',
    }}>
      {risk.toUpperCase()}
    </span>
  );
}

function SourceBadge({ source }: { source: string }) {
  const cfg = SOURCE_BADGE[source] ?? { label: source, bg: T.bg4 };
  return (
    <span style={{
      display: 'inline-block',
      background: cfg.bg,
      color: cfg.bg === T.bg4 ? T.ink : T.cream,
      fontFamily: FONTS.mono,
      fontWeight: 700,
      fontSize: 9,
      padding: '2px 7px',
      borderRadius: 6,
      border: `1.5px solid ${T.border}`,
      whiteSpace: 'nowrap',
    }}>
      {cfg.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// FilterChip
// ---------------------------------------------------------------------------

function FilterChip({
  label, active, accent, onClick,
}: { label: string; active: boolean; accent?: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '3px 10px',
        borderRadius: 999,
        border: `1.5px solid ${active ? T.border : T.borderSoft}`,
        background: active ? (accent ?? T.butter) : T.bg2,
        color: active ? T.ink : T.text2,
        fontFamily: FONTS.body,
        fontSize: 10,
        fontWeight: 800,
        cursor: 'pointer',
        transition: 'all 100ms',
        boxShadow: active ? SHADOWS.sm : 'none',
      }}
    >
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// ActionButton
// ---------------------------------------------------------------------------

function ActionButton({
  label, accent, disabled, onClick,
}: { label: string; accent: string; disabled: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: '6px 14px',
        background: disabled ? T.bg4 : accent,
        color: T.ink,
        border: `2px solid ${T.border}`,
        borderRadius: 8,
        fontFamily: FONTS.body,
        fontWeight: 800,
        fontSize: 12,
        cursor: disabled ? 'not-allowed' : 'pointer',
        boxShadow: disabled ? 'none' : SHADOWS.sm,
        opacity: disabled ? 0.6 : 1,
        transition: 'all 100ms',
      }}
    >
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// SectionHeader — reusable uppercase label
// ---------------------------------------------------------------------------

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontFamily: FONTS.body,
      fontWeight: 800,
      fontSize: 10,
      textTransform: 'uppercase',
      letterSpacing: '0.07em',
      color: T.text3,
      marginBottom: 6,
    }}>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SpecQualityRow — spec quality score badge + missing-element list
// ---------------------------------------------------------------------------

function SpecQualityRow({ quality }: { quality: SpecQualityReport }) {
  const score = quality.score ?? 0;
  // Badge colour: ≥80 mint / 50-79 butter / <50 cherry
  const badgeBg   = score >= 80 ? T.mint   : score >= 50 ? T.butter : T.cherry;
  const badgeText = score >= 50 ? T.ink : T.cream;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {/* Label + score badge on one row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          fontFamily: FONTS.body, fontSize: 10, fontWeight: 700, color: T.text3,
          whiteSpace: 'nowrap',
        }}>
          Spec quality
        </span>
        <span style={{
          display: 'inline-block',
          background: badgeBg,
          color: badgeText,
          fontFamily: FONTS.mono,
          fontWeight: 800,
          fontSize: 11,
          padding: '2px 10px',
          borderRadius: 999,
          border: `1.5px solid ${T.border}`,
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.15)',
          minWidth: 36,
          textAlign: 'center',
        }}>
          {score}/100
        </span>
      </div>

      {/* Missing elements */}
      {quality.missing.length > 0 && (
        <ul style={{
          margin: 0, paddingLeft: 16,
          display: 'flex', flexDirection: 'column', gap: 3,
        }}>
          {quality.missing.map((item, i) => (
            <li key={i} style={{
              fontFamily: FONTS.body, fontSize: 10, color: T.text2,
              lineHeight: 1.4,
            }}>
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SubmitForm — collapsible top form
// ---------------------------------------------------------------------------

interface SubmitFormProps {
  onSubmitted: (draft: SpecDraft) => void;
}

type SourceMode = 'manual' | 'github' | 'ado';

function SubmitForm({ onSubmitted }: SubmitFormProps) {
  const [open, setOpen] = useState(false);
  const [sourceMode, setSourceMode] = useState<SourceMode>('manual');
  const [title, setTitle]     = useState('');
  const [body, setBody]       = useState('');
  const [ref, setRef]         = useState('');
  const [owner, setOwner]     = useState('');
  const [repo, setRepo]       = useState('');
  const [busy, setBusy]       = useState(false);
  const [error, setError]     = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      let result: SpecDraft;
      if (sourceMode === 'manual') {
        const body2: SubmitSpecDraftBody = { title, body: body || undefined };
        result = await api.submitSpecDraft(body2);
      } else {
        const imp: ImportSpecDraftBody = {
          source: sourceMode === 'github' ? 'github' : 'ado',
          ref,
          owner: owner || undefined,
          repo: repo || undefined,
        };
        result = await api.importSpecDraft(imp);
      }
      onSubmitted(result);
      // Reset
      setTitle(''); setBody(''); setRef(''); setOwner(''); setRepo('');
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Submit failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{
      borderBottom: `2px solid ${T.border}`,
      background: T.bg1,
      flexShrink: 0,
    }}>
      {/* Collapse toggle */}
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width: '100%',
          padding: '8px 16px',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          fontFamily: FONTS.body,
          fontWeight: 800,
          fontSize: 12,
          color: T.text1,
          textAlign: 'left',
        }}
      >
        <span style={{ fontSize: 13 }}>{open ? '▾' : '▸'}</span>
        Submit New Spec
        <span style={{ fontFamily: FONTS.hand, fontWeight: 400, fontSize: 11, color: T.text4, marginLeft: 4 }}>
          {open ? 'collapse' : 'expand to submit'}
        </span>
      </button>

      {open && (
        <form
          onSubmit={handleSubmit}
          style={{ padding: '0 16px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}
        >
          {/* Source mode radio */}
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <span style={{ fontFamily: FONTS.body, fontSize: 11, fontWeight: 700, color: T.text2 }}>Source:</span>
            {(['manual', 'github', 'ado'] as SourceMode[]).map(s => (
              <label key={s} style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                <input
                  type="radio"
                  name="source_mode"
                  value={s}
                  checked={sourceMode === s}
                  onChange={() => setSourceMode(s)}
                />
                <span style={{ fontFamily: FONTS.body, fontSize: 11, color: T.text1 }}>
                  {s === 'manual' ? 'Manual' : s === 'github' ? 'GitHub Issue' : 'Azure DevOps'}
                </span>
              </label>
            ))}
          </div>

          {sourceMode === 'manual' && (
            <>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ fontFamily: FONTS.body, fontSize: 11, fontWeight: 700, color: T.text2 }}>Title *</span>
                <input
                  type="text"
                  value={title}
                  onChange={e => setTitle(e.target.value)}
                  required
                  placeholder="Short descriptive title"
                  style={{
                    fontFamily: FONTS.body,
                    fontSize: 12,
                    padding: '5px 9px',
                    border: `1.5px solid ${T.border}`,
                    borderRadius: 7,
                    background: T.bg0,
                    color: T.text0,
                    outline: 'none',
                  }}
                />
              </label>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ fontFamily: FONTS.body, fontSize: 11, fontWeight: 700, color: T.text2 }}>Body</span>
                <textarea
                  value={body}
                  onChange={e => setBody(e.target.value)}
                  rows={4}
                  placeholder="Detailed description, context, acceptance criteria…"
                  style={{
                    fontFamily: FONTS.body,
                    fontSize: 12,
                    padding: '5px 9px',
                    border: `1.5px solid ${T.border}`,
                    borderRadius: 7,
                    background: T.bg0,
                    color: T.text0,
                    resize: 'vertical',
                    outline: 'none',
                  }}
                />
              </label>
            </>
          )}

          {(sourceMode === 'github' || sourceMode === 'ado') && (
            <>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ fontFamily: FONTS.body, fontSize: 11, fontWeight: 700, color: T.text2 }}>
                  {sourceMode === 'github' ? 'Issue number *' : 'Work item ID *'}
                </span>
                <input
                  type="text"
                  value={ref}
                  onChange={e => setRef(e.target.value)}
                  required
                  placeholder={sourceMode === 'github' ? 'e.g. 123' : 'e.g. 12345'}
                  style={{
                    fontFamily: FONTS.mono,
                    fontSize: 12,
                    padding: '5px 9px',
                    border: `1.5px solid ${T.border}`,
                    borderRadius: 7,
                    background: T.bg0,
                    color: T.text0,
                    outline: 'none',
                  }}
                />
              </label>
              {sourceMode === 'github' && (
                <>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    <span style={{ fontFamily: FONTS.body, fontSize: 11, fontWeight: 700, color: T.text2 }}>Owner</span>
                    <input
                      type="text"
                      value={owner}
                      onChange={e => setOwner(e.target.value)}
                      placeholder="github-org"
                      style={{
                        fontFamily: FONTS.mono, fontSize: 12, padding: '5px 9px',
                        border: `1.5px solid ${T.border}`, borderRadius: 7,
                        background: T.bg0, color: T.text0, outline: 'none',
                      }}
                    />
                  </label>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    <span style={{ fontFamily: FONTS.body, fontSize: 11, fontWeight: 700, color: T.text2 }}>Repo</span>
                    <input
                      type="text"
                      value={repo}
                      onChange={e => setRepo(e.target.value)}
                      placeholder="my-repo"
                      style={{
                        fontFamily: FONTS.mono, fontSize: 12, padding: '5px 9px',
                        border: `1.5px solid ${T.border}`, borderRadius: 7,
                        background: T.bg0, color: T.text0, outline: 'none',
                      }}
                    />
                  </label>
                </>
              )}
            </>
          )}

          {error && (
            <div role="alert" style={{
              background: T.cherrySoft, border: `1.5px solid ${T.cherry}`,
              borderRadius: 7, padding: '6px 10px',
              fontFamily: FONTS.body, fontSize: 11, color: T.cherryDark,
            }}>
              {error}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <ActionButton
              label={busy ? 'Submitting…' : 'Submit'}
              accent={T.butter}
              disabled={busy}
              onClick={() => {/* submitted via form onSubmit */}}
            />
            <button
              type="button"
              onClick={() => setOpen(false)}
              style={{
                padding: '6px 14px', background: 'transparent', color: T.text2,
                border: `1.5px solid ${T.borderSoft}`, borderRadius: 8,
                fontFamily: FONTS.body, fontWeight: 700, fontSize: 12, cursor: 'pointer',
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// List header row
// ---------------------------------------------------------------------------

function ListHeader() {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1fr 80px auto auto 90px 80px',
      alignItems: 'center',
      gap: 10,
      padding: '6px 14px',
      borderBottom: `2px solid ${T.border}`,
      background: T.bg3,
    }}>
      {['Title', 'Source', 'Status', 'Risk', 'Cost (mid)', 'Submitted'].map(col => (
        <span key={col} style={{
          fontFamily: FONTS.body,
          fontWeight: 800,
          fontSize: 9,
          textTransform: 'uppercase',
          letterSpacing: '0.07em',
          color: T.text3,
          whiteSpace: 'nowrap',
        }}>
          {col}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SpecDraftRow
// ---------------------------------------------------------------------------

function SpecDraftRow({ draft, selected, onClick }: {
  draft: SpecDraft;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-selected={selected}
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 80px auto auto 90px 80px',
        alignItems: 'center',
        gap: 10,
        width: '100%',
        padding: '10px 14px',
        background: selected ? T.butterSoft : 'transparent',
        border: 'none',
        borderBottom: `1.5px solid ${T.borderSoft}`,
        cursor: 'pointer',
        textAlign: 'left',
        transition: 'background 80ms',
      }}
    >
      <span style={{
        fontFamily: FONTS.body, fontWeight: 700, fontSize: 12, color: T.text0,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {draft.title}
      </span>

      <SourceBadge source={draft.source ?? 'manual'} />
      <StatusBadge status={draft.status} />

      {draft.enrichment
        ? <RiskBadge risk={draft.enrichment.risk_level} />
        : <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: T.text4 }}>—</span>
      }

      <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: T.text2, whiteSpace: 'nowrap' }}>
        {draft.enrichment ? fmtUsd(draft.enrichment.est_usd_mid) : '—'}
      </span>

      <span style={{ fontFamily: FONTS.mono, fontSize: 9, color: T.text4, whiteSpace: 'nowrap' }}>
        {fmtDate(draft.submitted_at)}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// SpecDraftDetail — right-side detail pane
// ---------------------------------------------------------------------------

interface SpecDraftDetailProps {
  draft: SpecDraft;
  onClose: () => void;
  onUpdated: (draft: SpecDraft) => void;
  onFired: (draft: SpecDraft) => void;
}

function SpecDraftDetail({ draft, onClose, onUpdated, onFired }: SpecDraftDetailProps) {
  const [busy, setBusy]             = useState(false);
  const [error, setError]           = useState<string | null>(null);
  const [bounceOpen, setBounceOpen] = useState(false);
  const [feedback, setFeedback]     = useState('');
  const [fireOpen, setFireOpen]     = useState(false);
  const [projectId, setProjectId]   = useState('');

  async function doEnrich() {
    setBusy(true); setError(null);
    try {
      const res = await api.enrichSpecDraft(draft.id);
      onUpdated(res);
    } catch (e) { setError(e instanceof Error ? e.message : 'Enrich failed'); }
    finally { setBusy(false); }
  }

  async function doApprove() {
    setBusy(true); setError(null);
    try {
      const res = await api.approveSpecDraft(draft.id);
      onUpdated(res);
    } catch (e) { setError(e instanceof Error ? e.message : 'Approve failed'); }
    finally { setBusy(false); }
  }

  async function doBounce(e: React.FormEvent) {
    e.preventDefault();
    if (!feedback.trim()) return;
    setBusy(true); setError(null);
    try {
      const res = await api.bounceSpecDraft(draft.id, { feedback });
      onUpdated(res);
      setFeedback(''); setBounceOpen(false);
    } catch (e) { setError(e instanceof Error ? e.message : 'Bounce failed'); }
    finally { setBusy(false); }
  }

  async function doFire(e: React.FormEvent) {
    e.preventDefault();
    if (!projectId.trim()) return;
    setBusy(true); setError(null);
    try {
      const res = await api.fireSpecDraft(draft.id, { project_id: projectId });
      // Merge fire response into draft shape for onFired callback
      onFired({ ...draft, status: 'fired', task_id: res.task_id });
      setProjectId(''); setFireOpen(false);
    } catch (e) { setError(e instanceof Error ? e.message : 'Fire failed'); }
    finally { setBusy(false); }
  }

  const canEnrich  = draft.status === 'submitted' || draft.status === 'bounced';
  const canApprove = draft.status === 'enriched';
  const canBounce  = draft.status === 'enriched';
  const canFire    = draft.status === 'approved';

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      borderLeft: `2px solid ${T.border}`, background: T.bg1, overflow: 'hidden',
    }}>
      {/* Detail header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '0 14px', height: 44,
        borderBottom: `2px solid ${T.border}`, background: T.bg3, flexShrink: 0,
      }}>
        <button
          onClick={onClose}
          aria-label="Close spec detail"
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            padding: '3px 8px', border: `1.5px solid ${T.border}`,
            borderRadius: 7, background: T.bg1, color: T.text1,
            fontFamily: FONTS.body, fontSize: 11, fontWeight: 800,
            cursor: 'pointer', boxShadow: SHADOWS.sm,
          }}
        >
          ← List
        </button>
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <div style={{
            fontFamily: FONTS.display, fontWeight: 700, fontSize: 13, color: T.text0,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {draft.title}
          </div>
        </div>
        <StatusBadge status={draft.status} />
      </div>

      {/* Scrollable body */}
      <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 16 }}>

        {/* Metadata grid */}
        <section aria-label="Spec draft metadata">
          <SectionHeader>Details</SectionHeader>
          <div style={{
            display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '5px 14px',
            fontFamily: FONTS.body, fontSize: 11,
          }}>
            {([
              ['ID',           draft.id],
              ['Source',       draft.source ?? 'manual'],
              ['Source ref',   draft.source_ref ?? '—'],
              ['Submitted by', draft.submitted_by ?? '—'],
              ['Submitted at', fmtDate(draft.submitted_at)],
              ['Updated at',   fmtDate(draft.updated_at)],
              ...(draft.task_id ? [['Task ID', draft.task_id]] : []),
            ] as [string, string][]).map(([label, value]) => (
              <>
                <span key={`lbl-${label}`} style={{ color: T.text3, fontWeight: 700, whiteSpace: 'nowrap' }}>{label}</span>
                <span key={`val-${label}`} style={{ color: T.text1, fontFamily: FONTS.mono, wordBreak: 'break-all', fontSize: 10 }}>{value}</span>
              </>
            ))}
          </div>
        </section>

        {/* Body */}
        {draft.body && (
          <section aria-label="Spec body">
            <SectionHeader>Body</SectionHeader>
            <pre style={{
              fontFamily: FONTS.mono, fontSize: 11, color: T.text1,
              background: T.bg3, border: `2px solid ${T.border}`, borderRadius: 8,
              padding: '10px 12px', overflowX: 'auto', whiteSpace: 'pre-wrap',
              wordBreak: 'break-word', margin: 0, boxShadow: SHADOWS.sm,
            }}>
              {draft.body}
            </pre>
          </section>
        )}

        {/* Enrichment */}
        {draft.enrichment && (
          <section aria-label="Enrichment data">
            <SectionHeader>Enrichment</SectionHeader>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {/* Risk + preset + confidence */}
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <RiskBadge risk={draft.enrichment.risk_level} />
                <span style={{
                  fontFamily: FONTS.body, fontSize: 11, color: T.text1,
                  background: T.bg3, border: `1.5px solid ${T.borderSoft}`,
                  borderRadius: 6, padding: '2px 8px',
                }}>
                  {draft.enrichment.guardrail_preset}
                </span>
                <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: T.text4 }}>
                  confidence: {draft.enrichment.confidence}
                </span>
              </div>

              {/* Required reviewers */}
              {draft.enrichment.required_reviewers.length > 0 && (
                <div>
                  <span style={{ fontFamily: FONTS.body, fontSize: 10, fontWeight: 700, color: T.text3, marginRight: 6 }}>
                    Required reviewers:
                  </span>
                  <div style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
                    {draft.enrichment.required_reviewers.map(r => (
                      <span key={r} style={{
                        fontFamily: FONTS.mono, fontSize: 10,
                        background: T.blueberrySoft, color: T.blueberry,
                        padding: '2px 8px', borderRadius: 6,
                        border: `1.5px solid ${T.blueberry}`,
                      }}>
                        {r}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Signals */}
              {draft.enrichment.signals_found.length > 0 && (
                <div>
                  <span style={{ fontFamily: FONTS.body, fontSize: 10, fontWeight: 700, color: T.text3, marginRight: 6 }}>
                    Signals:
                  </span>
                  <div style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
                    {draft.enrichment.signals_found.map(s => (
                      <span key={s} style={{
                        fontFamily: FONTS.mono, fontSize: 9,
                        background: T.bg4, color: T.text1,
                        padding: '1px 6px', borderRadius: 4,
                        border: `1px solid ${T.borderSoft}`,
                      }}>
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Cost estimate */}
              <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontFamily: FONTS.body, fontSize: 11, color: T.text2 }}>
                  Estimated cost:
                </span>
                <span style={{ fontFamily: FONTS.mono, fontSize: 12, color: T.text0, fontWeight: 700 }}>
                  {fmtUsd(draft.enrichment.est_usd_low)} – {fmtUsd(draft.enrichment.est_usd_mid)} – {fmtUsd(draft.enrichment.est_usd_high)}
                </span>
                <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: T.text4 }}>
                  ({draft.enrichment.cost_confidence})
                </span>
              </div>

              {/* Breakdown table */}
              {draft.enrichment.breakdown.length > 0 && (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{
                    fontFamily: FONTS.mono, fontSize: 10, borderCollapse: 'collapse', width: '100%',
                  }}>
                    <thead>
                      <tr style={{ background: T.bg3 }}>
                        {['Agent', 'Model', 'Steps', 'Tokens', 'USD'].map(h => (
                          <th key={h} style={{
                            padding: '4px 8px', textAlign: 'left', color: T.text3,
                            border: `1px solid ${T.borderSoft}`, fontWeight: 800,
                            fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.06em',
                          }}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {draft.enrichment.breakdown.map((row, i) => (
                        <tr key={i} style={{ background: i % 2 === 0 ? T.bg2 : T.bg1 }}>
                          <td style={{ padding: '3px 8px', border: `1px solid ${T.borderSoft}`, color: T.text1 }}>{row.agent_name}</td>
                          <td style={{ padding: '3px 8px', border: `1px solid ${T.borderSoft}`, color: T.text2 }}>{row.model}</td>
                          <td style={{ padding: '3px 8px', border: `1px solid ${T.borderSoft}`, color: T.text2, textAlign: 'right' }}>
                            {row.est_steps}
                          </td>
                          <td style={{ padding: '3px 8px', border: `1px solid ${T.borderSoft}`, color: T.text2, textAlign: 'right' }}>
                            {row.est_tokens.toLocaleString()}
                          </td>
                          <td style={{ padding: '3px 8px', border: `1px solid ${T.borderSoft}`, color: T.text0, textAlign: 'right', fontWeight: 700 }}>
                            {fmtUsd(row.est_usd)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Spec quality */}
              {draft.enrichment.spec_quality != null && (
                <SpecQualityRow quality={draft.enrichment.spec_quality} />
              )}
            </div>
          </section>
        )}

        {/* Review section */}
        {draft.review && (
          <section aria-label="Review decision">
            <SectionHeader>Review</SectionHeader>
            <div style={{
              background: draft.review.action === 'approved' ? T.mintSoft : T.cherrySoft,
              border: `1.5px solid ${draft.review.action === 'approved' ? T.mint : T.cherry}`,
              borderRadius: 8, padding: '10px 12px',
              display: 'flex', flexDirection: 'column', gap: 5,
            }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{
                  fontFamily: FONTS.body, fontWeight: 800, fontSize: 11,
                  color: draft.review.action === 'approved' ? T.mintDark : T.cherryDark,
                  textTransform: 'capitalize',
                }}>
                  {draft.review.action}
                </span>
                <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: T.text3 }}>
                  by {draft.review.actor}
                </span>
                {draft.review.reviewed_at && (
                  <span style={{ fontFamily: FONTS.mono, fontSize: 9, color: T.text4 }}>
                    {fmtDate(draft.review.reviewed_at)}
                  </span>
                )}
              </div>
              {draft.review.feedback && (
                <div style={{
                  fontFamily: FONTS.body, fontSize: 11, color: T.text1,
                  borderTop: `1px solid ${draft.review.action === 'approved' ? T.mint : T.cherry}`,
                  paddingTop: 5,
                }}>
                  {draft.review.feedback}
                </div>
              )}
            </div>
          </section>
        )}

        {/* Actions */}
        <section aria-label="Lifecycle actions">
          <SectionHeader>Actions</SectionHeader>

          {error && (
            <div role="alert" style={{
              background: T.cherrySoft, border: `1.5px solid ${T.cherry}`,
              borderRadius: 7, padding: '6px 10px',
              fontFamily: FONTS.body, fontSize: 11, color: T.cherryDark, marginBottom: 8,
            }}>
              {error}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: bounceOpen || fireOpen ? 12 : 0 }}>
            {canEnrich && (
              <ActionButton
                label={busy ? 'Enriching…' : 'Re-enrich'}
                accent={T.butter}
                disabled={busy}
                onClick={doEnrich}
              />
            )}
            {canApprove && (
              <ActionButton
                label="Approve"
                accent={T.mint}
                disabled={busy}
                onClick={doApprove}
              />
            )}
            {canBounce && !bounceOpen && (
              <ActionButton
                label="Bounce"
                accent={T.cherrySoft}
                disabled={busy}
                onClick={() => setBounceOpen(true)}
              />
            )}
            {canFire && !fireOpen && (
              <ActionButton
                label="Fire →"
                accent={T.blueberrySoft}
                disabled={busy}
                onClick={() => setFireOpen(true)}
              />
            )}
            {!canEnrich && !canApprove && !canBounce && !canFire && (
              <span style={{ fontFamily: FONTS.body, fontSize: 11, color: T.text4, fontStyle: 'italic' }}>
                No actions available in current state.
              </span>
            )}
          </div>

          {/* Inline bounce form */}
          {bounceOpen && (
            <form onSubmit={doBounce} style={{
              border: `1.5px solid ${T.cherry}`, borderRadius: 8,
              padding: '10px 12px', background: T.cherrySoft,
              display: 'flex', flexDirection: 'column', gap: 8,
            }}>
              <SectionHeader>Bounce feedback (required)</SectionHeader>
              <textarea
                value={feedback}
                onChange={e => setFeedback(e.target.value)}
                required
                rows={3}
                placeholder="What needs to change before this can be approved?"
                style={{
                  fontFamily: FONTS.body, fontSize: 12, padding: '5px 9px',
                  border: `1.5px solid ${T.cherry}`, borderRadius: 7,
                  background: T.bg0, color: T.text0, resize: 'vertical', outline: 'none',
                }}
              />
              <div style={{ display: 'flex', gap: 8 }}>
                <ActionButton
                  label={busy ? 'Bouncing…' : 'Send bounce'}
                  accent={T.cherry}
                  disabled={busy || !feedback.trim()}
                  onClick={() => {/* via form submit */}}
                />
                <button
                  type="button"
                  onClick={() => { setBounceOpen(false); setFeedback(''); }}
                  style={{
                    padding: '6px 14px', background: 'transparent', color: T.text2,
                    border: `1.5px solid ${T.borderSoft}`, borderRadius: 8,
                    fontFamily: FONTS.body, fontWeight: 700, fontSize: 12, cursor: 'pointer',
                  }}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}

          {/* Inline fire form */}
          {fireOpen && (
            <form onSubmit={doFire} style={{
              border: `1.5px solid ${T.blueberry}`, borderRadius: 8,
              padding: '10px 12px', background: T.blueberrySoft,
              display: 'flex', flexDirection: 'column', gap: 8,
            }}>
              <SectionHeader>Fire into project</SectionHeader>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <span style={{ fontFamily: FONTS.body, fontSize: 11, fontWeight: 700, color: T.text2 }}>Project ID *</span>
                <input
                  type="text"
                  value={projectId}
                  onChange={e => setProjectId(e.target.value)}
                  required
                  placeholder="proj-abc123"
                  style={{
                    fontFamily: FONTS.mono, fontSize: 12, padding: '5px 9px',
                    border: `1.5px solid ${T.blueberry}`, borderRadius: 7,
                    background: T.bg0, color: T.text0, outline: 'none',
                  }}
                />
              </label>
              <div style={{ display: 'flex', gap: 8 }}>
                <ActionButton
                  label={busy ? 'Firing…' : 'Fire!'}
                  accent={T.blueberry}
                  disabled={busy || !projectId.trim()}
                  onClick={() => {/* via form submit */}}
                />
                <button
                  type="button"
                  onClick={() => { setFireOpen(false); setProjectId(''); }}
                  style={{
                    padding: '6px 14px', background: 'transparent', color: T.text2,
                    border: `1.5px solid ${T.borderSoft}`, borderRadius: 8,
                    fontFamily: FONTS.body, fontWeight: 700, fontSize: 12, cursor: 'pointer',
                  }}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SpecQueuePanel — top-level component
// ---------------------------------------------------------------------------

interface SpecQueuePanelProps {
  onBack: () => void;
}

export function SpecQueuePanel({ onBack }: SpecQueuePanelProps) {
  const [drafts, setDrafts]         = useState<SpecDraft[]>([]);
  const [loading, setLoading]       = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<SpecQueueStatus | null>(null);

  const loadDrafts = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const params: { status?: SpecQueueStatus } = {};
      if (statusFilter) params.status = statusFilter;
      const res = await api.listSpecDrafts(params);
      setDrafts(res);
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : 'Failed to load spec drafts');
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { loadDrafts(); }, [loadDrafts]);

  const selectedDraft = drafts.find(d => d.id === selectedId) ?? null;

  function handleSubmitted(draft: SpecDraft) {
    setDrafts(prev => [draft, ...prev]);
    setSelectedId(draft.id);
  }

  function handleUpdated(updated: SpecDraft) {
    setDrafts(prev => prev.map(d => d.id === updated.id ? updated : d));
  }

  function handleFired(updated: SpecDraft) {
    setDrafts(prev => prev.map(d => d.id === updated.id ? updated : d));
  }

  return (
    <div style={{
      height: '100%', display: 'flex', flexDirection: 'column',
      background: T.bg0, overflow: 'hidden',
    }}>
      {/* Panel header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '0 16px', borderBottom: `2px solid ${T.border}`,
        background: T.bg3, flexShrink: 0, height: 44,
      }}>
        <button
          onClick={onBack}
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            padding: '4px 10px', border: `1.5px solid ${T.border}`,
            borderRadius: 8, background: T.bg1, color: T.text1,
            fontFamily: FONTS.body, fontSize: 12, fontWeight: 800,
            cursor: 'pointer', boxShadow: SHADOWS.sm,
          }}
        >
          ← Rail
        </button>

        <div style={{
          fontFamily: FONTS.display, fontWeight: 900, fontSize: 15,
          color: T.text0, letterSpacing: -0.3,
        }}>
          Spec Queue
        </div>

        <div style={{
          fontFamily: FONTS.hand, fontSize: 11, color: T.text3, transform: 'rotate(-1deg)',
        }}>
          submit → enrich → approve → fire
        </div>

        <div style={{ flex: 1 }} />

        {!loading && (
          <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: T.text4 }}>
            {drafts.length} spec{drafts.length !== 1 ? 's' : ''}
          </span>
        )}

        <button
          onClick={loadDrafts}
          aria-label="Refresh spec queue"
          style={{
            padding: '4px 10px', border: `1.5px solid ${T.borderSoft}`,
            borderRadius: 8, background: T.bg1, color: T.text2,
            fontFamily: FONTS.mono, fontSize: 11, fontWeight: 700, cursor: 'pointer',
          }}
        >
          ↻
        </button>
      </div>

      {/* Submit form (collapsible) */}
      <SubmitForm onSubmitted={handleSubmitted} />

      {/* Filter strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '7px 16px', borderBottom: `1.5px solid ${T.borderSoft}`,
        background: T.bg1, flexShrink: 0, flexWrap: 'wrap',
      }}>
        <span style={{
          fontFamily: FONTS.body, fontSize: 10, fontWeight: 800,
          color: T.text3, textTransform: 'uppercase', letterSpacing: '0.07em', marginRight: 4,
        }}>
          Status:
        </span>
        <FilterChip
          label="All"
          active={statusFilter === null}
          onClick={() => setStatusFilter(null)}
        />
        {ALL_STATUSES.map(s => (
          <FilterChip
            key={s}
            label={STATUS_BADGE[s].label}
            active={statusFilter === s}
            accent={STATUS_BADGE[s].bg}
            onClick={() => setStatusFilter(prev => prev === s ? null : s)}
          />
        ))}
      </div>

      {/* Content area — list | split */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex' }}>

        {/* List pane */}
        <div style={{
          display: 'flex', flexDirection: 'column',
          width: selectedDraft ? '42%' : '100%',
          minWidth: 380,
          borderRight: selectedDraft ? `2px solid ${T.border}` : 'none',
          overflow: 'hidden',
          transition: 'width 160ms ease',
        }}>
          <ListHeader />
          <div style={{ flex: 1, overflow: 'auto' }} role="list" aria-label="Spec drafts list">
            {loading && (
              <div style={{
                padding: 24, fontFamily: FONTS.body, fontSize: 12,
                color: T.text3, textAlign: 'center',
              }}>
                Loading spec queue…
              </div>
            )}

            {!loading && fetchError && (
              <div role="alert" style={{
                margin: 16, background: T.cherrySoft,
                border: `1.5px solid ${T.cherry}`, borderRadius: 8,
                padding: '10px 14px', fontFamily: FONTS.body, fontSize: 12, color: T.cherryDark,
              }}>
                {fetchError}
                <button
                  onClick={loadDrafts}
                  style={{
                    marginLeft: 10, background: 'none', border: 'none',
                    color: T.cherry, fontFamily: FONTS.body, fontWeight: 700,
                    fontSize: 12, cursor: 'pointer', textDecoration: 'underline',
                  }}
                >
                  Retry
                </button>
              </div>
            )}

            {!loading && !fetchError && drafts.length === 0 && (
              <div style={{
                padding: 24, fontFamily: FONTS.hand, fontSize: 14,
                color: T.text4, textAlign: 'center',
              }}>
                No specs in queue — use the form above to submit one.
              </div>
            )}

            {!loading && drafts.map(draft => (
              <div key={draft.id} role="listitem">
                <SpecDraftRow
                  draft={draft}
                  selected={draft.id === selectedId}
                  onClick={() => setSelectedId(prev => prev === draft.id ? null : draft.id)}
                />
              </div>
            ))}
          </div>
        </div>

        {/* Detail pane */}
        {selectedDraft && (
          <div style={{ flex: 1, overflow: 'hidden' }}>
            <SpecDraftDetail
              draft={selectedDraft}
              onClose={() => setSelectedId(null)}
              onUpdated={handleUpdated}
              onFired={handleFired}
            />
          </div>
        )}
      </div>
    </div>
  );
}
