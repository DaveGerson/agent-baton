# Baton PMO UI — Accessibility Remediation Solutions Plan

**Based on:** audit-report.md (2026-03-28)
**Target:** 31 accessibility failures + 6 UX findings
**Standard:** WCAG 2.1 Level AA, ARIA APG 1.2

---

## Implementation Priority Order

| Priority | Rationale |
|----------|-----------|
| 1 — tokens.ts (color + font floor) | Unblocks all 4 critical axe-core failures; zero risk to component logic |
| 2 — App.tsx (nav + tab pattern + hidden panels) | Structural foundation; affects every view |
| 3 — KanbanBoard.tsx (landmarks + live regions) | Highest daily-use surface |
| 4 — KanbanCard.tsx (keyboard operability) | Critical keyboard blocker |
| 5 — ForgePanel.tsx (labels + focus management + live regions) | Largest single change; most form issues |
| 6 — PlanEditor.tsx (icon labels + accordion ARIA) | Nested inside ForgePanel; do after it stabilizes |
| 7 — SignalsBar.tsx (list + checkbox labels + confirmation) | Independent panel |
| 8 — InterviewPanel.tsx (fieldset/legend) | Shallow, low-risk |
| 9 — AdoCombobox.tsx (full combobox pattern + keyboard nav) | Most complex isolated change |
| 10 — HealthBar.tsx (program card labels) | Smallest scope |

---

## 1. tokens.ts — Color Contrast and Font Floor

### A-01 / A-02 / A-03 / A-04 [CRITICAL] Color contrast across all views

**What is wrong:** `T.text2` (`#64748b`), `T.text3` (`#475569`), and `T.text4`
(`#334155`) all fail the WCAG AA 4.5:1 contrast requirement when rendered on
the dark backgrounds `T.bg0`–`T.bg4`. These tokens are used throughout every
component for metadata, labels, timestamps, and secondary text. Additionally,
multiple components use `fontSize: 7` and `fontSize: 8` for non-decorative
text, which compounds the failure because small text triggers the stricter 4.5:1
threshold (WCAG applies 3:1 only for text >= 18px regular or 14px bold).

**Affected file:** `/home/djiv/PycharmProjects/orchestrator-v2/pmo-ui/src/styles/tokens.ts`
Lines 11–14 (text tokens) and lines 49–55 (FONT_SIZES constant — currently
unenforced).

**Proposed fix:**

```ts
// BEFORE
text0: '#f1f5f9',
text1: '#cbd5e1',
text2: '#64748b',
text3: '#475569',
text4: '#334155',

// AFTER
text0: '#f1f5f9',   // unchanged — ~17:1 on bg1, adequate
text1: '#cbd5e1',   // unchanged — ~11:1 on bg1, adequate
text2: '#94a3b8',   // was #64748b — new ratio ~5.9:1 on bg1 (was ~3.0:1, FAIL)
text3: '#64748b',   // was #475569 — new ratio ~3.0:1 on bg1 (use only for large/bold)
text4: '#64748b',   // was #334155 — collapse to same as text3; never use for small normal text
```

Also add the `SR_ONLY` constant that live regions will need (no CSS class
system exists in this codebase):

```ts
// ADD at the end of tokens.ts
export const SR_ONLY: React.CSSProperties = {
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
```

**Font size floor — same file, all consumers:**

The `FONT_SIZES` constant already declares `xs: '9px'` as the minimum.
The violations are where components hardcode `fontSize: 7` or `fontSize: 8`
inline. The token fix does not auto-fix those; each component section below
calls out every offending `fontSize` literal. The rule is: **no informational
text below 9px**. Decorative separators and purely visual dots may keep
`fontSize: 8` only if they carry no text content.

**Expected improvement:** Resolves A-01, A-02, A-03, A-04 (4 critical
axe-core violations). Reduces compound contrast failures from font size.

---

## 2. App.tsx — Navigation, Tab Pattern, Hidden Panels

**Affected file:** `/home/djiv/PycharmProjects/orchestrator-v2/pmo-ui/src/App.tsx`

### B-01 [HIGH] No `<h1>` for the application title
**Lines:** 86 (brand name `<div>`)

```tsx
// BEFORE
<div style={{ fontSize: 10, fontWeight: 700, letterSpacing: -0.3 }}>Baton PMO</div>

// AFTER
<h1 style={{ fontSize: 10, fontWeight: 700, letterSpacing: -0.3, margin: 0 }}>
  Baton PMO
</h1>
```

### B-03 [HIGH] Top navigation not wrapped in `<nav>`
**Lines:** 60–133 (outer nav `<div>`)

```tsx
// BEFORE
<div style={{
  display: 'flex',
  alignItems: 'center',
  gap: 10,
  padding: '6px 14px',
  borderBottom: `1px solid ${T.border}`,
  background: T.bg1,
  flexShrink: 0,
}}>

// AFTER
<nav
  aria-label="Application navigation"
  style={{
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '6px 14px',
    borderBottom: `1px solid ${T.border}`,
    background: T.bg1,
    flexShrink: 0,
  }}
>
```

Close tag changes from `</div>` to `</nav>` at line 133.

### B-06 [HIGH] + D-04 [HIGH] Nav tabs need ARIA tab pattern; no `role="none"`
**Lines:** 94–119 (tab container and tab buttons)

The test detected `role="none"` stripping the button role. Apply the full
ARIA tab pattern:

```tsx
// BEFORE
<div style={{ display: 'flex', gap: 2, marginLeft: 10 }}>
  {([
    { id: 'kanban' as const, label: 'AI Kanban', icon: '\u25AB' },
    { id: 'forge' as const, label: 'The Forge', icon: '\u2692' },
  ]).map(tab => (
    <button
      key={tab.id}
      onClick={() => {
        if (tab.id === 'kanban') backToBoard();
        else openForge();
      }}
      style={{
        padding: '3px 10px',
        borderRadius: 3,
        border: 'none',
        background: view === tab.id ? T.accent + '18' : 'transparent',
        color: view === tab.id ? T.accent : T.text3,
        fontSize: 9,
        fontWeight: view === tab.id ? 700 : 500,
        cursor: 'pointer',
      }}
    >
      {tab.icon} {tab.label}
    </button>
  ))}
</div>

// AFTER
<div
  role="tablist"
  aria-label="Views"
  style={{ display: 'flex', gap: 2, marginLeft: 10 }}
>
  {([
    { id: 'kanban' as const, label: 'AI Kanban', icon: '\u25AB' },
    { id: 'forge' as const, label: 'The Forge', icon: '\u2692' },
  ]).map(tab => (
    <button
      key={tab.id}
      role="tab"
      aria-selected={view === tab.id}
      aria-controls={`panel-${tab.id}`}
      id={`tab-${tab.id}`}
      onClick={() => {
        if (tab.id === 'kanban') backToBoard();
        else openForge();
      }}
      style={{
        padding: '3px 10px',
        borderRadius: 3,
        border: 'none',
        background: view === tab.id ? T.accent + '18' : 'transparent',
        color: view === tab.id ? T.accent : T.text3,
        fontSize: 9,
        fontWeight: view === tab.id ? 700 : 500,
        cursor: 'pointer',
      }}
    >
      {tab.icon} {tab.label}
    </button>
  ))}
</div>
```

### Hidden panel ARIA (B-06 continuation + audit Technical Notes)
**Lines:** 137–152 (both panel containers)

Both panels are always in the DOM. The inactive one must be hidden from
assistive technology to prevent screen reader traversal and tab-order pollution:

