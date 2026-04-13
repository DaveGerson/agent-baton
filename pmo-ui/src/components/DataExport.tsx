import { useState, useCallback } from 'react';
import type { PmoCard, ProgramHealth } from '../api/types';
import { T, FONT_SIZES } from '../styles/tokens';
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
      background: 'rgba(0,0,0,0.6)',
    }} onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 400,
          background: T.bg1,
          border: `1px solid ${T.border}`,
          borderRadius: 8,
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 16px',
          borderBottom: `1px solid ${T.border}`,
        }}>
          <h2 style={{ fontSize: FONT_SIZES.lg, fontWeight: 700, color: T.text0, margin: 0 }}>
            Export Data
          </h2>
          <button
            onClick={onClose}
            aria-label="Close export dialog"
            style={{ background: 'none', border: 'none', color: T.text3, fontSize: 16, cursor: 'pointer' }}
          >
            \u2715
          </button>
        </div>

        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Format selection */}
          <FieldGroup label="Format">
            <div style={{ display: 'flex', gap: 6 }}>
              {(['csv', 'json', 'markdown'] as ExportFormat[]).map((f) => (
                <ToggleButton
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
              <div style={{ display: 'flex', gap: 6 }}>
                <ToggleButton
                  active={scope === 'all'}
                  onClick={() => setScope('all')}
                  label={`All (${cards.length})`}
                />
                <ToggleButton
                  active={scope === 'filtered'}
                  onClick={() => setScope('filtered')}
                  label={`Filtered (${filteredCards.length})`}
                />
              </div>
            </FieldGroup>
          )}

          {/* Include health toggle */}
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={includeHealth}
              onChange={(e) => setIncludeHealth(e.target.checked)}
              style={{ accentColor: T.accent }}
            />
            <span style={{ fontSize: FONT_SIZES.sm, color: T.text1 }}>Include program health data</span>
          </label>

          {/* Preview */}
          <div style={{
            padding: 8,
            borderRadius: 4,
            background: T.bg2,
            border: `1px solid ${T.border}`,
            fontSize: FONT_SIZES.xs,
            color: T.text3,
          }}>
            {targetCards.length} cards
            {includeHealth ? ` + ${Object.keys(health).length} programs` : ''}
            {' \u2192 '}
            {format === 'csv' ? '.csv spreadsheet' : format === 'json' ? '.json structured data' : '.md report'}
          </div>
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 8,
          padding: '8px 16px',
          borderTop: `1px solid ${T.border}`,
        }}>
          <button
            onClick={onClose}
            style={{
              padding: '5px 14px',
              borderRadius: 4,
              border: `1px solid ${T.border}`,
              background: T.bg3,
              color: T.text1,
              fontSize: FONT_SIZES.sm,
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleExport}
            style={{
              padding: '5px 14px',
              borderRadius: 4,
              border: 'none',
              background: T.accent,
              color: '#fff',
              fontSize: FONT_SIZES.sm,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Export
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: FONT_SIZES.xs, color: T.text3, marginBottom: 4, fontWeight: 600 }}>{label}</div>
      {children}
    </div>
  );
}

function ToggleButton({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '4px 12px',
        borderRadius: 4,
        border: `1px solid ${active ? T.accent : T.border}`,
        background: active ? T.accent + '20' : T.bg3,
        color: active ? T.accent : T.text2,
        fontSize: FONT_SIZES.sm,
        fontWeight: active ? 600 : 400,
        cursor: 'pointer',
      }}
    >
      {label}
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
