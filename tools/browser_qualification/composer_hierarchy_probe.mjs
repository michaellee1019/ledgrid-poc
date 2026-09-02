#!/usr/bin/env node
// Focused rendered-layout guard for the movable desktop Composer palettes.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const composerUrl = process.argv[2];
if (!composerUrl) throw new Error('Usage: composer_hierarchy_probe.mjs <composer-url>');

const browser = await chromium.launch({headless: true});
try {
  for (const width of [1280, 1440]) {
    const context = await browser.newContext({viewport: {width, height: 1000}});
    const page = await context.newPage();
    await page.goto(composerUrl, {waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => document.querySelectorAll('.desktop-workspace.palette-board .palette-shell').length >= 8);

    const layout = await page.evaluate(() => {
      const workspace = document.querySelector('.desktop-workspace');
      return {
        scrollable: [...workspace.querySelectorAll('.palette-stack, .palette-shell')].every((node) => getComputedStyle(node).overflowY === 'auto'),
        paletteNames: [...workspace.querySelectorAll('.palette-shell .palette-header-controls')].map((node) => node.closest('.palette-shell').getAttribute('aria-labelledby')),
        controls: [...workspace.querySelectorAll('.palette-header-controls')].map((node) => node.textContent),
      };
    });
    assert.equal(layout.scrollable, true, `Palette scrolling was lost at ${width}px: ${JSON.stringify(layout)}`);
    for (const title of ['library-title', 'preview-title', 'global-scene-controls-title', 'operations-title']) {
      assert.ok(layout.paletteNames.includes(title), `Missing movable palette ${title} at ${width}px`);
    }
    assert.ok(layout.controls.every((text) => text.includes('⠿') && text.includes('⌄')), `Direct title-bar controls diverged at ${width}px`);

    const globalTitleControls = await page.locator('.global-scene-controls .pane-heading > button, .global-scene-controls .palette-header-controls > button').evaluateAll((nodes) => {
      const paletteRect = document.querySelector('.global-scene-controls').getBoundingClientRect();
      const palette = {left: paletteRect.left, right: paletteRect.right, top: paletteRect.top, bottom: paletteRect.bottom};
      return nodes.map((node) => {
        const rect = node.getBoundingClientRect();
        return {label: node.getAttribute('aria-label') || node.textContent.trim(), left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, palette};
      });
    });
    assert.ok(globalTitleControls.length >= 3, `Global scene title controls missing at ${width}px`);
    assert.ok(globalTitleControls.every(({left, right, top, bottom, palette}) => left >= palette.left && right <= palette.right && top >= palette.top && bottom <= palette.bottom), `Global scene title controls overflow at ${width}px: ${JSON.stringify(globalTitleControls)}`);

    await page.locator('[data-palette-id="global-scene-controls"][data-palette-action="collapse"]').click();
    await page.waitForFunction(() => document.querySelector('.global-scene-controls')?.classList.contains('is-collapsed'));
    await page.locator('[data-palette-id="global-scene-controls"][data-palette-action="collapse"]').click();

    const speedApplied = page.waitForResponse((response) =>
      response.url().endsWith('/api/config/animation-speed')
      && response.request().method() === 'POST'
      && response.status() === 200,
    );
    await page.locator('#sceneSpeed').evaluate((slider) => {
      slider.value = '1.25';
      slider.dispatchEvent(new Event('input', {bubbles: true}));
    });
    const speedBody = await (await speedApplied).json();
    assert.equal(speedBody.multiplier, 1.25, 'Speed did not use the immediate controller path');

    await page.locator('[data-palette-id="operations"][data-palette-action="collapse"]').click();
    await page.waitForFunction(() => document.querySelector('.operations-pane')?.classList.contains('is-collapsed'));
    await page.locator('[data-palette-id="operations"][data-palette-action="collapse"]').click();
    const directDrag = page.locator('[data-palette-id="library"][data-palette-action="drag"]');
    assert.equal(await directDrag.isVisible(), true, 'Direct drag handle is not visible');
    await directDrag.dragTo(page.locator('.operations-pane'));
    await page.waitForFunction(() => {
      const library = document.querySelector('.library-pane');
      const operations = document.querySelector('.operations-pane');
      return library?.parentElement === operations?.parentElement;
    });

    const currentAnimation = await page.locator('#animationChoice').inputValue();
    const targetAnimation = currentAnimation === 'snake' ? 'canopy_cup' : 'snake';
    const sceneApplied = page.waitForResponse((response) =>
      response.url().endsWith('/api/composer/scene')
      && response.request().method() === 'POST'
      && response.status() === 200,
    );
    await page.selectOption('#animationChoice', targetAnimation);
    const sceneBody = await (await sceneApplied).json();
    assert.equal(sceneBody.published, true, 'A normal edit did not publish immediately');
    await page.waitForFunction((animation) => {
      const selected = document.querySelector(`[data-animation-components="${animation}"]`);
      const other = document.querySelector(`[data-animation-components="${animation === 'snake' ? 'canopy_cup' : 'snake'}"]`);
      return selected && !selected.hidden && other?.hidden;
    }, targetAnimation);
    await context.close();
  }
} finally {
  await browser.close();
}
