/**
 * Derives a single, unambiguous display status for a card's execution from
 * the several independent signals the PMO API exposes (kanban column, last
 * error, and the outcome of a user-initiated pause/resume/decision action).
 *
 * `GET /pmo/cards/{card_id}/execution` currently mirrors `card.column`
 * verbatim as its `status` field (see `agent_baton/api/routes/pmo.py`'s
 * `get_card_execution`) -- it does NOT surface the richer engine
 * `ExecutionState.status` vocabulary (`running`, `paused-takeover`,
 * `complete`, etc). So "paused", "resuming", "failed", and "completed" are
 * NOT distinct `column` values on their own; this module reconstructs them
 * from the signals actually available to the PMO UI: the kanban column, the
 * card's `error` field, and the last pause/resume/decision-resolve action the
 * operator took in this session (`controlStatus` / `justResumed`).
 *
 * Kept as a pure function (no React, no fetch) so it can be unit-tested
 * directly against every input combination without mocking the API client.
 */

export type DisplayExecutionStatus =
  | 'completed'
  | 'failed'
  | 'paused'
  | 'resuming'
  | 'awaiting_decision'
  | 'executing'
  | 'queued'
  | 'other';

export interface ExecutionStatusInput {
  /** The card's kanban column (PmoCard.column). */
  column: string;
  /** The card's last recorded error message, if any. */
  error?: string | null;
  /** Result of the last pause/resume control call this session, if any. */
  controlStatus?: 'paused' | 'running' | null;
  /** True when a decision was just resolved and the engine reported
   * `execution_resumed: true` -- the board/execution poll hasn't
   * necessarily caught up to `column` flipping back to `executing` yet. */
  justResumedViaDecision?: boolean;
  /** True when at least one decision (manager or generic) is still pending
   * for this card. */
  hasPendingDecisions?: boolean;
}

/**
 * Priority order (highest first): a recorded failure always wins (an
 * operator must never see "paused" painted over a card that actually
 * failed); then a completed/deployed card; then an explicit local
 * pause/resume action; then "awaiting a decision"; then the coarse
 * column-derived buckets.
 */
export function deriveExecutionStatus(input: ExecutionStatusInput): DisplayExecutionStatus {
  const { column, error, controlStatus, justResumedViaDecision, hasPendingDecisions } = input;

  if (error && error.trim().length > 0) return 'failed';
  if (column === 'deployed') return 'completed';
  if (controlStatus === 'paused') return 'paused';
  if (justResumedViaDecision || controlStatus === 'running') return 'resuming';
  if (hasPendingDecisions || column === 'awaiting_human') return 'awaiting_decision';
  if (column === 'executing' || column === 'validating' || column === 'review' || column === 'awaiting_review') {
    return 'executing';
  }
  if (column === 'queued' || column === 'intake') return 'queued';
  return 'other';
}

export interface StatusMeta {
  label: string;
  /** A non-color glyph so the status is never conveyed by color alone. */
  symbol: string;
  colorKey: 'mint' | 'cherry' | 'tangerine' | 'butter' | 'blueberry' | 'text2';
}

export const STATUS_META: Record<DisplayExecutionStatus, StatusMeta> = {
  completed: { label: 'Completed', symbol: '✓', colorKey: 'mint' },
  failed: { label: 'Failed', symbol: '✕', colorKey: 'cherry' },
  paused: { label: 'Paused', symbol: '⏸', colorKey: 'tangerine' },
  resuming: { label: 'Resuming', symbol: '↻', colorKey: 'butter' },
  awaiting_decision: { label: 'Awaiting decision', symbol: '⏳', colorKey: 'blueberry' },
  executing: { label: 'Executing', symbol: '▶', colorKey: 'butter' },
  queued: { label: 'Queued', symbol: '•', colorKey: 'text2' },
  other: { label: 'Unknown', symbol: '?', colorKey: 'text2' },
};
