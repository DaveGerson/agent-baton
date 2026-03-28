import type { CSSProperties } from 'react';

export const T = {
  bg0: '#060a11',
  bg1: '#0b1120',
  bg2: '#111827',
  bg3: '#1a2236',
  bg4: '#222d42',
  border: '#1e2d4a',
  borderHover: '#2d4a6a',
  borderActive: '#3b82f6',
  text0: '#f1f5f9',
  text1: '#cbd5e1',
  text2: '#94a3b8',
  text3: '#8b9bb5',
  text4: '#8b9bb5',
  accent: '#3b82f6',
  green: '#10b981',
  yellow: '#f59e0b',
  red: '#ef4444',
  purple: '#8b5cf6',
  cyan: '#06b6d4',
  orange: '#f97316',
} as const;

export type TokenKey = keyof typeof T;

export const COLUMNS = [
  { id: 'queued' as const, label: 'Queued', color: T.text2, desc: 'Plan ready, awaiting execution slot' },
  { id: 'executing' as const, label: 'Executing', color: T.yellow, desc: 'Baton steps actively running' },
  { id: 'awaiting_human' as const, label: 'Awaiting Human', color: T.orange, desc: 'Interactive step paused for input' },
  { id: 'validating' as const, label: 'Validating', color: T.purple, desc: 'Test suites, baseline comparison' },
  { id: 'deployed' as const, label: 'Deployed', color: T.green, desc: 'Complete — ADO synced' },
];

export type ColumnId = typeof COLUMNS[number]['id'];

export const SEVERITY_COLOR: Record<string, string> = {
  critical: T.red,
  high: T.red,
  medium: T.yellow,
  low: T.text2,
};

export const PRIORITY_COLOR: Record<number, string> = {
  2: T.red,      // P0 — critical
  1: T.orange,   // P1 — high
  0: T.text2,    // P2 — normal (no chip needed)
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

export const FONT_SIZES = {
  xs: '9px',    // minimum — only for tertiary metadata
  sm: '11px',   // scannable content floor
  md: '12px',   // card titles, form labels
  lg: '14px',   // section headers
  xl: '16px',   // page titles
} as const;
