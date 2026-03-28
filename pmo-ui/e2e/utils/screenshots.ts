/**
 * screenshots.ts — helpers for capturing screenshots during tests.
 *
 * All helpers write to the e2e/screenshots/ directory by default.
 * Filenames follow the pattern:  <timestamp>-<name>.png
 *
 * Usage:
 *   import { captureFullPage, captureLocator, captureViewports } from '../utils/screenshots.js';
 *
 *   // Inside a test:
 *   await captureFullPage(page, 'board-empty-state');
 *   await captureLocator(page, kanban.healthBar, 'health-bar');
 */

/// <reference types="node" />
import type { Page, Locator } from '@playwright/test';
import * as path from 'node:path';
import * as fs from 'node:fs';

const SCREENSHOT_DIR = path.resolve(
  new URL('..', import.meta.url).pathname,
  'screenshots',
);

// Ensure the screenshots directory exists.
if (!fs.existsSync(SCREENSHOT_DIR)) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

/**
 * Build a deterministic screenshot filename.
 * Format: <timestamp>-<sanitised-name>.png
 */
function buildFilename(name: string): string {
  const ts = new Date().toISOString().replace(/[:.]/g, '-');
  const safe = name.replace(/[^a-zA-Z0-9_-]/g, '-').replace(/-+/g, '-');
  return path.join(SCREENSHOT_DIR, `${ts}-${safe}.png`);
}

// ---------------------------------------------------------------------------
// Full-page capture
// ---------------------------------------------------------------------------

/**
 * Capture a full-page screenshot.
 *
 * @param page   - Playwright Page object.
 * @param name   - Descriptive name for the screenshot (used in filename).
 * @returns      Absolute path to the written file.
 */
export async function captureFullPage(page: Page, name: string): Promise<string> {
  const filePath = buildFilename(name);
  await page.screenshot({ path: filePath, fullPage: true });
  return filePath;
}

// ---------------------------------------------------------------------------
// Component / locator capture
// ---------------------------------------------------------------------------

/**
 * Capture a screenshot of a specific element (locator).
 * The element must be visible and attached to the DOM.
 *
 * @param _page  - Unused but kept for API symmetry with captureFullPage.
 * @param locator - Playwright Locator pointing at the element to capture.
 * @param name   - Descriptive name.
 * @returns      Absolute path to the written file.
 */
export async function captureLocator(
  _page: Page,
  locator: Locator,
  name: string,
): Promise<string> {
  const filePath = buildFilename(name);
  await locator.screenshot({ path: filePath });
  return filePath;
}

// ---------------------------------------------------------------------------
// Viewport comparison capture
// ---------------------------------------------------------------------------

export type Viewport = {
  name: string;
  width: number;
  height: number;
};

export const STANDARD_VIEWPORTS: Viewport[] = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'mobile', width: 375, height: 812 },
];

/**
 * Capture the same page across multiple viewports.
 * Resizes the browser window for each capture then restores the original size.
 *
 * @param page      - Playwright Page object.
 * @param name      - Base name (viewport suffix appended automatically).
 * @param viewports - Viewport list (defaults to STANDARD_VIEWPORTS).
 * @returns         Array of { viewport, filePath } records.
 */
export async function captureViewports(
  page: Page,
  name: string,
  viewports: Viewport[] = STANDARD_VIEWPORTS,
): Promise<Array<{ viewport: Viewport; filePath: string }>> {
  const results: Array<{ viewport: Viewport; filePath: string }> = [];

  for (const vp of viewports) {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    // Allow the layout to reflow.
    await page.waitForTimeout(200);
    const filePath = await captureFullPage(page, `${name}-${vp.name}`);
    results.push({ viewport: vp, filePath });
  }

  return results;
}

// ---------------------------------------------------------------------------
// Annotated capture (adds a label overlay via page.evaluate)
// ---------------------------------------------------------------------------

/**
 * Capture a full-page screenshot with a text annotation burned in via DOM
 * manipulation.  The overlay is removed after capture.
 *
 * Useful for visual regression baselines where the commit / build number
 * should be visible in the image.
 *
 * @param page       - Playwright Page.
 * @param name       - Screenshot name.
 * @param annotation - Text to display in the top-left corner.
 */
export async function captureAnnotated(
  page: Page,
  name: string,
  annotation: string,
): Promise<string> {
  // Inject an annotation overlay.
  await page.evaluate((text) => {
    const el = document.createElement('div');
    el.id = '__pw-annotation__';
    el.textContent = text;
    Object.assign(el.style, {
      position: 'fixed',
      top: '4px',
      left: '4px',
      zIndex: '99999',
      padding: '2px 6px',
      background: 'rgba(0,0,0,0.7)',
      color: '#fff',
      fontSize: '11px',
      fontFamily: 'monospace',
      borderRadius: '3px',
      pointerEvents: 'none',
    });
    document.body.appendChild(el);
  }, annotation);

  const filePath = await captureFullPage(page, name);

  // Remove the overlay.
  await page.evaluate(() => {
    document.getElementById('__pw-annotation__')?.remove();
  });

  return filePath;
}

// ---------------------------------------------------------------------------
// Diff utility (structural — not pixel-diff)
// ---------------------------------------------------------------------------

/**
 * Compare two screenshot file sizes as a rough structural diff check.
 * Returns `true` if the files differ by more than `threshold` bytes.
 *
 * This is intentionally a coarse check — for pixel-level comparison use
 * Playwright's expect(page).toMatchSnapshot() instead.
 */
export function filesAreDifferent(fileA: string, fileB: string, threshold = 1000): boolean {
  if (!fs.existsSync(fileA) || !fs.existsSync(fileB)) return true;
  const sizeA = fs.statSync(fileA).size;
  const sizeB = fs.statSync(fileB).size;
  return Math.abs(sizeA - sizeB) > threshold;
}