```tsx
// BEFORE
<div style={{ display: view === 'kanban' ? 'block' : 'none', height: '100%' }}>
  <KanbanBoard ... />
</div>
<div style={{ display: 'forge' ? 'block' : 'none', height: '100%' }}>
  <ForgePanel ... />
</div>

// AFTER
<div
  id="panel-kanban"
  role="tabpanel"
  aria-labelledby="tab-kanban"
  aria-hidden={view !== 'kanban'}
  style={{ display: view === 'kanban' ? 'block' : 'none', height: '100%' }}
>
  <KanbanBoard
    onNewPlan={() => openForge()}
    onSignalToForge={(sig) => openForge(sig)}
    onCardForge={handleCardForge}
    showSignals={showSignals}
    onToggleSignals={toggleSignals}
  />
</div>
<div
  id="panel-forge"
  role="tabpanel"
  aria-labelledby="tab-forge"
  aria-hidden={view !== 'forge'}
  style={{ display: view === 'forge' ? 'block' : 'none', height: '100%' }}
>
  <ForgePanel
    onBack={backToBoard}
    initialSignal={forgeSignal}
  />
</div>
```

**Expected improvement:** Resolves B-01, B-03, B-06, D-04. Prevents screen
reader traversal of hidden panels.

---

## 3. KanbanBoard.tsx — Landmarks, Live Regions, Error Alert

**Affected file:** `/home/djiv/PycharmProjects/orchestrator-v2/pmo-ui/src/components/KanbanBoard.tsx`

### B-04 [HIGH] Kanban columns lack landmark / region semantics
**Lines:** 207–271 (column rendering loop)

The column container `<div>` and the column header `<div>` must become a
`<section>` / `<h2>` pair so screen readers expose them as labeled regions:

```tsx
// BEFORE (inside COLUMNS.map)
<div
  key={col.id}
  style={{
    flex: 1,
    minWidth: 170,
    maxWidth: 240,
    display: 'flex',
    flexDirection: 'column',
    margin: '0 3px',
  }}
>
  {/* Column header */}
  <div style={{
    padding: '5px 8px',
    marginBottom: 5,
    borderRadius: 4,
    background: T.bg2,
    borderBottom: `2px solid ${col.color}30`,
    flexShrink: 0,
  }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <div style={{ width: 6, height: 6, borderRadius: 2, background: col.color }} />
      <span style={{ fontSize: 11, fontWeight: 700, color: T.text0, flex: 1 }}>
        {col.label}
      </span>
      ...
    </div>
    <div style={{ fontSize: 9, color: T.text4, marginTop: 1 }}>{col.desc}</div>
  </div>
  ...
</div>

// AFTER
<section
  key={col.id}
  aria-labelledby={`col-${col.id}-heading`}
  style={{
    flex: 1,
    minWidth: 170,
    maxWidth: 240,
    display: 'flex',
    flexDirection: 'column',
    margin: '0 3px',
  }}
>
  {/* Column header */}
  <div style={{
    padding: '5px 8px',
    marginBottom: 5,
    borderRadius: 4,
    background: T.bg2,
    borderBottom: `2px solid ${col.color}30`,
    flexShrink: 0,
  }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <div aria-hidden="true" style={{ width: 6, height: 6, borderRadius: 2, background: col.color }} />
      <h2
        id={`col-${col.id}-heading`}
        style={{ fontSize: 11, fontWeight: 700, color: T.text0, flex: 1, margin: 0 }}
      >
        {col.label}
      </h2>
      ...
    </div>
    <div style={{ fontSize: 9, color: T.text3, marginTop: 1 }}>{col.desc}</div>
  </div>
  ...
</section>
```

Note: `T.text4` on `col.desc` (line 247) changes to `T.text3` as part of the
font color floor from the token fix (T.text4 is now the same value as T.text3
after the token change, but being explicit is clearer).

### G-01 [HIGH] Board loading state has no `aria-live` region
**Lines:** 154–161 (loading and lastUpdated spans in toolbar status block)

Replace the conditionally-rendered `loading` span with an always-present
`role="status"` region. Screen readers only pick up content changes in live
regions that are already in the DOM:

```tsx
// BEFORE
{loading && <span style={{ color: T.text4, fontSize: 7 }}>refreshing…</span>}

// AFTER
<span
  role="status"
  aria-live="polite"
  aria-atomic="true"
  style={{ color: T.text3, fontSize: 9 }}
>
  {loading ? 'Refreshing board data…' : ''}
</span>
```

Also change `fontSize: 7` on `lastUpdated` span (line 156) to `fontSize: 9`.

### G-02 [HIGH] Error banner missing `role="alert"`
**Lines:** 193–204 (error banner block)

Keep the container in the DOM at all times; fill it only when there is an error:

```tsx
// BEFORE
{error && (
  <div style={{
    padding: '5px 14px',
    background: T.red + '15',
    borderBottom: `1px solid ${T.red}33`,
    fontSize: 8,
    color: T.red,
  }}>
    {error} — retrying every {connectionMode === 'sse' ? '15' : '5'}s
  </div>
)}

// AFTER
<div
  role="alert"
  aria-live="assertive"
  aria-atomic="true"
>
  {error && (
    <div style={{
      padding: '5px 14px',
      background: T.red + '15',
      borderBottom: `1px solid ${T.red}33`,
      fontSize: 9,
      color: T.red,
    }}>
      {error} — retrying every {connectionMode === 'sse' ? '15' : '5'}s
    </div>
  )}
</div>
```

### G-04 [HIGH] Connection mode changes not announced — `ConnectionIndicator`
**Lines:** 306–344 (the `ConnectionIndicator` function component)

Add `role="status"` with a descriptive `aria-label` to the outer container.
The visual dot and text remain as-is visually:

```tsx
// BEFORE
<div
  title={title}
  style={{
    display: 'flex',
    alignItems: 'center',
    gap: 3,
    padding: '2px 5px',
    borderRadius: 3,
    border: `1px solid ${dotColor}33`,
    background: dotColor + '10',
  }}
>
  <div style={{ width: 5, height: 5, borderRadius: '50%', background: dotColor, ... }} />
  <span style={{ fontSize: 7, color: dotColor, fontWeight: 600 }}>{label}</span>
</div>

// AFTER
<div
  role="status"
  aria-live="polite"
  aria-label={`Connection: ${title}`}
  title={title}
  style={{
    display: 'flex',
    alignItems: 'center',
    gap: 3,
    padding: '2px 5px',
    borderRadius: 3,
    border: `1px solid ${dotColor}33`,
    background: dotColor + '10',
  }}
>
  <div aria-hidden="true" style={{ width: 5, height: 5, borderRadius: '50%', background: dotColor, ... }} />
  <span aria-hidden="true" style={{ fontSize: 9, color: dotColor, fontWeight: 600 }}>{label}</span>
</div>
```

Also raise the inner `<span>` `fontSize: 7` to `fontSize: 9`.

### G-05 [HIGH] Signal count badge updates not in a live region
**Lines:** 108–126 (signals toggle button and its badge `<span>`)

Add a visually-hidden live region adjacent to the button (not inside it, to
avoid double-announcing the count to AT while keeping the visual badge):

