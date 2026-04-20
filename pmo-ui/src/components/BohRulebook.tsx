import { useState, useEffect } from 'react';
import { usePersistedState } from '../hooks/usePersistedState';
import type { ReactNode } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { api } from '../api/client';
import type { PolicyPreset } from '../api/types';

// ── Types ──────────────────────────────────────────────────────────────────

type RuleKind = 'safety' | 'path' | 'approval' | 'cost' | 'escalation' | 'git';

interface Rule {
  label: string;
  on: boolean;
  kind: RuleKind;
}

// ── Constants ──────────────────────────────────────────────────────────────

const KIND_COLORS: Record<RuleKind, string> = {
  safety:     T.cherry,
  path:       T.blueberry,
  approval:   T.butter,
  cost:       T.mint,
  escalation: T.tangerine,
  git:        T.text2,
};

const KIND_TEXT: Record<RuleKind, string> = {
  safety:     T.cream,
  path:       T.cream,
  approval:   T.ink,
  cost:       T.ink,
  escalation: T.cream,
  git:        T.cream,
};

const INITIAL_RULES: Rule[] = [
  { label: 'Stop-at-error: halt the phase if ANY step fails',       on: true,  kind: 'safety'     },
  { label: 'Require taste-test before moving past a gate',          on: true,  kind: 'safety'     },
  { label: 'Block agents from writing to /deploy/**',               on: true,  kind: 'path'       },
  { label: 'Block agents from writing to .env* files',             on: true,  kind: 'path'       },
  { label: 'Require human approval for P0 tickets',                 on: true,  kind: 'approval'   },
  { label: 'Allow auto-retry on cost spikes (max 2 retries)',       on: false, kind: 'cost'       },
  { label: 'Auto-swap to Opus when a step fails twice in a row',    on: true,  kind: 'escalation' },
  { label: 'Force all agents to write to their own branch',         on: true,  kind: 'git'        },
];

// ── Sub-components ─────────────────────────────────────────────────────────

