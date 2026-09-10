#!/usr/bin/env node
// Focused Firefox guard for Composer's constrained dock preferences. It only
// talks to the loopback fixture supplied by the caller.
import assert from 'node:assert/strict';
import {firefox} from 'playwright';

const [composerUrl, evidenceDir] = process.argv.slice(2);
if (!composerUrl) throw new Error('Usage: composer_hierarchy_probe.mjs <composer-url> [evidence-dir]');

const browser = await firefox.launch({headless: true});
try {
  for (const viewport of [{width: 1280, height: 800}, {width: 1440, height: 900}, {width: 1920, height: 1080}]) {
    const context = await browser.newContext({viewport});
    await context.addInitScript(() => {
      window.__layoutMutations = [];
      const originalFetch = window.fetch;
      window.fetch = (...args) => {
        const init = args[1] || {};
        const url = String(args[0]);
        if ((init.method || 'GET').toUpperCase() !== 'GET' && /\/api\/composer\/(scene|stop|save|undo|redo)/.test(url)) window.__layoutMutations.push({url, method: init.method || 'GET'});
        return originalFetch(...args);
      };
    });
    const page = await context.newPage();
    await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    await page.waitForSelector('[data-dock-resizer="library"]');
    await page.waitForSelector('[aria-label="Collapse Background inspector"]');
    await page.waitForFunction(() => document.querySelector('#previewIdentity')?.textContent !== 'Rendering…');
    const original = await page.evaluate(() => ({
      identity: document.querySelector('#sceneIdentity')?.textContent,
      observed: document.querySelector('#observedIdentity')?.textContent,
      connection: document.querySelector('#connectionState')?.textContent,
      running: document.querySelector('#liveAction')?.textContent,
      previewIdentity: document.querySelector('#previewIdentity')?.textContent,
      previewSize: [document.querySelector('#scenePreview')?.width, document.querySelector('#scenePreview')?.height],
    }));
    await page.evaluate(() => { window.__layoutMutations.length = 0; });

    const library = page.locator('[data-dock-resizer="library"]');
    const inspector = page.locator('[data-dock-resizer="inspector"]');
    await library.focus(); await page.keyboard.press('End');
    await inspector.focus(); await page.keyboard.press('End');
    assert.equal(await library.getAttribute('aria-valuenow'), '360');
    assert.equal(await inspector.getAttribute('aria-valuenow'), '480');
    const visibleAtMaximum = await page.evaluate(() => ['.preview-pane', '#previewIdentity', '#liveAction'].every((selector) => {
      const rect = document.querySelector(selector).getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.left >= 0 && rect.right <= window.innerWidth && rect.top >= 0 && rect.bottom <= window.innerHeight;
    }));
    assert.equal(visibleAtMaximum, true, 'Preview, live identity, or Stop disappeared at maximum dock widths');
    await inspector.focus(); await page.keyboard.press('Home');
    assert.equal(await inspector.getAttribute('aria-valuenow'), '252');
    const libraryBox = await library.boundingBox();
    const workspaceBox = await page.locator('.desktop-workspace').boundingBox();
    assert.ok(libraryBox, 'Library separator is visible on desktop');
    assert.ok(workspaceBox, 'Desktop workspace is visible');
    await page.mouse.move(libraryBox.x + libraryBox.width / 2, libraryBox.y + 100);
    await page.mouse.down(); await page.mouse.move(workspaceBox.x + 10, libraryBox.y + 100); await page.mouse.up();
    assert.equal(await library.getAttribute('aria-valuenow'), '176');

    const backgroundToggle = page.locator('[data-inspector-key="background"] .inspector-toggle');
    await backgroundToggle.click();
    assert.equal(await backgroundToggle.getAttribute('aria-expanded'), 'false');
    assert.equal(await page.locator('#background-inspector-content').evaluate((node) => getComputedStyle(node).display), 'none');
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-controls')), 'background-inspector-content');
    for (const key of ['animation', 'widgets', 'plants', 'look']) await page.locator(`[data-inspector-key="${key}"] .inspector-toggle`).click();
    assert.equal(await page.evaluate(() => ['background', 'animation', 'widgets', 'plants', 'look'].every((key) => {
      const content = document.querySelector(`#${key}-inspector-content`);
      return content && getComputedStyle(content).display === 'none';
    })), true, 'A named inspector stayed visible after collapse');
    const animationVisibility = await page.evaluate(() => {
      const controls = [...document.querySelectorAll('#animation-inspector-content button, #animation-inspector-content input, #animation-inspector-content select')];
      return {count: controls.length, displays: controls.slice(0, 4).map((node) => getComputedStyle(node).display), hidden: controls.every((node) => node.offsetParent === null)};
    });
    assert.equal(animationVisibility.count > 0 && animationVisibility.hidden, true, `Animation controls stayed exposed after collapse: ${JSON.stringify(animationVisibility)}`);
    assert.deepEqual(await page.evaluate(() => window.__layoutMutations), [], 'Layout controls requested a Composer state or publication mutation');
    assert.deepEqual(await page.evaluate(() => ({
      identity: document.querySelector('#sceneIdentity')?.textContent,
      observed: document.querySelector('#observedIdentity')?.textContent,
      connection: document.querySelector('#connectionState')?.textContent,
      running: document.querySelector('#liveAction')?.textContent,
      previewIdentity: document.querySelector('#previewIdentity')?.textContent,
      previewSize: [document.querySelector('#scenePreview')?.width, document.querySelector('#scenePreview')?.height],
    })), original, 'Layout controls changed Composer or Preview state');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'Desktop page has horizontal overflow');
    assert.equal(await page.evaluate(() => [...document.querySelectorAll('*')].every((node) => getComputedStyle(node).overflowX !== 'auto' && getComputedStyle(node).overflowX !== 'scroll')), true, 'Desktop has a nested horizontal scroller');
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.waitForSelector('[aria-label="Expand Background inspector"]');
    assert.equal(await page.locator('[data-dock-resizer="library"]').getAttribute('aria-valuenow'), '176');
    await page.locator('#resetComposerLayout').click();
    assert.equal(await page.locator('[data-dock-resizer="library"]').getAttribute('aria-valuenow'), '220');
    assert.equal(await page.locator('[data-dock-resizer="inspector"]').getAttribute('aria-valuenow'), '306');
    assert.equal(await page.locator('[aria-label="Collapse Background inspector"]').getAttribute('aria-expanded'), 'true');
    await page.evaluate(() => localStorage.setItem('ledgrid.composer.constrained-docks.v1', '{broken'));
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.waitForSelector('[data-dock-resizer="library"]');
    await page.waitForFunction(() => document.querySelector('#previewIdentity')?.textContent !== 'Rendering…');
    assert.equal(await page.locator('[data-dock-resizer="library"]').getAttribute('aria-valuenow'), '220');
    if (evidenceDir && viewport.width === 1440) await page.screenshot({path: `${evidenceDir}/composer-constrained-docks-desktop.png`, fullPage: true});
    await context.close();
  }

  const context = await browser.newContext({viewport: {width: 390, height: 844}});
  const page = await context.newPage();
  await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
  await page.waitForSelector('#liveAction');
  await page.waitForFunction(() => document.querySelector('#previewIdentity')?.textContent !== 'Rendering…');
  assert.equal(await page.locator('[data-dock-resizer="library"]').evaluate((node) => getComputedStyle(node).display), 'none');
  const toggle = page.locator('[aria-label="Collapse Look inspector"]');
  const toggleBox = await toggle.boundingBox();
  assert.ok(toggleBox && toggleBox.height >= 44, 'Phone inspector toggle is smaller than 44px');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'Phone page has horizontal overflow');
  const order = await page.evaluate(() => {
    const top = (selector) => document.querySelector(selector).getBoundingClientRect().top;
    return {operations: top('.operations-pane'), library: top('.library-pane'), preview: top('.preview-pane')};
  });
  assert.ok(order.operations <= order.library && order.library <= order.preview, `Phone order changed: ${JSON.stringify(order)}`);
  if (evidenceDir) await page.screenshot({path: `${evidenceDir}/composer-constrained-docks-phone.png`, fullPage: true});
  await context.close();
} finally {
  await browser.close();
}