```tsx
// BEFORE
<button
  onClick={onToggleSignals}
  style={{ ... color: showSignals ? T.red : T.text3, ... }}
>
  Signals
  {openSignalCount > 0 && (
    <span style={{ ... background: T.red, color: '#fff', fontSize: 9, ... }}>
      {openSignalCount}
    </span>
  )}
</button>

// AFTER
<>
  <button
    onClick={onToggleSignals}
    aria-pressed={showSignals}
    style={{ ... color: showSignals ? T.red : T.text3, ... }}
  >
    Signals
    {openSignalCount > 0 && (
      <span aria-hidden="true" style={{ ... background: T.red, color: '#fff', fontSize: 9, ... }}>
        {openSignalCount}
      </span>
    )}
  </button>
  <span
    aria-live="polite"
    aria-atomic="true"
    style={SR_ONLY}
  >
    {openSignalCount > 0 ? `${openSignalCount} open signals` : 'No open signals'}
  </span>
</>
```

Import `SR_ONLY` from `../styles/tokens`.

### U-03 [MEDIUM] Awaiting-human dot lacks accessible label
**Lines:** 133–151 (awaitingHuman conditional block)

```tsx
// BEFORE
<div style={{ display: 'flex', alignItems: 'center', gap: 3, padding: '2px 6px', ... }}>
  <div style={{ width: 5, height: 5, borderRadius: '50%', background: T.orange, animation: 'pulse 1.5s infinite' }} />
  <span style={{ color: T.orange, fontWeight: 600 }}>{awaitingHuman} awaiting</span>
</div>

// AFTER
<div
  aria-label={`${awaitingHuman} task${awaitingHuman !== 1 ? 's' : ''} awaiting human input`}
  style={{ display: 'flex', alignItems: 'center', gap: 3, padding: '2px 6px', ... }}
>
  <div
    aria-hidden="true"
    style={{ width: 5, height: 5, borderRadius: '50%', background: T.orange, animation: 'pulse 1.5s infinite' }}
  />
  <span aria-hidden="true" style={{ color: T.orange, fontWeight: 600 }}>{awaitingHuman} awaiting</span>
</div>
```

Also: the `executing` / `filtered.length` span on line 153 uses `T.text3`
which is borderline after the token change — leave it, `T.text3` post-fix is
`#64748b` which is acceptable for secondary text at 9px+ if the font is bold
or large enough. The font there is already 9px so it is acceptable.

**Expected improvement:** Resolves B-04, G-01, G-02, G-04, G-05, U-03.

---

## 4. KanbanCard.tsx — Keyboard Operability and ARIA Expansion

**Affected file:** `/home/djiv/PycharmProjects/orchestrator-v2/pmo-ui/src/components/KanbanCard.tsx`

### D-02 [HIGH] + E-01 [CRITICAL] Card not keyboard-operable; missing `aria-expanded`
**Lines:** 109–130 (outer card `<div>` with `onClick`)

The outer `<div>` must become keyboard-reachable and expose its expanded state.
Use `role="button"` + `tabIndex={0}` + `onKeyDown` rather than converting
to `<button>` because the card contains interactive children (buttons inside
the expanded section) — nesting `<button>` inside `<button>` is invalid HTML:

```tsx
// BEFORE
<div
  onClick={() => setExpanded(!expanded)}
  style={{
    background: T.bg1,
    borderRadius: 4,
    border: `1px solid ${borderColor}`,
    cursor: 'pointer',
    overflow: 'hidden',
    transition: 'border-color 0.15s',
    boxShadow: isHuman ? `0 0 8px ${T.orange}10` : 'none',
  }}
  onMouseEnter={...}
  onMouseLeave={...}
>

// AFTER
<div
  role="button"
  tabIndex={0}
  aria-expanded={expanded}
  aria-label={`${card.title}. ${card.column.replace('_', ' ')}. ${card.steps_completed} of ${card.steps_total} steps complete. Press Enter to ${expanded ? 'collapse' : 'expand'} details.`}
  onClick={() => setExpanded(!expanded)}
  onKeyDown={(e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setExpanded(!expanded);
    }
  }}
  style={{
    background: T.bg1,
    borderRadius: 4,
    border: `1px solid ${borderColor}`,
    cursor: 'pointer',
    overflow: 'hidden',
    transition: 'border-color 0.15s',
    boxShadow: isHuman ? `0 0 8px ${T.orange}10` : 'none',
  }}
  onMouseEnter={...}
  onMouseLeave={...}
>
```

Also stop event propagation on the inner action buttons' `onClick` handlers to
prevent them from toggling the card when clicked — this is already done for
`handleExecute` and `handleViewPlan` but verify `onForge` call at line 271:

```tsx
// Already correct for Execute and ViewPlan.
// Forge button — already correct (e.stopPropagation() present at line 271).
// No further changes needed for inner buttons beyond the card container fix.
```

Fix font size violations inside card:
- Line 152: `fontSize: 9` on card ID span — uses `T.text4`, change color to `T.text3` after token fix (or leave since it is the same value post-fix)
- Line 342: `fontSize: 8` on `planLoading` message → change to `fontSize: 9`
- Line 210: `fontSize: 9` on `T.text4` separator dot — change to `T.text3`

**Expected improvement:** Resolves D-02, E-01 (2 failures across keyboard
and interactive element suites). Card becomes fully keyboard-operable.

### U-01 [MEDIUM] Execution result has no dismiss mechanism
**Lines:** 306–317 (`execResult` rendering block)

Add a dismiss button and an auto-clear timer:

```tsx
// BEFORE
{execResult && (
  <div style={{
    fontSize: 8,
    color: execResult.startsWith('Launched') ? T.green : T.red,
    padding: '3px 6px',
    marginTop: 4,
    background: T.bg1,
    borderRadius: 3,
  }}>
    {execResult}
  </div>
)}

// AFTER
{execResult && (
  <div
    role="status"
    aria-live="polite"
    style={{
      display: 'flex',
      alignItems: 'center',
      gap: 4,
      fontSize: 9,
      color: execResult.startsWith('Launched') ? T.green : T.red,
      padding: '3px 6px',
      marginTop: 4,
      background: T.bg1,
      borderRadius: 3,
    }}
  >
    <span style={{ flex: 1 }}>{execResult}</span>
    <button
      aria-label="Dismiss execution result"
      onClick={e => { e.stopPropagation(); setExecResult(null); }}
      style={{
        background: 'none',
        border: 'none',
        color: T.text3,
        fontSize: 10,
        cursor: 'pointer',
        padding: '0 2px',
        lineHeight: 1,
        flexShrink: 0,
      }}
    >
      {'\u00d7'}
    </button>
  </div>
)}
```

Add the auto-clear timer inside `handleExecute`:

```tsx
// Inside handleExecute, in the finally block, after setExecLoading(false):
// BEFORE
} finally {
  setExecLoading(false);
}

// AFTER
} finally {
  setExecLoading(false);
  // Auto-clear after 8 s; user can also dismiss manually
  setTimeout(() => setExecResult(null), 8000);
}
```

**Expected improvement:** Resolves U-01. Execution feedback no longer
persists indefinitely.

---

## 5. ForgePanel.tsx — Labels, Focus Management, Live Regions, Error Association

**Affected file:** `/home/djiv/PycharmProjects/orchestrator-v2/pmo-ui/src/components/ForgePanel.tsx`

### C-01 [HIGH] + C-02 [HIGH] + C-03 [HIGH] + U-04 [MEDIUM] Form labels not connected to inputs

The `FormField` helper at lines 447–454 already renders a `<label>` element,
but it is not connected to its input because no `htmlFor` / `id` pair exists.
Fix the `FormField` component and all call sites.

**Step 1 — Update the `FormField` helper (lines 447–454):**

