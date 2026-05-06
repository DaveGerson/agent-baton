import { useEffect, useMemo, useState } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import {
  beadsApi,
  BEAD_TYPE_COLOR,
  BEAD_TYPE_LABEL,
  beadSize,
  type Bead,
} from '../api/beads';
import {
  BeadFilterBar,
  EMPTY_FILTERS,
  applyBeadFilters,
  type BeadFilters,
} from '../components/BeadFilterBar';
import { BeadDetailPanel } from '../components/BeadDetailPanel';

// ---------------------------------------------------------------------------
// Helpers — group beads into ISO-week buckets.
// ---------------------------------------------------------------------------

function isoWeekKey(d: Date): { key: string; label: string; start: Date } {
  // Start of week = Monday. Use UTC to avoid TZ jitter near midnight.
  const tmp = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = tmp.getUTCDay() || 7; // 1..7 (Mon=1, Sun=7)
  if (day !== 1) tmp.setUTCDate(tmp.getUTCDate() - (day - 1));
  const y = tmp.getUTCFullYear();
  const m = String(tmp.getUTCMonth() + 1).padStart(2, '0');
  const day0 = String(tmp.getUTCDate()).padStart(2, '0');
  const key = `${y}-${m}-${day0}`;
  const label = new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric',
    timeZone: 'UTC',
  }).format(tmp);
  return { key, label, start: tmp };
}

interface WeekBucket {
  key: string;
  label: string;
  start: Date;
  beads: Bead[];
}

function bucketByWeek(beads: Bead[]): WeekBucket[] {
  const map = new Map<string, WeekBucket>();
  for (const b of beads) {
    if (!b.created_at) continue;
    const d = new Date(b.created_at);
    if (Number.isNaN(d.getTime())) continue;
    const { key, label, start } = isoWeekKey(d);
    if (!map.has(key)) map.set(key, { key, label, start, beads: [] });
    map.get(key)!.beads.push(b);
  }
  // Sort beads inside bucket by created_at asc, buckets by start asc.
  for (const bucket of map.values()) {
    bucket.beads.sort((a, b) => a.created_at.localeCompare(b.created_at));
  }
  return Array.from(map.values()).sort((a, b) => a.start.getTime() - b.start.getTime());
}

