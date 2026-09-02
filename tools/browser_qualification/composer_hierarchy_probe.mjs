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
    for (const title of ['library-title', 'preview-title', 'operations-title']) {
      assert.ok(layout.paletteNames.includes(title), `Missing movable palette ${title} at ${width}px`);
    }
    assert.ok(layout.controls.every((text) => text.includes('Collapse') && text.includes('Arrange')), `Title-bar controls diverged at ${width}px`);

    await page.locator('#paletteChooser summary').click();
    await page.getByRole('button', {name: 'Hide Library'}).click();
    await page.waitForFunction(() => !document.querySelector('.library-pane'));
    await page.getByRole('button', {name: 'Show Library'}).click();
    await page.waitForFunction(() => document.querySelector('.library-pane.palette-shell'));
    await page.getByRole('button', {name: 'Reset layout'}).click();
    await page.waitForFunction(() => document.querySelectorAll('.palette-shell').length >= 8);

    await page.locator('[data-palette-id="operations"][data-palette-action="collapse"]').click();
    await page.waitForFunction(() => document.querySelector('.operations-pane')?.classList.contains('is-collapsed'));
    await page.locator('[data-palette-id="operations"][data-palette-action="collapse"]').click();
    await page.locator('[data-palette-id="library"][data-palette-action="drag"]').dragTo(page.locator('.operations-pane'));
    await page.waitForFunction(() => {
      const library = document.querySelector('.library-pane');
      const operations = document.querySelector('.operations-pane');
      return library?.parentElement === operations?.parentElement;
    });

    await page.selectOption('#animationChoice', 'snake');
    await page.waitForFunction(() => {
      const snake = document.querySelector('[data-animation-components="snake"]');
      const canopy = document.querySelector('[data-animation-components="canopy_cup"]');
      return snake && !snake.hidden && canopy?.hidden;
    });
    await context.close();
  }
} finally {
  await browser.close();
}
