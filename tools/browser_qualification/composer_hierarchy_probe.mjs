#!/usr/bin/env node
// Focused rendered-layout guard for the current four-column Composer shell.
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
    await page.waitForFunction(() => document.querySelectorAll(
      '.desktop-workspace > .library-pane, .desktop-workspace > .preview-pane, .desktop-workspace > .inspectors, .desktop-workspace > .operations-pane',
    ).length === 4);

    const layout = await page.evaluate(() => {
      const workspace = document.querySelector('.desktop-workspace');
      const children = [...workspace.children];
      const operations = document.querySelector('.operations-pane');
      const inspector = document.querySelector('.inspectors');
      const box = operations.getBoundingClientRect();
      const inspectorBox = inspector.getBoundingClientRect();
      return {
        children: children.map((node) => node.className),
        operations: {left: box.left, right: box.right, top: box.top, position: getComputedStyle(operations).position},
        inspector: {left: inspectorBox.left, right: inspectorBox.right},
      };
    });
    assert.deepEqual(layout.children, ['library-pane', 'preview-pane', 'inspectors', 'operations-pane']);
    assert.equal(layout.operations.position, 'sticky');
    assert.ok(layout.operations.left >= layout.inspector.right, `Operations was displaced at ${width}px: ${JSON.stringify(layout)}`);

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
