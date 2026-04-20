import { useState } from 'react';
import type { ReactNode } from 'react';
import { usePersistedState } from '../hooks/usePersistedState';
import { T, FONTS, SHADOWS } from '../styles/tokens';

// ─── Shared primitives ────────────────────────────────────────────────────────

function RoomBanner({
  emoji,
  title,
  sub,
  accent,
}: {
  emoji: string;
  title: string;
  sub: string;
  accent: string;
}) {
  // Light accents (butter/mint/crust/cream) get ink text; dark accents get cream
  const darkAccents: string[] = [T.blueberry, T.ink, T.cherry, T.tangerine];
  const textColor = darkAccents.includes(accent) ? T.cream : T.ink;
  return (
    <div
      style={{
        background: accent,
        border: `2px solid ${T.border}`,
        borderBottom: 'none',
        borderRadius: '12px 12px 0 0',
        padding: '14px 20px',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
      }}
    >
      <span style={{ fontSize: 32, lineHeight: 1 }}>{emoji}</span>
      <div>
        <div
          style={{
            fontFamily: FONTS.display,
            fontWeight: 900,
            fontSize: 26,
            color: textColor,
            lineHeight: 1.15,
          }}
        >
          {title}
        </div>
        <div
          style={{
            fontFamily: FONTS.hand,
            fontSize: 16,
            color: textColor,
            opacity: 0.8,
            transform: 'rotate(-0.5deg)',
            display: 'inline-block',
            marginTop: 1,
          }}
        >
          {sub}
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  right,
  children,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      style={{
        background: T.bg1,
        border: `2px solid ${T.border}`,
        borderRadius: 12,
        boxShadow: SHADOWS.sm,
        overflow: 'hidden',
      }}
    >
      {/* Section header */}
      <div
        style={{
          background: T.bg3,
          padding: '10px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: `1.5px solid ${T.border}`,
        }}
      >
        <span
          style={{
            fontFamily: FONTS.display,
            fontWeight: 900,
            fontSize: 15,
            color: T.text0,
          }}
        >
          {title}
        </span>
        {right && <div>{right}</div>}
      </div>
      <div style={{ padding: '14px 16px' }}>{children}</div>
    </div>
  );
}

// ─── Model card ──────────────────────────────────────────────────────────────

type ModelTier = 'haiku' | 'sonnet' | 'opus';

const MODEL_META: Record<
  ModelTier,
  { color: string; tagline: string; useCase: string }
> = {
  haiku: {
    color: T.mint,
    tagline: 'quick hands',
    useCase: 'Classification, routing, fast one-liners',
  },
  sonnet: {
    color: T.blueberry,
    tagline: 'steady hands',
    useCase: 'Balanced — most tasks, code, analysis',
  },
  opus: {
    color: T.cherry,
    tagline: 'master hands',
    useCase: 'Complex reasoning, architecture, review',
  },
};

function ModelCard({
  tier,
  selected,
  onClick,
}: {
  tier: ModelTier;
  selected: boolean;
  onClick: () => void;
  key?: string;
}) {
  const [hovered, setHovered] = useState(false);
  const meta = MODEL_META[tier];
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      aria-pressed={selected}
      style={{
        background: selected ? meta.color : T.bg3,
        color: selected ? T.cream : T.text0,
        border: `2px solid ${T.border}`,
        borderRadius: 12,
        padding: 14,
        cursor: 'pointer',
        textAlign: 'left',
        boxShadow: selected ? SHADOWS.md : hovered ? SHADOWS.sm : 'none',
        transform: selected
          ? 'translate(-1px,-1px)'
          : hovered
          ? 'translate(-0.5px,-0.5px)'
          : 'none',
        transition:
          'transform 0.12s ease, box-shadow 0.12s ease, background 0.12s ease',
        fontFamily: FONTS.body,
      }}
    >
      <div
        style={{
          fontFamily: FONTS.display,
          fontWeight: 900,
          fontSize: 22,
          lineHeight: 1.1,
          textTransform: 'capitalize',
          color: selected ? T.cream : T.text0,
        }}
      >
        {tier}
      </div>
      <div
        style={{
          fontFamily: FONTS.hand,
          fontSize: 17,
          transform: 'rotate(-0.8deg)',
          display: 'inline-block',
          marginTop: 3,
          color: selected ? T.cream : T.text2,
          opacity: 0.9,
        }}
      >
        {meta.tagline}
      </div>
      <div
        style={{
          fontFamily: FONTS.body,
          fontSize: 11,
          fontWeight: 700,
          marginTop: 6,
          opacity: 0.9,
          color: selected ? T.cream : T.text1,
          lineHeight: 1.3,
        }}
      >
        {meta.useCase}
      </div>
    </button>
  );
}

// ─── Key status chip ──────────────────────────────────────────────────────────

type KeyStatus = 'fresh' | 'warm';

function StatusChip({ status }: { status: KeyStatus }) {
  const bg = status === 'fresh' ? T.mintSoft : T.butterSoft;
  const label = status === 'fresh' ? '❄ fresh' : '⚠ getting warm';
  const borderColor = status === 'fresh' ? T.mint : T.butter;
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 3,
        padding: '2px 8px',
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 700,
        background: bg,
        border: `1.5px solid ${borderColor}`,
        color: T.text0,
        fontFamily: FONTS.body,
        flexShrink: 0,
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </span>
  );
}

// ─── Ghost rotate button ──────────────────────────────────────────────────────

