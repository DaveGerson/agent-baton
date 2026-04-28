import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import { T, FONTS, SHADOWS, FONT_SIZES } from '../styles/tokens';
import type { BeadNode } from '../api/types';

/**
 * BeadGraphView — a lightweight node graph for the project's beads.
 *
 * Renders one circular node per bead, color-coded by `bead_type`.
 * Includes a tag-filter input and an empty state. Designed to be
 * dependency-free (no D3, no SVG layout libs) so the test surface
 * stays small and fast.
 */
export interface BeadGraphViewProps {
  beads: BeadNode[];
  onNodeClick?: (bead: BeadNode) => void;
  /** Optional initial tag filter, e.g. preset by the parent view. */
  initialTagFilter?: string;
}

const TYPE_COLOR: Record<string, string> = {
  warning: T.cherry,
  incident: T.cherry,
  bug: T.cherry,
  decision: T.blueberry,
  architecture: T.blueberry,
  knowledge: T.mint,
  pattern: T.mint,
  review: T.tangerine,
};

function colorFor(type: string): string {
  return TYPE_COLOR[type] ?? T.crust;
}

export function BeadGraphView(props: BeadGraphViewProps) {
  const { beads, onNodeClick, initialTagFilter = '' } = props;
  const [tagFilter, setTagFilter] = useState(initialTagFilter);

  const filtered = useMemo(() => {
    const needle = tagFilter.trim().toLowerCase();
    if (!needle) return beads;
    return beads.filter((b) =>
      b.tags.some((t) => t.toLowerCase().includes(needle))
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

  const gridStyle: CSSProperties = {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 16,
    minHeight: 120,
    alignItems: 'flex-start',
  };

  if (beads.length === 0) {
    return (
      <div style={containerStyle} data-testid="bead-graph-empty">
        <div style={{ textAlign: 'center', color: T.text2, padding: '32px 16px' }}>
          No beads to display yet. As agents emit beads, they will appear here as
          a graph of nodes.
        </div>
      </div>
    );
  }

  return (
    <div style={containerStyle} data-testid="bead-graph">
      <input
        type="search"
        aria-label="Filter beads by tag"
        placeholder="Filter by tag..."
        value={tagFilter}
        onChange={(e) => setTagFilter(e.target.value)}
        style={filterStyle}
        data-testid="bead-graph-filter"
      />
      <div style={gridStyle} data-testid="bead-graph-nodes">
        {filtered.map((bead) => (
          <button
            key={bead.bead_id}
            type="button"
            data-testid="bead-node"
            onClick={() => onNodeClick?.(bead)}
            title={`${bead.bead_type}: ${bead.content}`}
            aria-label={`Bead ${bead.bead_id} (${bead.bead_type})`}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              cursor: onNodeClick ? 'pointer' : 'default',
              border: 'none',
              background: 'transparent',
              padding: 0,
              fontFamily: FONTS.body,
            }}
          >
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: '50%',
                background: colorFor(bead.bead_type),
                border: `2px solid ${T.border}`,
                boxShadow: SHADOWS.sm,
              }}
            />
            <div
              style={{
                fontSize: FONT_SIZES.xs,
                color: T.text1,
                marginTop: 4,
                maxWidth: 80,
                textAlign: 'center',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {bead.bead_id}
            </div>
          </button>
        ))}
        {filtered.length === 0 && (
          <div
            data-testid="bead-graph-no-matches"
            style={{ color: T.text2, fontSize: FONT_SIZES.sm }}
          >
            No beads match this filter.
          </div>
        )}
      </div>
    </div>
  );
}

export default BeadGraphView;
