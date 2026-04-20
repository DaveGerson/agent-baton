import { useState, useCallback } from 'react';
import type { ReactNode } from 'react';
import type { PmoCard, ProgramHealth } from '../api/types';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { useToast } from '../contexts/ToastContext';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ExportFormat = 'csv' | 'json' | 'markdown';
type ExportScope = 'all' | 'filtered';

interface Props {
  cards: PmoCard[];
  health: Record<string, ProgramHealth>;
  filteredCards?: PmoCard[];
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DataExport({ cards, health, filteredCards, onClose }: Props) {
  const [format, setFormat] = useState<ExportFormat>('csv');
  const [scope, setScope] = useState<ExportScope>(filteredCards ? 'filtered' : 'all');
  const [includeHealth, setIncludeHealth] = useState(true);
  const { success, error: showError } = useToast();

  const targetCards = scope === 'filtered' && filteredCards ? filteredCards : cards;

  const handleExport = useCallback(() => {
    try {
      let content: string;
      let filename: string;
      let mimeType: string;

      switch (format) {
        case 'csv':
          content = exportCsv(targetCards, includeHealth ? health : undefined);
          filename = `baton-portfolio-${dateStamp()}.csv`;
          mimeType = 'text/csv;charset=utf-8';
          break;
        case 'json':
          content = exportJson(targetCards, includeHealth ? health : undefined);
          filename = `baton-portfolio-${dateStamp()}.json`;
          mimeType = 'application/json;charset=utf-8';
          break;
        case 'markdown':
          content = exportMarkdown(targetCards, includeHealth ? health : undefined);
          filename = `baton-portfolio-${dateStamp()}.md`;
          mimeType = 'text/markdown;charset=utf-8';
          break;
      }

      downloadFile(content, filename, mimeType);
      success(`Exported ${targetCards.length} cards as ${format.toUpperCase()}`);
      onClose();
    } catch (err) {
      showError(err instanceof Error ? err.message : 'Export failed');
    }
  }, [format, targetCards, includeHealth, health, success, showError, onClose, scope]);

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      zIndex: 1000,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'rgba(42,26,16,.6)',
    }} onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 480,
          background: T.bg1,
          border: `3px solid ${T.border}`,
          borderRadius: 18,
          boxShadow: SHADOWS.lg,
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          padding: '16px 20px',
          borderBottom: `3px solid ${T.border}`,
          background: T.crust,
          position: 'relative',
        }}>
          <span style={{ fontSize: 40, marginRight: 12, lineHeight: 1 }}>🥡</span>
          <div style={{ flex: 1 }}>
            <div style={{
              fontFamily: FONTS.display,
              fontWeight: 900,
              fontSize: 26,
              color: T.ink,
              lineHeight: 1.1,
            }}>
              Takeout
            </div>
            <div style={{
              fontFamily: FONTS.hand,
              fontSize: 18,
              color: T.inkSoft,
              transform: 'rotate(-1.5deg)',
              display: 'inline-block',
              marginTop: 2,
            }}>
              wrap it up to go
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close export dialog"
            style={{
              position: 'absolute',
              top: 12,
              right: 14,
              background: 'none',
              border: `1.5px solid ${T.border}`,
              borderRadius: 6,
              color: T.ink,
              fontSize: 16,
              cursor: 'pointer',
              lineHeight: 1,
              padding: '2px 7px',
            }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* Format selection */}
          <FieldGroup label="Format">
            <div style={{ display: 'flex', gap: 8 }}>
              {(['csv', 'json', 'markdown'] as ExportFormat[]).map((f) => (
                <FormatButton
                  key={f}
                  active={format === f}
                  onClick={() => setFormat(f)}
                  label={f.toUpperCase()}
                />
              ))}
            </div>
          </FieldGroup>

          {/* Scope selection */}
          {filteredCards && filteredCards.length !== cards.length && (
            <FieldGroup label="Scope">
              <div style={{ display: 'flex', gap: 8 }}>
                <ScopeButton
                  active={scope === 'all'}
                  onClick={() => setScope('all')}
                  label={`All (${cards.length})`}
                />
                <ScopeButton
                  active={scope === 'filtered'}
                  onClick={() => setScope('filtered')}
                  label={`Filtered (${filteredCards.length})`}
                />
              </div>
            </FieldGroup>
          )}

          {/* Include health toggle */}
          <div style={{
            background: T.bg3,
            border: `1.5px dashed ${T.border}`,
            borderRadius: 10,
            padding: '10px 12px',
          }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={includeHealth}
                onChange={(e) => setIncludeHealth(e.target.checked)}
                style={{ accentColor: T.cherry, width: 15, height: 15 }}
              />
              <div>
                <div style={{
                  fontFamily: FONTS.body,
                  fontWeight: 700,
                  fontSize: 13,
                  color: T.text0,
                }}>
                  Include program health data
                </div>
                <div style={{
                  fontFamily: FONTS.hand,
                  fontSize: 13,
                  color: T.text2,
                  marginTop: 1,
                }}>
                  makes a bigger bag
                </div>
              </div>
            </label>
          </div>

          {/* Preview */}
          <div style={{
            background: T.ink,
            color: T.mintSoft,
            borderRadius: 10,
            padding: '10px 12px',
            fontFamily: FONTS.mono,
            fontSize: 11,
            lineHeight: 1.6,
          }}>
            <span style={{ color: T.butter }}>$ pec export</span>
            <span style={{ color: T.mintSoft }}>
              {' '}--format {format}
              {scope === 'filtered' && filteredCards ? ` --scope filtered` : ''}
              {includeHealth ? ' --include-health' : ''}
              {` # ${targetCards.length} card${targetCards.length !== 1 ? 's' : ''}`}
              {includeHealth ? ` + ${Object.keys(health).length} programs` : ''}
            </span>
          </div>
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 8,
          padding: '12px 16px',
          borderTop: `2px solid ${T.border}`,
          background: T.bg3,
        }}>
          <button
            onClick={onClose}
            style={{
              padding: '6px 16px',
              borderRadius: 10,
              border: `2px dashed ${T.borderSoft}`,
              background: 'none',
              color: T.text1,
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 13,
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleExport}
            style={{
              padding: '6px 16px',
              borderRadius: 10,
              border: `2px solid ${T.border}`,
              background: T.cherry,
              color: T.cream,
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 13,
              cursor: 'pointer',
              boxShadow: SHADOWS.sm,
            }}
          >
            Pack it up →
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FieldGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div style={{
        fontFamily: FONTS.body,
        fontWeight: 800,
        textTransform: 'uppercase',
        fontSize: 11,
        letterSpacing: '.08em',
        color: T.text1,
        marginBottom: 8,
      }}>
        {label}
      </div>
      {children}
    </div>
  );
}

