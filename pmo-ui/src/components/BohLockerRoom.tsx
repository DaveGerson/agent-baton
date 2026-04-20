import { useState } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type CrewKind = 'human' | 'agent';

interface CrewMember {
  id: string;
  kind: CrewKind;
  initials: string;
  name: string;
  role: string;
  stations: string[];
  shift: string;
  color: string;
  on: boolean;
}

// ---------------------------------------------------------------------------
// Mock roster
// ---------------------------------------------------------------------------

const INITIAL_ROSTER: CrewMember[] = [
  // Humans
  { id: 'ec', kind: 'human', initials: 'EC', name: 'Ezra Chen',   role: 'Head chef',   stations: ['PIES', 'BREAD'],   shift: 'dinner', color: T.cherry,    on: true  },
  { id: 'np', kind: 'human', initials: 'NP', name: 'Nina Park',   role: 'Sous chef',   stations: ['CAKES', 'PASTRY'], shift: 'dinner', color: T.blueberry, on: true  },
  { id: 'jr', kind: 'human', initials: 'JR', name: 'Juno Reyes',  role: 'Expediter',   stations: ['ALL'],             shift: 'dinner', color: T.butter,    on: true  },
  { id: 'bc', kind: 'human', initials: 'BC', name: 'Basil Cho',   role: 'Pastry chef', stations: ['PIES', 'PASTRY'],  shift: 'lunch',  color: T.mint,      on: false },
  // Agents
  { id: 'pc', kind: 'agent', initials: 'PC', name: 'prep-cook',   role: 'Prep',   stations: ['ALL'],          shift: '24/7', color: T.crust,     on: true },
  { id: 'lc', kind: 'agent', initials: 'LC', name: 'line-cook',   role: 'Line',   stations: ['PIES', 'SAUCES'], shift: '24/7', color: T.tangerine, on: true },
  { id: 'ps', kind: 'agent', initials: 'PS', name: 'pastry-chef', role: 'Pastry', stations: ['PIES', 'CAKES'],  shift: '24/7', color: T.blueberry, on: true },
  { id: 'sa', kind: 'agent', initials: 'SA', name: 'saucier',     role: 'Sauce',  stations: ['SAUCES'],         shift: '24/7', color: T.mint,      on: true },
  { id: 'ex', kind: 'agent', initials: 'EX', name: 'expediter',   role: 'Pass',   stations: ['ALL'],            shift: '24/7', color: T.cherry,    on: true },
];

// ---------------------------------------------------------------------------
// ToggleSwitch
// ---------------------------------------------------------------------------

function ToggleSwitch({ on, onChange }: { on: boolean; onChange: () => void }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      onClick={onChange}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        width: 46,
        height: 26,
        borderRadius: 13,
        border: `2px solid ${T.border}`,
        background: on ? T.mint : T.crust,
        cursor: 'pointer',
        padding: 2,
        transition: 'background 0.15s',
        flexShrink: 0,
        boxSizing: 'border-box',
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: 'block',
          width: 18,
          height: 18,
          borderRadius: '50%',
          background: T.cream,
          border: `2px solid ${T.border}`,
          transform: on ? 'translateX(20px)' : 'translateX(0)',
          transition: 'transform 0.15s',
          fontFamily: FONTS.body,
          fontWeight: 700,
        }}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// CrewCard
// ---------------------------------------------------------------------------

