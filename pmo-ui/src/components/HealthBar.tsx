import type { ProgramHealth } from '../api/types';
import { T, FONTS, SHADOWS, programColor } from '../styles/tokens';

interface HealthBarProps {
  health: Record<string, ProgramHealth>;
  onProgramClick?: (program: string) => void;
  activeProgram?: string | null;
}

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

export function HealthBar({ health, onProgramClick, activeProgram }: HealthBarProps) {
  const programs = Object.values(health);

  if (programs.length === 0) {
    return (
      <div style={{
        padding: '10px 14px',
        borderBottom: `2px solid ${T.border}`,
        background: T.bg1,
        fontSize: 16,
        color: T.text2,
        fontFamily: FONTS.hand,
        textAlign: 'center',
      }}>
        No menus yet, chef — start cookin'!
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex',
      gap: 8,
      padding: '10px 14px',
      borderBottom: `2px solid ${T.border}`,
      background: T.bg1,
      flexShrink: 0,
      overflowX: 'auto',
    }}>
      {programs.map((pg) => {
        const pct = clamp(Math.round(pg.completion_pct), 0, 100);
        const barColor = programColor(pg.program);
        const isActive = activeProgram === pg.program;
        const isClickable = !!onProgramClick;
        const computedTotal = (pg.active || 0) + (pg.completed || 0) + (pg.blocked || 0) + (pg.failed || 0);
        const totalMismatch = pg.total_plans > 0 && computedTotal !== pg.total_plans;
        const isBlocked = (pg.blocked || 0) > 0;

        return (
          <div
            key={pg.program}
            role={isClickable ? 'button' : undefined}
            tabIndex={isClickable ? 0 : undefined}
            aria-pressed={isClickable ? isActive : undefined}
            aria-label={isClickable
              ? `${pg.program}: ${pct}% complete. ${pg.total_plans} plans${pg.active > 0 ? `, ${pg.active} active` : ''}${pg.blocked > 0 ? `, ${pg.blocked} blocked` : ''}. ${isActive ? 'Currently filtered. Click to show all.' : 'Click to filter.'}`
              : undefined}
            onClick={isClickable ? () => onProgramClick(pg.program) : undefined}
            onKeyDown={isClickable ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onProgramClick(pg.program);
              }
            } : undefined}
            style={{
              flex: '1 1 140px',
              minWidth: 120,
              padding: '7px 10px',
              background: isBlocked ? T.cherrySoft : T.bg0,
              borderRadius: 10,
              borderTop: `2px solid ${isActive ? barColor : T.border}`,
              borderRight: `2px solid ${isActive ? barColor : T.border}`,
              borderBottom: `2px solid ${isActive ? barColor : T.border}`,
              borderLeft: `3px solid ${barColor}`,
              boxShadow: isActive ? SHADOWS.md : SHADOWS.sm,
              cursor: isClickable ? 'pointer' : 'default',
              transition: 'transform 0.1s, box-shadow 0.1s',
              transform: isActive ? 'translate(-1px, -1px)' : undefined,
            }}
            onMouseEnter={isClickable ? e => {
              (e.currentTarget as HTMLDivElement).style.transform = 'translate(-1px, -1px)';
              (e.currentTarget as HTMLDivElement).style.boxShadow = SHADOWS.md;
            } : undefined}
            onMouseLeave={isClickable ? e => {
              (e.currentTarget as HTMLDivElement).style.transform = isActive ? 'translate(-1px, -1px)' : '';
              (e.currentTarget as HTMLDivElement).style.boxShadow = isActive ? SHADOWS.md : SHADOWS.sm;
            } : undefined}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{
                fontSize: 13,
                fontWeight: 900,
                fontFamily: FONTS.display,
                color: isActive ? barColor : T.text0,
              }}>
                {pg.program}
              </span>
              <span style={{
                fontSize: 9,
                fontWeight: 600,
                color: T.text1,
                fontFamily: FONTS.mono,
              }}>
                {pct}% done
              </span>
            </div>
            <div style={{ width: '100%', height: 5, borderRadius: 2, background: T.bg3, overflow: 'hidden' }}>
              <div style={{
                width: `${pct}%`,
                height: '100%',
                background: barColor,
                borderRadius: 2,
                transition: 'width 0.5s',
              }} />
            </div>
            <div style={{ fontSize: 9, color: T.text2, marginTop: 3, fontFamily: FONTS.body }}>
              {pg.total_plans} plans
              {totalMismatch && (
                <span
                  title="Data inconsistency: counts don't sum to total"
                  style={{ marginLeft: 3, color: T.butter, cursor: 'help' }}
                >
                  {'⚠'}
                </span>
              )}
              {pg.active > 0 && ` · ${pg.active} active`}
              {pg.completed > 0 && ` · ${pg.completed} done`}
              {pg.blocked > 0 && (
                <span style={{ color: T.tangerine }}>{` · ${pg.blocked} blocked`}</span>
              )}
              {pg.failed > 0 && (
                <span style={{ color: T.cherry }}>{` · ${pg.failed} failed`}</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
