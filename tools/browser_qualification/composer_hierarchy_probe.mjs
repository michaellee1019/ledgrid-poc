#!/usr/bin/env node
// Focused rendered-layout guard for the movable desktop Composer palettes.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const composerUrl = process.argv[2];
if (!composerUrl) throw new Error('Usage: composer_hierarchy_probe.mjs <composer-url>');

async function openPaletteChooser(page) {
  const chooser = page.locator('#paletteChooser');
  if (!await chooser.evaluate((node) => node.open)) await chooser.locator('summary').click();
}

async function closePaletteChooser(page) {
  const chooser = page.locator('#paletteChooser');
  if (await chooser.evaluate((node) => node.open)) await chooser.locator('summary').click();
  await page.waitForFunction(() => !document.querySelector('#paletteChooser')?.open);
}

async function openPaletteArrangeMenu(page, id) {
  const palette = page.locator(`[data-palette-id="${id}"][data-palette-action="collapse"]`).locator('xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " palette-shell ")]');
  const arrange = palette.locator('.palette-arrange-menu');
  if (!await arrange.evaluate((node) => node.open)) await arrange.locator('summary').click();
  await page.waitForFunction((paletteId) => document.querySelector(`[data-palette-id="${paletteId}"][data-palette-action="collapse"]`)?.closest('.palette-shell')?.querySelector('.palette-arrange-menu')?.open, id);
  const drag = page.locator(`[data-palette-id="${id}"][data-palette-action="drag"]`);
  assert.equal(await drag.isVisible(), true, `${id} Arrange drag action is not visible`);
  return drag;
}

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
    assert.ok(layout.controls.every((text) => text.includes('Collapse') && text.includes('Arrange')), `Title-bar controls diverged at ${width}px`);

    const globalTitleControls = await page.locator('.global-scene-controls .pane-heading > button, .global-scene-controls .palette-header-controls > .palette-collapse, .global-scene-controls .palette-arrange-menu > summary').evaluateAll((nodes) => {
      const paletteRect = document.querySelector('.global-scene-controls').getBoundingClientRect();
      const palette = {left: paletteRect.left, right: paletteRect.right, top: paletteRect.top, bottom: paletteRect.bottom};
      return nodes.map((node) => {
        const rect = node.getBoundingClientRect();
        return {label: node.getAttribute('aria-label') || node.textContent.trim(), left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, palette};
      });
    });
    assert.ok(globalTitleControls.length >= 3, `Global scene title controls missing at ${width}px`);
    assert.ok(globalTitleControls.every(({left, right, top, bottom, palette}) => left >= palette.left && right <= palette.right && top >= palette.top && bottom <= palette.bottom), `Global scene title controls overflow at ${width}px: ${JSON.stringify(globalTitleControls)}`);

    await openPaletteChooser(page);
    await page.getByRole('button', {name: 'Hide Library'}).click();
    await page.waitForFunction(() => !document.querySelector('.library-pane'));
    await page.getByRole('button', {name: 'Show Library'}).click();
    await page.waitForFunction(() => document.querySelector('.library-pane.palette-shell'));
    await page.getByRole('button', {name: 'Reset layout'}).click();
    await page.waitForFunction(() => document.querySelectorAll('.palette-shell').length >= 8);
    await closePaletteChooser(page);

    await page.locator('[data-palette-id="global-scene-controls"][data-palette-action="collapse"]').click();
    await page.waitForFunction(() => document.querySelector('.global-scene-controls')?.classList.contains('is-collapsed'));
    await page.locator('[data-palette-id="global-scene-controls"][data-palette-action="collapse"]').click();
    await openPaletteChooser(page);
    await page.getByRole('button', {name: 'Hide Global scene'}).click();
    await page.waitForFunction(() => !document.querySelector('.global-scene-controls'));
    await page.getByRole('button', {name: 'Show Global scene'}).click();
    await page.waitForFunction(() => document.querySelector('.global-scene-controls.palette-shell #sceneSpeed'));
    await page.getByRole('button', {name: 'Reset layout'}).click();
    await page.waitForFunction(() => {
      const global = document.querySelector('.global-scene-controls');
      const background = document.querySelector('.library-pane')?.parentElement?.nextElementSibling?.nextElementSibling;
      return global?.parentElement?.querySelector('#background-title') && document.querySelector('#sceneSpeed');
    });
    await closePaletteChooser(page);

    await page.locator('[data-palette-id="operations"][data-palette-action="collapse"]').click();
    await page.waitForFunction(() => document.querySelector('.operations-pane')?.classList.contains('is-collapsed'));
    await page.locator('[data-palette-id="operations"][data-palette-action="collapse"]').click();
    await (await openPaletteArrangeMenu(page, 'library')).dragTo(page.locator('.operations-pane'));
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
