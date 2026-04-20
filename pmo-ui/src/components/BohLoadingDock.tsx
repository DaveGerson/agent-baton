import { useState } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Integration {
  id: string;
  name: string;
  sub: string;
  icon: string;
  connected: boolean;
  color: string;
  since?: string;
}

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

const INITIAL_INTEGRATIONS: Integration[] = [
  { id: 'ado',    name: 'Azure DevOps', sub: 'orders from the office', icon: '📦', connected: true,  color: T.blueberry, since: 'Feb 12' },
  { id: 'jira',   name: 'Jira',         sub: 'the supplier catalog',   icon: '📋', connected: true,  color: T.cherry,    since: 'Dec 4'  },
  { id: 'github', name: 'GitHub',       sub: 'the recipe archive',     icon: '🐙', connected: true,  color: T.ink,       since: 'Jan 3'  },
  { id: 'slack',  name: 'Slack',        sub: 'the walkie-talkies',     icon: '💬', connected: true,  color: T.tangerine, since: 'Mar 18' },
  { id: 'linear', name: 'Linear',       sub: 'sharper order tickets',  icon: '📐', connected: false, color: T.text2     },
  { id: 'sentry', name: 'Sentry',       sub: 'kitchen smoke alarm',    icon: '🚨', connected: false, color: T.butter    },
];

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
  const { id, name, sub, icon, connected, color, since } = integration;

  // Use T.cream text for dark backgrounds, T.ink for light ones.
  // The light-bg colors in this list are T.butter and T.text2.
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
      </div>

      {/* Action button */}
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
// BohLoadingDock
// ---------------------------------------------------------------------------

export function BohLoadingDock() {
  const [integrations, setIntegrations] = useState<Integration[]>(INITIAL_INTEGRATIONS);

  function handleToggle(id: string) {
    setIntegrations((prev) =>
      prev.map((i) => (i.id === id ? { ...i, connected: !i.connected } : i))
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
        <span
          aria-hidden="true"
          style={{
            fontSize: 22,
            lineHeight: 1,
          }}
        >
          🚚
        </span>
        <div>
          <div
            style={{
              fontFamily: FONTS.display,
              fontWeight: 900,
              fontSize: 20,
              color: T.mint,
              lineHeight: 1.1,
            }}
          >
            The Loading Dock
          </div>
          <div
            style={{
              fontFamily: FONTS.hand,
              fontSize: 14,
              color: T.text2,
              lineHeight: 1.2,
            }}
          >
            where the trucks pull in
          </div>
        </div>
      </div>

      {/* Body */}
      <div style={{ padding: '16px 18px' }}>
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
          {integrations.map((integration) => (
            <div key={integration.id} role="listitem">
              <IntegrationCard integration={integration} onToggle={handleToggle} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
