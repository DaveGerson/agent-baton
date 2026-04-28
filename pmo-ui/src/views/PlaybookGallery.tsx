import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { T, FONTS, FONT_SIZES, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import type { Playbook } from '../api/types';

/**
 * H3.8 — Playbook Gallery.
 *
 * Displays curated workflow templates served from
 * `templates/playbooks/*.md` via `GET /pmo/playbooks`. Selecting a
 * playbook reveals its full markdown body in a side pane (rendered as
 * preformatted text — markdown rendering is intentionally out of scope
 * for the first cut).
 */
export function PlaybookGallery() {
  const [playbooks, setPlaybooks] = useState<Playbook[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeSlug, setActiveSlug] = useState<string | null>(null);

  useEffect(() => {
    api
      .listPlaybooks()
      .then((data) => {
        setPlaybooks(data);
        if (data.length > 0) setActiveSlug(data[0].slug);
      })
      .catch((err) => setError(String(err.message ?? err)))
      .finally(() => setLoading(false));
  }, []);

  const active = playbooks.find((p) => p.slug === activeSlug) ?? null;

  const containerStyle: CSSProperties = {
    padding: 16,
    background: T.bg0,
    color: T.text0,
    fontFamily: FONTS.body,
    display: 'flex',
    flexDirection: 'column',
    minHeight: '100%',
  };

  const layoutStyle: CSSProperties = {
    display: 'flex',
    gap: 16,
    flex: 1,
    minHeight: 0,
  };

  const listStyle: CSSProperties = {
    width: 240,
    flexShrink: 0,
    background: T.bg1,
    border: `2px solid ${T.border}`,
    borderRadius: 6,
    padding: 8,
    boxShadow: SHADOWS.sm,
    overflowY: 'auto',
  };

  const itemStyle = (active: boolean): CSSProperties => ({
    padding: 8,
    borderRadius: 4,
    cursor: 'pointer',
    border: `2px solid ${active ? T.borderActive : 'transparent'}`,
    background: active ? T.butterSoft : T.cream,
    marginBottom: 4,
    fontFamily: FONTS.body,
    fontSize: FONT_SIZES.sm,
    color: T.text0,
    width: '100%',
    textAlign: 'left',
  });

  const detailStyle: CSSProperties = {
    flex: 1,
    background: T.cream,
    border: `2px solid ${T.border}`,
    borderRadius: 6,
    padding: 16,
    boxShadow: SHADOWS.sm,
    overflowY: 'auto',
  };

  return (
    <div style={containerStyle} data-testid="playbook-gallery">
      <h1 style={{ fontFamily: FONTS.display, fontSize: 24, margin: 0 }}>
        Transformation Playbooks
      </h1>
      <div style={{ color: T.text2, fontSize: FONT_SIZES.sm, marginBottom: 16 }}>
        Curated workflow templates. Each is a markdown runbook for a common
        multi-phase initiative.
      </div>

      {loading && <div style={{ color: T.text2 }}>Loading playbooks...</div>}
      {error && (
        <div
          role="alert"
          style={{
            color: T.cherry,
            border: `2px solid ${T.cherry}`,
            padding: 8,
            borderRadius: 4,
          }}
        >
          {error}
        </div>
      )}

      {!loading && playbooks.length === 0 && (
        <div
          data-testid="playbook-empty"
          style={{ color: T.text2, padding: 24, textAlign: 'center' }}
        >
          No playbooks installed yet. Drop markdown files into{' '}
          <code>templates/playbooks/</code> to populate this gallery.
        </div>
      )}

      {playbooks.length > 0 && (
        <div style={layoutStyle}>
          <div style={listStyle} data-testid="playbook-list">
            {playbooks.map((p) => (
              <button
                key={p.slug}
                type="button"
                onClick={() => setActiveSlug(p.slug)}
                style={itemStyle(p.slug === activeSlug)}
                data-testid="playbook-item"
              >
                {p.title}
              </button>
            ))}
          </div>
          <div style={detailStyle} data-testid="playbook-detail">
            {active ? (
              <>
                <h2 style={{ marginTop: 0, fontFamily: FONTS.display }}>
                  {active.title}
                </h2>
                <pre
                  style={{
                    fontFamily: FONTS.mono,
                    fontSize: FONT_SIZES.sm,
                    whiteSpace: 'pre-wrap',
                    background: T.bg1,
                    padding: 12,
                    borderRadius: 4,
                    border: `1px solid ${T.borderSoft}`,
                  }}
                >
                  {active.body}
                </pre>
              </>
            ) : (
              <div style={{ color: T.text2 }}>Select a playbook to view its body.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default PlaybookGallery;
