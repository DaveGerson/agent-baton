import { useState, useEffect, useCallback } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import type { Spec, SpecState } from '../api/types';

// ---------------------------------------------------------------------------
// State badge colours — kitchen palette mapping
// ---------------------------------------------------------------------------

const STATE_BADGE: Record<SpecState, { bg: string; text: string; label: string }> = {
  draft:     { bg: T.bg4,        text: T.ink,   label: 'Draft'     },
  reviewed:  { bg: T.butter,     text: T.ink,   label: 'Reviewed'  },
  approved:  { bg: T.mint,       text: T.ink,   label: 'Approved'  },
  executing: { bg: T.blueberry,  text: T.cream, label: 'Executing' },
  completed: { bg: T.mintDark,   text: T.cream, label: 'Completed' },
  archived:  { bg: T.inkFaint,   text: T.cream, label: 'Archived'  },
};

const ALL_STATES: SpecState[] = ['draft', 'reviewed', 'approved', 'executing', 'completed', 'archived'];

const TASK_TYPE_ACCENT: Record<string, string> = {
  'feature':    T.blueberry,
  'bug-fix':    T.cherry,
  'refactor':   T.tangerine,
  'migration':  T.crust,
};

function taskTypeAccent(taskType: string): string {
  return TASK_TYPE_ACCENT[taskType] ?? T.inkFaint;
}

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

function scoreBar(value: number): string {
  const pct = Math.round(value * 100);
  return `${pct}%`;
}

// ---------------------------------------------------------------------------
// SpecStateBadge
// ---------------------------------------------------------------------------

