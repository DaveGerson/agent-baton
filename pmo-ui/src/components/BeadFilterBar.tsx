import { useMemo } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import {
  BEAD_TYPE_COLOR,
  BEAD_TYPE_LABEL,
  type Bead,
  type BeadType,
  type BeadStatus,
} from '../api/beads';

export interface BeadFilters {
  types: Set<BeadType>;
  status: 'all' | BeadStatus;
  tags: Set<string>;
  search: string;
}

export const EMPTY_FILTERS: BeadFilters = {
  types: new Set<BeadType>(),
  status: 'all',
  tags: new Set<string>(),
  search: '',
};

/** Apply filters to a bead list — pure helper used by both views and tests. */
export function applyBeadFilters(beads: Bead[], filters: BeadFilters): Bead[] {
  const q = filters.search.trim().toLowerCase();
  return beads.filter(b => {
    if (filters.types.size > 0 && !filters.types.has(b.bead_type)) return false;
    if (filters.status !== 'all' && b.status !== filters.status) return false;
    if (filters.tags.size > 0) {
      // OR-semantics on tags — show beads matching ANY selected tag.
      const matches = b.tags.some(t => filters.tags.has(t));
      if (!matches) return false;
    }
    if (q && !b.content.toLowerCase().includes(q) && !b.bead_id.toLowerCase().includes(q)) {
      return false;
    }
    return true;
  });
}

interface ChipProps {
  label: string;
  active: boolean;
  color?: string;
  onClick: () => void;
}

function Chip({ label, active, color, onClick }: ChipProps) {
  const bg = active ? (color ?? T.butter) : 'transparent';
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '3px 9px',
        borderRadius: 999,
        border: `2px solid ${active ? T.ink : T.borderSoft}`,
        background: bg,
        color: active ? T.ink : T.text1,
        fontFamily: FONTS.body,
        fontSize: 11,
        fontWeight: 800,
        letterSpacing: '.02em',
        cursor: 'pointer',
        boxShadow: active ? SHADOWS.sm : 'none',
        transition: 'all 100ms',
      }}
    >
      {color && (
        <span
          aria-hidden
          style={{
            display: 'inline-block',
            width: 8, height: 8, borderRadius: '50%',
            background: color, border: `1px solid ${T.ink}`,
          }}
        />
      )}
      {label}
    </button>
  );
}

interface BeadFilterBarProps {
  beads: Bead[];
  filters: BeadFilters;
  onChange: (filters: BeadFilters) => void;
  matchedCount: number;
  /** Optional rightmost slot — e.g. view toggle. */
  trailing?: React.ReactNode;
}

export function BeadFilterBar({
  beads,
  filters,
  onChange,
  matchedCount,
  trailing,
}: BeadFilterBarProps) {
  const allTypes = useMemo(() => {
    const set = new Set<BeadType>();
    beads.forEach(b => set.add(b.bead_type));
    return Array.from(set).sort();
  }, [beads]);

  const allTags = useMemo(() => {
    const counts = new Map<string, number>();
    beads.forEach(b => b.tags.forEach(t => counts.set(t, (counts.get(t) ?? 0) + 1)));
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 24)
      .map(([t]) => t);
  }, [beads]);

  function toggleType(t: BeadType) {
    const next = new Set(filters.types);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    onChange({ ...filters, types: next });
  }

  function toggleTag(t: string) {
    const next = new Set(filters.tags);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    onChange({ ...filters, tags: next });
  }

  function setStatus(s: 'all' | BeadStatus) {
    onChange({ ...filters, status: s });
  }

  function clearAll() {
    onChange({ ...EMPTY_FILTERS });
  }

  const hasActiveFilters =
    filters.types.size > 0 ||
    filters.tags.size > 0 ||
    filters.status !== 'all' ||
    filters.search.trim().length > 0;

  return (
    <div
      data-testid="bead-filter-bar"
      style={{
        background: T.bg1,
        borderBottom: `2px solid ${T.border}`,
        padding: '10px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        flexShrink: 0,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {/* Search */}
        <input
          type="search"
          aria-label="Search beads"
          placeholder="Search bead content or ID…"
          value={filters.search}
          onChange={(e) => onChange({ ...filters, search: e.target.value })}
          style={{
            flex: '1 1 240px',
            minWidth: 200,
            padding: '6px 10px',
            border: `2px solid ${T.border}`,
            borderRadius: 6,
            background: T.cream,
            color: T.ink,
            fontFamily: FONTS.body,
            fontSize: 12,
            fontWeight: 600,
            boxShadow: SHADOWS.sm,
          }}
        />

        {/* Status segmented control */}
        <div role="radiogroup" aria-label="Status" style={{ display: 'flex', gap: 0, border: `2px solid ${T.border}`, borderRadius: 6, overflow: 'hidden', boxShadow: SHADOWS.sm }}>
          {(['all', 'open', 'closed', 'archived'] as const).map(s => {
            const active = filters.status === s;
            return (
              <button
                key={s}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setStatus(s)}
                style={{
                  padding: '5px 10px',
                  background: active ? T.ink : T.cream,
                  color: active ? T.cream : T.ink,
                  border: 'none',
                  borderRight: s !== 'archived' ? `1px solid ${T.border}` : 'none',
                  fontFamily: FONTS.body,
                  fontSize: 11,
                  fontWeight: 800,
                  textTransform: 'capitalize',
                  cursor: 'pointer',
                  letterSpacing: '.02em',
                }}
              >
                {s}
              </button>
            );
          })}
        </div>

        <span
          data-testid="bead-match-count"
          style={{ fontFamily: FONTS.mono, fontSize: 11, color: T.text2 }}
        >
          {matchedCount} / {beads.length} beads
        </span>

        {hasActiveFilters && (
          <button
            type="button"
            onClick={clearAll}
            style={{
              padding: '4px 10px',
              border: `2px solid ${T.cherry}`,
              borderRadius: 6,
              background: 'transparent',
              color: T.cherry,
              fontFamily: FONTS.body,
              fontSize: 11,
              fontWeight: 800,
              cursor: 'pointer',
            }}
          >
            Clear
          </button>
        )}

        <div style={{ flex: 1 }} />
        {trailing}
      </div>

      {/* Type chips */}
      {allTypes.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: T.text2, letterSpacing: '.08em', textTransform: 'uppercase' }}>
            Type
          </span>
          {allTypes.map(t => (
            <Chip
              key={t}
              label={BEAD_TYPE_LABEL[t]}
              color={BEAD_TYPE_COLOR[t]}
              active={filters.types.has(t)}
              onClick={() => toggleType(t)}
            />
          ))}
        </div>
      )}

      {/* Tag chips */}
      {allTags.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: FONTS.mono, fontSize: 10, color: T.text2, letterSpacing: '.08em', textTransform: 'uppercase' }}>
            Tags
          </span>
          {allTags.map(t => (
            <Chip
              key={t}
              label={`#${t}`}
              active={filters.tags.has(t)}
              onClick={() => toggleTag(t)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
