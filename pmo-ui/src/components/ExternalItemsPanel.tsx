/**
 * ExternalItemsPanel — shows work items synced from external sources
 * (ADO, GitHub, Jira, Linear) that are stored in central.db.
 *
 * Rendered as a modal overlay, following the same pattern as
 * AnalyticsDashboard.  Opens from the KanbanBoard toolbar.
 */
import { useState, useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import type { ExternalItem, ExternalMapping } from '../api/types';

// ---------------------------------------------------------------------------
// Source type metadata
// ---------------------------------------------------------------------------

const SOURCE_META: Record<string, { label: string; color: string }> = {
  ado:    { label: 'ADO',    color: T.accent },
  github: { label: 'GitHub', color: T.purple },
  jira:   { label: 'Jira',   color: T.cherry },
  linear: { label: 'Linear', color: T.green },
};

function sourceColor(sourceType: string): string {
  return SOURCE_META[sourceType]?.color ?? T.text2;
}

function sourceLabel(sourceType: string): string {
  return SOURCE_META[sourceType]?.label ?? sourceType.toUpperCase();
}

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function Badge({ children, color }: { children: React.ReactNode; color: string }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      padding: '2px 8px',
      borderRadius: 999,
      fontSize: 9,
      fontWeight: 800,
      fontFamily: FONTS.body,
      color,
      background: color + '18',
      border: `1.5px solid ${color}`,
      whiteSpace: 'nowrap',
      flexShrink: 0,
    }}>
      {children}
    </span>
  );
}

function StateChip({ state }: { state: string }) {
  const lower = state.toLowerCase();
  const color =
    lower.includes('done') || lower.includes('closed') || lower.includes('complete')
      ? T.green
      : lower.includes('progress') || lower.includes('active') || lower.includes('open')
      ? T.yellow
      : T.text2;
  return <Badge color={color}>{state || '—'}</Badge>;
}

function TypeChip({ type }: { type: string }) {
  const lower = type.toLowerCase();
  const color =
    lower === 'bug'   ? T.red :
    lower === 'epic'  ? T.purple :
    lower === 'story' ? T.blueberry :
    T.text2;
  return <Badge color={color}>{type || 'item'}</Badge>;
}

// ---------------------------------------------------------------------------
// Mapping detail row
// ---------------------------------------------------------------------------

function MappingRow({ mapping }: { mapping: ExternalMapping }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '3px 8px',
      background: T.bg3,
      border: `1.5px dashed ${T.borderSoft}`,
      borderRadius: 8,
      marginTop: 4,
    }}>
      <span style={{ fontSize: 9, color: T.text3, fontFamily: FONTS.mono }}>Mapped to plan:</span>
      <span style={{ fontSize: 9, color: T.text1, fontFamily: FONTS.mono, fontWeight: 700 }}>
        {mapping.task_id || '—'}
      </span>
      {mapping.mapping_type && (
        <Badge color={T.text3}>{mapping.mapping_type}</Badge>
      )}
      <span style={{ fontSize: 9, color: T.text4, fontFamily: FONTS.mono }}>{mapping.project_id}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single external item card
// ---------------------------------------------------------------------------