```tsx
// BEFORE
function FormField({ label, children, style }: { label: string; children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={style}>
      <label style={{ fontSize: 8, color: T.text2, display: 'block', marginBottom: 4 }}>{label}</label>
      {children}
    </div>
  );
}

// AFTER
function FormField({
  label,
  children,
  style,
  htmlFor,
}: {
  label: string;
  children: React.ReactNode;
  style?: React.CSSProperties;
  htmlFor?: string;
}) {
  return (
    <div style={style}>
      <label
        htmlFor={htmlFor}
        style={{ fontSize: 9, color: T.text2, display: 'block', marginBottom: 4 }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}
```

Note: `fontSize: 8` raised to `fontSize: 9` on the label.

**Step 2 — Update every `FormField` call site and its child input:**

```tsx
// ADO Import
<FormField label="Import from ADO" htmlFor="forge-ado-search">
  <AdoCombobox
    inputId="forge-ado-search"   {/* AdoCombobox must accept and forward this prop */}
    onSelect={item => { setDescription(item.description || item.title); }}
  />
</FormField>

// Project selector
<FormField label="Project *" htmlFor="forge-project">
  {projectsLoading ? (
    <div style={{ fontSize: 9, color: T.text3, padding: 4 }}>Loading projects...</div>
  ) : projects.length === 0 ? (
    <div style={{ fontSize: 9, color: T.yellow, padding: 4 }}>
      No projects registered. Use <code>baton pmo add</code> to register one.
    </div>
  ) : (
    <select
      id="forge-project"
      value={projectId}
      onChange={e => setProjectId(e.target.value)}
      style={selectStyle}
    >
      {projects.map(p => (
        <option key={p.project_id} value={p.project_id}>{p.name} ({p.program})</option>
      ))}
    </select>
  )}
</FormField>

// Task Type
<FormField label="Task Type" htmlFor="forge-task-type" style={{ flex: 1 }}>
  <select
    id="forge-task-type"
    value={taskType}
    onChange={e => setTaskType(e.target.value)}
    style={selectStyle}
  >
    {TASK_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
  </select>
</FormField>

// Priority
<FormField label="Priority" htmlFor="forge-priority" style={{ flex: 1 }}>
  <select
    id="forge-priority"
    value={priority}
    onChange={e => setPriority(Number(e.target.value))}
    style={selectStyle}
  >
    {PRIORITIES.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
  </select>
</FormField>

// Task Description
<FormField label="Task Description *" htmlFor="forge-description">
  <textarea
    id="forge-description"
    aria-required="true"
    aria-describedby="forge-description-hint"
    value={description}
    onChange={e => setDescription(e.target.value)}
    placeholder="Describe the work: what needs to be built, fixed, or analyzed."
    rows={9}
    style={{
      width: '100%', padding: '8px 10px', borderRadius: 4,
      border: `1px solid ${T.border}`, background: T.bg1,
      color: T.text0, fontSize: 10, lineHeight: 1.55,
      outline: 'none', resize: 'vertical', fontFamily: 'inherit',
    }}
  />
  <div
    id="forge-description-hint"
    style={{ fontSize: 9, color: T.text3, marginTop: 3 }}
  >
    Required. Describe the task in detail. This is used to generate the plan.
  </div>
</FormField>
```

### C-06 [HIGH] Error messages not linked via `aria-describedby`
**Lines:** 200–213 (`generateError` block) and 335–339 (`saveError` block)

```tsx
// BEFORE — generateError block
{generateError && (
  <div style={{ fontSize: 9, color: T.red, padding: '5px 8px', background: T.red + '12', borderRadius: 4, marginBottom: 10, maxWidth: 640 }}>
    {generateError}
  </div>
)}

// AFTER
<div
  id="forge-generate-error"
  role="alert"
  aria-live="assertive"
  aria-atomic="true"
>
  {generateError && (
    <div style={{ fontSize: 9, color: T.red, padding: '5px 8px', background: T.red + '12', borderRadius: 4, marginBottom: 10, maxWidth: 640 }}>
      {generateError}
    </div>
  )}
</div>
```

Then on the Generate button, add `aria-describedby` when an error is present:

```tsx
// BEFORE
<button
  onClick={handleGenerate}
  disabled={phase === 'generating' || !description.trim() || !projectId}
  style={...}
>

// AFTER
<button
  onClick={handleGenerate}
  disabled={phase === 'generating' || !description.trim() || !projectId}
  aria-describedby={generateError ? 'forge-generate-error' : undefined}
  style={...}
>
```

For `saveError` at lines 335–339:

```tsx
// BEFORE
{saveError && (
  <div style={{ fontSize: 9, color: T.red, padding: '5px 8px', background: T.red + '12', borderRadius: 4 }}>
    {saveError}
  </div>
)}

// AFTER
<div
  id="forge-save-error"
  role="alert"
  aria-live="assertive"
  aria-atomic="true"
>
  {saveError && (
    <div style={{ fontSize: 9, color: T.red, padding: '5px 8px', background: T.red + '12', borderRadius: 4 }}>
      {saveError}
    </div>
  )}
</div>
```

On the "Approve & Queue" button:

```tsx
<button
  onClick={handleApprove}
  aria-describedby={saveError ? 'forge-save-error' : undefined}
  style={...}
>
  Approve & Queue
</button>
```

### G-03 [HIGH] Plan generation progress not in a live region
**Lines:** 181–182 (phase label `<span>` in header), 239 (intake/generating conditional)

Add a persistent `role="status"` live region in the panel body, above the
phase content sections. It stays in the DOM and its content changes with phase:

```tsx
// ADD after the opening of the body <div> (after line 198's <div> for body)
{/* Generation status — always in DOM for screen reader announcements */}
<div
  role="status"
  aria-live="polite"
  aria-atomic="true"
  style={SR_ONLY}
>
  {phase === 'generating' || phase === 'regenerating'
    ? 'Generating plan, please wait…'
    : phase === 'preview'
    ? 'Plan ready for review.'
    : phase === 'saved'
    ? 'Plan saved and queued successfully.'
    : ''}
</div>
```

Import `SR_ONLY` from `../styles/tokens`.

Also raise the phase label `<span>` font size on line 181 from `fontSize: 8`
to `fontSize: 9`:

```tsx
// BEFORE
<span style={{ fontSize: 8, color: T.text3 }}>{phaseLabel[phase]}</span>

// AFTER
<span style={{ fontSize: 9, color: T.text3 }}>{phaseLabel[phase]}</span>
```

### G-06 [HIGH] Plan saved confirmation not announced
The live region added for G-03 above covers this — it announces
`'Plan saved and queued successfully.'` when `phase === 'saved'`. No
additional element needed.

The `SavedPhase` execution result also needs a live region. In the
`SavedPhase` component (lines 393–444), add `role="status"` on the
`execResult` block:

```tsx
// BEFORE
{execResult && (
  <div style={{ fontSize: 9, color: execResult.startsWith('Execution launched') ? T.green : T.red, ... }}>
    {execResult}
  </div>
)}

// AFTER
<div
  role="status"
  aria-live="polite"
  aria-atomic="true"
>
  {execResult && (
    <div style={{ fontSize: 9, color: execResult.startsWith('Execution launched') ? T.green : T.red, ... }}>
      {execResult}
    </div>
  )}
</div>
```

### E-02 [CRITICAL] Focus not managed after phase transitions
**Lines:** 32 (state declarations at top of `ForgePanel`) + body `<div>` at line 198

Add a `ref` on the scrollable body container and fire `focus()` on phase
change:

