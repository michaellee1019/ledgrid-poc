#!/usr/bin/env node
import assert from 'node:assert/strict';
import {chromium} from './node_modules/playwright/index.mjs';
const url = process.argv[2] || 'http://127.0.0.1:8767/composer';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(url).hostname));
const browser = await chromium.launch({headless:true});
try {
  for (const width of [1440,390]) {
    const context = await browser.newContext({viewport:{width,height:900}});
    const page = await context.newPage();
    await page.goto(url);
    await page.waitForSelector('.gallery-select');
    await page.locator('[data-workspace-view="browse"]').click();
    await page.locator('#gallerySearch').fill('Aurora');
    const thumb = page.locator('.gallery-select img').first();
    await thumb.evaluate(image => image.decode());
    assert.ok((await thumb.boundingBox()).width >= 32);
    const published = [];
    page.on('request', request => {if(request.method() === 'POST' && new URL(request.url()).pathname === '/api/composer/scene') published.push(request.postDataJSON());});
    const publication = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === '/api/composer/scene');
    await page.locator('.gallery-select').first().click();
    assert.equal((await publication).postDataJSON().scene.animation.component_id, 'aurora_curtains');
    await page.waitForSelector('.gallery-preset-list button');
    const presetPublication = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === '/api/composer/scene');
    await page.locator('.gallery-preset-list button').first().click();
    assert.equal((await presetPublication).postDataJSON().scene.animation.component_id, 'aurora_curtains');
    const count = published.length;
    await page.locator('.gallery-favorite').first().click();
    await page.locator('[data-browse-view="favorites"]').click();
    assert.equal(await page.locator('.gallery-select').count(),1);
    assert.equal(published.length,count,'Favorite filtering does not publish');
    await page.locator('[data-browse-view="saved"]').click();
    assert.equal(await page.locator('[data-library-filter="starter"]').isVisible(),true);
    assert.equal(await page.locator('[data-library-filter="look"]').isVisible(),true);
    await page.locator('[data-browse-view="animations"]').click();
    await page.screenshot({path:`/tmp/composer-gallery-${width}.png`});
    console.log(JSON.stringify({width,thumbnail:'decoded',animationAndPreset:'immediate publication',filter:'no publication'}));
    await context.close();
  }
} finally {await browser.close();}
