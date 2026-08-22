import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(root, 'index.html'), 'utf8');
const css = readFileSync(join(root, 'styles.css'), 'utf8');
const js = readFileSync(join(root, 'app.js'), 'utf8');
const paradigm = readFileSync(join(root, 'PARADIGM.md'), 'utf8');
const readme = readFileSync(join(root, 'README.md'), 'utf8');
const all = [html, css, js, paradigm, readme].join('\n');

const failures = [];
const check = (condition, message) => { if (!condition) failures.push(message); };

const componentBlock = js.slice(js.indexOf('const componentSeed = ['), js.indexOf('\n  ];', js.indexOf('const componentSeed = [')));
check((componentBlock.match(/^\s*\['[^\n]+$/gm) || []).length === 52, 'componentSeed must contain exactly 52 component rows');
check(js.includes('32×6 + 20×5 = 292'), 'the explicit 292-preset generation contract is missing');
check(js.includes("components.length !== 52 || presets.length !== 292"), 'runtime atlas count assertion is missing');
check(js.includes("'host-python'") && js.includes("'receiver-native'"), 'both provider identities must be represented');
check(js.includes("'background'") && js.includes("'overlay'") && js.includes("'full-scene'"), 'all content roles must be represented');
check(js.includes("'build'") && js.includes("'unavailable'") && js.includes("'quarantined'"), 'availability states must be represented');

[
  'materialField', 'specimenTable', 'ghostWall', 'sceneCanvas', 'vibeArc', 'plantGestures',
  'validateButton', 'saveLayoutButton', 'takeLiveButton', 'thresholdDialog', 'holdButton',
  'healthDrawer', 'toolsDrawer', 'devDrawer'
].forEach(id => check(html.includes(`id="${id}"`), `missing required surface #${id}`));

check(html.includes('ISOLATED REHEARSAL'), 'preview isolation copy is missing');
check(html.includes('PRESS AND HOLD TO TAKE LIVE'), 'confirmed take-live copy is missing');
check(html.includes('Expected degraded'), 'expected-degraded health semantics are missing');
check(html.includes('Save layout only'), 'save-layout-only action is missing');
check(html.includes('Point') && html.includes('Hole') && html.includes('D-pad'), 'direct interaction modes are missing');
check(html.includes('Painter') && html.includes('Masks') && html.includes('Emoji toss'), 'secondary touch tools are missing');
check(css.includes('@media (max-width: 560px)'), 'phone-specific layout is missing');
check(css.includes('aspect-ratio: 32 / 138'), 'physical wall aspect ratio is missing');

check(!/<(?:script|link|img)[^>]+(?:src|href)=["']https?:/i.test(html), 'prototype must not load remote assets');
check(!/\b(fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(/.test(js), 'prototype must not contain network primitives');
check(!/\/api\/|hass-cli|ssh\s|spi|serialport|child_process/i.test(js), 'prototype must not contain backend or hardware mutation routes');
check(!all.includes('../web/') && !all.includes('web/static') && !all.includes('web/templates'), 'prototype must stay isolated from existing web implementation');
check(paradigm.includes('This prototype never sends a network request'), 'hardware-safety boundary must be documented');
check(readme.includes('No backend files, live configuration, or hardware are touched'), 'scope boundary must be documented in README');

if (failures.length) {
  console.error(`Lumen Loom static checks failed (${failures.length}):`);
  failures.forEach(failure => console.error(`- ${failure}`));
  process.exit(1);
}

console.log('Lumen Loom static checks passed.');
console.log('Verified: 52/292 corpus contract, isolated previews, state/threshold semantics, phone layout, and no network/hardware routes.');