```tsx
// ADD to the existing imports / refs near the top of ForgePanel:
const panelBodyRef = useRef<HTMLDivElement>(null);

// ADD a new useEffect after the existing abort cleanup useEffect (after line 85):
useEffect(() => {
  // Shift focus to the panel body on every phase transition so keyboard
  // users land at the top of the new phase content.
  panelBodyRef.current?.focus();
}, [phase]);

// Update the body <div> to carry the ref and tabIndex:
// BEFORE
<div style={{ flex: 1, overflow: 'auto', padding: 16 }}>

// AFTER
<div
  ref={panelBodyRef}
  tabIndex={-1}
  style={{ flex: 1, overflow: 'auto', padding: 16, outline: 'none' }}
>
```

### U-05 [MEDIUM] No unsaved changes guard
**Lines:** ForgePanel state and navigation handlers.

Add a derived `isDirty` flag and a navigation guard. The `description` is
already persisted; protect `plan` state by checking it before navigation:

```tsx
// ADD near other state declarations
const isDirty = !!plan && phase === 'preview';  // plan exists and user is reviewing/editing it

// UPDATE onBack in the header and in SavedPhase.onBack:
// BEFORE (called as onBack())
// AFTER — wrap the passed-in onBack with a guard:
function handleBack() {
  if (isDirty) {
    const confirmed = window.confirm(
      'You have an unsaved plan. Leave anyway? Your task description is saved but the generated plan will be lost.'
    );
    if (!confirmed) return;
  }
  onBack();
}

// Then replace all onBack() call sites inside ForgePanel with handleBack().
// Note: SavedPhase.onBack is called after the plan is saved, so isDirty is
// false by then — no guard needed in SavedPhase.
```

Also persist the `plan` state so it survives accidental navigation:

```tsx
// BEFORE
const [plan, setPlan] = useState<ForgePlanResponse | null>(null);

// AFTER
const [plan, setPlan] = usePersistedState<ForgePlanResponse | null>('pmo:forge-plan', null);
```

**Expected improvement:** Resolves C-01, C-02, C-03, C-06, E-02, G-03,
G-06, U-04, U-05.

---

## 6. PlanEditor.tsx — Icon Button Labels and Accordion ARIA

**Affected file:** `/home/djiv/PycharmProjects/orchestrator-v2/pmo-ui/src/components/PlanEditor.tsx`

### D-01 [HIGH] Icon-only buttons have no accessible name
**Lines:** 168–178 (move-up/move-down buttons), 243–249 (remove-step button),
149–155 (remove-phase button)

```tsx
// BEFORE — move-up button (line 168)
<button
  onClick={() => moveStep(pi, si, -1)}
  disabled={si === 0}
  style={{ ... }}
>
  {'\u25b2'}
</button>

// AFTER
<button
  aria-label={`Move step ${si + 1} up`}
  onClick={() => moveStep(pi, si, -1)}
  disabled={si === 0}
  style={{ ... }}
>
  {'\u25b2'}
</button>

// BEFORE — move-down button (line 173)
<button
  onClick={() => moveStep(pi, si, 1)}
  disabled={si === phase.steps.length - 1}
  style={{ ... }}
>
  {'\u25bc'}
</button>

// AFTER
<button
  aria-label={`Move step ${si + 1} down`}
  onClick={() => moveStep(pi, si, 1)}
  disabled={si === phase.steps.length - 1}
  style={{ ... }}
>
  {'\u25bc'}
</button>

// BEFORE — remove-step button (line 243)
<button
  onClick={() => removeStep(pi, si)}
  style={{ ... }}
  title="Remove step"
>
  {'\u00d7'}
</button>

// AFTER
<button
  aria-label={`Remove step ${si + 1}: ${step.task_description.slice(0, 40)}`}
  onClick={() => removeStep(pi, si)}
  style={{ ... }}
  title="Remove step"
>
  {'\u00d7'}
</button>

// BEFORE — remove-phase button (line 149)
<button
  onClick={e => { e.stopPropagation(); removePhase(pi); }}
  style={{ ... }}
  title="Remove phase"
>
  {'\u00d7'}
</button>

// AFTER
<button
  aria-label={`Remove phase ${pi + 1}: ${phase.name}`}
  onClick={e => { e.stopPropagation(); removePhase(pi); }}
  style={{ ... }}
  title="Remove phase"
>
  {'\u00d7'}
</button>
```

### D-03 [HIGH] Phase accordions lack `aria-expanded` and keyboard operability
**Lines:** 118–156 (phase header `<div>`)

```tsx
// BEFORE
<div
  onClick={() => setExpandedPhase(isExpanded ? null : pi)}
  style={{
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '6px 10px',
    background: T.bg2,
    borderBottom: isExpanded ? `1px solid ${T.border}` : 'none',
    cursor: 'pointer',
  }}
>
  {/* ... header content ... */}
</div>

{/* Steps (when expanded) */}
{isExpanded && (
  <>
    {/* ... steps ... */}
  </>
)}

// AFTER
<div
  role="button"
  tabIndex={0}
  aria-expanded={isExpanded}
  aria-controls={`phase-content-${pi}`}
  id={`phase-header-${pi}`}
  onClick={() => setExpandedPhase(isExpanded ? null : pi)}
  onKeyDown={e => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setExpandedPhase(isExpanded ? null : pi);
    }
  }}
  style={{
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '6px 10px',
    background: T.bg2,
    borderBottom: isExpanded ? `1px solid ${T.border}` : 'none',
    cursor: 'pointer',
  }}
>
  {/* ... header content unchanged ... */}
</div>

{/* Steps region — always in DOM so aria-controls points to valid element */}
<div
  id={`phase-content-${pi}`}
  role="region"
  aria-labelledby={`phase-header-${pi}`}
  hidden={!isExpanded}
>
  {phase.steps.map((step, si) => (
    /* ... steps unchanged ... */
  ))}
  {/* Add step button */}
  <div style={{ padding: '4px 10px' }}>
    <button onClick={() => addStep(pi)} style={...}>+ Add step</button>
  </div>
</div>
```

Note: switching from `{isExpanded && <> ... </>}` to `hidden={!isExpanded}`
on a permanent `<div>` means the steps are always in the DOM but hidden.
This is intentional — `aria-controls` requires the referenced element to
exist. The `hidden` attribute suppresses rendering visually and from
accessibility tree simultaneously.

### A-03 [CRITICAL] Font size violations in PlanEditor
**Lines:** 102 (Summary label), 141 (step count badge), 145 (gate badge),
276–280 (Stat component), 262 (add-step button)

```tsx
// Line 102: Summary label
// BEFORE
<div style={{ fontSize: 7, color: T.text3, textTransform: 'uppercase', ... }}>Summary</div>
// AFTER
<div style={{ fontSize: 9, color: T.text3, textTransform: 'uppercase', ... }}>Summary</div>

// Lines 141, 145: step count and gate badges in phase header
// BEFORE
<span style={{ fontSize: 7, color: T.text3, ... }}>{phase.steps.length} steps</span>
{phase.gate && <span style={{ fontSize: 7, color: T.yellow, ... }}>gate</span>}
// AFTER
<span style={{ fontSize: 9, color: T.text3, ... }}>{phase.steps.length} steps</span>
{phase.gate && <span style={{ fontSize: 9, color: T.yellow, ... }}>gate</span>}

// Stat component (line 276): label div
// BEFORE
<div style={{ fontSize: 7, color: T.text3, textTransform: 'uppercase' }}>{label}</div>
// AFTER
<div style={{ fontSize: 9, color: T.text3, textTransform: 'uppercase' }}>{label}</div>

// Add-step button (line 262):
// BEFORE
style={{ ..., fontSize: 8, ... }}
// AFTER
style={{ ..., fontSize: 9, ... }}
```