function fmtDay(iso: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

export function BeadTimelineView() {
  const [allBeads, setAllBeads] = useState<Bead[]>([]);
  const [loading, setLoading] = useState(true);
  const [fixtureMode, setFixtureMode] = useState(false);
  const [filters, setFilters] = useState<BeadFilters>(EMPTY_FILTERS);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    beadsApi.list({ status: 'all', limit: 500 }).then(res => {
      if (cancelled) return;
      setAllBeads(res.beads);
      setFixtureMode(!!res.fixture);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  const visibleBeads = useMemo(
    () => applyBeadFilters(allBeads, filters),
    [allBeads, filters],
  );

  const buckets = useMemo(() => bucketByWeek(visibleBeads), [visibleBeads]);

  const byId = useMemo(() => {
    const m = new Map<string, Bead>();
    allBeads.forEach(b => m.set(b.bead_id, b));
    return m;
  }, [allBeads]);

  const maxBarSize = useMemo(() => {
    let m = 1;
    for (const b of visibleBeads) m = Math.max(m, beadSize(b));
    return m;
  }, [visibleBeads]);

  const selectedBead = selectedId ? byId.get(selectedId) ?? null : null;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: T.bg0 }}>
      <BeadFilterBar
        beads={allBeads}
        filters={filters}
        onChange={setFilters}
        matchedCount={visibleBeads.length}
      />

      {fixtureMode && (
        <div style={{
          padding: '6px 14px',
          background: T.tangerineSoft,
          borderBottom: `2px solid ${T.tangerine}`,
          fontFamily: FONTS.mono,
          fontSize: 11,
          color: T.ink,
        }}>
          Showing fixture data — backend route /api/v1/pmo/beads not yet available (see bead bd-aade).
        </div>
      )}

      <div
        data-testid="bead-timeline-container"
        style={{
          flex: 1,
          position: 'relative',
          overflow: 'auto',
          padding: 16,
        }}
      >
        {loading ? (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            height: '100%', fontFamily: FONTS.mono, color: T.text2,
          }}>Loading beads…</div>
        ) : buckets.length === 0 ? (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            height: '100%', fontFamily: FONTS.body, color: T.text2, fontSize: 14,
          }}>
            No beads match the current filters.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            {buckets.map(bucket => (
              <div key={bucket.key} style={{ display: 'flex', gap: 14 }}>
                {/* Week label rail */}
                <div style={{
                  flexShrink: 0,
                  width: 110,
                  paddingTop: 4,
                  borderRight: `2px dashed ${T.borderSoft}`,
                  paddingRight: 12,
                  textAlign: 'right',
                }}>
                  <div style={{
                    fontFamily: FONTS.display,
                    fontSize: 14, fontWeight: 800,
                    color: T.ink,
                  }}>
                    Week of {bucket.label}
                  </div>
                  <div style={{
                    fontFamily: FONTS.mono, fontSize: 10,
                    color: T.text2, marginTop: 2,
                  }}>
                    {bucket.beads.length} bead{bucket.beads.length === 1 ? '' : 's'}
                  </div>
                </div>

                {/* Bars */}
                <div style={{
                  flex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                  paddingTop: 4,
                  position: 'relative',
                }}>
                  {bucket.beads.map(b => {
                    const widthPct = 25 + (beadSize(b) / maxBarSize) * 70;
                    const isHover = b.bead_id === hoverId;
                    const isSel = b.bead_id === selectedId;
                    const color = BEAD_TYPE_COLOR[b.bead_type] ?? T.text2;
                    const closed = b.status === 'closed';
                    return (
                      <button
                        key={b.bead_id}
                        type="button"
                        data-testid="bead-timeline-bar"
                        data-bead-id={b.bead_id}
                        onClick={() => setSelectedId(b.bead_id)}
                        onMouseEnter={() => setHoverId(b.bead_id)}
                        onMouseLeave={() => setHoverId(null)}
                        onFocus={() => setHoverId(b.bead_id)}
                        onBlur={() => setHoverId(null)}
                        style={{
                          all: 'unset',
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          padding: '6px 10px',
                          width: `${widthPct}%`,
                          minWidth: 200,
                          background: closed ? `${color}66` : color,
                          border: `2px solid ${isSel ? T.cherry : T.ink}`,
                          borderRadius: 6,
                          boxShadow: isHover || isSel ? SHADOWS.md : SHADOWS.sm,
                          transition: 'box-shadow 100ms, transform 100ms',
                          transform: isHover ? 'translate(-1px, -1px)' : 'none',
                          position: 'relative',
                        }}
                        title={b.content}
                      >
                        <span style={{
                          fontFamily: FONTS.mono, fontSize: 9, fontWeight: 800,
                          color: T.ink, padding: '1px 5px',
                          background: T.cream, border: `1px solid ${T.ink}`,
                          borderRadius: 3, flexShrink: 0,
                        }}>
                          {BEAD_TYPE_LABEL[b.bead_type]}
                        </span>
                        <span style={{
                          fontFamily: FONTS.mono, fontSize: 10, color: T.ink,
                          flexShrink: 0,
                        }}>
                          {b.bead_id}
                        </span>
                        <span style={{
                          fontFamily: FONTS.body, fontSize: 12,
                          color: T.ink, fontWeight: 600,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          flex: 1,
                        }}>
                          {b.content}
                        </span>
                        <span style={{
                          fontFamily: FONTS.mono, fontSize: 9,
                          color: T.text1, flexShrink: 0,
                        }}>
                          {fmtDay(b.created_at)}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}

        <BeadDetailPanel
          bead={selectedBead}
          byId={byId}
          onClose={() => setSelectedId(null)}
          onLinkClick={(id) => setSelectedId(id)}
        />
      </div>
    </div>
  );
}
