import { useState, useEffect, useCallback } from 'react';
import type { ConsolidationResult, FileAttribution } from '../api/types';
import { api } from '../api/client';
import { T, FONTS, FONT_SIZES, SHADOWS, SR_ONLY } from '../styles/tokens';
import { useBodyScrollLock } from '../hooks/useBodyScrollLock';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ChangelistPanelProps {
  cardId: string;
  onMerged?: () => void;
  onClose?: () => void;
}

// ---------------------------------------------------------------------------
// Internal types
// ---------------------------------------------------------------------------

type LoadState = 'loading' | 'error' | 'ready';

interface PrDialogState {
  open: boolean;
  title: string;
  body: string;
  baseBranch: string;
  submitting: boolean;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function changeTypeLabel(path: string): string {
  // Heuristic: if the path has a known extension pattern indicating deletion,
  // the API would expose it differently. Without a dedicated field, we treat
  // all attributions as modifications. The caller can extend this if a
  // `change_type` field is added to FileAttribution.
  void path;
  return 'M';
}

function changeTypeBg(type: string): string {
  if (type === 'A') return T.mint;
  if (type === 'D') return T.cherry;
  return T.crust;
}

function fmtStat(n: number, sign: '+' | '-'): string {
  if (!n) return '';
  return `${sign}${n}`;
}

function groupByAgent(attributions: FileAttribution[]): Map<string, FileAttribution[]> {
  const map = new Map<string, FileAttribution[]>();
  for (const attr of attributions) {
    const key = attr.agent_name || attr.step_id || 'unknown';
    const bucket = map.get(key) ?? [];
    bucket.push(attr);
    map.set(key, bucket);
  }
  return map;
}

function statusColor(status: ConsolidationResult['status']): string {
  if (status === 'success') return T.mint;
  if (status === 'conflict') return T.cherry;
  return T.butter;
}

function statusLabel(status: ConsolidationResult['status']): string {
  if (status === 'success') return 'Ready to merge';
  if (status === 'conflict') return 'Conflicts detected';
  return 'Partial — review needed';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: ConsolidationResult['status'] }) {
  const color = statusColor(status);
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '2px 10px',
        borderRadius: 999,
        fontSize: FONT_SIZES.sm,
        fontWeight: 700,
        fontFamily: FONTS.body,
        color,
        background: color + '22',
        border: `2px solid ${color}`,
        boxShadow: SHADOWS.sm,
      }}
    >
      {status === 'success' ? '\u2714' : status === 'conflict' ? '\u26a0' : '\u29d6'}
      {' '}{statusLabel(status)}
    </span>
  );
}

function StatPill({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 3,
        padding: '2px 8px',
        borderRadius: 999,
        fontSize: FONT_SIZES.xs,
        fontFamily: FONTS.mono,
        fontWeight: 700,
        color,
        background: T.bg3,
        border: `1.5px solid ${T.border}`,
        boxShadow: SHADOWS.sm,
      }}
    >
      <span style={{ color: T.text2, fontFamily: FONTS.body, fontWeight: 600 }}>{label}</span>
      {value}
    </span>
  );
}

interface FileRowProps {
  attribution: FileAttribution;
  isExpanded: boolean;
  onToggle: () => void;
}