function ItemCard({ item }: { item: ExternalItem }) {
  const [expanded, setExpanded] = useState(false);
  const [mappings, setMappings] = useState<ExternalMapping[] | null>(null);
  const [loadingMappings, setLoadingMappings] = useState(false);
  const color = sourceColor(item.source_type);

  async function handleExpand() {
    const next = !expanded;
    setExpanded(next);
    if (next && mappings === null) {
      setLoadingMappings(true);
      try {
        const result = await api.getExternalItemMappings(item.id);
        setMappings(result);
      } catch {
        setMappings([]);
      } finally {
        setLoadingMappings(false);
      }
    }
  }

  return (
    <div
      style={{
        background: T.bg1,
        border: `2px solid ${expanded ? color : T.border}`,
        borderRadius: 12,
        overflow: 'hidden',
        boxShadow: SHADOWS.sm,
        transition: 'border-color 0.15s',
      }}
    >
      {/* Header row */}
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={handleExpand}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleExpand();
          }
        }}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '10px 12px',
          cursor: 'pointer',
        }}
      >
        {/* Source badge */}
        <Badge color={color}>{sourceLabel(item.source_type)}</Badge>

        {/* External ID */}
        <span style={{
          fontSize: 9,
          color,
          fontFamily: FONTS.mono,
          fontWeight: 700,
        }}>
          {item.external_id}
        </span>

        {/* Type + state chips */}
        <TypeChip type={item.item_type} />
        <StateChip state={item.state} />

        {/* Title — takes remaining space */}
        <span style={{
          flex: 1,
          fontFamily: FONTS.body,
          fontSize: 13,
          fontWeight: 700,
          color: T.text0,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>
          {item.title || '(no title)'}
        </span>

        {/* Assignee */}
        {item.assigned_to && (
          <span style={{
            fontSize: 9,
            color: T.text3,
            flexShrink: 0,
            fontFamily: FONTS.body,
          }}>
            {item.assigned_to}
          </span>
        )}

        {/* Expand chevron */}
        <span
          aria-hidden="true"
          style={{
            fontSize: 10,
            color: T.text2,
            transition: 'transform 0.15s',
            transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
            display: 'inline-block',
            flexShrink: 0,
          }}
        >
          {'▾'}
        </span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={{
          borderTop: `1.5px dashed ${T.borderSoft}`,
          padding: '10px 12px',
          background: T.bg3,
        }}>
          {/* Description */}
          {item.description && (
            <p style={{
              fontFamily: FONTS.body,
              fontSize: 11,
              color: T.text1,
              lineHeight: 1.5,
              margin: '0 0 6px',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}>
              {item.description.length > 400
                ? item.description.slice(0, 400) + '…'
                : item.description}
            </p>
          )}

          {/* Tags */}
          {item.tags.length > 0 && (
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
              {item.tags.map(tag => (
                <span
                  key={tag}
                  style={{
                    padding: '2px 8px',
                    borderRadius: 999,
                    background: T.bg1,
                    border: `1.5px solid ${T.border}`,
                    fontFamily: FONTS.body,
                    fontWeight: 700,
                    fontSize: 9,
                    color: T.text1,
                  }}
                >
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* External link */}
          {item.url && (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              style={{
                fontSize: 11,
                color: color,
                textDecoration: 'none',
                display: 'inline-block',
                marginBottom: 6,
                fontFamily: FONTS.body,
                fontWeight: 700,
              }}
            >
              Open in {sourceLabel(item.source_type)} &rarr;
            </a>
          )}

          {/* Mappings */}
          <div>
            <span style={{
              fontSize: 9,
              color: T.text2,
              fontWeight: 800,
              fontFamily: FONTS.body,
              textTransform: 'uppercase',
              letterSpacing: '.06em',
            }}>
              Plan mappings
            </span>
            {loadingMappings && (
              <div style={{
                fontSize: 9,
                color: T.text4,
                fontStyle: 'italic',
                marginTop: 4,
                fontFamily: FONTS.mono,
              }}>
                Loading…
              </div>
            )}
            {!loadingMappings && mappings !== null && mappings.length === 0 && (
              <div style={{
                fontSize: 9,
                color: T.text4,
                fontStyle: 'italic',
                marginTop: 4,
                fontFamily: FONTS.mono,
              }}>
                No plan mappings yet.
              </div>
            )}
            {!loadingMappings && mappings && mappings.map((m) => (
              <MappingRow key={m.id} mapping={m} />
            ))}
          </div>

          {/* Updated at */}
          <div style={{
            fontSize: 9,
            color: T.text3,
            marginTop: 6,
            fontFamily: FONTS.mono,
          }}>
            Last updated: {item.updated_at || '—'}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter bar
// ---------------------------------------------------------------------------

const SOURCE_FILTERS = [
  { value: '', label: 'All sources' },
  { value: 'ado',    label: 'ADO' },
  { value: 'github', label: 'GitHub' },
  { value: 'jira',   label: 'Jira' },
  { value: 'linear', label: 'Linear' },
];

function FilterBar({
  source, onSource,
  search, onSearch,
}: {
  source: string;
  onSource: (v: string) => void;
  search: string;
  onSearch: (v: string) => void;
}) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      padding: '8px 12px',
      borderBottom: `2px solid ${T.border}`,
      background: T.bg3,
      flexShrink: 0,
    }}>
      {/* Source type pills */}
      <div style={{ display: 'flex', gap: 4 }}>
        {SOURCE_FILTERS.map(f => (
          <button
            key={f.value}
            onClick={() => onSource(f.value)}
            style={{
              padding: '3px 10px',
              borderRadius: 999,
              border: `1.5px solid ${T.border}`,
              background: source === f.value ? T.butter : T.bg1,
              color: T.text0,
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 11,
              cursor: 'pointer',
              boxShadow: source === f.value ? SHADOWS.sm : 'none',
              transform: source === f.value ? 'translate(-1px, -1px)' : 'none',
              transition: 'all 0.1s',
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div style={{ flex: 1 }} />

      {/* Search box */}
      <input
        type="search"
        placeholder="Filter by title or ID…"
        value={search}
        onChange={(e) => onSearch(e.target.value)}
        style={{
          padding: '4px 10px',
          borderRadius: 8,
          border: `2px solid ${T.border}`,
          background: T.bg1,
          color: T.text0,
          fontFamily: FONTS.body,
          fontSize: 11,
          width: 200,
          outline: 'none',
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

interface ExternalItemsPanelProps {
  onClose: () => void;
}

export function ExternalItemsPanel({ onClose }: ExternalItemsPanelProps) {
  const [sourceFilter, setSourceFilter] = useState('');
  const [search, setSearch] = useState('');
  const [items, setItems] = useState<ExternalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (src: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.getExternalItems(src || undefined);
      setItems(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load external items.');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(sourceFilter);
  }, [sourceFilter, load]);

  // Client-side title/ID search filter.
  const filtered = search.trim()
    ? items.filter(item => {
        const q = search.toLowerCase();
        return (
          item.title.toLowerCase().includes(q) ||
          item.external_id.toLowerCase().includes(q)
        );
      })
    : items;

  return (
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
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 780,
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          background: T.bg1,
          border: `3px solid ${T.border}`,
          borderRadius: 18,
          boxShadow: SHADOWS.xl,
          overflow: 'hidden',
        }}
      >
        {/* Title bar */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '14px 18px',
          borderBottom: `3px solid ${T.border}`,
          background: T.mint,
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 28, lineHeight: 1, marginRight: 4 }}>🚚</span>
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <span style={{
                fontFamily: FONTS.display,
                fontWeight: 900,
                fontSize: 22,
                color: T.ink,
                lineHeight: 1.1,
              }}>
                Deliveries
              </span>
              <span style={{
                fontFamily: FONTS.hand,
                fontSize: 16,
                color: T.inkSoft,
                transform: 'rotate(-1deg)',
                display: 'inline-block',
              }}>
                fresh deliveries out back
              </span>
            </div>
          </div>

          {/* Item count chip */}
          <span style={{
            padding: '2px 10px',
            borderRadius: 999,
            background: T.bg0,
            border: `1.5px solid ${T.border}`,
            fontFamily: FONTS.mono,
            fontWeight: 700,
            fontSize: 11,
            color: T.text0,
            flexShrink: 0,
          }}>
            {filtered.length} item{filtered.length !== 1 ? 's' : ''}
          </span>

          {/* Close button */}
          <button
            aria-label="Close external items panel"
            onClick={onClose}
            style={{
              background: 'none',
              border: `1.5px solid ${T.border}`,
              borderRadius: 6,
              color: T.ink,
              fontSize: 14,
              cursor: 'pointer',
              lineHeight: 1,
              padding: '2px 8px',
            }}
          >
            {'\u00d7'}
          </button>
        </div>

        {/* Filter bar */}
        <FilterBar
          source={sourceFilter}
          onSource={(v) => { setSourceFilter(v); }}
          search={search}
          onSearch={setSearch}
        />

        {/* Content */}
        <div style={{ flex: 1, overflowY: 'auto', padding: 10 }}>
          {loading && (
            <div style={{
              fontFamily: FONTS.hand,
              fontSize: 16,
              color: T.text2,
              padding: 12,
            }}>
              heatin' up the pantry…
            </div>
          )}

          {!loading && error && (
            <div style={{
              fontFamily: FONTS.body,
              fontWeight: 700,
              fontSize: 11,
              color: T.cherry,
              padding: '8px 10px',
              background: T.cherrySoft,
              borderRadius: 8,
              border: `1.5px solid ${T.cherry}`,
            }}>
              {error}
            </div>
          )}

          {!loading && !error && filtered.length === 0 && (
            <div style={{
              fontFamily: FONTS.hand,
              fontSize: 16,
              color: T.text2,
              padding: 12,
            }}>
              {items.length === 0
                ? "nothin' out back yet 📦"
                : 'no crates match that filter'}
            </div>
          )}

          {!loading && !error && filtered.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {filtered.map((item) => (
                <ItemCard key={item.id} item={item} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
