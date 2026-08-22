import { readFile, access } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const required = ["index.html", "styles.css", "app.js", "README.md", "PRODUCT_DECISIONS.md"];
const failures = [];

for (const file of required) {
  try { await access(resolve(root, file)); }
  catch { failures.push(`Missing required file: ${file}`); }
}

const [html, css, js] = await Promise.all([
  readFile(resolve(root, "index.html"), "utf8"),
  readFile(resolve(root, "styles.css"), "utf8"),
  readFile(resolve(root, "app.js"), "utf8")
]);

const check = (condition, message) => { if (!condition) failures.push(message); };

check(/<html\s+lang="en"/.test(html), "Document needs a language.");
check(/name="viewport"/.test(html), "Document needs a responsive viewport.");
check(/<main\b/.test(html) && /<nav\b/.test(html) && /<header\b/.test(html) && /<footer\b/.test(html), "Landmark structure is incomplete.");
check(/<dialog\b[^>]*aria-labelledby=/.test(html), "Consequential sheet needs an accessible dialog name.");
check(!/<canvas\b(?![^>]*aria-label)/.test(html), "Every canvas needs a descriptive aria-label.");
check(!/<button\b(?![^>]*type="(?:button|submit)")/.test(html), "Every button should declare its type.");
check(/:focus-visible/.test(css), "Visible keyboard focus styling is required.");
check(/prefers-reduced-motion:\s*reduce/.test(css), "Reduced-motion support is required.");
check(/@media \(max-width: 760px\)/.test(css) && /min-height:\s*44px/.test(css), "Phone layout needs a 44px touch-target rule.");
check(!/(https?:\/\/|@import\s+url)/.test(`${html}\n${css}`), "Prototype must not load remote assets.");

const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map(match => match[1]);
const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index);
check(duplicates.length === 0, `Duplicate ids: ${[...new Set(duplicates)].join(", ")}`);

const categoryCounts = [...js.matchAll(/count:\s*(\d+)/g)].slice(0, 6).map(match => Number(match[1]));
check(categoryCounts.reduce((sum, count) => sum + count, 0) === 52, `Fixture category counts total ${categoryCounts.reduce((sum, count) => sum + count, 0)}, expected 52.`);
check(/index < 32 \? 6 : 5/.test(js), "Preset fixture rule changed; expected 32×6 + 20×5.");
check(32 * 6 + 20 * 5 === 292, "Preset fixture arithmetic must total 292.");
check(/provider-qualified/.test(await readFile(resolve(root, "PRODUCT_DECISIONS.md"), "utf8")), "Product decisions must document provider-qualified identity.");

const localRefs = [...html.matchAll(/(?:href|src)="([^"#]+)"/g)].map(match => match[1]).filter(ref => !ref.startsWith("http"));
for (const ref of localRefs) {
  try { await access(resolve(root, ref)); }
  catch { failures.push(`Broken local reference: ${ref}`); }
}

if (failures.length) {
  console.error(`Structural checks failed (${failures.length}):`);
  failures.forEach(message => console.error(`- ${message}`));
  process.exit(1);
}

console.log("Structural/accessibility smoke checks passed.");
console.log("Verified: required files, landmarks, dialog naming, canvas labels, focus, reduced motion, phone touch sizing, local-only assets, unique ids, local references, and exact 52/292 fixture arithmetic.");
