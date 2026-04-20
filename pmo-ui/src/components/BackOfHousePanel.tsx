import { useState } from 'react';
import { T, FONTS, SHADOWS } from '../styles/tokens';
import { BohWalkIn } from './BohWalkIn';
import { BohBossOffice } from './BohBossOffice';
import { BohLockerRoom } from './BohLockerRoom';
import { BohLoadingDock } from './BohLoadingDock';
import { BohTipJar } from './BohTipJar';
import { BohRulebook } from './BohRulebook';

type RoomId = 'walk-in' | 'locker-room' | 'boss-office' | 'loading-dock' | 'tip-jar' | 'rulebook';

type DataStatus = 'live' | 'mock';

interface Room {
  id: RoomId;
  emoji: string;
  label: string;
  sub: string;
  accent: string;
  status: DataStatus;
  component: () => JSX.Element;
}

const ROOMS: Room[] = [
  { id: 'walk-in',      emoji: '🧊', label: 'The Walk-In',       sub: 'creds & models',     accent: T.blueberry, status: 'mock', component: BohWalkIn      },
  { id: 'locker-room',  emoji: '👥', label: 'Locker Room',        sub: 'the crew',           accent: T.cherry,    status: 'live', component: BohLockerRoom  },
  { id: 'boss-office',  emoji: '💰', label: "Boss's Office",      sub: 'budgets & caps',     accent: T.butter,    status: 'mock', component: BohBossOffice  },
  { id: 'loading-dock', emoji: '🚚', label: 'Loading Dock',       sub: 'integrations',       accent: T.mint,      status: 'live', component: BohLoadingDock },
  { id: 'tip-jar',      emoji: '🔔', label: 'The Tip Jar',        sub: 'notifications',      accent: T.tangerine, status: 'mock', component: BohTipJar      },
  { id: 'rulebook',     emoji: '📘', label: 'The Rulebook',       sub: 'house rules',        accent: T.crust,     status: 'live', component: BohRulebook    },
];

function isLightAccent(accent: string): boolean {
  return accent === T.butter || accent === T.mint || accent === T.crust || accent === T.tangerine;
}

interface BackOfHousePanelProps {
  onBack: () => void;
}

export function BackOfHousePanel({ onBack }: BackOfHousePanelProps) {
  const [activeRoom, setActiveRoom] = useState<RoomId>('walk-in');
  const room = ROOMS.find(r => r.id === activeRoom)!;
  const RoomComponent = room.component;

  return (
    <div style={{
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      background: T.bg0,
      overflow: 'hidden',
    }}>
      {/* BOH header strip */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '0 16px',
        borderBottom: `2px solid ${T.border}`,
        background: T.bg3,
        flexShrink: 0,
        height: 44,
      }}>
        <button
          onClick={onBack}
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            padding: '4px 10px',
            border: `1.5px solid ${T.border}`,
            borderRadius: 8,
            background: T.bg1,
            color: T.text1,
            fontFamily: FONTS.body,
            fontSize: 12, fontWeight: 800,
            cursor: 'pointer',
            boxShadow: SHADOWS.sm,
          }}
        >
          ← Rail
        </button>

        <span style={{
          fontFamily: FONTS.display,
          fontWeight: 900, fontSize: 18,
          color: T.ink, letterSpacing: '-.01em',
        }}>
          Back of House
        </span>
        <span style={{
          fontFamily: FONTS.hand,
          fontSize: 14, color: T.text2,
          transform: 'rotate(-1deg)', display: 'inline-block',
        }}>
          "staff only"
        </span>

        <div style={{ flex: 1 }} />

        <span style={{
          fontFamily: FONTS.mono, fontSize: 9,
          color: T.text3, letterSpacing: '.06em',
        }}>
          {ROOMS.length} rooms
        </span>
      </div>

      {/* Room selector + content */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Left sidebar — room nav */}
        <nav
          aria-label="Back of House rooms"
          style={{
            width: 160,
            flexShrink: 0,
            borderRight: `2px solid ${T.border}`,
            background: T.bg1,
            display: 'flex',
            flexDirection: 'column',
            gap: 3,
            padding: '10px 8px',
            overflowY: 'auto',
          }}
        >
          <div style={{
            fontFamily: FONTS.body, fontWeight: 800,
            fontSize: 9, textTransform: 'uppercase',
            letterSpacing: '.12em', color: T.text3,
            padding: '2px 6px', marginBottom: 4,
          }}>
            ROOMS
          </div>
          {ROOMS.map(r => {
            const active = r.id === activeRoom;
            const light = isLightAccent(r.accent);
            return (
              <button
                key={r.id}
                onClick={() => setActiveRoom(r.id)}
                aria-current={active ? 'page' : undefined}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '8px 10px',
                  border: `2px solid ${active ? T.border : 'transparent'}`,
                  borderRadius: 10,
                  background: active ? r.accent : 'transparent',
                  color: active ? (light ? T.ink : T.cream) : T.text1,
                  fontFamily: FONTS.body,
                  fontSize: 12, fontWeight: 800,
                  cursor: 'pointer',
                  textAlign: 'left',
                  boxShadow: active ? SHADOWS.sm : 'none',
                  transition: 'all 120ms',
                  width: '100%',
                }}
              >
                <span style={{ fontSize: 15, flexShrink: 0 }}>{r.emoji}</span>
                <span style={{ flex: 1, lineHeight: 1.2, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.label}
                    </span>
                    <span style={{
                      flexShrink: 0,
                      fontFamily: FONTS.mono,
                      fontWeight: 800,
                      fontSize: 8,
                      letterSpacing: '0.06em',
                      textTransform: 'uppercase',
                      padding: '1px 5px',
                      borderRadius: 999,
                      border: `1.5px solid ${active ? (light ? T.ink : T.cream) : T.border}`,
                      background: r.status === 'live'
                        ? (active ? 'rgba(0,0,0,0.15)' : T.mintSoft)
                        : (active ? 'rgba(0,0,0,0.10)' : T.butterSoft),
                      color: active
                        ? (light ? T.ink : T.cream)
                        : (r.status === 'live' ? T.mintDark : T.inkFaint),
                      lineHeight: 1.6,
                    }}>
                      {r.status === 'live' ? 'LIVE' : 'MOCK'}
                    </span>
                  </div>
                  {active && (
                    <div style={{
                      fontFamily: FONTS.hand,
                      fontSize: 11, fontWeight: 500,
                      opacity: .85,
                      transform: 'rotate(-.5deg)',
                      display: 'inline-block',
                    }}>
                      {r.sub}
                    </div>
                  )}
                </span>
              </button>
            );
          })}
        </nav>

        {/* Room content */}
        <main
          aria-label={room.label}
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '20px 24px',
            background: T.bg0,
          }}
        >
          <RoomComponent />
        </main>
      </div>
    </div>
  );
}