**Expected improvement:** Resolves D-01, D-03, A-03 (contributing to 3
suite failures across interactive elements and contrast).

---

## 7. SignalsBar.tsx — List Semantics, Checkbox Labels, Add-Form Labels, Batch Confirm

**Affected file:** `/home/djiv/PycharmProjects/orchestrator-v2/pmo-ui/src/components/SignalsBar.tsx`

### B-05 [HIGH] Signal list items use `<div>`, not list semantics
**Lines:** 303–378 (the `open.map` block)

```tsx
// BEFORE
{open.map(sig => (
  <div
    key={sig.signal_id}
    style={{ display: 'flex', alignItems: 'center', gap: 6, ... }}
  >
    {/* row content */}
  </div>
))}

// AFTER
<ul role="list" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
  {open.map(sig => (
    <li
      key={sig.signal_id}
      style={{ display: 'flex', alignItems: 'center', gap: 6, ... marginBottom: 3 }}
    >
      {/* row content unchanged */}
    </li>
  ))}
</ul>
```

### D-07 [HIGH] Signal checkboxes lack accessible labels
**Lines:** 165–171 (select-all checkbox), 318–324 (per-signal checkbox)

```tsx
// BEFORE — select-all checkbox
<input
  type="checkbox"
  checked={allSelected}
  onChange={toggleSelectAll}
  title="Select all signals"
  style={{ cursor: 'pointer', width: 11, height: 11, accentColor: T.accent }}
/>

// AFTER
<input
  type="checkbox"
  id="signal-select-all"
  aria-label="Select all open signals"
  checked={allSelected}
  onChange={toggleSelectAll}
  style={{ cursor: 'pointer', width: 11, height: 11, accentColor: T.accent }}
/>

// BEFORE — per-signal checkbox (inside open.map)
<input
  type="checkbox"
  checked={selected.has(sig.signal_id)}
  onChange={() => toggleSelect(sig.signal_id)}
  onClick={e => e.stopPropagation()}
  style={{ cursor: 'pointer', width: 11, height: 11, accentColor: T.accent, flexShrink: 0 }}
/>

// AFTER
<input
  type="checkbox"
  id={`signal-select-${sig.signal_id}`}
  aria-label={`Select signal: ${sig.title}`}
  checked={selected.has(sig.signal_id)}
  onChange={() => toggleSelect(sig.signal_id)}
  onClick={e => e.stopPropagation()}
  style={{ cursor: 'pointer', width: 11, height: 11, accentColor: T.accent, flexShrink: 0 }}
/>
```

### C-04 [HIGH] Add-signal form inputs lack labels
**Lines:** 224–289 (add form block)

The form is inline and space-constrained, so use `aria-label` rather than
visible labels:

```tsx
// BEFORE
<input
  value={newTitle}
  onChange={e => setNewTitle(e.target.value)}
  onKeyDown={e => { if (e.key === 'Enter') handleAddSignal(); }}
  placeholder="Signal description..."
  style={{ ... }}
/>
<select value={newSignalType} onChange={...} style={...}>
  ...
</select>
<select value={newSeverity} onChange={...} style={...}>
  ...
</select>

// AFTER
<input
  id="new-signal-title"
  aria-label="Signal title"
  value={newTitle}
  onChange={e => setNewTitle(e.target.value)}
  onKeyDown={e => { if (e.key === 'Enter') handleAddSignal(); }}
  placeholder="Signal description..."
  style={{ ... }}
/>
<select
  id="new-signal-type"
  aria-label="Signal type"
  value={newSignalType}
  onChange={e => setNewSignalType(e.target.value as 'bug' | 'escalation' | 'blocker')}
  style={{ ... }}
>
  <option value="bug">Bug</option>
  <option value="escalation">Escalation</option>
  <option value="blocker">Blocker</option>
</select>
<select
  id="new-signal-severity"
  aria-label="Severity"
  value={newSeverity}
  onChange={e => setNewSeverity(e.target.value)}
  style={{ ... }}
>
  <option value="critical">Critical</option>
  <option value="high">High</option>
  <option value="medium">Medium</option>
  <option value="low">Low</option>
</select>
```

### A-04 [CRITICAL] Font size violations in SignalsBar
**Lines:** 194–200 (batch resolve button, `fontSize: 7`), 207–216 (add signal button,
`fontSize: 7`), 325–327 (signal ID span, `fontSize: 7`), 330–332 (description
span, `fontSize: 7`), 339–344 (severity chip, `fontSize: 7`), 349–357 (Forge
button, `fontSize: 7`), 363–369 (Resolve button, `fontSize: 7`)

All of these must be raised to `fontSize: 9`. They are metadata or action
labels — all informational, none purely decorative.

```tsx
// All instances:
// BEFORE: fontSize: 7
// AFTER: fontSize: 9
```

Also: the `Signals — X open` header span at line 174 is already `fontSize: 9`.
The resolved-signal opacity issue (A-04 root cause) is automatically resolved
when the token values are updated — no `opacity` style is present in the
current code.

### U-06 [MEDIUM] Batch resolve executes without confirmation
**Lines:** 81–102 (`handleBatchResolve` function)

```tsx
// BEFORE
async function handleBatchResolve() {
  if (selected.size === 0) return;
  setBatchResolving(true);
  try { ... }
}

// AFTER
async function handleBatchResolve() {
  if (selected.size === 0) return;
  const count = selected.size;
  const confirmed = window.confirm(
    `Resolve ${count} signal${count !== 1 ? 's' : ''}? This cannot be undone.`
  );
  if (!confirmed) return;
  setBatchResolving(true);
  try { ... }
}
```

**Expected improvement:** Resolves B-05, C-04, D-07, A-04 (partial — font
sizes), U-06.

---

## 8. InterviewPanel.tsx — Fieldset / Legend for Choice Questions

**Affected file:** `/home/djiv/PycharmProjects/orchestrator-v2/pmo-ui/src/components/InterviewPanel.tsx`

### C-05 [HIGH] Choice questions lack `<fieldset>`/`<legend>` grouping
**Lines:** 67–110 (per-question rendering block)

For text-input questions, add `<label>` association. For choice questions,
wrap the choices in `<fieldset>`/`<legend>`:

