import { useState } from 'react';
import type { PmoCard } from '../api/types';
import { T, PRIORITY_COLOR } from '../styles/tokens';

interface KanbanCardProps {
  card: PmoCard;
  columnColor: string;
  onForge?: (card: PmoCard) => void;
}

function Chip({ children, color = T.text2 }: { children: React.ReactNode; color?: string }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 3,
      padding: '1px 6px',
      borderRadius: 3,
      fontSize: 8,
      fontWeight: 600,
      color,
      background: color + '14',
      border: `1px solid ${color}22`,
      whiteSpace: 'nowrap',
    }}>
      {children}
    </span>
  );
}

function Pips({ done, total, color }: { done: number; total: number; color: string }) {
  if (!total) return null;
  return (
    <div style={{ display: 'flex', gap: 2 }}>
      {Array.from({ length: total }).map((_, i) => (
        <div
          key={i}
          style={{
            width: 4,
            height: 4,
            borderRadius: 1,
            background: i < done ? color : T.bg3,
          }}
        />
      ))}
    </div>
  );
}

function ProgramDot({ program, size = 7 }: { program: string; size?: number }) {
  const color = programDotColor(program);
  return (
    <div
      title={program}
      style={{ width: size, height: size, borderRadius: 2, background: color, flexShrink: 0 }}
    />
  );
}

export function KanbanCard({ card, columnColor, onForge }: KanbanCardProps) {
  const [expanded, setExpanded] = useState(false);
  const isHuman = card.column === 'awaiting_human';
  const priorityColor = PRIORITY_COLOR[card.priority] ?? T.text2;

  const borderColor = isHuman ? T.orange + '55' : expanded ? columnColor + '55' : T.border;

  return (
    <div
      onClick={() => setExpanded(!expanded)}
      style={{
        background: T.bg1,
        borderRadius: 4,
        border: `1px solid ${borderColor}`,
        cursor: 'pointer',
        overflow: 'hidden',
        transition: 'border-color 0.15s',
        boxShadow: isHuman ? `0 0 8px ${T.orange}10` : 'none',
      }}
      onMouseEnter={e => {
        if (!expanded) {
          (e.currentTarget as HTMLDivElement).style.borderColor = columnColor + '66';
        }
      }}
      onMouseLeave={e => {
        if (!expanded) {
          (e.currentTarget as HTMLDivElement).style.borderColor = borderColor;
        }
      }}
    >
      <div style={{ padding: '7px 8px 6px' }}>
        {/* Title row */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 4, marginBottom: 3 }}>
          <ProgramDot program={card.program} size={6} />
          <div style={{
            fontSize: 12,
            fontWeight: 600,
            color: T.text0,
            lineHeight: 1.25,
            flex: 1,
            overflow: 'hidden',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
          }}>
            {card.title}
          </div>
        </div>

        {/* Meta row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 3, flexWrap: 'wrap', marginBottom: 3 }}>
          <span style={{ fontSize: 9, color: T.text4, fontFamily: 'monospace' }}>{card.card_id}</span>
          {card.priority <= 1 && (
            <Chip color={priorityColor}>P{card.priority}</Chip>
          )}
          {card.risk_level && card.risk_level !== 'low' && (
            <Chip color={card.risk_level === 'high' ? T.red : T.yellow}>
              {card.risk_level}
            </Chip>
          )}
        </div>

        {/* Step progress pips */}
        {card.steps_total > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 3 }}>
            <Pips done={card.steps_completed} total={card.steps_total} color={columnColor} />
            <span style={{ fontSize: 9, color: T.text3 }}>
              {card.steps_completed}/{card.steps_total}
            </span>
          </div>
        )}

        {/* Current phase / error */}
        {card.current_phase && !card.error && (
          <div style={{
            fontSize: 9,
            color: isHuman ? T.orange : T.text2,
            lineHeight: 1.2,
            marginTop: 2,
            padding: '2px 4px',
            background: T.bg2,
            borderRadius: 2,
            borderLeft: `2px solid ${isHuman ? T.orange : columnColor}`,
          }}>
            {card.current_phase.length > 65
              ? card.current_phase.slice(0, 65) + '…'
              : card.current_phase}
          </div>
        )}
        {card.error && (
          <div style={{
            fontSize: 9,
            color: T.red,
            lineHeight: 1.2,
            marginTop: 2,
            padding: '2px 4px',
            background: T.bg2,
            borderRadius: 2,
            borderLeft: `2px solid ${T.red}`,
          }}>
            {card.error.length > 80 ? card.error.slice(0, 80) + '…' : card.error}
          </div>
        )}

        {/* Footer */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 5 }}>
          <span style={{ fontSize: 9, color: T.text3 }}>{card.project_id}</span>
          {card.agents.length > 0 && (
            <>
              <span style={{ fontSize: 9, color: T.text4 }}>·</span>
              <span style={{ fontSize: 9, color: T.text3 }}>
                {card.agents.slice(0, 2).join(', ')}
                {card.agents.length > 2 && ` +${card.agents.length - 2}`}
              </span>
            </>
          )}
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 9, color: T.text4 }}>{fmtTime(card.updated_at)}</span>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={{
          borderTop: `1px solid ${T.border}`,
          padding: '6px 8px',
          background: T.bg2,
        }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
            <div>
              <span style={{ fontSize: 9, color: T.text3 }}>Program: </span>
              <span style={{ fontSize: 9, color: T.text0, fontWeight: 600 }}>{card.program}</span>
            </div>
            <div>
              <span style={{ fontSize: 9, color: T.text3 }}>Gates passed: </span>
              <span style={{ fontSize: 9, color: T.text0, fontWeight: 600 }}>{card.gates_passed}</span>
            </div>
          </div>
          {card.agents.length > 0 && (
            <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', marginBottom: 6 }}>
              {card.agents.map(a => (
                <Chip key={a} color={T.cyan}>{a}</Chip>
              ))}
            </div>
          )}

          {/* Forge navigation actions */}
          {onForge && (
            <div style={{ display: 'flex', gap: 4, marginTop: 4, paddingTop: 4, borderTop: `1px solid ${T.border}` }}>
              <button
                onClick={e => { e.stopPropagation(); onForge(card); }}
                style={{
                  padding: '3px 9px',
                  borderRadius: 3,
                  border: `1px solid ${T.accent}44`,
                  background: T.accent + '12',
                  color: T.accent,
                  fontSize: 9,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
                title="Open Forge with this card's context"
              >
                Re-forge
              </button>
              <button
                onClick={e => { e.stopPropagation(); onForge(card); }}
                style={{
                  padding: '3px 9px',
                  borderRadius: 3,
                  border: `1px solid ${T.purple}44`,
                  background: T.purple + '12',
                  color: T.purple,
                  fontSize: 9,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
                title="Edit this plan in Forge"
              >
                Edit in Forge
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '—';
  }
}

const DOT_PALETTE = [
  '#1e40af', '#7c3aed', '#059669', '#dc2626',
  '#0284c7', '#c2410c', '#0d9488', '#7e22ce',
];

function programDotColor(program: string): string {
  let hash = 0;
  for (let i = 0; i < program.length; i++) {
    hash = (hash * 31 + program.charCodeAt(i)) >>> 0;
  }
  return DOT_PALETTE[hash % DOT_PALETTE.length];
}
