/**
 * audit-reporter.ts — collects test results and generates a structured
 * audit-report.md in e2e/reports/ after the suite completes.
 *
 * Usage inside tests:
 *
 *   import { AuditReporter } from '../utils/audit-reporter.js';
 *   const reporter = AuditReporter.getInstance();
 *   reporter.record('smoke', 'navbar renders', 'pass', { screenshotPath });
 *
 * Usage in a global setup / teardown (playwright.config.ts globalSetup):
 *
 *   import { AuditReporter } from './e2e/utils/audit-reporter.js';
 *   AuditReporter.getInstance().writeReport();
 *
 * The report is written to: e2e/reports/audit-report.md
 */

/// <reference types="node" />
import * as fs from 'node:fs';
import * as path from 'node:path';

export type TestStatus = 'pass' | 'fail' | 'skip';

export interface TestRecord {
  /** Test suite / spec file name */
  suite: string;
  /** Individual test title */
  title: string;
  /** Outcome */
  status: TestStatus;
  /** Elapsed time in milliseconds */
  durationMs?: number;
  /** Absolute path to screenshot (if captured) */
  screenshotPath?: string;
  /** Any additional metadata */
  metadata?: Record<string, string | number | boolean>;
  /** Error message on failure */
  error?: string;
  /** Timestamp (ISO-8601) */
  timestamp: string;
}

export interface AuditSummary {
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  passRate: string;
  generatedAt: string;
}

// ---------------------------------------------------------------------------

const REPORT_DIR = path.resolve(
  new URL('..', import.meta.url).pathname,
  'reports',
);

const REPORT_PATH = path.join(REPORT_DIR, 'audit-report.md');

// ---------------------------------------------------------------------------

export class AuditReporter {
  private static _instance: AuditReporter | null = null;
  private records: TestRecord[] = [];

  private constructor() {}

  static getInstance(): AuditReporter {
    if (!AuditReporter._instance) {
      AuditReporter._instance = new AuditReporter();
    }
    return AuditReporter._instance;
  }

  // ---------------------------------------------------------------------------
  // Recording
  // ---------------------------------------------------------------------------

  /**
   * Record a single test result.
   */
  record(
    suite: string,
    title: string,
    status: TestStatus,
    opts: {
      durationMs?: number;
      screenshotPath?: string;
      metadata?: Record<string, string | number | boolean>;
      error?: string;
    } = {},
  ): void {
    this.records.push({
      suite,
      title,
      status,
      durationMs: opts.durationMs,
      screenshotPath: opts.screenshotPath,
      metadata: opts.metadata,
      error: opts.error,
      timestamp: new Date().toISOString(),
    });
  }

  /**
   * Merge in records from an external JSON results file (e.g. Playwright's
   * built-in JSON reporter output).
   */
  mergeFromPlaywrightJson(resultsPath: string): void {
    if (!fs.existsSync(resultsPath)) return;

    try {
      const raw = JSON.parse(fs.readFileSync(resultsPath, 'utf8'));
      // Playwright JSON reporter shape: { suites: [{ title, specs: [{ title, tests: [{ status, ... }] }] }] }
      for (const suite of raw.suites ?? []) {
        for (const spec of suite.specs ?? []) {
          for (const testResult of spec.tests ?? []) {
            const result = testResult.results?.[0];
            const status: TestStatus =
              result?.status === 'passed' ? 'pass'
              : result?.status === 'skipped' ? 'skip'
              : 'fail';
            this.record(suite.title ?? 'unknown', spec.title ?? 'unknown', status, {
              durationMs: result?.duration,
              error: result?.error?.message,
            });
          }
        }
      }
    } catch {
      // If parsing fails, continue without merging.
    }
  }

  // ---------------------------------------------------------------------------
  // Summary calculation
  // ---------------------------------------------------------------------------

  getSummary(): AuditSummary {
    const total = this.records.length;
    const passed = this.records.filter(r => r.status === 'pass').length;
    const failed = this.records.filter(r => r.status === 'fail').length;
    const skipped = this.records.filter(r => r.status === 'skip').length;
    const passRate = total > 0 ? `${Math.round((passed / total) * 100)}%` : 'N/A';
    return {
      total,
      passed,
      failed,
      skipped,
      passRate,
      generatedAt: new Date().toISOString(),
    };
  }