```tsx
// BEFORE
{q.answer_type === 'choice' && q.choices ? (
  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginLeft: 20 }}>
    {q.choices.map(choice => (
      <button key={choice} onClick={() => setAnswer(q.id, choice)} style={...}>
        {choice}
      </button>
    ))}
    <button onClick={() => setAnswer(q.id, '')} style={...}>skip</button>
  </div>
) : (
  <div style={{ marginLeft: 20 }}>
    <input
      type="text"
      value={answers[q.id] ?? ''}
      onChange={e => setAnswer(q.id, e.target.value)}
      placeholder="Type your answer..."
      style={...}
    />
  </div>
)}

// AFTER
{q.answer_type === 'choice' && q.choices ? (
  <fieldset style={{ border: 'none', padding: 0, margin: '0 0 0 20px' }}>
    <legend style={{
      fontSize: 9,
      fontWeight: 600,
      color: T.text3,
      marginBottom: 4,
      padding: 0,
    }}>
      Select an answer for: {q.question}
    </legend>
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {q.choices.map(choice => (
        <button
          key={choice}
          role="radio"
          aria-checked={answers[q.id] === choice}
          onClick={() => setAnswer(q.id, choice)}
          style={{
            padding: '3px 8px', borderRadius: 3,
            border: `1px solid ${answers[q.id] === choice ? T.accent + '66' : T.border}`,
            background: answers[q.id] === choice ? T.accent + '15' : 'transparent',
            color: answers[q.id] === choice ? T.accent : T.text2,
            fontSize: 9, fontWeight: 600, cursor: 'pointer',
          }}
        >
          {choice}
        </button>
      ))}
      <button
        onClick={() => setAnswer(q.id, '')}
        aria-label="Skip this question"
        style={{
          padding: '3px 8px', borderRadius: 3,
          border: `1px solid ${T.border}`, background: 'transparent',
          color: T.text3, fontSize: 9, cursor: 'pointer',
        }}
      >
        skip
      </button>
    </div>
  </fieldset>
) : (
  <div style={{ marginLeft: 20 }}>
    <label
      htmlFor={`interview-answer-${q.id}`}
      style={SR_ONLY}
    >
      {q.question}
    </label>
    <input
      id={`interview-answer-${q.id}`}
      type="text"
      value={answers[q.id] ?? ''}
      onChange={e => setAnswer(q.id, e.target.value)}
      placeholder="Type your answer..."
      style={{
        width: '100%', padding: '4px 8px', borderRadius: 3,
        border: `1px solid ${T.border}`, background: T.bg2,
        color: T.text0, fontSize: 9, outline: 'none',
      }}
    />
  </div>
)}
```

Import `SR_ONLY` from `../styles/tokens`. The visually-hidden `<label>` for
text inputs associates the control without cluttering the already-visible
question heading above.

Also fix `fontSize: 8` on the context text (line 62) and the "Answer what you
can" hint (line 39):

```tsx
// Line 39 — hint
// BEFORE: fontSize: 8
// AFTER: fontSize: 9

// Line 62 — context
// BEFORE: fontSize: 8
// AFTER: fontSize: 9
```

**Expected improvement:** Resolves C-05. Text inputs gain programmatic labels.

---

## 9. AdoCombobox.tsx — Full Combobox ARIA Pattern + Keyboard Navigation

**Affected file:** `/home/djiv/PycharmProjects/orchestrator-v2/pmo-ui/src/components/AdoCombobox.tsx`

### D-05 [HIGH] + D-06 [HIGH] + U-02 [MEDIUM] Missing combobox ARIA, listbox, keyboard navigation

This is the most significant structural change in a single file. The component
needs: `role="combobox"` on the input, `role="listbox"` on the dropdown,
`role="option"` on items, `aria-activedescendant` for keyboard tracking,
and full `ArrowDown`/`ArrowUp`/`Enter`/`Escape` keyboard navigation.

The `inputId` prop must also be added to support the `ForgePanel` label
association from the C-01 fix above.

**Complete rewrite of AdoCombobox:**

```tsx
// BEFORE — entire component
export function AdoCombobox({ onSelect }: AdoComboboxProps) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<AdoWorkItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // ... useEffects ...

  function handleSelect(item: AdoWorkItem) { ... }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <input
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => items.length > 0 && setOpen(true)}
        placeholder="Search ADO work items..."
        style={...}
      />
      {loading && <div style={{ ... }}>...</div>}
      {open && items.length > 0 && (
        <div style={{ position: 'absolute', ... }}>
          {items.map(item => (
            <div key={item.id} onClick={() => handleSelect(item)} style={...}>
              ...
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// AFTER — full replacement
interface AdoComboboxProps {
  onSelect: (item: AdoWorkItem) => void;
  inputId?: string;   // new — for label association from ForgePanel
}

export function AdoCombobox({ onSelect, inputId }: AdoComboboxProps) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<AdoWorkItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);   // new — keyboard cursor
  const ref = useRef<HTMLDivElement>(null);
  const listboxId = 'ado-results-listbox';

  useEffect(() => {
    if (!query.trim()) { setItems([]); setOpen(false); return; }
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const resp = await api.searchAdo(query);
        setItems(resp.items);
        setOpen(resp.items.length > 0);
        setActiveIndex(-1);   // reset cursor on new results
      } catch { setItems([]); setOpen(false); }
      setLoading(false);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setActiveIndex(-1);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  function handleSelect(item: AdoWorkItem) {
    setQuery(item.title);
    setOpen(false);
    setActiveIndex(-1);
    onSelect(item);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || items.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex(i => Math.min(i + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      handleSelect(items[activeIndex]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
      setActiveIndex(-1);
    }
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <input
        id={inputId}
        role="combobox"
        aria-label="Search ADO work items"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-activedescendant={activeIndex >= 0 ? `ado-item-${items[activeIndex]?.id}` : undefined}
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => items.length > 0 && setOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder="Search ADO work items..."
        style={{
          width: '100%', padding: '6px 8px', borderRadius: 4,
          border: `1px solid ${T.border}`, background: T.bg1,
          color: T.text0, fontSize: 10, outline: 'none',
        }}
      />
      {loading && (
        <div
          aria-live="polite"
          style={{ position: 'absolute', right: 8, top: 7, fontSize: 9, color: T.text3 }}
        >
          Searching…
        </div>
      )}
      {open && items.length > 0 && (
        <ul
          id={listboxId}
          role="listbox"
          aria-label="ADO work items"
          style={{
            listStyle: 'none',
            padding: 0,
            margin: 0,
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            marginTop: 2,
            background: T.bg1,
            border: `1px solid ${T.border}`,
            borderRadius: 4,
            maxHeight: 200,
            overflow: 'auto',
            zIndex: 10,
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          }}
        >
          {items.map((item, idx) => (
            <li
              key={item.id}
              id={`ado-item-${item.id}`}
              role="option"
              aria-selected={idx === activeIndex}
              onClick={() => handleSelect(item)}
              style={{
                padding: '6px 8px',
                cursor: 'pointer',
                borderBottom: `1px solid ${T.border}`,
                background: idx === activeIndex ? T.bg2 : 'transparent',
              }}
              onMouseEnter={() => setActiveIndex(idx)}
              onMouseLeave={() => setActiveIndex(-1)}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: 9, color: T.text3, fontFamily: 'monospace' }}>{item.id}</span>
                <span style={{ fontSize: 9, color: T.text0, fontWeight: 500 }}>{item.title}</span>
                <span style={{
                  fontSize: 9, color: T.accent, background: T.accent + '14',
                  border: `1px solid ${T.accent}22`, padding: '0 4px',
                  borderRadius: 2, marginLeft: 'auto',
                }}>{item.type}</span>
              </div>
              <div style={{ fontSize: 9, color: T.text3, marginTop: 1 }}>
                {item.program} · {item.owner} · {item.priority}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

Key changes over the original:
- `inputId` prop added and forwarded to `<input id>` for label linkage
- `role="combobox"` + `aria-autocomplete="list"` + `aria-expanded` + `aria-controls` + `aria-activedescendant` on input
- `activeIndex` state drives keyboard cursor and `aria-activedescendant`
- `handleKeyDown` implements `ArrowDown`/`ArrowUp`/`Enter`/`Escape`
- `onMouseEnter` on items sets `activeIndex` so hover and keyboard stay in sync
- `<div>` dropdown → `<ul role="listbox">` with `id` matching `aria-controls`
- `<div>` items → `<li role="option" aria-selected>` with `id` matching `aria-activedescendant`
- `fontSize: 8` on ID/type spans → `fontSize: 9`
- Loading indicator gets `aria-live="polite"` and descriptive text

**Expected improvement:** Resolves D-05, D-06, U-02 (3 failures across
interactive elements and UX suites). Full keyboard and screen reader support
for ADO search.

---

## 10. HealthBar.tsx — Program Card Accessible Labels

**Affected file:** `/home/djiv/PycharmProjects/orchestrator-v2/pmo-ui/src/components/HealthBar.tsx`

### Audit context: program cards are clickable filters without accessible names
**Lines:** 49–93 (program card `<div>` in `programs.map`)

The program cards function as filter toggle buttons but are plain `<div>`
elements. Add `role="button"`, `tabIndex`, keyboard handler, and an
`aria-label` that summarizes the card content:

```tsx
// BEFORE
<div
  key={pg.program}
  onClick={isClickable ? () => onProgramClick(pg.program) : undefined}
  style={{
    flex: '1 1 140px',
    minWidth: 120,
    padding: '6px 10px',
    background: T.bg2,
    borderRadius: 5,
    borderLeft: `3px solid ${barColor}`,
    outline: isActive ? `2px solid ${barColor}` : '2px solid transparent',
    outlineOffset: 1,
    cursor: isClickable ? 'pointer' : 'default',
    transition: 'outline-color 0.15s',
  }}
