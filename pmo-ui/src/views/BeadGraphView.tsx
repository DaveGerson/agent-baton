import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import {
  beadsApi,
  BEAD_TYPE_COLOR,
  LINK_TYPE_STYLE,
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
// Hand-rolled force layout (avoid heavy deps).
// Nodes are simulated with: spring edges, charge repulsion, gravity to centre.
// Simulation runs for a fixed number of ticks then re-runs on filter change.
// ---------------------------------------------------------------------------

interface SimNode {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  bead: Bead;
}

interface SimEdge {
  source: string;
  target: string;
  type: keyof typeof LINK_TYPE_STYLE;
}

interface LayoutInput {
  nodes: Bead[];
  edges: SimEdge[];
  width: number;
  height: number;
}

function buildEdges(beads: Bead[]): SimEdge[] {
  const known = new Set(beads.map(b => b.bead_id));
  const out: SimEdge[] = [];
  for (const b of beads) {
    for (const link of b.links) {
      if (!known.has(link.target_bead_id)) continue;
      out.push({
        source: b.bead_id,
        target: link.target_bead_id,
        type: link.link_type,
      });
    }
  }
  return out;
}

function runSimulation({ nodes, edges, width, height }: LayoutInput): SimNode[] {
  const cx = width / 2;
  const cy = height / 2;
  const sim: SimNode[] = nodes.map((b, i) => {
    // Seed in a circle so the first frame isn't a horrible singularity.
    const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2;
    const seedR = Math.min(width, height) * 0.3;
    const sz = 6 + Math.sqrt(beadSize(b)) * 3;
    return {
      id: b.bead_id,
      x: cx + Math.cos(angle) * seedR,
      y: cy + Math.sin(angle) * seedR,
      vx: 0,
      vy: 0,
      r: Math.min(28, sz),
      bead: b,
    };
  });

  const byId = new Map(sim.map(n => [n.id, n]));
  const TICKS = 220;
  const REPULSION = 1400;
  const SPRING_K = 0.04;
  const SPRING_LEN = 90;
  const GRAVITY = 0.012;
  const DAMP = 0.82;

  for (let t = 0; t < TICKS; t++) {
    // Repulsion (O(n^2) — fine for a few hundred nodes; PMO bead counts are small).
    for (let i = 0; i < sim.length; i++) {
      const a = sim[i];
      for (let j = i + 1; j < sim.length; j++) {
        const b = sim[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const dist2 = dx * dx + dy * dy + 0.01;
        const force = REPULSION / dist2;
        const dist = Math.sqrt(dist2);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
    }
    // Spring edges
    for (const e of edges) {
      const a = byId.get(e.source);
      const b = byId.get(e.target);
      if (!a || !b) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) + 0.01;
      const disp = (dist - SPRING_LEN) * SPRING_K;
      const fx = (dx / dist) * disp;
      const fy = (dy / dist) * disp;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }
    // Gravity to centre
    for (const n of sim) {
      n.vx += (cx - n.x) * GRAVITY;
      n.vy += (cy - n.y) * GRAVITY;
      n.vx *= DAMP;
      n.vy *= DAMP;
      n.x += n.vx;
      n.y += n.vy;
      // Clamp inside box.
      n.x = Math.max(n.r + 4, Math.min(width - n.r - 4, n.x));
      n.y = Math.max(n.r + 4, Math.min(height - n.r - 4, n.y));
    }
  }
  return sim;
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

export function BeadGraphView() {
  const [allBeads, setAllBeads] = useState<Bead[]>([]);
  const [loading, setLoading] = useState(true);
  const [fixtureMode, setFixtureMode] = useState(false);
  const [filters, setFilters] = useState<BeadFilters>(EMPTY_FILTERS);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });

  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const layoutRef = useRef<SimNode[]>([]);

  // --- data ---
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    beadsApi.list().then(res => {
      if (cancelled) return;
      setAllBeads(res.beads);
      setFixtureMode(!!res.fixture);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  // --- filtered subset ---
  const visibleBeads = useMemo(
    () => applyBeadFilters(allBeads, filters),
    [allBeads, filters],
  );

  const visibleEdges = useMemo(() => buildEdges(visibleBeads), [visibleBeads]);

  const byId = useMemo(() => {
    const m = new Map<string, Bead>();
    allBeads.forEach(b => m.set(b.bead_id, b));
    return m;
  }, [allBeads]);

  // --- responsive container size ---
  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const cr = entry.contentRect;
        setSize({ w: Math.max(200, cr.width), h: Math.max(200, cr.height) });
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // --- layout + draw ---
  useEffect(() => {
    if (loading || size.w < 50) return;
    layoutRef.current = runSimulation({
      nodes: visibleBeads,
      edges: visibleEdges,
      width: size.w,
      height: size.h,
    });
    drawGraph();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleBeads, visibleEdges, size.w, size.h, loading]);

  // Redraw on hover/select without re-running simulation.
  useEffect(() => {
    drawGraph();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hoverId, selectedId]);

  const drawGraph = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = size.w;
    const h = size.h;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const nodes = layoutRef.current;
    const idx = new Map(nodes.map(n => [n.id, n]));

    // Edges first
    for (const e of visibleEdges) {
      const a = idx.get(e.source);
      const b = idx.get(e.target);
      if (!a || !b) continue;
      const style = LINK_TYPE_STYLE[e.type];
      ctx.beginPath();
      ctx.strokeStyle = style.color;
      ctx.lineWidth = 1.5;
      if (style.dash) ctx.setLineDash(style.dash.split(' ').map(Number));
      else ctx.setLineDash([]);
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();

      // Arrowhead at target
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len = Math.sqrt(dx * dx + dy * dy);
      if (len > 0) {
        const ux = dx / len;
        const uy = dy / len;
        // Stop short of target node circumference
        const tipX = b.x - ux * (b.r + 2);
        const tipY = b.y - uy * (b.r + 2);
        const ah = 7;
        const aw = 4;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.fillStyle = style.color;
        ctx.moveTo(tipX, tipY);
        ctx.lineTo(tipX - ux * ah - uy * aw, tipY - uy * ah + ux * aw);
        ctx.lineTo(tipX - ux * ah + uy * aw, tipY - uy * ah - ux * aw);
        ctx.closePath();
        ctx.fill();
      }
    }
    ctx.setLineDash([]);

    // Nodes
    for (const n of nodes) {
      const isHover = n.id === hoverId;
      const isSel = n.id === selectedId;
      const color = BEAD_TYPE_COLOR[n.bead.bead_type] ?? T.text2;

      // Outer ring (selection)
      if (isSel) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = T.cherry;
        ctx.lineWidth = 3;
        ctx.stroke();
      }

      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = isHover ? 2.5 : 1.5;
      ctx.strokeStyle = T.ink;
      ctx.stroke();

      // Closed beads get a checkmark dot in the centre.
      if (n.bead.status === 'closed') {
        ctx.beginPath();
        ctx.arc(n.x, n.y, Math.max(2, n.r * 0.25), 0, Math.PI * 2);
        ctx.fillStyle = T.cream;
        ctx.fill();
      }

      // Label below
      if (isHover || isSel || n.r > 14) {
        ctx.fillStyle = T.ink;
        ctx.font = `${isHover || isSel ? 'bold ' : ''}10px ${FONTS.mono}`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(n.id, n.x, n.y + n.r + 3);
      }
    }
  }, [size.w, size.h, visibleEdges, hoverId, selectedId]);

  // --- mouse handling ---
  function nodeAt(x: number, y: number): SimNode | null {
    const nodes = layoutRef.current;
    // Iterate in reverse — last-drawn = top.
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const dx = n.x - x;
      const dy = n.y - y;
      if (dx * dx + dy * dy <= (n.r + 2) * (n.r + 2)) return n;
    }
    return null;
  }

  function handleMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const hit = nodeAt(x, y);
    setHoverId(hit?.id ?? null);
    e.currentTarget.style.cursor = hit ? 'pointer' : 'default';
  }

  function handleClick(e: React.MouseEvent<HTMLCanvasElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const hit = nodeAt(x, y);
    if (hit) setSelectedId(hit.id);
  }

  const hoverNode = hoverId ? layoutRef.current.find(n => n.id === hoverId) : null;
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
        ref={containerRef}
        data-testid="bead-graph-container"
        style={{
          flex: 1,
          position: 'relative',
          overflow: 'hidden',
          background: T.bg0,
        }}
      >
        {loading ? (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: FONTS.mono, color: T.text2,
          }}>Loading beads…</div>
        ) : visibleBeads.length === 0 ? (
          <div style={{
            position: 'absolute', inset: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: FONTS.body, color: T.text2, fontSize: 14,
          }}>
            No beads match the current filters.
          </div>
        ) : (
          <canvas
            ref={canvasRef}
            data-testid="bead-graph-canvas"
            data-node-count={visibleBeads.length}
            onMouseMove={handleMouseMove}
            onMouseLeave={() => setHoverId(null)}
            onClick={handleClick}
            role="img"
            aria-label={`Force-directed graph of ${visibleBeads.length} beads`}
          />
        )}

        {/* Hover tooltip */}
        {hoverNode && (
          <div
            role="tooltip"
            style={{
              position: 'absolute',
              left: Math.min(size.w - 260, hoverNode.x + 14),
              top: Math.min(size.h - 100, hoverNode.y + 14),
              maxWidth: 260,
              background: T.cream,
              border: `2px solid ${T.border}`,
              borderRadius: 6,
              padding: '6px 9px',
              boxShadow: SHADOWS.md,
              pointerEvents: 'none',
              zIndex: 10,
            }}
          >
            <div style={{ fontFamily: FONTS.mono, fontSize: 10, color: T.text2 }}>
              {hoverNode.id} · {hoverNode.bead.status}
            </div>
            <div style={{
              fontFamily: FONTS.body, fontSize: 12, fontWeight: 700,
              color: T.ink, marginTop: 2, lineHeight: 1.3,
            }}>
              {hoverNode.bead.content.slice(0, 110)}
              {hoverNode.bead.content.length > 110 ? '…' : ''}
            </div>
            {hoverNode.bead.tags.length > 0 && (
              <div style={{
                fontFamily: FONTS.mono, fontSize: 10, color: T.text2,
                marginTop: 4,
              }}>
                {hoverNode.bead.tags.slice(0, 4).map(t => `#${t}`).join(' ')}
              </div>
            )}
          </div>
        )}

        {/* Legend */}
        <div style={{
          position: 'absolute',
          left: 12, bottom: 12,
          background: T.cream,
          border: `2px solid ${T.border}`,
          borderRadius: 6,
          padding: '6px 9px',
          boxShadow: SHADOWS.sm,
          fontFamily: FONTS.mono,
          fontSize: 10,
        }}>
          <div style={{ fontWeight: 800, marginBottom: 3, color: T.text1, textTransform: 'uppercase', letterSpacing: '.06em' }}>
            Legend
          </div>
          {Object.entries(BEAD_TYPE_COLOR).slice(0, 5).map(([k, c]) => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{
                width: 8, height: 8, borderRadius: '50%',
                background: c, border: `1px solid ${T.ink}`,
              }} />
              <span>{k}</span>
            </div>
          ))}
        </div>

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
