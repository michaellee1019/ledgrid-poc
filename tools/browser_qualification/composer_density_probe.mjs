#!/usr/bin/env node
// Presentation-only acceptance against an isolated Composer app, never the wall.
import assert from 'node:assert/strict';
import {chromium} from './node_modules/playwright/index.mjs';
const url = process.argv[2];
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(url).hostname));
const browser = await chromium.launch();
try {
  for (const width of [320, 390, 1440]) {
    const page = await browser.newPage({viewport:{width,height:844}});
    await page.goto(url);
    await page.waitForSelector('.gallery-select');
    const metrics = await page.evaluate(() => {
      const tabs = [...document.querySelectorAll('[data-browse-view]')].map(e => e.getBoundingClientRect());
      const card = document.querySelector('.gallery-select').getBoundingClientRect();
      return {oneRow:tabs.every(r => r.top === tabs[0].top), targets:tabs.every(r => r.height >= 44), cardHeight:card.height, cardTop:card.top, overflow:document.documentElement.scrollWidth > innerWidth};
    });
    assert.ok(metrics.oneRow && metrics.targets);
    assert.ok(metrics.cardHeight <= 120, JSON.stringify(metrics));
    assert.equal(metrics.overflow, false);
    if (width < 760) assert.ok(metrics.cardTop < (width < 360 ? 375 : 350), JSON.stringify(metrics));
    await page.locator('#gallerySearch').fill('Fireworks');
    assert.equal(await page.locator('.gallery-select').count(), 1);
    await page.locator('.gallery-select').click();
    await page.locator('[data-workspace-view="edit"]').click();
    await page.waitForSelector('.control-disclosure[data-component-id="fireworks"]');
    assert.ok(await page.locator('#scenePreview').isVisible());
    await page.locator('[data-workspace-view="playlists"]').click();
    assert.ok(await page.locator('#playlistChoice').isVisible());
    await page.locator('[data-workspace-view="browse"]').click();
    await page.locator('#gallerySearch').fill('');
    await page.locator('[data-browse-view="animations"]').focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.browseView), 'favorites');
    await page.screenshot({path:`/tmp/composer-density-${width}.png`});
    console.log(JSON.stringify({width,...metrics}));
    await page.close();
  }
} finally { await browser.close(); }
