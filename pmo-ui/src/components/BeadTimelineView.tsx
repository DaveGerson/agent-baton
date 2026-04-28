import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { T, FONTS, SHADOWS, FONT_SIZES } from '../styles/tokens';
import type { BeadNode } from '../api/types';

/**
 * BeadTimelineView — a vertical, time-ordered list of beads.
 *
 * Sorted newest-first by `created_at`. Includes a tag-filter input
 * and an empty state. Each entry is a clickable button that fires the
 * `onEntryClick` callback (when supplied).
 */
export interface BeadTimelineViewProps {
  beads: BeadNode[];
  onEntryClick?: (bead: BeadNode) => void;
  initialTagFilter?: string;
}

const TYPE_DOT_COLOR: Record<string, string> = {
  warning: T.cherry,
  incident: T.cherry,
  bug: T.cherry,
  decision: T.blueberry,
  architecture: T.blueberry,
  knowledge: T.mint,
  pattern: T.mint,
  review: T.tangerine,
};

function dotColor(type: string): string {
  return TYPE_DOT_COLOR[type] ?? T.crust;
}

export function BeadTimelineView(props: BeadTimelineViewProps) {
  const { beads, onEntryClick, initialTagFilter = '' } = props;
  const [tagFilter, setTagFilter] = useState(initialTagFilter);

  const filteredAndSorted = useMemo(() => {
    const needle = tagFilter.trim().toLowerCase();
    const filtered = needle
      ? beads.filter((b) => b.tags.some((t) => t.toLowerCase().includes(needle)))
      : beads;
    return [...filtered].sort((a, b) =>
      (b.created_at || '').localeCompare(a.created_at || '')
    );
  }, [beads, tagFilter]);

  const containerStyle: CSSProperties = {
    background: T.bg1,
    border: `2px solid ${T.border}`,
    borderRadius: 8,
    padding: 16,
    fontFamily: FONTS.body,
    color: T.text0,
    boxShadow: SHADOWS.sm,
  };

  const filterStyle: CSSProperties = {
    width: '100%',
    padding: '6px 10px',
    fontSize: FONT_SIZES.md,
    fontFamily: FONTS.body,
    border: `2px solid ${T.border}`,
    borderRadius: 4,
    background: T.cream,
    marginBottom: 12,
    boxSizing: 'border-box',
  };

  if (beads.length === 0) {
    return (
      <div style={containerStyle} data-testid="bead-timeline-empty">
        <div style={{ textAlign: 'center', color: T.text2, padding: '32px 16px' }}>
          No timeline entries yet. Beads emitted by agents will appear here in
          chronological order.
        </div>
      </div>
    );
  }

  return (
    <div style={containerStyle} data-testid="bead-timeline">
      <input
        type="search"
        aria-label="Filter timeline by tag"
        placeholder="Filter by tag..."
        value={tagFilter}
        onChange={(e) => setTagFilter(e.target.value)}
        style={filterStyle}
        data-testid="bead-timeline-filter"
      />
      <ol
        data-testid="bead-timeline-entries"
        style={{
          listStyle: 'none',
          margin: 0,
          padding: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        {filteredAndSorted.map((bead) => (
          <li key={bead.bead_id} style={{ margin: 0 }}>
            <button
              type="button"
              data-testid="bead-timeline-entry"
              onClick={() => onEntryClick?.(bead)}
              aria-label={`Bead ${bead.bead_id} (${bead.bead_type})`}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 10,
                width: '100%',
                padding: '8px 10px',
                background: T.cream,
                border: `2px solid ${T.border}`,
                borderRadius: 4,
                cursor: onEntryClick ? 'pointer' : 'default',
                fontFamily: FONTS.body,
                textAlign: 'left',
                color: T.text0,
              }}
            >
              <div
                aria-hidden
                style={{
                  width: 12,
                  height: 12,
                  borderRadius: '50%',
                  background: dotColor(bead.bead_type),
                  border: `2px solid ${T.border}`,
                  marginTop: 4,
                  flexShrink: 0,
                }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: FONT_SIZES.md,
                    fontWeight: 700,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {bead.bead_id} · {bead.bead_type}
                </div>
                <div
                  style={{
                    fontSize: FONT_SIZES.sm,
                    color: T.text1,
                    marginTop: 2,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  {bead.content}
                </div>
                <div
                  style={{
                    fontSize: FONT_SIZES.xs,
                    color: T.text3,
                    marginTop: 2,
                  }}
                >
                  {bead.created_at}
                  {bead.tags.length > 0 && ` · tags: ${bead.tags.join(', ')}`}
                </div>
              </div>
            </button>
          </li>
        ))}
        {filteredAndSorted.length === 0 && (
          <li
            data-testid="bead-timeline-no-matches"
            style={{ color: T.text2, fontSize: FONT_SIZES.sm }}
          >
            No timeline entries match this filter.
          </li>
        )}
      </ol>
    </div>
  );
}

export default BeadTimelineView;
