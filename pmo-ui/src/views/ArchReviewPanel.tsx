import { useEffect, useState, useCallback } from 'react';
import type { CSSProperties } from 'react';
import { T, FONTS, FONT_SIZES, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import type { ArchBead } from '../api/types';

/**
 * H3.7 — Architectural Review Panel.
 *
 * Lists open beads of type `architecture` or `decision` and exposes
 * approve / reject buttons. Each click files a follow-up bead via
 * `POST /pmo/arch-beads/{id}/review`. The original bead is left intact
 * to preserve the audit trail.
 */
export function ArchReviewPanel() {
  const [beads, setBeads] = useState<ArchBead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [decided, setDecided] = useState<Record<string, string>>({});

  const refresh = useCallback(() => {
    setLoading(true);
    api
      .listArchBeads('open')
      .then(setBeads)
      .catch((err) => setError(String(err.message ?? err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleReview(beadId: string, action: 'approve' | 'reject') {
    setActing(beadId);
    try {
      const reason = action === 'reject'
        ? window.prompt('Reason for rejection (required):') ?? ''
        : '';
      if (action === 'reject' && !reason.trim()) {
        setActing(null);
        return;
      }
      await api.reviewArchBead(beadId, { action, reason, reviewer: 'pmo-ui' });
      setDecided((d) => ({ ...d, [beadId]: action }));
    } catch (err) {
      setError(String((err as Error).message ?? err));
    } finally {
      setActing(null);
    }
  }

  const containerStyle: CSSProperties = {
    padding: 16,
    background: T.bg0,
    color: T.text0,
    fontFamily: FONTS.body,
  };

  const itemStyle: CSSProperties = {
    background: T.cream,
    border: `2px solid ${T.border}`,
    borderRadius: 6,
    padding: 12,
    marginBottom: 12,
    boxShadow: SHADOWS.sm,
  };

  const buttonStyle = (color: string): CSSProperties => ({
    background: color,
    color: T.cream,
    border: `2px solid ${T.border}`,
    borderRadius: 4,
    padding: '6px 14px',
    fontWeight: 700,
    cursor: 'pointer',
    fontFamily: FONTS.body,
    fontSize: FONT_SIZES.sm,
  });

  return (
    <div style={containerStyle} data-testid="arch-review-panel">
      <h1 style={{ fontFamily: FONTS.display, fontSize: 24, margin: 0 }}>
        Architectural Review
      </h1>
      <div style={{ color: T.text2, fontSize: FONT_SIZES.sm, marginBottom: 16 }}>
        Open architecture and decision beads awaiting a review verdict.
      </div>

      {loading && <div style={{ color: T.text2 }}>Loading open beads...</div>}
      {error && (
        <div
          role="alert"
          style={{
            color: T.cherry,
            border: `2px solid ${T.cherry}`,
            padding: 8,
            borderRadius: 4,
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      )}

      {!loading && beads.length === 0 && (
        <div
          data-testid="arch-empty"
          style={{ color: T.text2, padding: 24, textAlign: 'center' }}
        >
          Nothing awaiting architectural review. New beads of type
          <code> architecture </code> or <code> decision </code> will appear
          here.
        </div>
      )}

      {beads.map((b) => {
        const verdict = decided[b.bead_id];
        return (
          <div key={b.bead_id} style={itemStyle} data-testid="arch-bead-item">
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'baseline',
                marginBottom: 6,
              }}
            >
              <div style={{ fontWeight: 700, fontSize: FONT_SIZES.md }}>
                {b.bead_id} · <span style={{ color: T.blueberry }}>{b.bead_type}</span>
              </div>
              <div style={{ fontSize: FONT_SIZES.xs, color: T.text3 }}>
                {b.created_at}
              </div>
            </div>
            <div style={{ fontSize: FONT_SIZES.sm, color: T.text1, marginBottom: 8 }}>
              {b.content}
            </div>
            {b.affected_files.length > 0 && (
              <div
                style={{
                  fontSize: FONT_SIZES.xs,
                  color: T.text2,
                  fontFamily: FONTS.mono,
                  marginBottom: 8,
                }}
              >
                Files: {b.affected_files.join(', ')}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                type="button"
                style={buttonStyle(T.mint)}
                disabled={!!verdict || acting === b.bead_id}
                onClick={() => handleReview(b.bead_id, 'approve')}
                data-testid="arch-approve"
              >
                Approve
              </button>
              <button
                type="button"
                style={buttonStyle(T.cherry)}
                disabled={!!verdict || acting === b.bead_id}
                onClick={() => handleReview(b.bead_id, 'reject')}
                data-testid="arch-reject"
              >
                Reject
              </button>
              {verdict && (
                <span
                  style={{
                    color: verdict === 'approve' ? T.mintDark : T.cherryDark,
                    alignSelf: 'center',
                    fontWeight: 700,
                  }}
                >
                  {verdict === 'approve' ? 'Approved' : 'Rejected'}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default ArchReviewPanel;