function SpecStateBadge({ state }: { state: SpecState }) {
  const cfg = STATE_BADGE[state] ?? STATE_BADGE.draft;
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

// ---------------------------------------------------------------------------
// Filter chip
// ---------------------------------------------------------------------------

interface ChipProps {
  label: string;
  active: boolean;
  accent?: string;
  onClick: () => void;
}

function FilterChip({ label, active, accent, onClick }: ChipProps) {
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
// SpecRow — one row in the list view
// ---------------------------------------------------------------------------

interface SpecRowProps {
  spec: Spec;
  selected: boolean;
  onClick: () => void;
}

function SpecRow({ spec, selected, onClick }: SpecRowProps) {
  return (
    <button
      onClick={onClick}
      aria-selected={selected}
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr auto auto auto auto',
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
      {/* Title */}
      <span style={{
        fontFamily: FONTS.body,
        fontWeight: 700,
        fontSize: 12,
        color: T.text0,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}>
        {spec.title}
      </span>

      {/* Task type badge */}
      <span style={{
        flexShrink: 0,
        background: taskTypeAccent(spec.task_type),
        color: T.cream,
        fontFamily: FONTS.mono,
        fontSize: 9,
        fontWeight: 700,
        padding: '2px 7px',
        borderRadius: 6,
        border: `1.5px solid ${T.border}`,
        whiteSpace: 'nowrap',
      }}>
        {spec.task_type}
      </span>

      {/* State badge */}
      <SpecStateBadge state={spec.state} />

      {/* Author */}
      <span style={{
        flexShrink: 0,
        fontFamily: FONTS.mono,
        fontSize: 10,
        color: T.text3,
        whiteSpace: 'nowrap',
      }}>
        {spec.author_id}
      </span>

      {/* Date */}
      <span style={{
        flexShrink: 0,
        fontFamily: FONTS.mono,
        fontSize: 9,
        color: T.text4,
        whiteSpace: 'nowrap',
      }}>
        {fmtDate(spec.created_at)}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// SpecDetail — right-side detail pane
// ---------------------------------------------------------------------------

interface SpecDetailProps {
  spec: Spec;
  onClose: () => void;
  onStateChange: (specId: string, newState: SpecState) => void;
}

function SpecDetail({ spec, onClose, onStateChange }: SpecDetailProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function doAction(action: 'approve' | 'review' | 'archive') {
    setBusy(true);
    setError(null);
    try {
      if (action === 'approve') {
        const res = await api.approveSpec(spec.spec_id);
        onStateChange(spec.spec_id, res.state);
      } else if (action === 'review') {
        const res = await api.markSpecReviewed(spec.spec_id);
        onStateChange(spec.spec_id, res.state);
      } else {
        const res = await api.archiveSpec(spec.spec_id);
        onStateChange(spec.spec_id, res.state);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  }

  const canApprove  = spec.state === 'reviewed';
  const canReview   = spec.state === 'draft';
  const canArchive  = spec.state !== 'archived' && spec.state !== 'executing';

  const scoreEntries = spec.score
    ? Object.entries(spec.score).filter(([, v]) => v !== undefined) as [string, number][]
    : [];

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      borderLeft: `2px solid ${T.border}`,
      background: T.bg1,
      overflow: 'hidden',
    }}>
      {/* Detail header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '0 14px',
        height: 44,
        borderBottom: `2px solid ${T.border}`,
        background: T.bg3,
        flexShrink: 0,
      }}>
        <button
          onClick={onClose}
          aria-label="Close spec detail"
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            padding: '3px 8px',
            border: `1.5px solid ${T.border}`,
            borderRadius: 7,
            background: T.bg1,
            color: T.text1,
            fontFamily: FONTS.body,
            fontSize: 11, fontWeight: 800,
            cursor: 'pointer',
            boxShadow: SHADOWS.sm,
          }}
        >
          ← List
        </button>
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <div style={{
            fontFamily: FONTS.display,
            fontWeight: 700,
            fontSize: 13,
            color: T.text0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {spec.title}
          </div>
        </div>
        <SpecStateBadge state={spec.state} />
      </div>

      {/* Scrollable body */}
      <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 16 }}>

        {/* Metadata grid */}
        <section aria-label="Spec metadata">
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'auto 1fr',
            gap: '5px 14px',
            fontFamily: FONTS.body,
            fontSize: 11,
          }}>
            {[
              ['ID',         spec.spec_id],
              ['Task type',  spec.task_type],
              ['Author',     spec.author_id],
              ['Template',   spec.template_id ?? '—'],
              ['Created',    fmtDate(spec.created_at)],
              ['Updated',    fmtDate(spec.updated_at)],
            ].map(([label, value]) => (
              <>
                <span key={`lbl-${label}`} style={{ color: T.text3, fontWeight: 700, whiteSpace: 'nowrap' }}>{label}</span>
                <span key={`val-${label}`} style={{ color: T.text1, fontFamily: FONTS.mono, wordBreak: 'break-all' }}>{value}</span>
              </>
            ))}
          </div>
        </section>

        {/* Linked plan IDs */}
        {spec.linked_plan_ids.length > 0 && (
          <section aria-label="Linked plans">
            <div style={{
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 10,
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
              color: T.text3,
              marginBottom: 6,
            }}>
              Linked Plans
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
              {spec.linked_plan_ids.map(pid => (
                <span key={pid} style={{
                  fontFamily: FONTS.mono,
                  fontSize: 10,
                  background: T.blueberrySoft,
                  color: T.blueberry,
                  padding: '2px 8px',
                  borderRadius: 6,
                  border: `1.5px solid ${T.blueberry}`,
                }}>
                  {pid}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Score breakdown */}
        {scoreEntries.length > 0 && (
          <section aria-label="Spec score breakdown">
            <div style={{
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 10,
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
              color: T.text3,
              marginBottom: 8,
            }}>
              Score Breakdown
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {scoreEntries.map(([dim, val]) => (
                <div key={dim} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{
                    fontFamily: FONTS.body,
                    fontSize: 11,
                    color: T.text2,
                    width: 100,
                    textTransform: 'capitalize',
                    flexShrink: 0,
                  }}>
                    {dim}
                  </span>
                  {/* Bar track */}
                  <div style={{
                    flex: 1,
                    height: 8,
                    background: T.bg4,
                    borderRadius: 4,
                    border: `1.5px solid ${T.border}`,
                    overflow: 'hidden',
                  }}>
                    <div style={{
                      width: scoreBar(val),
                      height: '100%',
                      background: val >= 0.8 ? T.mint : val >= 0.6 ? T.butter : T.cherry,
                      borderRadius: 4,
                      transition: 'width 300ms ease',
                    }} />
                  </div>
                  <span style={{
                    fontFamily: FONTS.mono,
                    fontSize: 10,
                    color: T.text2,
                    flexShrink: 0,
                    width: 32,
                    textAlign: 'right',
                  }}>
                    {scoreBar(val)}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Content — YAML pretty-print */}
        <section aria-label="Spec content">
          <div style={{
            fontFamily: FONTS.body,
            fontWeight: 800,
            fontSize: 10,
            textTransform: 'uppercase',
            letterSpacing: '0.07em',
            color: T.text3,
            marginBottom: 6,
          }}>
            Content
          </div>
          <pre style={{
            fontFamily: FONTS.mono,
            fontSize: 11,
            color: T.text1,
            background: T.bg3,
            border: `2px solid ${T.border}`,
            borderRadius: 8,
            padding: '10px 12px',
            overflowX: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            margin: 0,
            boxShadow: SHADOWS.sm,
          }}>
            {spec.content}
          </pre>
        </section>

        {/* Lifecycle actions */}
        <section aria-label="Lifecycle actions">
          <div style={{
            fontFamily: FONTS.body,
            fontWeight: 800,
            fontSize: 10,
            textTransform: 'uppercase',
            letterSpacing: '0.07em',
            color: T.text3,
            marginBottom: 8,
          }}>
            Actions
          </div>

          {error && (
            <div role="alert" style={{
              background: T.cherrySoft,
              border: `1.5px solid ${T.cherry}`,
              borderRadius: 7,
              padding: '6px 10px',
              fontFamily: FONTS.body,
              fontSize: 11,
              color: T.cherryDark,
              marginBottom: 8,
            }}>
              {error}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {canReview && (
              <ActionButton
                label="Mark Reviewed"
                accent={T.butter}
                disabled={busy}
                onClick={() => doAction('review')}
              />
            )}
            {canApprove && (
              <ActionButton
                label="Approve"
                accent={T.mint}
                disabled={busy}
                onClick={() => doAction('approve')}
              />
            )}
            {canArchive && (
              <ActionButton
                label="Archive"
                accent={T.bg4}
                disabled={busy}
                onClick={() => doAction('archive')}
              />
            )}
            {!canReview && !canApprove && !canArchive && (
              <span style={{ fontFamily: FONTS.body, fontSize: 11, color: T.text4, fontStyle: 'italic' }}>
                No actions available in current state.
              </span>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

interface ActionButtonProps {
  label: string;
  accent: string;
  disabled: boolean;
  onClick: () => void;
}

function ActionButton({ label, accent, disabled, onClick }: ActionButtonProps) {
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
// Column header for list view
// ---------------------------------------------------------------------------

function ListHeader() {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1fr auto auto auto auto',
      alignItems: 'center',
      gap: 10,
      padding: '6px 14px',
      borderBottom: `2px solid ${T.border}`,
      background: T.bg3,
    }}>
      {['Title', 'Type', 'State', 'Author', 'Created'].map(col => (
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
// SpecsPanel — top-level component (list + optional detail split)
// ---------------------------------------------------------------------------

interface SpecsPanelProps {
  onBack: () => void;
}

export function SpecsPanel({ onBack }: SpecsPanelProps) {
  const [specs, setSpecs] = useState<Spec[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Filters
  const [stateFilter, setStateFilter] = useState<SpecState | null>(null);
  const [taskTypeFilter, setTaskTypeFilter] = useState<string | null>(null);

  const loadSpecs = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const params: { state?: string; task_type?: string } = {};
      if (stateFilter)    params.state = stateFilter;
      if (taskTypeFilter) params.task_type = taskTypeFilter;
      const res = await api.listSpecs(params);
      setSpecs(res.specs);
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : 'Failed to load specs');
    } finally {
      setLoading(false);
    }
  }, [stateFilter, taskTypeFilter]);

  useEffect(() => { loadSpecs(); }, [loadSpecs]);

  const selectedSpec = specs.find(s => s.spec_id === selectedId) ?? null;

  // Derived filter options
  const taskTypes = Array.from(new Set(specs.map(s => s.task_type))).sort();

  function handleStateChange(specId: string, newState: SpecState) {
    setSpecs(prev => prev.map(s => s.spec_id === specId ? { ...s, state: newState } : s));
  }

  return (
    <div style={{
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      background: T.bg0,
      overflow: 'hidden',
    }}>
      {/* Panel header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '0 16px',
        borderBottom: `2px solid ${T.border}`,
        background: T.bg3,
        flexShrink: 0,
        height: 44,
      }}>
        <button
          onClick={onBack}
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            padding: '4px 10px',
            border: `1.5px solid ${T.border}`,
            borderRadius: 8,
            background: T.bg1,
            color: T.text1,
            fontFamily: FONTS.body,
            fontSize: 12, fontWeight: 800,
            cursor: 'pointer',
            boxShadow: SHADOWS.sm,
          }}
        >
          ← Rail
        </button>

        <div style={{
          fontFamily: FONTS.display,
          fontWeight: 900,
          fontSize: 15,
          color: T.text0,
          letterSpacing: -0.3,
        }}>
          Specs
        </div>

        <div style={{
          fontFamily: FONTS.hand,
          fontSize: 11,
          color: T.text3,
          transform: 'rotate(-1deg)',
        }}>
          the recipe cards
        </div>

        <div style={{ flex: 1 }} />

        {/* Total count */}
        {!loading && (
          <span style={{
            fontFamily: FONTS.mono,
            fontSize: 10,
            color: T.text4,
          }}>
            {specs.length} spec{specs.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Filter strip */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '7px 16px',
        borderBottom: `1.5px solid ${T.borderSoft}`,
        background: T.bg1,
        flexShrink: 0,
        flexWrap: 'wrap',
      }}>
        <span style={{
          fontFamily: FONTS.body,
          fontSize: 10,
          fontWeight: 800,
          color: T.text3,
          textTransform: 'uppercase',
          letterSpacing: '0.07em',
          marginRight: 4,
        }}>
          State:
        </span>
        <FilterChip
          label="All"
          active={stateFilter === null}
          onClick={() => setStateFilter(null)}
        />
        {ALL_STATES.map(s => (
          <FilterChip
            key={s}
            label={STATE_BADGE[s].label}
            active={stateFilter === s}
            accent={STATE_BADGE[s].bg}
            onClick={() => setStateFilter((prev: SpecState | null) => prev === s ? null : s)}
          />
        ))}

        {taskTypes.length > 0 && (
          <>
            <div style={{ width: 1, height: 16, background: T.borderSoft, margin: '0 4px' }} />
            <span style={{
              fontFamily: FONTS.body,
              fontSize: 10,
              fontWeight: 800,
              color: T.text3,
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
              marginRight: 4,
            }}>
              Type:
            </span>
            <FilterChip
              label="All"
              active={taskTypeFilter === null}
              onClick={() => setTaskTypeFilter(null)}
            />
            {taskTypes.map(t => (
              <FilterChip
                key={t}
                label={t}
                active={taskTypeFilter === t}
                accent={taskTypeAccent(t)}
                onClick={() => setTaskTypeFilter((prev: string | null) => prev === t ? null : t)}
              />
            ))}
          </>
        )}
      </div>

      {/* Content area — list | split */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex' }}>

        {/* List pane */}
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          width: selectedSpec ? '42%' : '100%',
          minWidth: 340,
          borderRight: selectedSpec ? `2px solid ${T.border}` : 'none',
          overflow: 'hidden',
          transition: 'width 160ms ease',
        }}>
          <ListHeader />

          <div style={{ flex: 1, overflow: 'auto' }} role="list" aria-label="Specs list">
            {loading && (
              <div style={{
                padding: 24,
                fontFamily: FONTS.body,
                fontSize: 12,
                color: T.text3,
                textAlign: 'center',
              }}>
                Loading specs...
              </div>
            )}

            {!loading && fetchError && (
              <div role="alert" style={{
                margin: 16,
                background: T.cherrySoft,
                border: `1.5px solid ${T.cherry}`,
                borderRadius: 8,
                padding: '10px 14px',
                fontFamily: FONTS.body,
                fontSize: 12,
                color: T.cherryDark,
              }}>
                {fetchError}
                <button
                  onClick={loadSpecs}
                  style={{
                    marginLeft: 10,
                    background: 'none',
                    border: 'none',
                    color: T.cherry,
                    fontFamily: FONTS.body,
                    fontWeight: 700,
                    fontSize: 12,
                    cursor: 'pointer',
                    textDecoration: 'underline',
                  }}
                >
                  Retry
                </button>
              </div>
            )}

            {!loading && !fetchError && specs.length === 0 && (
              <div style={{
                padding: 24,
                fontFamily: FONTS.hand,
                fontSize: 14,
                color: T.text4,
                textAlign: 'center',
              }}>
                No specs yet — create one with{' '}
                <code style={{ fontFamily: FONTS.mono, fontSize: 12 }}>baton spec create</code>
              </div>
            )}

            {!loading && specs.map(spec => (
              <div key={spec.spec_id} role="listitem">
                <SpecRow
                  spec={spec}
                  selected={spec.spec_id === selectedId}
                  onClick={() => setSelectedId(prev => prev === spec.spec_id ? null : spec.spec_id)}
                />
              </div>
            ))}
          </div>
        </div>

        {/* Detail pane — only when a spec is selected */}
        {selectedSpec && (
          <div style={{ flex: 1, overflow: 'hidden' }}>
            <SpecDetail
              spec={selectedSpec}
              onClose={() => setSelectedId(null)}
              onStateChange={handleStateChange}
            />
          </div>
        )}
      </div>
    </div>
  );
}
