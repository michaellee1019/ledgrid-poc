#!/usr/bin/env node
// Focused browser acceptance for compact Composer panels. The supplied origin
// must be the loopback-only qualification fixture, which rejects wall writes.
import assert from 'node:assert/strict';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const [composerUrl, evidenceDir, moduleRootArg] = process.argv.slice(2);
if (!composerUrl) throw new Error('Usage: composer_hierarchy_probe.mjs <composer-url> [evidence-dir] [playwright-module]');
const moduleRoot = moduleRootArg || path.join(import.meta.dirname, 'node_modules', 'playwright');
const imported = await import(pathToFileURL(path.join(moduleRoot, 'index.js')));
const {chromium} = imported.default || imported;

const cases = [
  {width: 1280, height: 800, zoom: 1},
  {width: 1440, height: 900, zoom: 1},
  {width: 1280, height: 800, zoom: .8},
  {width: 1440, height: 900, zoom: .8},
  {width: 390, height: 844, zoom: 1},
];
const browser = await chromium.launch({headless: true});
try {
  for (const testCase of cases) {
    const context = await browser.newContext({viewport: testCase});
    await context.addInitScript(() => {
      window.__layoutMutations = [];
      const originalFetch = window.fetch;
      window.fetch = (...args) => {
        const requestUrl = String(args[0]);
        const method = (args[1]?.method || 'GET').toUpperCase();
        if (method !== 'GET' && /\/api\/(?:composer\/(?:scene|stop|looks|undo|redo)|v1\/scene)/.test(requestUrl)) {
          window.__layoutMutations.push({url: requestUrl, method});
        }
        return originalFetch(...args);
      };
    });
    const page = await context.newPage();
    await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    if (testCase.zoom !== 1) await page.evaluate((zoom) => { document.documentElement.style.zoom = String(zoom); }, testCase.zoom);
    await page.waitForSelector('[data-panel-id="operations"] .panel-toggle');
    await page.waitForTimeout(1500);
    await page.evaluate(() => { window.__layoutMutations.length = 0; });

    const initial = await page.evaluate(() => {
      const panels = [...document.querySelectorAll('[data-panel-id]')];
      const visibleRects = panels.map((panel) => panel.getBoundingClientRect());
      const overlaps = visibleRects.some((left, index) => visibleRects.slice(index + 1).some((right) => (
        Math.min(left.right, right.right) - Math.max(left.left, right.left) > 1
        && Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top) > 1
      )));
      return {
        panelIds: panels.map((panel) => panel.dataset.panelId),
        allCollapsed: panels.every((panel) => panel.querySelector('.panel-toggle')?.getAttribute('aria-expanded') === 'false'
          && getComputedStyle(panel.querySelector('.panel-content')).display === 'none'),
        stopVisible: document.querySelector('#liveAction')?.offsetParent !== null,
        stopInHeader: Boolean(document.querySelector('.operations-pane > .pane-heading #liveAction')),
        operationsPosition: getComputedStyle(document.querySelector('.operations-pane')).position,
        horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        overlaps,
      };
    });
    for (const required of ['scenes', 'gallery', 'preview', 'global-scene', 'background', 'animation', 'widgets', 'plants', 'look', 'operations']) {
      assert.ok(initial.panelIds.includes(required), `Missing ${required} panel at ${JSON.stringify(testCase)}`);
    }
    assert.equal(initial.allCollapsed, true, `Fresh panels were expanded at ${JSON.stringify(testCase)}`);
    assert.equal(initial.stopVisible && initial.stopInHeader, true, 'Stop is hidden by the collapsed Operations panel');
    assert.equal(initial.operationsPosition, 'static', 'Operations is sticky or fixed');
    assert.equal(initial.horizontalOverflow, false, `Horizontal overflow at ${JSON.stringify(testCase)}`);
    assert.equal(initial.overlaps, false, `Panels overlap at ${JSON.stringify(testCase)}`);
    if (evidenceDir && testCase.width === 1440) {
      const suffix = testCase.zoom === 1 ? '100' : '80';
      await page.screenshot({path: `${evidenceDir}/composer-compact-panels-${suffix}.png`, fullPage: true});
    }

    if (testCase.width > 760) {
      const row = await page.evaluate(() => {
        const global = document.querySelector('[data-panel-id="global-scene"]').getBoundingClientRect();
        const background = document.querySelector('[data-panel-id="background"]').getBoundingClientRect();
        return {globalTop: global.top, backgroundTop: background.top, globalLeft: global.left, backgroundLeft: background.left};
      });
      assert.ok(Math.abs(row.globalTop - row.backgroundTop) < 2 && row.globalLeft !== row.backgroundLeft,
        `Global scene and Background do not share a row: ${JSON.stringify({...testCase, ...row})}`);
    } else {
      const targetHeight = await page.locator('[data-panel-id="widgets"] .panel-toggle').evaluate((node) => node.getBoundingClientRect().height);
      assert.ok(targetHeight >= 44, `Phone panel toggle is ${targetHeight}px tall`);
    }

    for (const id of ['preview', 'gallery']) {
      const toggle = page.locator(`[data-panel-id="${id}"] .panel-toggle`);
      await toggle.click();
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      assert.equal(await page.locator(`#composer-panel-${id}`).evaluate((node) => getComputedStyle(node).display), 'grid');
      assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('panel-toggle')), true);
    }
    assert.deepEqual(await page.locator('#scenePreview').evaluate((canvas) => [canvas.width, canvas.height]), [33, 138]);
    assert.deepEqual(await page.evaluate(() => window.__layoutMutations), [], 'Panel toggles caused publication or wall mutations');

    await page.reload({waitUntil: 'domcontentloaded'});
    await page.waitForSelector('[data-panel-id="preview"] .panel-toggle[aria-expanded="true"]');
    assert.equal(await page.locator('[data-panel-id="gallery"] .panel-toggle').getAttribute('aria-expanded'), 'true');
    await page.setViewportSize({width: testCase.width > 760 ? 390 : 1280, height: testCase.height});
    assert.equal(await page.locator('[data-panel-id="preview"] .panel-toggle').getAttribute('aria-expanded'), 'true');

    await page.locator('#resetComposerLayout').click();
    assert.equal(await page.locator('[data-panel-id="preview"] .panel-toggle').getAttribute('aria-expanded'), 'false');
    assert.equal(await page.locator('#scenePreview').evaluate((node) => node.offsetParent === null), true, 'Collapsed Preview control remains focusable/visible');
    await page.evaluate(() => {
      localStorage.setItem('ledgrid.composer.constrained-docks.v1', JSON.stringify({version: 1, collapsed: {preview: false}}));
      localStorage.setItem('ledgrid.composer.compact-panels.v2', '{broken');
    });
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.waitForSelector('[data-panel-id="preview"] .panel-toggle');
    assert.equal(await page.locator('[data-panel-id="preview"] .panel-toggle').getAttribute('aria-expanded'), 'false');
    assert.equal(await page.evaluate(() => localStorage.getItem('ledgrid.composer.constrained-docks.v1')), null);

    await context.close();
  }
} finally {
  await browser.close();
}
