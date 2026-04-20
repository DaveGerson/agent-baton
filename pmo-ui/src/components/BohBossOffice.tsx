import { usePersistedState } from '../hooks/usePersistedState';
import type { ReactNode, ChangeEvent } from 'react';
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

function ToggleSwitch({
  on,
  onChange,
  label,
}: {
  on: boolean;
  onChange: () => void;
  label?: string;
}) {
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onChange}
      style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        width: 44,
        height: 24,
        borderRadius: 999,
        border: `2px solid ${T.border}`,
        background: on ? T.mint : T.bg3,
        cursor: 'pointer',
        padding: 0,
        flexShrink: 0,
        transition: 'background 0.15s ease',
        boxShadow: on ? SHADOWS.sm : 'none',
      }}
    >
      <span
        aria-hidden="true"
        style={{
          position: 'absolute',
          left: on ? 22 : 2,
          width: 16,
          height: 16,
          borderRadius: '50%',
          background: on ? T.cream : T.text3,
          border: `1.5px solid ${T.border}`,
          transition: 'left 0.15s ease, background 0.15s ease',
          display: 'block',
        }}
      />
    </button>
  );
}

// ─── Budget Dial ──────────────────────────────────────────────────────────────

interface BudgetDialProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (v: number) => void;
  spent: number;
}

function BudgetDial({
  label,
  value,
  min,
  max,
  step,
  unit,
  onChange,
  spent,
}: BudgetDialProps) {
  const pct = Math.min(100, Math.round((spent / value) * 100));
  const fillColor = pct > 90 ? T.cherry : pct > 75 ? T.butter : T.mint;

  return (
    <div
      style={{
        background: T.bg3,
        border: `1.5px dashed ${T.border}`,
        borderRadius: 10,
        padding: 14,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      {/* Label */}
      <div
        style={{
          fontFamily: FONTS.body,
          fontWeight: 800,
          fontSize: 11,
          textTransform: 'uppercase' as const,
          letterSpacing: '0.08em',
          color: T.text1,
        }}
      >
        {label}
      </div>

      {/* Value display */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span
          style={{
            fontFamily: FONTS.display,
            fontWeight: 900,
            fontSize: 30,
            color: T.text0,
            lineHeight: 1,
          }}
        >
          {unit}{value.toLocaleString()}
        </span>
        <span
          style={{
            fontFamily: FONTS.mono,
            fontSize: 12,
            color: T.text2,
          }}
        >
          · spent {unit}{spent.toLocaleString()}
        </span>
      </div>

      {/* Range slider */}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(Number(e.target.value))}
        aria-label={`${label} cap`}
        style={{ width: '100%', accentColor: T.mint, cursor: 'pointer' }}
      />

      {/* Progress bar */}
      <div
        style={{
          position: 'relative',
          height: 10,
          background: T.bg1,
          border: `1.5px solid ${T.border}`,
          borderRadius: 999,
          overflow: 'hidden',
        }}
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${label} usage`}
      >
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            bottom: 0,
            width: `${pct}%`,
            background: fillColor,
            borderRadius: 999,
            transition: 'width 0.3s ease, background 0.3s ease',
          }}
        />
      </div>

      <div
        style={{
          fontFamily: FONTS.mono,
          fontSize: 11,
          color: pct > 90 ? T.cherry : pct > 75 ? T.text1 : T.text2,
          fontWeight: pct > 90 ? 700 : 400,
        }}
      >
        {pct}% used
      </div>
    </div>
  );
}

// ─── Main export ──────────────────────────────────────────────────────────────

export function BohBossOffice() {
  const [monthCap, setMonthCap] = usePersistedState<number>('pmo:boh-month-cap', 800, localStorage);
  const [perTicket, setPerTicket] = usePersistedState<number>('pmo:boh-per-ticket-cap', 5, localStorage);
  const [alertAt, setAlertAt] = usePersistedState<number>('pmo:boh-alert-threshold', 75, localStorage);
  const [freezeOn, setFreezeOn] = usePersistedState<boolean>('pmo:boh-freeze-on', true, localStorage);

  const SPENT_MONTH = 412;
  const SPENT_TICKET = 1.18;

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
        emoji="💰"
        title="The Boss's Office"
        sub="who's paying for all these pies?"
        accent={T.butter}
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
        {/* Section 1: Spending caps */}
        <Section title="Spending Caps">
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, 1fr)',
              gap: 14,
            }}
          >
            <BudgetDial
              label="Monthly Cap"
              value={monthCap}
              min={100}
              max={5000}
              step={50}
              unit="$"
              onChange={setMonthCap}
              spent={SPENT_MONTH}
            />
            <BudgetDial
              label="Per-Ticket Ceiling"
              value={perTicket}
              min={1}
              max={100}
              step={0.5}
              unit="$"
              onChange={setPerTicket}
              spent={SPENT_TICKET}
            />
          </div>
        </Section>

        {/* Section 2: When to ring the bell */}
        <Section title="When to Ring the Bell">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* Alert threshold */}
            <div
              style={{
                background: T.bg3,
                border: `1.5px dashed ${T.border}`,
                borderRadius: 10,
                padding: '10px 12px',
              }}
            >
              <div
                style={{
                  fontFamily: FONTS.body,
                  fontWeight: 800,
                  fontSize: 11,
                  textTransform: 'uppercase' as const,
                  letterSpacing: '0.08em',
                  color: T.text1,
                  marginBottom: 8,
                }}
              >
                Alert at {alertAt}% of monthly cap
              </div>
              <input
                type="range"
                min={50}
                max={100}
                step={5}
                value={alertAt}
                onChange={(e: ChangeEvent<HTMLInputElement>) =>
                  setAlertAt(Number(e.target.value))
                }
                aria-label="Alert threshold percentage"
                style={{
                  width: '100%',
                  accentColor: T.cherry,
                  cursor: 'pointer',
                  marginBottom: 4,
                }}
              />
              {/* Scale labels */}
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  paddingTop: 2,
                }}
              >
                {['50%', '75%', '100%'].map((lbl: string) => (
                  <span
                    key={lbl}
                    style={{
                      fontFamily: FONTS.mono,
                      fontSize: 10,
                      color: T.text2,
                    }}
                  >
                    {lbl}
                  </span>
                ))}
              </div>
            </div>

            {/* Freeze switch */}
            <div
              style={{
                background: freezeOn ? T.cherrySoft : T.bg3,
                border: `1.5px dashed ${T.border}`,
                borderRadius: 10,
                padding: '10px 12px',
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                transition: 'background 0.2s ease',
              }}
            >
              <span aria-hidden="true" style={{ fontSize: 24, flexShrink: 0 }}>
                🧯
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontFamily: FONTS.display,
                    fontWeight: 800,
                    fontSize: 16,
                    color: T.ink,
                    lineHeight: 1.2,
                  }}
                >
                  Freeze the kitchen at 100%
                </div>
                <div
                  style={{
                    fontFamily: FONTS.hand,
                    fontSize: 15,
                    color: T.cherry,
                    transform: 'rotate(-0.5deg)',
                    display: 'inline-block',
                    marginTop: 2,
                  }}
                >
                  no more orders 'til next month
                </div>
              </div>
              <ToggleSwitch
                on={freezeOn}
                onChange={() => setFreezeOn((f: boolean) => !f)}
                label="Freeze kitchen at 100% budget"
              />
            </div>
          </div>
        </Section>
      </div>
    </div>
  );
}
