import type { ProgramHealth } from '../api/types';
import { T } from '../styles/tokens';

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
        padding: '8px 14px',
        borderBottom: `1px solid ${T.border}`,
        background: T.bg1,
        fontSize: 8,
        color: T.text3,
        fontStyle: 'italic',
      }}>
        No programs tracked yet.
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex',
      gap: 8,
      padding: '8px 14px',
      borderBottom: `1px solid ${T.border}`,
      background: T.bg1,
      flexShrink: 0,
      overflowX: 'auto',
    }}>
      {programs.map((pg) => {
        const pct = clamp(Math.round(pg.completion_pct), 0, 100);
        const barColor = programColor(pg.program);
        const isActive = activeProgram === pg.program;
        const isClickable = !!onProgramClick;

        return (
          <div
            key={pg.program}
            onClick={isClickable ? () => onProgramClick(pg.program) : undefined}
            style={{
              flex: '1 1 140px',
              minWidth: 120,
              padding: '6px 10px',
              background: T.bg2,
              borderRadius: 5,
              borderLeft: `3px solid ${barColor}`,
              outline: isActive ? `2px solid ${barColor}` : '2px solid transparent',
              outlineOffset: 1,
              cursor: isClickable ? 'pointer' : 'default',
              transition: 'outline-color 0.15s',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 3 }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: isActive ? barColor : T.text0 }}>{pg.program}</span>
              <span style={{ fontSize: 9, fontWeight: 600, color: barColor, fontFamily: 'monospace' }}>
                {pct}%
              </span>
            </div>
            <div style={{ width: '100%', height: 3, borderRadius: 2, background: T.bg3, overflow: 'hidden' }}>
              <div style={{
                width: `${pct}%`,
                height: '100%',
                background: barColor,
                borderRadius: 2,
                transition: 'width 0.5s',
              }} />
            </div>
            <div style={{ fontSize: 9, color: T.text3, marginTop: 2 }}>
              {pg.total_plans} plans
              {pg.active > 0 && ` · ${pg.active} active`}
              {pg.completed > 0 && ` · ${pg.completed} done`}
              {pg.blocked > 0 && (
                <span style={{ color: T.orange }}>{` · ${pg.blocked} blocked`}</span>
              )}
              {pg.failed > 0 && (
                <span style={{ color: T.red }}>{` · ${pg.failed} failed`}</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const PROGRAM_PALETTE = [
  '#1e40af', '#7c3aed', '#059669', '#dc2626',
  '#0284c7', '#c2410c', '#0d9488', '#7e22ce',
];

function programColor(program: string): string {
  let hash = 0;
  for (let i = 0; i < program.length; i++) {
    hash = (hash * 31 + program.charCodeAt(i)) >>> 0;
  }
  return PROGRAM_PALETTE[hash % PROGRAM_PALETTE.length];
}
