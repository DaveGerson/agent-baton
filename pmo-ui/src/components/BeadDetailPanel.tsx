import { useEffect } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import {
  BEAD_TYPE_COLOR,
  BEAD_TYPE_LABEL,
  LINK_TYPE_STYLE,
  type Bead,
} from '../api/beads';

interface BeadDetailPanelProps {
  bead: Bead | null;
  onClose: () => void;
  /** Map of bead-id -> bead so we can render link target titles. */
  byId: Map<string, Bead>;
  /** Optional click on a link target — typically wired to "select that bead". */
  onLinkClick?: (targetBeadId: string) => void;
}

function fmtDate(iso: string): string {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

export function BeadDetailPanel({ bead, onClose, byId, onLinkClick }: BeadDetailPanelProps) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && bead) {
        onClose();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [bead, onClose]);

  if (!bead) return null;

  const accent = BEAD_TYPE_COLOR[bead.bead_type] ?? T.text2;

  return (
    <aside
      role="dialog"
      aria-label={`Bead ${bead.bead_id}`}
      data-testid="bead-detail-panel"
      style={{
        position: 'absolute',
        top: 0,
        right: 0,
        bottom: 0,
        width: 400,
        maxWidth: '90vw',
        background: T.cream,
        borderLeft: `3px solid ${T.border}`,
        boxShadow: SHADOWS.lg,
        display: 'flex',
        flexDirection: 'column',
        zIndex: 20,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div style={{
        padding: '12px 14px',
        borderBottom: `2px solid ${T.border}`,
        background: T.bg1,
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
      }}>
        <div
          aria-hidden
          style={{
            width: 14, height: 14, borderRadius: '50%',
            background: accent, border: `2px solid ${T.ink}`,
            flexShrink: 0, marginTop: 3,
          }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: FONTS.mono, fontSize: 11, color: T.text2 }}>
            {bead.bead_id}
          </div>
          <div style={{
            fontFamily: FONTS.display,
            fontSize: 15, fontWeight: 800,
            color: T.ink,
            lineHeight: 1.2,
            marginTop: 2,
          }}>
            {BEAD_TYPE_LABEL[bead.bead_type]} · {bead.agent_name || 'unknown'}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close detail panel"
          style={{
            background: 'transparent',
            border: `2px solid ${T.border}`,
            borderRadius: 6,
            padding: '2px 8px',
            fontFamily: FONTS.body,
            fontSize: 14, fontWeight: 900,
            color: T.ink,
            cursor: 'pointer',
            lineHeight: 1,
          }}
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
      }}>
        {/* Status / metadata grid */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'auto 1fr',
          gap: '6px 12px',
          fontFamily: FONTS.body,
          fontSize: 12,
        }}>
          <Label>Status</Label>
          <span style={{
            display: 'inline-block', padding: '1px 8px', borderRadius: 999,
            background: bead.status === 'open' ? T.butter : bead.status === 'closed' ? T.mintSoft : T.bg4,
            border: `1.5px solid ${T.border}`,
            fontWeight: 800, fontSize: 10,
            textTransform: 'uppercase', letterSpacing: '.06em',
            justifySelf: 'start',
          }}>{bead.status}</span>

          <Label>Confidence</Label>
          <Value>{bead.confidence}</Value>

          <Label>Scope</Label>
          <Value>{bead.scope}</Value>

          <Label>Source</Label>
          <Value>{bead.source}</Value>

          <Label>Task</Label>
          <Value mono>{bead.task_id || '—'}</Value>

          <Label>Step</Label>
          <Value mono>{bead.step_id || '—'}</Value>

          <Label>Created</Label>
          <Value>{fmtDate(bead.created_at)}</Value>

          {bead.closed_at && (
            <>
              <Label>Closed</Label>
              <Value>{fmtDate(bead.closed_at)}</Value>
            </>
          )}

          <Label>Tokens</Label>
          <Value mono>{bead.token_estimate}</Value>

          {typeof bead.retrieval_count === 'number' && (
            <>
              <Label>Retrieved</Label>
              <Value mono>{bead.retrieval_count}×</Value>
            </>
          )}
        </div>

        {/* Content */}
        <Section title="Content">
          <div style={{
            background: T.bg1,
            border: `2px solid ${T.borderSoft}`,
            borderRadius: 6,
            padding: 10,
            fontFamily: FONTS.body,
            fontSize: 12.5,
            lineHeight: 1.45,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            color: T.ink,
          }}>
            {bead.content || <em style={{ color: T.text2 }}>(no content)</em>}
          </div>
        </Section>

        {bead.summary && (
          <Section title="Summary">
            <div style={{
              fontFamily: FONTS.body, fontSize: 12, color: T.text1,
              fontStyle: 'italic', lineHeight: 1.4,
            }}>
              {bead.summary}
            </div>
          </Section>
        )}

        {bead.tags.length > 0 && (
          <Section title="Tags">
            <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
              {bead.tags.map(tag => (
                <span key={tag} style={{
                  background: T.bg3, border: `1.5px solid ${T.borderSoft}`,
                  borderRadius: 999, padding: '1px 8px',
                  fontFamily: FONTS.mono, fontSize: 10.5,
                  color: T.text1,
                }}>#{tag}</span>
              ))}
            </div>
          </Section>
        )}

        {bead.affected_files.length > 0 && (
          <Section title="Affected files">
            <ul style={{
              margin: 0, padding: 0, listStyle: 'none',
              display: 'flex', flexDirection: 'column', gap: 3,
            }}>
              {bead.affected_files.map(f => (
                <li key={f} style={{
                  fontFamily: FONTS.mono, fontSize: 11,
                  color: T.text1, wordBreak: 'break-all',
                }}>{f}</li>
              ))}
            </ul>
          </Section>
        )}

        {bead.links.length > 0 && (
          <Section title="Links">
            <ul style={{
              margin: 0, padding: 0, listStyle: 'none',
              display: 'flex', flexDirection: 'column', gap: 4,
            }}>
              {bead.links.map((link, i) => {
                const style = LINK_TYPE_STYLE[link.link_type];
                const target = byId.get(link.target_bead_id);
                return (
                  <li key={`${link.target_bead_id}-${i}`}>
                    <button
                      type="button"
                      disabled={!target || !onLinkClick}
                      onClick={() => onLinkClick?.(link.target_bead_id)}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        width: '100%', textAlign: 'left',
                        padding: '5px 8px',
                        border: `1.5px solid ${T.borderSoft}`,
                        borderRadius: 6,
                        background: target ? T.bg1 : 'transparent',
                        cursor: target && onLinkClick ? 'pointer' : 'default',
                        opacity: target ? 1 : 0.55,
                      }}
                    >
                      <span style={{
                        background: style.color, color: T.cream,
                        fontFamily: FONTS.mono, fontSize: 9,
                        padding: '1px 6px', borderRadius: 4,
                        fontWeight: 800, textTransform: 'uppercase',
                        letterSpacing: '.05em', flexShrink: 0,
                      }}>{style.label}</span>
                      <span style={{ fontFamily: FONTS.mono, fontSize: 11, color: T.ink }}>
                        {link.target_bead_id}
                      </span>
                      {target && (
                        <span style={{
                          fontFamily: FONTS.body, fontSize: 11,
                          color: T.text2, overflow: 'hidden',
                          textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                          flex: 1,
                        }}>
                          {target.content.slice(0, 60)}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          </Section>
        )}
      </div>
    </aside>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span style={{
      fontFamily: FONTS.mono, fontSize: 10,
      color: T.text2, textTransform: 'uppercase',
      letterSpacing: '.08em', alignSelf: 'center',
    }}>{children}</span>
  );
}

function Value({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return (
    <span style={{
      fontFamily: mono ? FONTS.mono : FONTS.body,
      fontSize: 12, color: T.ink, fontWeight: 600,
      wordBreak: 'break-all',
    }}>{children}</span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 style={{
        margin: '0 0 6px',
        fontFamily: FONTS.body, fontSize: 11, fontWeight: 900,
        color: T.text1, textTransform: 'uppercase',
        letterSpacing: '.08em',
      }}>{title}</h3>
      {children}
    </section>
  );
}
