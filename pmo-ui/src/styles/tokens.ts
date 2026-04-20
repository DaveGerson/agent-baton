import type { CSSProperties } from 'react';

// ===================================================================
// THE PIE EATING CONTEST — Design Tokens
// A warm, kitchen-themed orchestration board.
// Butcher-paper cream backdrops · jam-jar reds · butter yellows ·
// blueberry purples · pistachio mint · chunky ink borders.
// ===================================================================

export const T = {
  // Backgrounds — butcher-paper cream spectrum
  bg0: '#fff4de',    // cream — main body background
  bg1: '#fff9ec',    // cream-soft — panels, nav, sidebars
  bg2: '#fff4de',    // cream — cards, wells
  bg3: '#f3e4c2',    // cream-deep — sunken areas, pie-crust edge
  bg4: '#e8d4a8',    // darker crust — interactive inactive

  // Borders
  border: '#2a1a10',      // ink — all primary borders (hand-drawn feel)
  borderHover: '#5b3a23', // ink-soft — hover state borders
  borderActive: '#e23a3a', // cherry — active/selected state
  borderSoft: '#c9a97a',  // warm tan — secondary hairlines

  // Text
  text0: '#2a1a10',  // ink — primary text
  text1: '#5b3a23',  // ink-soft — secondary text
  text2: '#8a6a4f',  // ink-faint — tertiary/muted
  text3: '#a4805f',  // lighter muted
  text4: '#c9a97a',  // faintest — timestamps, subtleties

  // Brand / Accent palette
  accent: '#e23a3a',    // cherry — primary action color
  cherry: '#e23a3a',    // cherry pie red
  cherryDark: '#a8151a',
  cherrySoft: '#ffd3ce',

  green: '#4db892',     // mint / pistachio — success, served
  mint: '#4db892',
  mintDark: '#2f7a5e',
  mintSoft: '#c3e8d2',

  yellow: '#ffc94a',    // butter / golden — in-progress
  butter: '#ffc94a',
  butterSoft: '#fff0b8',

  red: '#e23a3a',       // same as cherry
  purple: '#4b3bb3',    // blueberry — validating/taste-test
  blueberry: '#4b3bb3',
  blueberrySoft: '#d6cfff',

  orange: '#ff8a3d',    // tangerine — awaiting human
  tangerine: '#ff8a3d',
  tangerineSoft: '#ffd7b0',

  cyan: '#4db892',      // maps to mint (no cool blue in kitchen palette)
  crust: '#d4a15a',     // golden crust — queued/resting
  crustDark: '#a4691a',
  ink: '#2a1a10',       // darkest — always ink brown
  inkSoft: '#5b3a23',
  inkFaint: '#8a6a4f',
  cream: '#fff4de',
  creamSoft: '#fff9ec',
  creamDeep: '#f3e4c2',
} as const;

export type TokenKey = keyof typeof T;

// Column definitions — kitchen station metaphors
export const COLUMNS = [
  {
    id: 'queued' as const,
    label: 'On Deck',
    color: T.crust,
    desc: "Dough's resting — plan ready, awaiting execution slot",
  },
  {
    id: 'executing' as const,
    label: 'In the Oven',
    color: T.butter,
    desc: 'Actively baking — Baton steps running',
  },
  {
    id: 'awaiting_human' as const,
    label: 'Ding! Pick Up',
    color: T.tangerine,
    desc: 'Chef needs input — interactive step paused',
  },
  {
    id: 'validating' as const,
    label: 'Taste Test',
    color: T.blueberry,
    desc: 'Expediter sampling — test suites, baseline comparison',
  },
  {
    id: 'deployed' as const,
    label: 'Served!',
    color: T.mint,
    desc: 'Out the window — complete, ADO synced',
  },
  {
    id: 'review' as const,
    label: 'Plating Review',
    color: T.crust,
    desc: 'Expediter checks — consolidation ready for merge',
  },
];

export type ColumnId = typeof COLUMNS[number]['id'];

export const SEVERITY_COLOR: Record<string, string> = {
  critical: T.cherry,
  high: T.cherry,
  medium: T.butter,
  low: T.text2,
};

export const PRIORITY_COLOR: Record<number, string> = {
  2: T.cherry,      // P0 — kitchen is on fire
  1: T.tangerine,   // P1 — hungry VIP
  0: T.text2,       // P2 — on the menu (no chip needed)
};

export const SR_ONLY: CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
};

// Font families
export const FONTS = {
  display: "'Fraunces', 'Playfair Display', Georgia, serif",
  body: "'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif",
  hand: "'Caveat', 'Comic Sans MS', cursive",
  mono: "'JetBrains Mono', 'Courier New', monospace",
} as const;

// Numeric values are React inline-style compatible (px assumed).
export const FONT_SIZES = {
  xs: 9,    // minimum — only for tertiary metadata
  sm: 11,   // scannable content floor
  md: 12,   // card titles, form labels
  lg: 14,   // section headers
  xl: 16,   // page titles
} as const;

// Hard-edged sticker shadows — no blur, offset ink
export const SHADOWS = {
  sm: '2px 2px 0 0 #2a1a10',
  md: '3px 3px 0 0 #2a1a10',
  lg: '5px 5px 0 0 #2a1a10',
  xl: '8px 8px 0 0 #2a1a10',
} as const;

// Kitchen program palette — warm, saturated
const PROGRAM_PALETTE = [
  '#e23a3a', // cherry
  '#4b3bb3', // blueberry
  '#4db892', // mint
  '#d4a15a', // crust
  '#ffc94a', // butter
  '#ff8a3d', // tangerine
  '#d6336c', // raspberry
  '#6db9ff', // sky
];

export function programColor(program: string): string {
  let hash = 0;
  for (let i = 0; i < program.length; i++) {
    hash = (hash * 31 + program.charCodeAt(i)) >>> 0;
  }
  return PROGRAM_PALETTE[hash % PROGRAM_PALETTE.length];
}
