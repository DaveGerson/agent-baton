import { useState, useEffect } from 'react';
import { usePersistedState } from '../hooks/usePersistedState';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Integration {
  id: string;
  name: string;
  sub: string;
  icon: string;
  /** User-toggled "enabled" state — stored in localStorage */
  connected: boolean;
  color: string;
  since?: string;
  /**
   * Whether the backend endpoint that backs this integration is reachable.
   * undefined = not yet probed, true = live, false = unreachable.
   */
  liveStatus?: boolean;
  /** PMO endpoint path to HEAD-probe for liveness. Relative to /api/v1/pmo. */
  probeEndpoint?: string;
}

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

const INITIAL_INTEGRATIONS: Integration[] = [
  {
    id: 'ado',
    name: 'Azure DevOps',
    sub: 'orders from the office',
    icon: '📦',
    connected: true,
    color: T.blueberry,
    since: 'Feb 12',
    probeEndpoint: '/ado/search?q=_probe',
  },
  {
    id: 'github',
    name: 'GitHub',
    sub: 'the recipe archive',
    icon: '🐙',
    connected: true,
    color: T.ink,
    since: 'Jan 3',
    probeEndpoint: '/external-items?source=github&status=open',
  },
  {
    id: 'jira',
    name: 'Jira',
    sub: 'the supplier catalog',
    icon: '📋',
    connected: true,
    color: T.cherry,
    since: 'Dec 4',
    probeEndpoint: '/external-items?source=jira&status=open',
  },
  {
    id: 'linear',
    name: 'Linear',
    sub: 'sharper order tickets',
    icon: '📐',
    connected: false,
    color: T.text2,
    probeEndpoint: '/external-items?source=linear&status=open',
  },
  {
    id: 'slack',
    name: 'Slack',
    sub: 'the walkie-talkies',
    icon: '💬',
    connected: true,
    color: T.tangerine,
    since: 'Mar 18',
  },
  {
    id: 'sentry',
    name: 'Sentry',
    sub: 'kitchen smoke alarm',
    icon: '🚨',
    connected: false,
    color: T.butter,
  },
];

// ---------------------------------------------------------------------------
// Connection status badge
// ---------------------------------------------------------------------------

function ConnectionBadge({ live }: { live: boolean | undefined }) {
  if (live === undefined) return null;
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 3,
      background: live ? T.mintSoft : T.bg3,
      border: `1.5px solid ${live ? T.mint : T.borderSoft}`,
      borderRadius: 999,
      fontFamily: FONTS.mono,
      fontWeight: 700,
      fontSize: 9,
      color: live ? T.mintDark : T.text3,
      padding: '2px 7px',
      marginTop: 4,
      letterSpacing: '0.04em',
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: live ? T.mint : T.borderSoft,
        display: 'inline-block',
        flexShrink: 0,
      }} />
      {live ? 'Connected' : 'No endpoint'}
    </span>
  );
}

// ---------------------------------------------------------------------------
// IntegrationCard
// ---------------------------------------------------------------------------