function FormatButton({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '10px 14px',
        borderRadius: 10,
        border: `2px solid ${T.border}`,
        background: active ? T.mintSoft : T.bg3,
        boxShadow: active ? SHADOWS.sm : 'none',
        transform: active ? 'translate(-1px, -1px)' : 'none',
        cursor: 'pointer',
        transition: 'all 0.1s',
      }}
    >
      <span style={{
        fontFamily: FONTS.display,
        fontWeight: 900,
        fontSize: 16,
        color: T.text0,
      }}>
        {label}
      </span>
    </button>
  );
}

function ScopeButton({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '6px 12px',
        borderRadius: 10,
        border: `2px solid ${T.border}`,
        background: active ? T.mintSoft : T.bg3,
        boxShadow: active ? SHADOWS.sm : 'none',
        transform: active ? 'translate(-1px, -1px)' : 'none',
        cursor: 'pointer',
        transition: 'all 0.1s',
      }}
    >
      <span style={{
        fontFamily: FONTS.display,
        fontWeight: 900,
        fontSize: 13,
        color: T.text0,
      }}>
        {label}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Export logic
// ---------------------------------------------------------------------------

function dateStamp(): string {
  return new Date().toISOString().slice(0, 10);
}

function downloadFile(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function escapeCsv(value: string): string {
  if (value.includes(',') || value.includes('"') || value.includes('\n')) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function exportCsv(cards: PmoCard[], health?: Record<string, ProgramHealth>): string {
  const headers = ['card_id', 'title', 'project_id', 'program', 'column', 'risk_level', 'priority', 'agents', 'steps_completed', 'steps_total', 'gates_passed', 'current_phase', 'error', 'external_id', 'created_at', 'updated_at'];
  const rows = cards.map(c => [
    c.card_id, c.title, c.project_id, c.program, c.column,
    c.risk_level, String(c.priority), c.agents.join('; '),
    String(c.steps_completed), String(c.steps_total), String(c.gates_passed),
    c.current_phase, c.error, c.external_id, c.created_at, c.updated_at,
  ].map(escapeCsv));

  let csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');

  if (health && Object.keys(health).length > 0) {
    csv += '\n\n# Program Health\n';
    csv += 'program,total_plans,active,completed,blocked,failed,completion_pct\n';
    for (const h of Object.values(health)) {
      csv += [h.program, h.total_plans, h.active, h.completed, h.blocked, h.failed, h.completion_pct].join(',') + '\n';
    }
  }

  return csv;
}

function exportJson(cards: PmoCard[], health?: Record<string, ProgramHealth>): string {
  const data: Record<string, unknown> = {
    exported_at: new Date().toISOString(),
    card_count: cards.length,
    cards,
  };
  if (health) {
    data.health = health;
  }
  return JSON.stringify(data, null, 2);
}

function exportMarkdown(cards: PmoCard[], health?: Record<string, ProgramHealth>): string {
  const lines: string[] = [];
  lines.push('# Baton Portfolio Report');
  lines.push(`\n> Exported: ${new Date().toISOString()}`);
  lines.push(`> Total Plans: ${cards.length}`);
  lines.push('');

  // Summary
  const deployed = cards.filter(c => c.column === 'deployed').length;
  const active = cards.filter(c => c.column === 'executing' || c.column === 'validating').length;
  const blocked = cards.filter(c => c.column === 'awaiting_human').length;
  lines.push('## Summary');
  lines.push(`| Metric | Value |`);
  lines.push(`|--------|-------|`);
  lines.push(`| Deployed | ${deployed} |`);
  lines.push(`| Active | ${active} |`);
  lines.push(`| Blocked | ${blocked} |`);
  lines.push(`| Queued | ${cards.filter(c => c.column === 'queued').length} |`);
  lines.push('');

  // Health
  if (health && Object.keys(health).length > 0) {
    lines.push('## Program Health');
    lines.push('| Program | Plans | Active | Done | Blocked | Failed | % |');
    lines.push('|---------|-------|--------|------|---------|--------|---|');
    for (const h of Object.values(health)) {
      lines.push(`| ${h.program} | ${h.total_plans} | ${h.active} | ${h.completed} | ${h.blocked} | ${h.failed} | ${h.completion_pct}% |`);
    }
    lines.push('');
  }

  // Cards table
  lines.push('## Plans');
  lines.push('| ID | Title | Project | Column | Risk | Steps | Agents |');
  lines.push('|----|-------|---------|--------|------|-------|--------|');
  for (const c of cards) {
    lines.push(`| ${c.card_id.slice(0, 8)} | ${c.title.slice(0, 40)} | ${c.project_id} | ${c.column} | ${c.risk_level} | ${c.steps_completed}/${c.steps_total} | ${c.agents.join(', ')} |`);
  }

  return lines.join('\n');
}