function FileRow({ attribution, isExpanded, onToggle }: FileRowProps) {
  const type = changeTypeLabel(attribution.file_path);
  const typeBg = changeTypeBg(type);
  const hasStats = attribution.insertions > 0 || attribution.deletions > 0;

  return (
    <div>
      <button
        onClick={onToggle}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          width: '100%',
          padding: '5px 8px',
          background: 'none',
          border: 'none',
          borderBottom: `1px solid ${T.borderSoft}`,
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        {/* Change type badge */}
        <span
          aria-label={`Change type: ${type}`}
          style={{
            width: 16,
            height: 16,
            borderRadius: 3,
            background: typeBg,
            color: T.cream,
            fontSize: 9,
            fontWeight: 800,
            fontFamily: FONTS.mono,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          {type}
        </span>

        {/* File path */}
        <span
          style={{
            flex: 1,
            fontFamily: FONTS.mono,
            fontSize: FONT_SIZES.sm,
            color: T.text0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {attribution.file_path}
        </span>

        {/* Diff stats */}
        {hasStats && (
          <span style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
            {attribution.insertions > 0 && (
              <span style={{ fontFamily: FONTS.mono, fontSize: FONT_SIZES.xs, color: T.mint, fontWeight: 700 }}>
                {fmtStat(attribution.insertions, '+')}
              </span>
            )}
            {attribution.deletions > 0 && (
              <span style={{ fontFamily: FONTS.mono, fontSize: FONT_SIZES.xs, color: T.cherry, fontWeight: 700 }}>
                {fmtStat(attribution.deletions, '-')}
              </span>
            )}
          </span>
        )}

        {/* Expand chevron */}
        <span
          aria-hidden="true"
          style={{
            fontSize: 9,
            color: T.text3,
            flexShrink: 0,
            transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: 'transform 0.12s ease',
            display: 'inline-block',
          }}
        >
          {'▾'}
        </span>
      </button>

      {/* Expanded detail */}
      {isExpanded && (
        <div
          style={{
            padding: '6px 12px 8px 30px',
            background: T.bg3,
            borderBottom: `1px solid ${T.borderSoft}`,
            fontSize: FONT_SIZES.xs,
            fontFamily: FONTS.body,
            color: T.text2,
          }}
        >
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <div>
              <span style={{ color: T.text3 }}>Step: </span>
              <span style={{ fontFamily: FONTS.mono, color: T.text1 }}>{attribution.step_id}</span>
            </div>
            <div>
              <span style={{ color: T.text3 }}>Agent: </span>
              <span style={{ fontWeight: 700, color: T.blueberry }}>{attribution.agent_name || '—'}</span>
            </div>
            {hasStats && (
              <div>
                <span style={{ color: T.text3 }}>Diff: </span>
                <span style={{ fontFamily: FONTS.mono }}>
                  <span style={{ color: T.mint }}>{fmtStat(attribution.insertions, '+')}</span>
                  {attribution.insertions > 0 && attribution.deletions > 0 && ' '}
                  <span style={{ color: T.cherry }}>{fmtStat(attribution.deletions, '-')}</span>
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

interface AgentGroupProps {
  agentKey: string;
  files: FileAttribution[];
  expandedFiles: Set<string>;
  onToggleFile: (path: string) => void;
}

function AgentGroup({ agentKey, files, expandedFiles, onToggleFile }: AgentGroupProps) {
  const [collapsed, setCollapsed] = useState(false);
  const totalIns = files.reduce((s, f) => s + f.insertions, 0);
  const totalDel = files.reduce((s, f) => s + f.deletions, 0);

  return (
    <div style={{ borderBottom: `2px solid ${T.border}` }}>
      {/* Group header */}
      <button
        onClick={() => setCollapsed(c => !c)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          width: '100%',
          padding: '6px 10px',
          background: T.bg3,
          border: 'none',
          borderBottom: `1.5px solid ${T.borderSoft}`,
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <span
          aria-hidden="true"
          style={{
            fontSize: 9,
            color: T.text3,
            transform: collapsed ? 'rotate(-90deg)' : 'rotate(0deg)',
            transition: 'transform 0.12s ease',
            display: 'inline-block',
          }}
        >
          {'▾'}
        </span>
        <span style={{
          fontFamily: FONTS.body,
          fontWeight: 800,
          fontSize: FONT_SIZES.sm,
          color: T.blueberry,
          flex: 1,
        }}>
          {agentKey}
        </span>
        <span style={{ fontFamily: FONTS.mono, fontSize: FONT_SIZES.xs, color: T.text2 }}>
          {files.length} file{files.length !== 1 ? 's' : ''}
        </span>
        {(totalIns > 0 || totalDel > 0) && (
          <span style={{ fontFamily: FONTS.mono, fontSize: FONT_SIZES.xs }}>
            {totalIns > 0 && <span style={{ color: T.mint }}>+{totalIns}</span>}
            {totalIns > 0 && totalDel > 0 && ' '}
            {totalDel > 0 && <span style={{ color: T.cherry }}>-{totalDel}</span>}
          </span>
        )}
      </button>

      {/* File rows */}
      {!collapsed && (
        <div>
          {files.map(attr => (
            <FileRow
              key={attr.file_path}
              attribution={attr}
              isExpanded={expandedFiles.has(attr.file_path)}
              onToggle={() => onToggleFile(attr.file_path)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PR Dialog
// ---------------------------------------------------------------------------

interface PrDialogProps {
  state: PrDialogState;
  onChange: (patch: Partial<PrDialogState>) => void;
  onSubmit: () => void;
  onCancel: () => void;
}

function PrDialog({ state, onChange, onSubmit, onCancel }: PrDialogProps) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(42,26,16,.55)',
      }}
      onClick={onCancel}
    >
      <div
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="pr-dialog-title"
        style={{
          width: 460,
          background: T.bg1,
          border: `3px solid ${T.border}`,
          borderRadius: 16,
          boxShadow: SHADOWS.xl,
          overflow: 'hidden',
        }}
      >
        {/* Dialog header */}
        <div style={{
          padding: '10px 16px',
          background: T.ink,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <span id="pr-dialog-title" style={{
            fontFamily: FONTS.display,
            fontWeight: 900,
            fontSize: 16,
            color: T.cream,
          }}>
            Create Pull Request
          </span>
          <button
            onClick={onCancel}
            aria-label="Cancel PR creation"
            style={{
              background: 'none',
              border: `1.5px solid ${T.cherry}`,
              color: T.cherry,
              fontSize: 14,
              cursor: 'pointer',
              padding: '1px 7px',
              borderRadius: 6,
              fontFamily: FONTS.body,
              fontWeight: 700,
            }}
          >
            {'\u00d7'}
          </button>
        </div>

        {/* Dialog body */}
        <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <span style={{ fontFamily: FONTS.body, fontWeight: 700, fontSize: FONT_SIZES.sm, color: T.text1 }}>
              Title <span style={{ color: T.cherry }}>*</span>
            </span>
            <input
              type="text"
              value={state.title}
              onChange={e => onChange({ title: e.target.value })}
              placeholder="e.g. feat: implement changelist review panel"
              style={{
                padding: '6px 10px',
                borderRadius: 8,
                border: `2px solid ${T.border}`,
                background: T.bg3,
                fontFamily: FONTS.body,
                fontSize: FONT_SIZES.sm,
                color: T.text0,
                outline: 'none',
              }}
            />
          </label>

          <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <span style={{ fontFamily: FONTS.body, fontWeight: 700, fontSize: FONT_SIZES.sm, color: T.text1 }}>
              Description
            </span>
            <textarea
              value={state.body}
              onChange={e => onChange({ body: e.target.value })}
              rows={4}
              placeholder="Optional — summarise the changes for reviewers."
              style={{
                padding: '6px 10px',
                borderRadius: 8,
                border: `2px solid ${T.border}`,
                background: T.bg3,
                fontFamily: FONTS.body,
                fontSize: FONT_SIZES.sm,
                color: T.text0,
                resize: 'vertical',
                outline: 'none',
              }}
            />
          </label>

          <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <span style={{ fontFamily: FONTS.body, fontWeight: 700, fontSize: FONT_SIZES.sm, color: T.text1 }}>
              Base branch
            </span>
            <input
              type="text"
              value={state.baseBranch}
              onChange={e => onChange({ baseBranch: e.target.value })}
              placeholder="main"
              style={{
                padding: '6px 10px',
                borderRadius: 8,
                border: `2px solid ${T.border}`,
                background: T.bg3,
                fontFamily: FONTS.mono,
                fontSize: FONT_SIZES.sm,
                color: T.text0,
                outline: 'none',
              }}
            />
          </label>

          {state.error && (
            <div style={{
              padding: '6px 10px',
              background: T.cherrySoft,
              border: `1.5px solid ${T.cherry}`,
              borderRadius: 8,
              fontFamily: FONTS.body,
              fontSize: FONT_SIZES.sm,
              color: T.cherryDark,
            }}>
              {state.error}
            </div>
          )}
        </div>

        {/* Dialog footer */}
        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 8,
          padding: '10px 16px',
          background: T.bg3,
          borderTop: `2px solid ${T.border}`,
        }}>
          <button
            onClick={onCancel}
            style={{
              padding: '5px 16px',
              borderRadius: 10,
              border: `2px solid ${T.border}`,
              background: T.bg1,
              color: T.text1,
              fontFamily: FONTS.body,
              fontWeight: 700,
              fontSize: FONT_SIZES.sm,
              cursor: 'pointer',
              boxShadow: SHADOWS.sm,
            }}
          >
            Cancel
          </button>
          <button
            onClick={onSubmit}
            disabled={!state.title.trim() || state.submitting}
            style={{
              padding: '5px 16px',
              borderRadius: 10,
              border: `2px solid ${T.blueberry}`,
              background: T.blueberry,
              color: T.cream,
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: FONT_SIZES.sm,
              cursor: !state.title.trim() || state.submitting ? 'not-allowed' : 'pointer',
              opacity: !state.title.trim() || state.submitting ? 0.6 : 1,
              boxShadow: SHADOWS.sm,
            }}
          >
            {state.submitting ? 'Creating…' : 'Create PR'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ChangelistPanel({ cardId, onMerged, onClose }: ChangelistPanelProps) {
  useBodyScrollLock();

  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [changelist, setChangelist] = useState<ConsolidationResult | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const [filter, setFilter] = useState('');
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());

  const [merging, setMerging] = useState(false);
  const [mergeError, setMergeError] = useState<string | null>(null);
  const [mergeSuccess, setMergeSuccess] = useState<string | null>(null);

  const [prDialog, setPrDialog] = useState<PrDialogState>({
    open: false,
    title: '',
    body: '',
    baseBranch: 'main',
    submitting: false,
    error: null,
  });
  const [prSuccess, setPrSuccess] = useState<{ url: string; number: number } | null>(null);

  // Fetch changelist on mount
  const load = useCallback(async () => {
    setLoadState('loading');
    setFetchError(null);
    try {
      const result = await api.getChangelist(cardId);
      setChangelist(result);
      setLoadState('ready');
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : 'Failed to load changelist');
      setLoadState('error');
    }
  }, [cardId]);

  useEffect(() => { load(); }, [load]);

  // Derived: filtered attributions
  const allAttributions: FileAttribution[] = changelist?.attributions ?? [];
  const filteredAttributions = filter.trim()
    ? allAttributions.filter(a => a.file_path.toLowerCase().includes(filter.trim().toLowerCase()))
    : allAttributions;

  // Group by agent/step
  const grouped = groupByAgent(filteredAttributions);

  function toggleFile(path: string) {
    setExpandedFiles(prev => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  async function handleMerge() {
    setMerging(true);
    setMergeError(null);
    try {
      const result = await api.mergeCard(cardId);
      setMergeSuccess(`Merged — commit ${result.merge_commit.slice(0, 8)}`);
      onMerged?.();
    } catch (err) {
      setMergeError(err instanceof Error ? err.message : 'Merge failed');
    } finally {
      setMerging(false);
    }
  }

  async function handlePrSubmit() {
    setPrDialog(s => ({ ...s, submitting: true, error: null }));
    try {
      const result = await api.createPr(cardId, {
        title: prDialog.title.trim(),
        body: prDialog.body.trim() || undefined,
        base_branch: prDialog.baseBranch.trim() || undefined,
      });
      setPrSuccess({ url: result.pr_url, number: result.pr_number });
      setPrDialog(s => ({ ...s, open: false, submitting: false }));
    } catch (err) {
      setPrDialog(s => ({
        ...s,
        submitting: false,
        error: err instanceof Error ? err.message : 'PR creation failed',
      }));
    }
  }

  const canMerge = changelist?.status === 'success' && !mergeSuccess;

  return (
    <>
      {/* Backdrop */}
      <div
        style={{
          position: 'fixed',
          inset: 0,
          zIndex: 1000,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgba(42,26,16,.6)',
        }}
        onClick={onClose}
      >
        {/* Panel */}
        <div
          onClick={e => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="changelist-panel-title"
          style={{
            width: 640,
            maxHeight: '84vh',
            display: 'flex',
            flexDirection: 'column',
            background: T.bg1,
            border: `3px solid ${T.border}`,
            borderRadius: 18,
            boxShadow: SHADOWS.xl,
            overflow: 'hidden',
          }}
        >
          {/* ----------------------------------------------------------------
              Header
          ---------------------------------------------------------------- */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '12px 16px',
            background: T.ink,
            flexShrink: 0,
          }}>
            {/* Kitchen icon — the "plating inspection" framing */}
            <span aria-hidden="true" style={{ fontSize: 18 }}>{'[*]'}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                id="changelist-panel-title"
                style={{
                  fontFamily: FONTS.display,
                  fontWeight: 900,
                  fontSize: 18,
                  color: T.cream,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                Plating Inspection
              </div>
              <div style={{ fontFamily: FONTS.mono, fontSize: FONT_SIZES.xs, color: T.crust }}>
                {cardId}
              </div>
            </div>
            <button
              onClick={onClose}
              aria-label="Close changelist panel"
              style={{
                background: 'none',
                border: `1.5px solid ${T.cherry}`,
                color: T.cherry,
                fontSize: 14,
                cursor: 'pointer',
                padding: '2px 8px',
                borderRadius: 6,
                fontFamily: FONTS.body,
                fontWeight: 700,
                lineHeight: 1.4,
              }}
            >
              {'\u00d7'}
            </button>
          </div>

          {/* ----------------------------------------------------------------
              Summary bar (only when loaded)
          ---------------------------------------------------------------- */}
          {loadState === 'ready' && changelist && (
            <div style={{
              padding: '10px 16px',
              background: T.bg3,
              borderBottom: `2px solid ${T.border}`,
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexWrap: 'wrap',
            }}>
              <StatusBadge status={changelist.status} />
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <StatPill
                  label="files"
                  value={String(changelist.files_changed.length)}
                  color={T.text0}
                />
                {changelist.total_insertions > 0 && (
                  <StatPill
                    label="ins"
                    value={`+${changelist.total_insertions}`}
                    color={T.mint}
                  />
                )}
                {changelist.total_deletions > 0 && (
                  <StatPill
                    label="del"
                    value={`-${changelist.total_deletions}`}
                    color={T.cherry}
                  />
                )}
                <StatPill
                  label="commits"
                  value={String(changelist.rebased_commits.length)}
                  color={T.blueberry}
                />
              </div>
              {changelist.conflict_files.length > 0 && (
                <span style={{
                  fontFamily: FONTS.body,
                  fontSize: FONT_SIZES.xs,
                  color: T.cherry,
                  fontWeight: 700,
                }}>
                  {changelist.conflict_files.length} conflict{changelist.conflict_files.length !== 1 ? 's' : ''}
                </span>
              )}
            </div>
          )}

          {/* ----------------------------------------------------------------
              Search bar (only when loaded with files)
          ---------------------------------------------------------------- */}
          {loadState === 'ready' && allAttributions.length > 0 && (
            <div style={{
              padding: '8px 12px',
              background: T.bg1,
              borderBottom: `1.5px solid ${T.borderSoft}`,
              flexShrink: 0,
            }}>
              <label style={SR_ONLY} htmlFor="changelist-filter">
                Filter files by path
              </label>
              <input
                id="changelist-filter"
                type="search"
                value={filter}
                onChange={e => setFilter(e.target.value)}
                placeholder="Filter files..."
                style={{
                  width: '100%',
                  padding: '5px 10px',
                  borderRadius: 8,
                  border: `2px solid ${T.border}`,
                  background: T.bg3,
                  fontFamily: FONTS.mono,
                  fontSize: FONT_SIZES.sm,
                  color: T.text0,
                  outline: 'none',
                  boxSizing: 'border-box',
                }}
              />
            </div>
          )}

          {/* ----------------------------------------------------------------
              File list (scrollable body)
          ---------------------------------------------------------------- */}
          <div
            role="region"
            aria-label="Changed files"
            style={{
              flex: 1,
              overflowY: 'auto',
              background: T.bg1,
              minHeight: 120,
            }}
          >
            <span style={SR_ONLY} aria-live="polite" aria-atomic="true">
              {loadState === 'loading' && 'Loading changelist...'}
              {loadState === 'error' && `Error: ${fetchError}`}
              {loadState === 'ready' && `${filteredAttributions.length} files`}
            </span>

            {/* Loading */}
            {loadState === 'loading' && (
              <div style={{
                padding: 24,
                textAlign: 'center',
                fontFamily: FONTS.hand,
                fontSize: 18,
                color: T.text2,
              }}>
                "Checking the plate..."
              </div>
            )}

            {/* Error */}
            {loadState === 'error' && (
              <div style={{
                padding: '16px',
                display: 'flex',
                flexDirection: 'column',
                gap: 10,
                alignItems: 'center',
              }}>
                <div style={{
                  padding: '8px 14px',
                  background: T.cherrySoft,
                  border: `2px solid ${T.cherry}`,
                  borderRadius: 10,
                  fontFamily: FONTS.body,
                  fontSize: FONT_SIZES.sm,
                  color: T.cherryDark,
                  maxWidth: '100%',
                  wordBreak: 'break-word',
                }}>
                  {fetchError}
                </div>
                <button
                  onClick={load}
                  style={{
                    padding: '5px 16px',
                    borderRadius: 10,
                    border: `2px solid ${T.border}`,
                    background: T.bg3,
                    color: T.text0,
                    fontFamily: FONTS.body,
                    fontWeight: 700,
                    fontSize: FONT_SIZES.sm,
                    cursor: 'pointer',
                    boxShadow: SHADOWS.sm,
                  }}
                >
                  Retry
                </button>
              </div>
            )}

            {/* Empty state */}
            {loadState === 'ready' && filteredAttributions.length === 0 && (
              <div style={{
                padding: 24,
                textAlign: 'center',
                fontFamily: FONTS.hand,
                fontSize: 18,
                color: T.text2,
              }}>
                {filter.trim()
                  ? 'No files match the filter.'
                  : 'No file attributions recorded.'}
              </div>
            )}

            {/* Grouped file list */}
            {loadState === 'ready' && grouped.size > 0 && (
              <div>
                {Array.from(grouped.entries()).map(([agentKey, files]) => (
                  <AgentGroup
                    key={agentKey}
                    agentKey={agentKey}
                    files={files}
                    expandedFiles={expandedFiles}
                    onToggleFile={toggleFile}
                  />
                ))}
              </div>
            )}

            {/* Conflict notice */}
            {loadState === 'ready' && changelist && changelist.conflict_files.length > 0 && (
              <div style={{
                margin: '10px 12px',
                padding: '8px 12px',
                background: T.cherrySoft,
                border: `2px solid ${T.cherry}`,
                borderRadius: 10,
                fontFamily: FONTS.body,
                fontSize: FONT_SIZES.sm,
                color: T.cherryDark,
              }}>
                <div style={{ fontWeight: 800, marginBottom: 4 }}>
                  Conflicts in step: {changelist.conflict_step_id || '—'}
                </div>
                {changelist.conflict_files.map(f => (
                  <div key={f} style={{ fontFamily: FONTS.mono, fontSize: FONT_SIZES.xs, color: T.cherryDark }}>
                    {f}
                  </div>
                ))}
                {changelist.error && (
                  <div style={{ marginTop: 6, fontSize: FONT_SIZES.xs, fontStyle: 'italic' }}>
                    {changelist.error}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ----------------------------------------------------------------
              Footer — feedback + actions
          ---------------------------------------------------------------- */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 14px',
            background: T.bg3,
            borderTop: `2px solid ${T.border}`,
            flexShrink: 0,
            flexWrap: 'wrap',
          }}>
            {/* Inline feedback */}
            <div style={{ flex: 1, minWidth: 120 }}>
              {mergeError && (
                <div style={{
                  fontFamily: FONTS.body,
                  fontSize: FONT_SIZES.xs,
                  color: T.cherry,
                  fontWeight: 700,
                }}>
                  {mergeError}
                </div>
              )}
              {mergeSuccess && (
                <div style={{
                  fontFamily: FONTS.body,
                  fontSize: FONT_SIZES.xs,
                  color: T.mint,
                  fontWeight: 700,
                }}>
                  {mergeSuccess}
                </div>
              )}
              {prSuccess && (
                <div style={{
                  fontFamily: FONTS.body,
                  fontSize: FONT_SIZES.xs,
                  color: T.blueberry,
                  fontWeight: 700,
                }}>
                  PR #{prSuccess.number} created —{' '}
                  <a
                    href={prSuccess.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: T.blueberry }}
                  >
                    {prSuccess.url}
                  </a>
                </div>
              )}
            </div>

            {/* Create PR (secondary) */}
            <button
              onClick={() => setPrDialog(s => ({ ...s, open: true }))}
              disabled={loadState !== 'ready'}
              style={{
                padding: '5px 14px',
                borderRadius: 10,
                border: `2px solid ${T.blueberry}`,
                background: T.blueberry + '22',
                color: T.blueberry,
                fontFamily: FONTS.body,
                fontWeight: 800,
                fontSize: FONT_SIZES.sm,
                cursor: loadState !== 'ready' ? 'not-allowed' : 'pointer',
                opacity: loadState !== 'ready' ? 0.5 : 1,
                boxShadow: SHADOWS.sm,
              }}
            >
              Create PR
            </button>

            {/* Merge & Submit (primary) */}
            <button
              onClick={handleMerge}
              disabled={!canMerge || merging}
              title={!canMerge && !mergeSuccess ? 'Only available when status is success' : undefined}
              style={{
                padding: '5px 16px',
                borderRadius: 10,
                border: `2px solid ${canMerge && !merging ? T.mint : T.borderSoft}`,
                background: canMerge && !merging ? T.mint : T.bg4,
                color: canMerge && !merging ? T.ink : T.text3,
                fontFamily: FONTS.body,
                fontWeight: 800,
                fontSize: FONT_SIZES.sm,
                cursor: !canMerge || merging ? 'not-allowed' : 'pointer',
                opacity: !canMerge || merging ? 0.6 : 1,
                boxShadow: canMerge && !merging ? SHADOWS.md : SHADOWS.sm,
                transition: 'background 0.1s ease, border-color 0.1s ease',
              }}
            >
              {merging ? 'Merging…' : 'Merge & Submit'}
            </button>

            {/* Close */}
            <button
              onClick={onClose}
              style={{
                padding: '5px 14px',
                borderRadius: 10,
                border: `2px solid ${T.border}`,
                background: T.bg1,
                color: T.text1,
                fontFamily: FONTS.body,
                fontWeight: 700,
                fontSize: FONT_SIZES.sm,
                cursor: 'pointer',
                boxShadow: SHADOWS.sm,
              }}
            >
              Close
            </button>
          </div>
        </div>
      </div>

      {/* PR creation dialog — rendered above the main panel */}
      {prDialog.open && (
        <PrDialog
          state={prDialog}
          onChange={patch => setPrDialog(s => ({ ...s, ...patch }))}
          onSubmit={handlePrSubmit}
          onCancel={() => setPrDialog(s => ({ ...s, open: false, error: null }))}
        />
      )}
    </>
  );
}
