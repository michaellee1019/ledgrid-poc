// Focused real Composer ⇄ native HA action smoke against the isolated runtime.
import {chromium} from './node_modules/playwright/index.mjs';
import {writeFile} from 'node:fs/promises';
const [url, marker] = process.argv.slice(2);
const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'});
  await page.goto(url + '/');
  await page.waitForSelector('#animationChoice');
  await page.waitForFunction(() => document.querySelector('#animationChoice').value === 'firefly_synchrony');
  await writeFile(marker, 'ready');
  await page.waitForFunction(() => document.querySelector('#animationChoice').value === 'rainbow', null, {timeout: 20000});
  const publication = page.waitForResponse(response => response.url().endsWith('/api/v1/scene') && response.request().method() === 'PUT');
  await page.selectOption('#animationChoice', 'gradient');
  const response = await publication;
  if (!response.ok()) throw new Error('Composer selection failed to dispatch controller playback');
  const deadline = Date.now() + 20000;
  let observed = null;
  while (Date.now() < deadline) {
    observed = await (await page.request.get(url + '/api/v1/automation/status')).json();
    if (observed.active_content.animation === 'animation:python:gradient:v1') break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (observed?.active_content.animation !== 'animation:python:gradient:v1') throw new Error('Controller did not observe the browser selection');
  console.log('Browser content smoke: native HA → live Composer controls and Composer → observed HA selector passed');
} finally {
  await browser.close();
}