function RotateButton({ onClick }: { onClick: () => void }) {
  const [hov, setHov] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{
        padding: '3px 10px',
        borderRadius: 8,
        border: `1.5px dashed ${T.border}`,
        background: 'transparent',
        color: T.text1,
        fontSize: 11,
        fontWeight: 800,
        fontFamily: FONTS.body,
        cursor: 'pointer',
        boxShadow: hov ? SHADOWS.sm : 'none',
        transform: hov ? 'translate(-1px,-1px)' : 'none',
        transition: 'transform 0.1s ease, box-shadow 0.1s ease',
        flexShrink: 0,
      }}
    >
      Rotate
    </button>
  );
}

// ─── Main export ──────────────────────────────────────────────────────────────

interface ApiKey {
  id: string;
  label: string;
  masked: string;
  status: KeyStatus;
  rotated: string;
}

export function BohWalkIn() {
  const [preferredModel, setPreferredModel] = usePersistedState<'haiku' | 'sonnet' | 'opus'>('pmo:boh-preferred-model', 'sonnet', localStorage);
  const [keys, setKeys] = usePersistedState<ApiKey[]>('pmo:boh-api-keys', [
    {
      id: 'anthropic',
      label: 'Anthropic',
      masked: 'sk-ant-•••••••••••••4f2a',
      status: 'fresh',
      rotated: '3 days ago',
    },
    {
      id: 'openai',
      label: 'OpenAI',
      masked: 'sk-•••••••••••••8c21',
      status: 'fresh',
      rotated: '12 days ago',
    },
    {
      id: 'bedrock',
      label: 'AWS Bedrock',
      masked: 'AKIA••••••••••••',
      status: 'warm',
      rotated: '45 days ago',
    },
  ]);

  function handleRotate(id: string) {
    setKeys((ks: ApiKey[]) =>
      ks.map((k: ApiKey) =>
        k.id === id ? { ...k, status: 'fresh' as KeyStatus, rotated: 'just now' } : k,
      ),
    );
  }

  return (
    <div
      style={{
        fontFamily: FONTS.body,
        color: T.text0,
        maxWidth: 660,
        margin: '0 auto',
      }}
    >
      {/* Banner */}
      <RoomBanner
        emoji="🧊"
        title="The Walk-In"
        sub="cold storage — creds & models"
        accent={T.blueberry}
      />

      {/* Body */}
      <div
        style={{
          background: T.bg0,
          border: `2px solid ${T.border}`,
          borderTop: 'none',
          borderRadius: '0 0 12px 12px',
          padding: 20,
          display: 'flex',
          flexDirection: 'column',
          gap: 20,
        }}
      >
        {/* Section 1: Model tiers */}
        <Section title="Model Tier">
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: 12,
            }}
          >
            {(['haiku', 'sonnet', 'opus'] as ModelTier[]).map((tier: ModelTier) => (
              <ModelCard
                key={tier}
                tier={tier}
                selected={preferredModel === tier}
                onClick={() => { setPreferredModel(tier); }}
              />
            ))}
          </div>
          <div
            style={{
              fontFamily: FONTS.hand,
              fontSize: 15,
              color: T.text2,
              marginTop: 10,
              transform: 'rotate(-0.3deg)',
              display: 'inline-block',
            }}
          >
            house default — used when the recipe doesn't specify
          </div>
        </Section>

        {/* Section 2: API keys "kept on ice" */}
        <Section
          title="API Keys — kept on ice"
          right={
            <button
              style={{
                padding: '4px 12px',
                borderRadius: 8,
                border: `2px solid ${T.border}`,
                background: T.butter,
                color: T.ink,
                fontSize: 11,
                fontWeight: 800,
                fontFamily: FONTS.body,
                cursor: 'pointer',
                boxShadow: SHADOWS.sm,
              }}
            >
              + Add a new key
            </button>
          }
        >
          <div>
            {keys.map((apiKey: ApiKey, i: number) => (
              <div
                key={apiKey.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '10px 0',
                  borderBottom:
                    i < keys.length - 1
                      ? `1.5px dashed ${T.borderSoft}`
                      : 'none',
                }}
              >
                {/* Icon */}
                <div
                  aria-hidden="true"
                  style={{
                    width: 44,
                    height: 44,
                    borderRadius: 10,
                    background: T.ink,
                    color: T.cream,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 20,
                    flexShrink: 0,
                    border: `1.5px solid ${T.border}`,
                  }}
                >
                  🔒
                </div>

                {/* Label + masked key + rotated */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontFamily: FONTS.display,
                      fontWeight: 900,
                      fontSize: 17,
                      color: T.text0,
                      lineHeight: 1.2,
                    }}
                  >
                    {apiKey.label}
                  </div>
                  <div
                    style={{
                      fontFamily: FONTS.mono,
                      fontSize: 11,
                      color: T.text2,
                      marginTop: 2,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {apiKey.masked}
                  </div>
                  <div
                    style={{
                      fontFamily: FONTS.hand,
                      fontSize: 13,
                      color: T.text2,
                      marginTop: 1,
                    }}
                  >
                    rotated {apiKey.rotated}
                  </div>
                </div>

                {/* Status chip */}
                <StatusChip status={apiKey.status} />

                {/* Rotate button */}
                <RotateButton onClick={() => handleRotate(apiKey.id)} />
              </div>
            ))}
          </div>
        </Section>
      </div>
    </div>
  );
}
