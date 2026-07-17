import { describe, it, expect } from 'vitest';
import { deriveExecutionStatus, STATUS_META } from '../executionStatus';

describe('deriveExecutionStatus', () => {
  it('reports failed whenever an error is present, regardless of column', () => {
    expect(deriveExecutionStatus({ column: 'executing', error: 'boom' })).toBe('failed');
    expect(deriveExecutionStatus({ column: 'deployed', error: 'boom' })).toBe('failed');
    expect(deriveExecutionStatus({ column: 'awaiting_human', error: 'boom' })).toBe('failed');
  });

  it('ignores a blank/whitespace-only error string', () => {
    expect(deriveExecutionStatus({ column: 'executing', error: '' })).not.toBe('failed');
    expect(deriveExecutionStatus({ column: 'executing', error: '   ' })).not.toBe('failed');
  });

  it('reports completed for a deployed card with no error', () => {
    expect(deriveExecutionStatus({ column: 'deployed' })).toBe('completed');
  });

  it('reports paused when the last control action was pause, even mid-execution', () => {
    expect(deriveExecutionStatus({ column: 'executing', controlStatus: 'paused' })).toBe('paused');
  });

  it('reports resuming right after a decision resolve reports execution_resumed', () => {
    expect(
      deriveExecutionStatus({ column: 'awaiting_human', justResumedViaDecision: true }),
    ).toBe('resuming');
  });

  it('reports resuming after an explicit resume control call', () => {
    expect(deriveExecutionStatus({ column: 'awaiting_human', controlStatus: 'running' })).toBe('resuming');
  });

  it('prioritizes failed over a pause/resume control flag (a failed worker cannot be paused)', () => {
    expect(
      deriveExecutionStatus({ column: 'validating', error: 'crashed', controlStatus: 'paused' }),
    ).toBe('failed');
  });

  it('prioritizes completed over a stale pause flag', () => {
    expect(deriveExecutionStatus({ column: 'deployed', controlStatus: 'paused' })).toBe('completed');
  });

  it('reports awaiting_decision when the column is awaiting_human with no other signal', () => {
    expect(deriveExecutionStatus({ column: 'awaiting_human' })).toBe('awaiting_decision');
  });

  it('reports awaiting_decision when pending decisions exist even on an executing column', () => {
    expect(
      deriveExecutionStatus({ column: 'executing', hasPendingDecisions: true }),
    ).toBe('awaiting_decision');
  });

  it('reports executing for active work columns', () => {
    expect(deriveExecutionStatus({ column: 'executing' })).toBe('executing');
    expect(deriveExecutionStatus({ column: 'validating' })).toBe('executing');
    expect(deriveExecutionStatus({ column: 'review' })).toBe('executing');
    expect(deriveExecutionStatus({ column: 'awaiting_review' })).toBe('executing');
  });

  it('reports queued for intake/queued columns', () => {
    expect(deriveExecutionStatus({ column: 'queued' })).toBe('queued');
    expect(deriveExecutionStatus({ column: 'intake' })).toBe('queued');
  });

  it('falls back to other for an unrecognized column', () => {
    expect(deriveExecutionStatus({ column: 'some-future-column' })).toBe('other');
  });

  it('exposes a non-color glyph for every status so color is never the only signal', () => {
    for (const key of Object.keys(STATUS_META) as (keyof typeof STATUS_META)[]) {
      expect(STATUS_META[key].symbol.length).toBeGreaterThan(0);
      expect(STATUS_META[key].label.length).toBeGreaterThan(0);
    }
  });

  it('gives paused, resuming, failed, and completed each a distinct label', () => {
    const labels = new Set([
      STATUS_META.paused.label,
      STATUS_META.resuming.label,
      STATUS_META.failed.label,
      STATUS_META.completed.label,
    ]);
    expect(labels.size).toBe(4);
  });
});
