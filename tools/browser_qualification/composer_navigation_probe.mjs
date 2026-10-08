#!/usr/bin/env node
// Run only against a loopback fixture with isolated state and no wall consumer.
import assert from 'node:assert/strict';
import {chromium} from './node_modules/playwright/index.mjs';
const url = process.argv[2] || 'http://127.0.0.1:8767/composer';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(url).hostname));
const browser = await chromium.launch({headless:true});
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({viewport:{width,height:width === 390 ? 844 : 900}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url);
    await page.waitForSelector('.gallery-select');
    const writes = [];
    page.on('request', request => {if (request.method() !== 'GET' && !new URL(request.url()).pathname.endsWith('/preview')) writes.push(request.url());});
    const sceneBefore = await page.locator('#sceneIdentity').textContent();
    await page.locator('#gallerySearch').fill('Aurora');
    for (const view of ['edit', 'playlists', 'browse']) {
      await page.locator(`[data-workspace-view="${view}"]`).click();
      assert.equal(await page.locator('.composer').getAttribute('data-view'), view);
      await page.locator(`[data-workspace-view="${view}"]`).focus();
      assert.equal(await page.locator(`[data-workspace-view="${view}"]`).evaluate(e => e === document.activeElement), true);
      assert.equal(await page.locator('#gallerySearch').inputValue(), 'Aurora');
      const geometry = await page.evaluate(() => {
        const rect = selector => {const r=document.querySelector(selector).getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height,width:r.width};};
        return {preview:rect('#scenePreview'),bar:rect('.operations-pane'),nav:rect('.workspace-nav'),overflow:document.documentElement.scrollWidth > innerWidth,height:innerHeight};
      });
      assert.equal(geometry.overflow, false);
      assert.ok(geometry.preview.height > 0 && geometry.preview.bottom <= geometry.bar.top);
      assert.ok(geometry.bar.bottom <= geometry.height + 1);
      assert.ok(geometry.nav.height >= 44);
    }
    await page.locator('#gallerySearch').fill('');
    const count = await page.locator('.gallery-select').count();
    assert.ok(count >= 40, `Full catalog loaded: ${count}`);
    const before = await page.locator('#scenePreview').boundingBox();
    await page.locator('.browse-workspace').evaluate(e => {e.scrollTop=e.scrollHeight;});
    assert.deepEqual(await page.locator('#scenePreview').boundingBox(), before);
    await page.locator('[data-workspace-view="edit"]').click();
    await page.locator('#openScene').click();
    assert.equal(await page.locator('#librarySearch').evaluate(e => e === document.activeElement), true);
    assert.equal(await page.locator('.composer').getAttribute('data-view'), 'browse');
    await page.locator('[data-workspace-view="playlists"]').click();
    await page.locator('#playlistBrowse').click();
    assert.equal(await page.locator('#gallerySearch').evaluate(e => e === document.activeElement), true);
    assert.equal(await page.locator('#sceneIdentity').textContent(), sceneBefore);
    assert.deepEqual(writes, [], 'Navigation must not publish a Scene');
    const navigationWrites = writes.length;
    await page.locator('[data-workspace-view="edit"]').click();
    await page.reload();
    assert.equal(await page.locator('.composer').getAttribute('data-view'), 'edit');
    await page.setViewportSize({width:width===390?1440:390,height:844});
    assert.equal(await page.locator('.composer').getAttribute('data-view'), 'edit');
    assert.equal(await page.locator('#sceneSpeed').isVisible(), true);
    assert.deepEqual(errors, []);
    await page.screenshot({path:`/tmp/composer-navigation-${width}.png`});
    console.log(JSON.stringify({width,catalog:count,navigation:'passed',writes:navigationWrites}));
    await context.close();
  }
} finally {await browser.close();}