function CrewCard({
  member,
  onToggle,
}: {
  member: CrewMember;
  onToggle: (id: string) => void;
}) {
  const isAgent = member.kind === 'agent';

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        padding: 10,
        background: member.on ? T.bg2 : T.bg3,
        border: `2px solid ${T.border}`,
        borderRadius: 10,
        boxShadow: SHADOWS.sm,
        opacity: member.on ? 1 : 0.55,
        transition: 'opacity 0.15s, background 0.15s',
      }}
    >
      {/* Avatar */}
      <div
        aria-hidden="true"
        style={{
          width: 42,
          height: 42,
          flexShrink: 0,
          borderRadius: isAgent ? 8 : '50%',
          background: member.color,
          border: `2px solid ${T.border}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: FONTS.display,
          fontWeight: 900,
          fontSize: 13,
          color: T.ink,
          userSelect: 'none',
        }}
      >
        {member.initials}
      </div>

      {/* Info */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontFamily: FONTS.display,
            fontWeight: 900,
            fontSize: 16,
            color: T.ink,
            lineHeight: 1.1,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {member.name}
        </div>
        <div
          style={{
            fontFamily: FONTS.body,
            fontWeight: 700,
            fontSize: 11,
            color: T.text2,
            marginTop: 2,
          }}
        >
          {member.role} · {member.shift}
        </div>
        {/* Station chips */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginTop: 5 }}>
          {member.stations.map((s) => (
            <span
              key={s}
              style={{
                background: T.bg1,
                border: `1px solid ${T.border}`,
                borderRadius: 4,
                fontFamily: FONTS.mono,
                fontSize: 9,
                color: T.text1,
                padding: '1px 4px',
                lineHeight: 1.4,
              }}
            >
              {s}
            </span>
          ))}
        </div>
      </div>

      {/* Toggle */}
      <div style={{ flexShrink: 0, paddingTop: 2 }}>
        <ToggleSwitch on={member.on} onChange={() => onToggle(member.id)} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section header
// ---------------------------------------------------------------------------

function SectionHeader({
  title,
  actionLabel,
}: {
  title: string;
  actionLabel: string;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: 10,
      }}
    >
      <span
        style={{
          fontFamily: FONTS.display,
          fontWeight: 900,
          fontSize: 13,
          color: T.text1,
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
        }}
      >
        {title}
      </span>
      <button
        style={{
          background: T.butterSoft,
          border: `2px solid ${T.border}`,
          borderRadius: 8,
          boxShadow: SHADOWS.sm,
          fontFamily: FONTS.body,
          fontWeight: 800,
          fontSize: 12,
          color: T.ink,
          padding: '5px 12px',
          cursor: 'pointer',
        }}
      >
        {actionLabel}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BohLockerRoom
// ---------------------------------------------------------------------------

export function BohLockerRoom() {
  const [roster, setRoster] = useState<CrewMember[]>(INITIAL_ROSTER);

  function handleToggle(id: string) {
    setRoster((prev) =>
      prev.map((m) => (m.id === id ? { ...m, on: !m.on } : m))
    );
  }

  const humans = roster.filter((m) => m.kind === 'human');
  const agents = roster.filter((m) => m.kind === 'agent');

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
          background: T.cherrySoft,
        }}
      >
        <span
          aria-hidden="true"
          style={{
            fontSize: 22,
            lineHeight: 1,
          }}
        >
          👥
        </span>
        <div>
          <div
            style={{
              fontFamily: FONTS.display,
              fontWeight: 900,
              fontSize: 20,
              color: T.cherry,
              lineHeight: 1.1,
            }}
          >
            The Locker Room
          </div>
          <div
            style={{
              fontFamily: FONTS.hand,
              fontSize: 14,
              color: T.text2,
              lineHeight: 1.2,
            }}
          >
            the crew — humans &amp; bots
          </div>
        </div>
      </div>

      {/* Body */}
      <div style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 20 }}>
        {/* Section 1: Humans */}
        <section aria-label="Chefs on the schedule">
          <SectionHeader title="Chefs on the schedule" actionLabel="Hire" />
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, 1fr)',
              gap: 8,
            }}
          >
            {humans.map((m) => (
              <CrewCard key={m.id} member={m} onToggle={handleToggle} />
            ))}
          </div>
        </section>

        {/* Section 2: Agents */}
        <section aria-label="Agents on the line">
          <SectionHeader title="Agents on the line" actionLabel="Add agent" />
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, 1fr)',
              gap: 8,
            }}
          >
            {agents.map((m) => (
              <CrewCard key={m.id} member={m} onToggle={handleToggle} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
