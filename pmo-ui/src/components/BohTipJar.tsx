import { useState } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';

// ── Types ──────────────────────────────────────────────────────────────────

type Channel = 'desktop' | 'slack' | 'email';

interface NotifPref {
  desktop: boolean;
  slack: boolean;
  email: boolean;
}

interface NotifEvent {
  k: string;
  label: string;
  pri: 'high' | 'medium' | 'low';
}

// ── Constants ──────────────────────────────────────────────────────────────

const NOTIF_EVENTS: NotifEvent[] = [
  { k: 'gate_pending',  label: 'On the pass — waiting for you',        pri: 'high'   },
  { k: 'step_fail',     label: 'A step burned',                         pri: 'high'   },
  { k: 'cost_alert',    label: 'Running hot on budget',                 pri: 'high'   },
  { k: 'step_complete', label: 'A step plated',                         pri: 'low'    },
  { k: 'recipe_saved',  label: 'A recipe got clipped to the rail',      pri: 'low'    },
  { k: 'delivery_in',   label: 'New delivery out back',                 pri: 'medium' },
];

const CHANNELS: Channel[] = ['desktop', 'slack', 'email'];

const CHANNEL_LABELS: Record<Channel, string> = {
  desktop: 'Desktop',
  slack: 'Slack',
  email: 'Email',
};

// ── Sub-components ─────────────────────────────────────────────────────────

function ToggleSwitch({ on, onChange, tiny = false }: {
  on: boolean;
  onChange: () => void;
  tiny?: boolean;
}) {
  const w = tiny ? 38 : 46;
  const h = tiny ? 22 : 26;
  const knobSize = tiny ? 16 : 20;
  const knobOffset = tiny ? 2 : 3;
  const travel = w - knobSize - knobOffset * 2;

  return (
    <button
      role="switch"
      aria-checked={on}
      onClick={onChange}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        width: w,
        height: h,
        borderRadius: 999,
        background: on ? T.mint : T.crust,
        border: `2px solid ${T.border}`,
        cursor: 'pointer',
        padding: 0,
        position: 'relative',
        flexShrink: 0,
        transition: 'background 0.15s',
      }}
    >
      <span
        style={{
          position: 'absolute',
          left: on ? knobOffset + travel : knobOffset,
          width: knobSize,
          height: knobSize,
          borderRadius: '50%',
          background: T.bg1,
          border: `1.5px solid ${T.border}`,
          transition: 'left 0.15s',
          display: 'block',
        }}
      />
    </button>
  );
}

function PriPill({ pri }: { pri: NotifEvent['pri'] }) {
  const bg = pri === 'high' ? T.cherrySoft : pri === 'medium' ? T.butterSoft : T.bg3;
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 6px',
      borderRadius: 4,
      background: bg,
      border: `1px solid ${T.border}`,
      fontFamily: FONTS.body,
      fontWeight: 800,
      fontSize: 9,
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
      color: T.ink,
      whiteSpace: 'nowrap',
    }}>
      {pri}
    </span>
  );
}

// ── Room Banner ────────────────────────────────────────────────────────────

function RoomBanner({ accent, emoji, title, sub, rightSlot }: {
  accent: string;
  emoji: string;
  title: string;
  sub: string;
  rightSlot?: React.ReactNode;
}) {
  return (
    <div style={{
      background: accent,
      border: `2.5px solid ${T.border}`,
      borderRadius: '14px 14px 0 0',
      padding: '14px 20px',
      display: 'flex',
      alignItems: 'center',
      gap: 14,
    }}>
      <span style={{ fontSize: 32, lineHeight: 1 }}>{emoji}</span>
      <div style={{ flex: 1 }}>
        <div style={{
          fontFamily: FONTS.display,
          fontWeight: 800,
          fontSize: 22,
          color: T.ink,
          lineHeight: 1.1,
        }}>
          {title}
        </div>
        <div style={{
          fontFamily: FONTS.hand,
          fontSize: 15,
          color: T.ink,
          opacity: 0.7,
          lineHeight: 1.2,
        }}>
          {sub}
        </div>
      </div>
      {rightSlot}
    </div>
  );
}

// ── Section Header ─────────────────────────────────────────────────────────

function SectionHeader({ title, rightSlot }: {
  title: string;
  rightSlot?: React.ReactNode;
}) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '10px 16px 6px',
      borderBottom: `1.5px solid ${T.borderSoft}`,
    }}>
      <span style={{
        fontFamily: FONTS.display,
        fontWeight: 800,
        fontSize: 13,
        color: T.text1,
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
      }}>
        {title}
      </span>
      {rightSlot}
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────