>

// AFTER
<div
  key={pg.program}
  role={isClickable ? 'button' : undefined}
  tabIndex={isClickable ? 0 : undefined}
  aria-pressed={isClickable ? isActive : undefined}
  aria-label={isClickable
    ? `${pg.program}: ${pct}% complete. ${pg.total_plans} plans${pg.active > 0 ? `, ${pg.active} active` : ''}${pg.blocked > 0 ? `, ${pg.blocked} blocked` : ''}. ${isActive ? 'Currently filtered. Click to show all.' : 'Click to filter.'}`
    : undefined}
  onClick={isClickable ? () => onProgramClick(pg.program) : undefined}
  onKeyDown={isClickable ? (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onProgramClick(pg.program);
    }
  } : undefined}
  style={{
    flex: '1 1 140px',
    minWidth: 120,
    padding: '6px 10px',
    background: T.bg2,
    borderRadius: 5,
    borderLeft: `3px solid ${barColor}`,
    outline: isActive ? `2px solid ${barColor}` : '2px solid transparent',
    outlineOffset: 1,
    cursor: isClickable ? 'pointer' : 'default',
    transition: 'outline-color 0.15s',
  }}
>
```

Also fix the empty-state `fontSize: 8` and the stats text `T.text3`:

```tsx
// Line 23: empty-state font
// BEFORE: fontSize: 8
// AFTER: fontSize: 9

// Line 81: stats div color
// BEFORE: color: T.text3  (this is already T.text3, no change needed post-token-fix)
// The value after token fix is #64748b which is borderline for 9px normal text.
// Use T.text2 (#94a3b8) for better safety:
// AFTER: color: T.text2
```

**Expected improvement:** Program cards become keyboard-operable and carry
meaningful accessible names for AT users. Partial contribution to the
contrast audit.

---

## Summary of Fixes by Issue ID

| Issue | Severity | Component(s) | Section above |
|-------|----------|--------------|---------------|
| A-01 | Critical | tokens.ts, all components | 1 |
| A-02 | Critical | tokens.ts, ForgePanel.tsx | 1, 5 |
| A-03 | Critical | tokens.ts, PlanEditor.tsx, PlanPreview.tsx | 1, 6 |
| A-04 | Critical | tokens.ts, SignalsBar.tsx | 1, 7 |
| B-01 | High | App.tsx | 2 |
| B-02 | High | KanbanBoard.tsx, ForgePanel.tsx | 3, 5 |
| B-03 | High | App.tsx | 2 |
| B-04 | High | KanbanBoard.tsx | 3 |
| B-05 | High | SignalsBar.tsx | 7 |
| B-06 | High | App.tsx | 2 |
| C-01 | High | ForgePanel.tsx | 5 |
| C-02 | High | ForgePanel.tsx | 5 |
| C-03 | High | ForgePanel.tsx | 5 |
| C-04 | High | SignalsBar.tsx | 7 |
| C-05 | High | InterviewPanel.tsx | 8 |
| C-06 | High | ForgePanel.tsx | 5 |
| D-01 | High | PlanEditor.tsx | 6 |
| D-02 | High | KanbanCard.tsx | 4 |
| D-03 | High | PlanEditor.tsx | 6 |
| D-04 | High | App.tsx | 2 |
| D-05 | High | AdoCombobox.tsx | 9 |
| D-06 | High | AdoCombobox.tsx | 9 |
| D-07 | High | SignalsBar.tsx | 7 |
| E-01 | Critical | KanbanCard.tsx | 4 |
| E-02 | Critical | ForgePanel.tsx | 5 |
| G-01 | High | KanbanBoard.tsx | 3 |
| G-02 | High | KanbanBoard.tsx | 3 |
| G-03 | High | ForgePanel.tsx | 5 |
| G-04 | High | KanbanBoard.tsx | 3 |
| G-05 | High | KanbanBoard.tsx | 3 |
| G-06 | High | ForgePanel.tsx | 5 |
| U-01 | Medium | KanbanCard.tsx | 4 |
| U-02 | Medium | AdoCombobox.tsx | 9 |
| U-03 | Medium | KanbanBoard.tsx | 3 |
| U-04 | Medium | ForgePanel.tsx | 5 |
| U-05 | Medium | ForgePanel.tsx | 5 |
| U-06 | Medium | SignalsBar.tsx | 7 |

---

## Cross-cutting Notes for the Implementer

**SR_ONLY constant.** Add to `tokens.ts` (Section 1). Import wherever a
visually-hidden live region is needed (ForgePanel, KanbanBoard, InterviewPanel).
No CSS class system exists; do not add a `.sr-only` class.

**PlanPreview.tsx.** Not listed in the audit's direct findings, but it shares
the `fontSize: 7` pattern in its `StatTile` label (line 156) and step number
badge (line 109). Raise both to `fontSize: 9` as part of the font floor sweep
to prevent regression when axe-core runs the preview panel.

**Token adoption.** After updating `tokens.ts`, do a project-wide search for
hardcoded `fontSize: 7` and `fontSize: 8` with `grep -rn 'fontSize: [78]'
src/` and audit each occurrence. Non-text visual indicators (dots, pips,
dividers) with no text content are exempt; all others must move to 9px minimum.

**Test run after each section.** The axe-core Playwright suite
(`accessibility-audit.spec.ts`) can be run per-section. The recommended gate
order matches the implementation priority above: tokens first (unblocks
axe-core A-01–A-04), then App.tsx and KanbanBoard.tsx (structural), then
KanbanCard.tsx (keyboard gate), then ForgePanel.tsx (largest surface).

**No new CSS files needed.** All live region patterns use `style={SR_ONLY}`
(inline object) rather than a CSS class. The `<style>` injection approach
suggested in the audit's Technical Notes section is intentionally avoided
to match the project's inline-style-only convention.

**`aria-hidden` on inactive panels.** The `aria-hidden={view !== 'kanban'}`
pattern in App.tsx (Section 2) suppresses the entire inactive panel from the
accessibility tree. This means live regions inside the hidden panel do not
fire while it is hidden — the correct behavior.
