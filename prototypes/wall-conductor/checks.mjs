import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const directory = dirname(fileURLToPath(import.meta.url));
const [html, css, js, readme, decisions] = await Promise.all([
  "index.html", "styles.css", "app.js", "README.md", "PRODUCT_DECISIONS.md"
].map((file) => readFile(join(directory, file), "utf8")));

const checks = [
  ["six responsive places", ["now", "find", "compose", "touch", "health", "more"].every((view) => html.includes(`data-nav="${view}"`))],
  ["explicit live and preview states", html.includes("state-live") && html.includes("state-preview")],
  ["exact preset fixture arithmetic", js.includes("32×6 + 20×5 = 292") && js.includes("componentCount = 52")],
  ["core view renderers", ["renderNow", "renderFind", "renderCompose", "renderTouch", "renderHealth"].every((name) => js.includes(`function ${name}`))],
  ["real accessible comparison surface", html.includes('id="comparisonDialog"') && html.includes('id="comparisonGrid"') && js.includes("function openComparison") && js.includes("comparisonDialog.showModal()")],
  ["comparison keeps three tall previews", js.includes("state.compare.map") && css.includes(".comparison-choice .wall-frame") && css.includes("grid-template-columns: repeat(3")],
  ["no destructive CSS ellipsis", !css.includes("text-overflow")],
  ["physical aspect ratio", css.includes("aspect-ratio: 32 / 138")],
  ["reduced motion", css.includes("prefers-reduced-motion")],
  ["responsive phone breakpoint", css.includes("@media (max-width: 680px)")],
  ["backend wiring documented", readme.includes("/api/v1/scene/preview") && readme.includes("/api/device/state")],
  ["paradigm rationale documented", decisions.includes("reading index plus one visual stage")]
];

for (const [label, passed] of checks) {
  if (!passed) throw new Error(`Check failed: ${label}`);
  console.log(`✓ ${label}`);
}

console.log(`\n${checks.length} structural checks passed.`);