export function BohTipJar() {
  const [prefs, setPrefs] = useState<Record<string, NotifPref>>({
    gate_pending:  { desktop: true,  slack: true,  email: true  },
    step_fail:     { desktop: true,  slack: true,  email: false },
    cost_alert:    { desktop: true,  slack: false, email: true  },
    step_complete: { desktop: false, slack: false, email: false },
    recipe_saved:  { desktop: false, slack: true,  email: false },
    delivery_in:   { desktop: true,  slack: false, email: false },
  });

  const [quiet, setQuiet] = useState({ start: '22:00', end: '06:00', on: true });

  function toggle(k: string, ch: Channel) {
    setPrefs(p => ({ ...p, [k]: { ...p[k], [ch]: !p[k][ch] } }));
  }

  const timeInputStyle: React.CSSProperties = {
    background: T.bg1,
    border: `2px solid ${T.border}`,
    borderRadius: 6,
    fontFamily: FONTS.mono,
    fontSize: 13,
    padding: '6px 8px',
    color: T.text0,
    outline: 'none',
    boxShadow: SHADOWS.sm,
  };

  return (
    <div style={{
      background: T.bg1,
      border: `2.5px solid ${T.border}`,
      borderRadius: 14,
      boxShadow: SHADOWS.md,
      overflow: 'hidden',
      fontFamily: FONTS.body,
    }}>
      {/* Banner */}
      <RoomBanner
        accent={T.tangerine}
        emoji="🔔"
        title="The Tip Jar"
        sub="how we holler at you"
      />

      {/* Section 1 — What to yell about */}
      <SectionHeader title="What to yell about" />

      <div style={{ padding: '0 0 4px' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '8px 16px', width: '100%' }} />
              {CHANNELS.map(ch => (
                <th
                  key={ch}
                  style={{
                    padding: '8px 0',
                    width: 80,
                    textAlign: 'center',
                    fontFamily: FONTS.body,
                    fontWeight: 800,
                    fontSize: 10,
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                    color: T.text1,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {CHANNEL_LABELS[ch]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {NOTIF_EVENTS.map((ev, i) => (
              <tr
                key={ev.k}
                style={{
                  background: i % 2 === 0 ? T.bg2 : T.bg3,
                }}
              >
                {/* Label cell */}
                <td style={{ padding: '10px 16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{
                      fontFamily: FONTS.display,
                      fontWeight: 800,
                      fontSize: 15,
                      color: T.ink,
                    }}>
                      {ev.label}
                    </span>
                    <PriPill pri={ev.pri} />
                  </div>
                  <div style={{
                    fontFamily: FONTS.mono,
                    fontSize: 10,
                    color: T.text2,
                    marginTop: 2,
                  }}>
                    {ev.k} · {ev.pri} priority
                  </div>
                </td>

                {/* Toggle cells */}
                {CHANNELS.map(ch => (
                  <td
                    key={ch}
                    style={{
                      width: 80,
                      textAlign: 'center',
                      verticalAlign: 'middle',
                      padding: '10px 0',
                    }}
                  >
                    <ToggleSwitch
                      on={prefs[ev.k][ch]}
                      onChange={() => toggle(ev.k, ch)}
                      tiny
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Section 2 — Quiet hours */}
      <SectionHeader title="Quiet hours — kitchen's closed" />

      <div style={{ padding: 14 }}>
        <div style={{
          background: T.bg2,
          border: `1.5px dashed ${T.border}`,
          borderRadius: 10,
          padding: 14,
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          flexWrap: 'wrap',
        }}>
          <span style={{ fontSize: 32, lineHeight: 1, flexShrink: 0 }}>🌙</span>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <label style={{
              fontFamily: FONTS.body,
              fontWeight: 700,
              fontSize: 13,
              color: T.text1,
            }}>
              From
            </label>
            <input
              type="time"
              value={quiet.start}
              onChange={e => setQuiet(q => ({ ...q, start: e.target.value }))}
              style={timeInputStyle}
              aria-label="Quiet hours start time"
            />
            <label style={{
              fontFamily: FONTS.body,
              fontWeight: 700,
              fontSize: 13,
              color: T.text1,
            }}>
              to
            </label>
            <input
              type="time"
              value={quiet.end}
              onChange={e => setQuiet(q => ({ ...q, end: e.target.value }))}
              style={timeInputStyle}
              aria-label="Quiet hours end time"
            />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 'auto' }}>
            <span style={{
              fontFamily: FONTS.body,
              fontSize: 13,
              fontWeight: 600,
              color: T.text2,
            }}>
              {quiet.on ? 'quiet hours on' : 'always loud'}
            </span>
            <ToggleSwitch
              on={quiet.on}
              onChange={() => setQuiet(q => ({ ...q, on: !q.on }))}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
