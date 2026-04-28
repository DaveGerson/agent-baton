import { T, FONTS, SHADOWS } from '../styles/tokens';
import type { ProjectActivity } from '../api/workforce';

// ===================================================================
// ProjectHeatGrid — one square cell per project.
// Cell color = activity_score over the last hour.
//   cold (0)         → cream
//   warm (~0.5)      → butter
//   hot  (1)         → cherry
// Click → onSelect(project_id).
// ===================================================================

interface Props {
  data: ProjectActivity[];
  loading: boolean;
  onSelect?: (projectId: string) => void;
  selectedProject?: string | null;
}

function heatColor(score: number): string {
  // 0..0.33 = cool (gray-tan), 0.33..0.66 = warm (butter), 0.66..1 = hot (cherry)
  if (score <= 0.05) return T.bg4;
  if (score < 0.33)  return T.crust;
  if (score < 0.66)  return T.butter;
  if (score < 0.85)  return T.tangerine;
  return T.cherry;
}

function textOn(score: number): string {
  return score >= 0.66 ? T.cream : T.text0;
}

export function ProjectHeatGrid({ data, loading, onSelect, selectedProject }: Props) {
  return (
    <section
      aria-label="Project activity heat grid (last hour)"
      aria-busy={loading}
      style={{
        background: T.bg1,
        border: `2px solid ${T.border}`,
        borderRadius: 14,
        padding: 14,
        boxShadow: SHADOWS.sm,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      <div>
        <h3 style={{
          margin: 0,
          fontFamily: FONTS.display,
          fontSize: 16,
          fontWeight: 800,
          color: T.text0,
          letterSpacing: -0.4,
        }}>
          Project Heat
        </h3>
        <div style={{ fontSize: 10, color: T.text2, fontFamily: FONTS.body }}>
          activity over the last hour
        </div>
      </div>
      {loading && data.length === 0 ? (
        <SkeletonGrid />
      ) : (
        <div
          role="list"
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(96px, 1fr))',
            gap: 8,
          }}
        >
          {data.map(p => (
            <Cell
              key={p.project_id}
              project={p}
              selected={selectedProject === p.project_id}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
      <Legend />
    </section>
  );
}

interface CellProps {
  project: ProjectActivity;
  selected: boolean;
  onSelect?: (projectId: string) => void;
}

function Cell({ project, selected, onSelect }: CellProps) {
  const bg = heatColor(project.activity_score);
  const fg = textOn(project.activity_score);
  const interactive = !!onSelect;
  const Tag = interactive ? 'button' : 'div';

  return (
    <Tag
      role="listitem"
      title={`${project.project_name}\n${project.step_count} steps · ${project.error_count} errors\nactivity ${(project.activity_score * 100).toFixed(0)}%`}
      aria-label={`${project.project_name}, activity ${(project.activity_score * 100).toFixed(0)} percent, ${project.step_count} steps, ${project.error_count} errors`}
      onClick={interactive ? () => onSelect!(project.project_id) : undefined}
      style={{
        aspectRatio: '1 / 1',
        background: bg,
        border: selected ? `3px solid ${T.borderActive}` : `2px solid ${T.border}`,
        borderRadius: 10,
        padding: 6,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        cursor: interactive ? 'pointer' : 'default',
        font: 'inherit',
        color: fg,
        textAlign: 'left',
        position: 'relative',
        boxShadow: selected ? SHADOWS.md : 'none',
      }}
    >
      <div style={{
        fontSize: 10,
        fontWeight: 800,
        fontFamily: FONTS.body,
        lineHeight: 1.15,
        overflow: 'hidden',
        display: '-webkit-box',
        WebkitBoxOrient: 'vertical',
        WebkitLineClamp: 2,
      }}>
        {project.project_name}
      </div>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'baseline',
        fontFamily: FONTS.mono,
        fontSize: 9,
      }}>
        <span style={{ fontSize: 14, fontWeight: 900 }}>{project.step_count}</span>
        {project.error_count > 0 && (
          <span style={{
            background: T.cherryDark,
            color: T.cream,
            padding: '0 4px',
            borderRadius: 4,
            fontWeight: 800,
          }}>
            {project.error_count}!
          </span>
        )}
      </div>
    </Tag>
  );
}

function SkeletonGrid() {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(96px, 1fr))',
      gap: 8,
    }}>
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} style={{
          aspectRatio: '1 / 1',
          background: T.bg3,
          borderRadius: 10,
          animation: 'pulse 1.4s ease-in-out infinite',
          opacity: 0.4 + (i % 3) * 0.2,
        }} />
      ))}
    </div>
  );
}

function Legend() {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      paddingTop: 6,
      borderTop: `1px dashed ${T.borderSoft}`,
      fontSize: 10,
      color: T.text2,
      fontFamily: FONTS.body,
    }}>
      <span>cold</span>
      {[0.0, 0.2, 0.4, 0.6, 0.8, 1.0].map(s => (
        <span key={s} style={{
          display: 'inline-block',
          width: 14, height: 14,
          background: heatColor(s),
          border: `1px solid ${T.border}`,
          borderRadius: 3,
        }} />
      ))}
      <span>hot</span>
    </div>
  );
}