  getRecords(): TestRecord[] {
    return [...this.records];
  }

  // ---------------------------------------------------------------------------
  // Report generation
  // ---------------------------------------------------------------------------

  /**
   * Write the audit report to e2e/reports/audit-report.md.
   * Creates the directory if it does not exist.
   */
  writeReport(): string {
    if (!fs.existsSync(REPORT_DIR)) {
      fs.mkdirSync(REPORT_DIR, { recursive: true });
    }

    const md = this.buildMarkdown();
    fs.writeFileSync(REPORT_PATH, md, 'utf8');
    return REPORT_PATH;
  }

  /**
   * Build the Markdown content for the audit report.
   */
  buildMarkdown(): string {
    const summary = this.getSummary();
    const suiteMap = new Map<string, TestRecord[]>();

    for (const record of this.records) {
      const existing = suiteMap.get(record.suite) ?? [];
      existing.push(record);
      suiteMap.set(record.suite, existing);
    }

    const statusEmoji: Record<TestStatus, string> = {
      pass: 'PASS',
      fail: 'FAIL',
      skip: 'SKIP',
    };

    const lines: string[] = [
      '# Baton PMO UI — E2E Audit Report',
      '',
      `**Generated:** ${summary.generatedAt}`,
      '',
      '## Summary',
      '',
      '| Metric | Value |',
      '|--------|-------|',
      `| Total tests | ${summary.total} |`,
      `| Passed | ${summary.passed} |`,
      `| Failed | ${summary.failed} |`,
      `| Skipped | ${summary.skipped} |`,
      `| Pass rate | ${summary.passRate} |`,
      '',
    ];

    if (summary.failed > 0) {
      lines.push('## Failed Tests', '');
      for (const [suite, records] of suiteMap) {
        const failed = records.filter(r => r.status === 'fail');
        if (failed.length === 0) continue;
        lines.push(`### ${suite}`, '');
        for (const r of failed) {
          lines.push(`- **${r.title}**`);
          if (r.error) {
            lines.push(`  - Error: \`${r.error.slice(0, 200)}\``);
          }
          if (r.screenshotPath) {
            lines.push(`  - Screenshot: \`${r.screenshotPath}\``);
          }
        }
        lines.push('');
      }
    }

    lines.push('## All Results', '');

    for (const [suite, records] of suiteMap) {
      lines.push(`### ${suite}`, '');
      lines.push('| Status | Test | Duration | Notes |');
      lines.push('|--------|------|----------|-------|');

      for (const r of records) {
        const status = statusEmoji[r.status];
        const duration = r.durationMs != null ? `${r.durationMs}ms` : '—';
        const notes: string[] = [];
        if (r.screenshotPath) notes.push('has screenshot');
        if (r.error) notes.push(`error: ${r.error.slice(0, 60)}`);
        lines.push(`| ${status} | ${r.title} | ${duration} | ${notes.join('; ') || '—'} |`);
      }
      lines.push('');
    }

    if (this.records.length === 0) {
      lines.push('_No test results recorded._', '');
    }

    lines.push('---');
    lines.push('_Report generated by `e2e/utils/audit-reporter.ts`_');

    return lines.join('\n');
  }

  /**
   * Reset all recorded results (useful between test runs in watch mode).
   */
  reset(): void {
    this.records = [];
  }
}

// ---------------------------------------------------------------------------
// Convenience factory for one-shot use in test afterAll hooks
// ---------------------------------------------------------------------------

/**
 * Write an audit report from a Playwright JSON results file.
 * Call from a `test.afterAll` or global teardown:
 *
 *   import { writeAuditReport } from '../utils/audit-reporter.js';
 *   test.afterAll(() => writeAuditReport('e2e/reports/results.json'));
 */
export function writeAuditReport(jsonResultsPath?: string): string {
  const reporter = AuditReporter.getInstance();
  if (jsonResultsPath) {
    reporter.mergeFromPlaywrightJson(jsonResultsPath);
  }
  return reporter.writeReport();
}
