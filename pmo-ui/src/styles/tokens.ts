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
  text2: '#64748b',
  text3: '#475569',
  text4: '#334155',
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
  { id: 'planning' as const, label: 'Planning', color: T.cyan, desc: 'Claude decomposing scope into steps' },
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
  0: T.red,
  1: T.orange,
  2: T.yellow,
  3: T.text2,
};

export const FONT_SIZES = {
  xs: '9px',    // minimum — only for tertiary metadata
  sm: '11px',   // scannable content floor
  md: '12px',   // card titles, form labels
  lg: '14px',   // section headers
  xl: '16px',   // page titles
} as const;