function IntegrationCard({
  integration,
  onToggle,
}: {
  integration: Integration;
  onToggle: (id: string) => void;
}) {
  const { id, name, sub, icon, connected, color, since, liveStatus } = integration;

  const isLightBg = color === T.butter || color === T.text2;
  const iconText = isLightBg ? T.ink : T.cream;

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '14px 12px',
        background: T.bg2,
        border: `2px solid ${T.border}`,
        borderRadius: 12,
        boxShadow: SHADOWS.sm,
        opacity: connected ? 1 : 0.65,
        transition: 'opacity 0.15s',
      }}
    >
      {/* Icon square */}
      <div
        aria-hidden="true"
        style={{
          width: 48,
          height: 48,
          flexShrink: 0,
          borderRadius: 10,
          background: color,
          border: `2px solid ${T.border}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 22,
          color: iconText,
          userSelect: 'none',
        }}
      >
        {icon}
      </div>

      {/* Info */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontFamily: FONTS.display,
            fontWeight: 900,
            fontSize: 17,
            color: T.ink,
            lineHeight: 1.15,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {name}
        </div>
        <div
          style={{
            fontFamily: FONTS.hand,
            fontSize: 15,
            color: T.text2,
            lineHeight: 1.15,
          }}
        >
          {sub}
        </div>
        {connected && since && (
          <div
            style={{
              fontFamily: FONTS.mono,
              fontSize: 10,
              color: T.mint,
              lineHeight: 1,
              marginTop: 4,
            }}
          >
            ● delivering since {since}
          </div>
        )}
        {/* Live endpoint status badge — only for integrations we probed */}
        {'liveStatus' in integration && <ConnectionBadge live={liveStatus} />}
      </div>

      {/* Toggle */}
      <button
        onClick={() => onToggle(id)}
        aria-pressed={connected}
        style={{
          flexShrink: 0,
          background: connected ? T.cherrySoft : T.mintSoft,
          border: `2px solid ${T.border}`,
          borderRadius: 8,
          boxShadow: SHADOWS.sm,
          fontFamily: FONTS.body,
          fontWeight: 800,
          fontSize: 12,
          color: T.ink,
          padding: '6px 12px',
          cursor: 'pointer',
          whiteSpace: 'nowrap',
        }}
      >
        {connected ? 'Cancel' : 'Sign up'}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Webhooks section
// ---------------------------------------------------------------------------

interface WebhookRow {
  id: string;
  url: string;
  events: string[];
  active: boolean;
}

function WebhooksSection({ webhooks, error }: { webhooks: WebhookRow[] | null; error: string | null }) {
  if (error) {
    return (
      <div style={{
        background: T.bg3,
        border: `1.5px dashed ${T.borderSoft}`,
        borderRadius: 10,
        padding: '14px 16px',
        fontFamily: FONTS.body,
        fontSize: 12,
        color: T.text2,
        display: 'flex',
        gap: 8,
        alignItems: 'flex-start',
      }}>
        <span>📭</span>
        <div>
          <strong style={{ color: T.text1 }}>No webhook registry found.</strong>
          <div style={{ marginTop: 3 }}>Register webhooks via <code style={{ fontFamily: FONTS.mono, fontSize: 11 }}>baton webhooks add</code> to see them here.</div>
        </div>
      </div>
    );
  }

  if (!webhooks) {
    return (
      <div style={{
        fontFamily: FONTS.hand, fontSize: 15, color: T.text2, padding: '10px 0',
      }}>
        checking the loading bay…
      </div>
    );
  }

  if (webhooks.length === 0) {
    return (
      <div style={{
        background: T.bg3,
        border: `1.5px dashed ${T.borderSoft}`,
        borderRadius: 10,
        padding: '14px 16px',
        textAlign: 'center',
        fontFamily: FONTS.hand,
        fontSize: 15,
        color: T.text2,
      }}>
        No webhooks registered yet.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {webhooks.map(wh => (
        <div key={wh.id} style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '10px 14px',
          background: T.bg2,
          border: `2px solid ${T.border}`,
          borderRadius: 10,
          boxShadow: SHADOWS.sm,
        }}>
          <span style={{
            width: 8, height: 8, borderRadius: '50%',
            background: wh.active ? T.mint : T.crust,
            border: `1.5px solid ${T.border}`,
            flexShrink: 0,
            display: 'inline-block',
          }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontFamily: FONTS.mono,
              fontSize: 11,
              color: T.ink,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}>
              {wh.url}
            </div>
            <div style={{
              fontFamily: FONTS.body,
              fontSize: 10,
              color: T.text2,
              marginTop: 2,
            }}>
              {wh.events.join(', ')}
            </div>
          </div>
          <span style={{
            fontFamily: FONTS.body,
            fontWeight: 800,
            fontSize: 9,
            textTransform: 'uppercase',
            color: wh.active ? T.mintDark : T.text3,
            letterSpacing: '0.05em',
          }}>
            {wh.active ? 'active' : 'paused'}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BohLoadingDock
// ---------------------------------------------------------------------------

export function BohLoadingDock() {
  const [integrations, setIntegrations] = usePersistedState<Integration[]>(
    'pmo:boh-integrations',
    INITIAL_INTEGRATIONS,
    localStorage,
  );

  const [webhooks, setWebhooks] = useState<WebhookRow[] | null>(null);
  const [webhookError, setWebhookError] = useState<string | null>(null);

  // Probe each integration's endpoint and update liveStatus
  useEffect(() => {
    let cancelled = false;
    const toProbe = INITIAL_INTEGRATIONS.filter(i => i.probeEndpoint);
    Promise.all(
      toProbe.map(async i => ({
        id: i.id,
        live: await api.checkEndpoint(i.probeEndpoint!),
      }))
    ).then(results => {
      if (cancelled) return;
      setIntegrations(prev =>
        prev.map(i => {
          const result = results.find(r => r.id === i.id);
          return result !== undefined ? { ...i, liveStatus: result.live } : i;
        })
      );
    });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch webhooks
  useEffect(() => {
    let cancelled = false;
    api.getWebhooks()
      .then(res => {
        if (!cancelled) setWebhooks(res.webhooks);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setWebhookError(err instanceof Error ? err.message : String(err));
          setWebhooks([]);
        }
      });
    return () => { cancelled = true; };
  }, []);

  function handleToggle(id: string) {
    setIntegrations(prev =>
      prev.map(i => (i.id === id ? { ...i, connected: !i.connected } : i))
    );
  }

  return (
    <div
      style={{
        fontFamily: FONTS.body,
        background: T.bg1,
        border: `2px solid ${T.border}`,
        borderRadius: 16,
        boxShadow: SHADOWS.md,
        overflow: 'hidden',
      }}
    >
      {/* Room banner */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '14px 18px',
          borderBottom: `2px solid ${T.border}`,
          background: T.mintSoft,
        }}
      >
        <span aria-hidden="true" style={{ fontSize: 22, lineHeight: 1 }}>🚚</span>
        <div>
          <div style={{
            fontFamily: FONTS.display,
            fontWeight: 900,
            fontSize: 20,
            color: T.mint,
            lineHeight: 1.1,
          }}>
            The Loading Dock
          </div>
          <div style={{
            fontFamily: FONTS.hand,
            fontSize: 14,
            color: T.text2,
            lineHeight: 1.2,
          }}>
            where the trucks pull in
          </div>
        </div>
      </div>

      {/* Body */}
      <div style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* Integrations section */}
        <section aria-label="Integrations">
          <div
            style={{
              fontFamily: FONTS.display,
              fontWeight: 900,
              fontSize: 13,
              color: T.text1,
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
              marginBottom: 10,
            }}
          >
            Suppliers — your integrations
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, 1fr)',
              gap: 8,
            }}
            role="list"
            aria-label="Integrations"
          >
            {integrations.map(integration => (
              <div key={integration.id} role="listitem">
                <IntegrationCard integration={integration} onToggle={handleToggle} />
              </div>
            ))}
          </div>
        </section>

        {/* Webhooks section */}
        <section aria-label="Webhooks">
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 10,
          }}>
            <div style={{
              fontFamily: FONTS.display,
              fontWeight: 900,
              fontSize: 13,
              color: T.text1,
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
            }}>
              Inbound Deliveries — webhooks
            </div>
          </div>
          <WebhooksSection webhooks={webhooks} error={webhookError} />
        </section>
      </div>
    </div>
  );
}