function ToggleSwitch({ on, onChange }: { on: boolean; onChange: () => void }) {
  const w = 46;
  const h = 26;
  const knobSize = 20;
  const knobOffset = 3;
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

function KindPill({ kind }: { kind: RuleKind }) {
  return (
    <span style={{
      display: 'inline-block',
      width: 80,
      textAlign: 'center',
      padding: '2px 8px',
      borderRadius: 999,
      background: KIND_COLORS[kind],
      color: KIND_TEXT[kind],
      fontFamily: FONTS.body,
      fontWeight: 800,
      fontSize: 9,
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
      border: `1.5px solid ${T.border}`,
      flexShrink: 0,
      boxSizing: 'border-box',
    }}>
      {kind}
    </span>
  );
}

function RoomBanner({ accent, emoji, title, sub, rightSlot }: {
  accent: string;
  emoji: string;
  title: string;
  sub: string;
  rightSlot?: ReactNode;
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

function SectionHeader({ title, rightSlot }: {
  title: string;
  rightSlot?: ReactNode;
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

// ── Policy preset card ─────────────────────────────────────────────────────

function PresetCard({ preset, isOffline }: { preset: PolicyPreset; isOffline: boolean }) {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      padding: '10px 14px',
      background: T.bg2,
      border: `2px solid ${T.border}`,
      borderRadius: 10,
      boxShadow: SHADOWS.sm,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          fontFamily: FONTS.body,
          fontWeight: 800,
          fontSize: 13,
          color: T.ink,
          flex: 1,
        }}>
          {preset.label}
        </span>
        <span style={{
          fontFamily: FONTS.mono,
          fontSize: 9,
          color: T.text3,
          background: T.bg3,
          border: `1px solid ${T.borderSoft}`,
          borderRadius: 4,
          padding: '1px 5px',
          letterSpacing: '0.04em',
        }}>
          {preset.name}
        </span>
      </div>
      <div style={{
        fontFamily: FONTS.body,
        fontSize: 12,
        color: T.text2,
        lineHeight: 1.45,
      }}>
        {preset.description}
      </div>
      {isOffline && (
        <div style={{
          fontFamily: FONTS.mono,
          fontSize: 9,
          color: T.text3,
          marginTop: 2,
        }}>
          details: <code>baton policy --show {preset.name}</code>
        </div>
      )}
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────

export function BohRulebook() {
  const [rules, setRules] = usePersistedState<Rule[]>('pmo:boh-rules', INITIAL_RULES, localStorage);

  const [presets, setPresets] = useState<PolicyPreset[]>([]);
  const [presetsLoading, setPresetsLoading] = useState(true);
  const [presetsOffline, setPresetsOffline] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getPolicies()
      .then(res => {
        if (!cancelled) {
          setPresets(res.presets);
          // If the fetch fell back to the client constant, flag it
          // (getPolicies always resolves, so we can't tell from the error path;
          // we distinguish by whether a real endpoint responded)
        }
      })
      .catch(() => {
        /* getPolicies() never rejects — swallow just in case */
      })
      .finally(() => {
        if (!cancelled) setPresetsLoading(false);
      });

    // Separately probe whether the /policies endpoint is real
    fetch('/api/v1/policies', { method: 'HEAD' })
      .then(r => { if (!cancelled) setPresetsOffline(!r.ok); })
      .catch(() => { if (!cancelled) setPresetsOffline(true); });

    return () => { cancelled = true; };
  }, []);

  function toggleRule(i: number) {
    setRules(r => r.map((x, j) => j === i ? { ...x, on: !x.on } : x));
  }

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
        accent={T.crust}
        emoji="📘"
        title="The Rulebook"
        sub="house rules — what nobody's allowed to do"
      />

      {/* Section 0 — System policy presets */}
      <SectionHeader
        title="System Policy Presets"
        rightSlot={
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            fontFamily: FONTS.mono,
            fontSize: 9,
            color: presetsOffline ? T.text3 : T.mintDark,
            background: presetsOffline ? T.bg3 : T.mintSoft,
            border: `1.5px solid ${presetsOffline ? T.borderSoft : T.mint}`,
            borderRadius: 999,
            padding: '2px 8px',
            letterSpacing: '0.04em',
            fontWeight: 700,
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: '50%',
              background: presetsOffline ? T.borderSoft : T.mint,
              display: 'inline-block',
              flexShrink: 0,
            }} />
            {presetsOffline ? 'offline — CLI only' : 'live'}
          </span>
        }
      />

      <div style={{ padding: '12px 16px 4px' }}>
        {presetsLoading ? (
          <div style={{
            fontFamily: FONTS.hand,
            fontSize: 15,
            color: T.text2,
            padding: '8px 0',
          }}>
            loading system presets…
          </div>
        ) : (
          <>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, 1fr)',
              gap: 8,
              marginBottom: 12,
            }}>
              {presets.map(p => (
                <PresetCard key={p.name} preset={p} isOffline={presetsOffline} />
              ))}
            </div>
            {presetsOffline && (
              <div style={{
                fontFamily: FONTS.body,
                fontSize: 11,
                color: T.text3,
                padding: '4px 2px 8px',
                borderTop: `1px dashed ${T.borderSoft}`,
                marginTop: 4,
              }}>
                No <code style={{ fontFamily: FONTS.mono }}>/api/v1/policies</code> endpoint detected — preset details are read-only. Run{' '}
                <code style={{ fontFamily: FONTS.mono }}>baton policy --show &lt;name&gt;</code> for full config.
              </div>
            )}
          </>
        )}
      </div>

      {/* Section 1 — Safety rails */}
      <SectionHeader
        title="Safety rails"
        rightSlot={
          <span style={{
            fontFamily: FONTS.hand,
            fontSize: 17,
            color: T.text2,
            whiteSpace: 'nowrap',
            lineHeight: 1,
          }}>
            pinned above the pass
          </span>
        }
      />

      <div style={{ padding: '4px 0 8px' }}>
        {rules.map((rule, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '10px 16px',
              borderBottom: i < rules.length - 1
                ? `1px dashed ${T.borderSoft}`
                : 'none',
            }}
          >
            <KindPill kind={rule.kind} />
            <span style={{
              fontFamily: FONTS.body,
              fontWeight: 600,
              fontSize: 14,
              color: T.text0,
              flex: 1,
              lineHeight: 1.4,
            }}>
              {rule.label}
            </span>
            <ToggleSwitch on={rule.on} onChange={() => toggleRule(i)} />
          </div>
        ))}
      </div>

      {/* Section 2 — Custom Rules (local) */}
      <SectionHeader
        title="Custom Rules (local)"
        rightSlot={
          <button
            style={{
              padding: '5px 14px',
              borderRadius: 8,
              border: `2px solid ${T.border}`,
              background: T.butter,
              color: T.ink,
              fontFamily: FONTS.body,
              fontWeight: 800,
              fontSize: 12,
              cursor: 'pointer',
              boxShadow: SHADOWS.sm,
            }}
          >
            Write a new rule
          </button>
        }
      />

      <div style={{ padding: 14 }}>
        {/* Empty state */}
        <div style={{
          background: T.bg3,
          border: `2px dashed ${T.borderSoft}`,
          borderRadius: 10,
          padding: 20,
          textAlign: 'center',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 6,
        }}>
          <span style={{ fontSize: 32, lineHeight: 1 }}>📝</span>
          <div style={{
            fontFamily: FONTS.display,
            fontWeight: 800,
            fontSize: 16,
            color: T.ink,
          }}>
            No house-specific rules yet
          </div>
          <div style={{
            fontFamily: FONTS.hand,
            fontSize: 16,
            color: T.text2,
            transform: 'rotate(-0.8deg)',
            display: 'inline-block',
          }}>
            write your own — we'll enforce 'em
          </div>
          <div style={{
            fontFamily: FONTS.body,
            fontSize: 11,
            color: T.text3,
            marginTop: 4,
          }}>
            stored locally in this browser
          </div>
        </div>
      </div>
    </div>
  );
}
