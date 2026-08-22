import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "index.html"), "utf8");
const css = readFileSync(join(here, "styles.css"), "utf8");
const js = readFileSync(join(here, "app.js"), "utf8");
const paradigm = readFileSync(join(here, "PARADIGM.md"), "utf8");

const checks = [];
const check = (condition, message) => {
  checks.push({ condition: Boolean(condition), message });
  if (!condition) process.exitCode = 1;
};

check(/<canvas[^>]+width="32"[^>]+height="138"/s.test(html), "wall canvases use the 32 × 138 logical contract");
check(html.includes("4,416 LEDs"), "physical pixel count is visible");
check(html.includes("52 sources and 292 presets"), "catalog scale is visible");
check(js.includes("catalog.length !== 292") && js.includes("COMPONENTS.length !== 52"), "runtime asserts exact fixture scale");
check((js.match(/^    \["[a-z0-9_]+",/gm) || []).length === 52, "fixture declares exactly 52 component sources");

const counts = [...js.matchAll(/^    \["[a-z0-9_]+", "[^"]+", (\d+),/gm)].map(match => Number(match[1]));
check(counts.reduce((sum, count) => sum + count, 0) === 292, "component preset counts sum to exactly 292");

for (const vibe of ["Neutral", "Quiet", "Cozy", "Vivid", "Celebration"]) {
  check(html.includes(`>${vibe}<`), `vibe ${vibe} is reachable`);
}

for (const modifier of ["illuminate", "shadow", "refract", "hue_shift", "liquid_glass", "attractor", "repulsor", "slow_zone", "obstacle", "portal", "bumper", "hazard", "habitat", "emitter"]) {
  check(html.includes(`value="${modifier}"`), `plant modifier ${modifier} is present`);
}

check(html.includes('name="field"') && html.includes('name="surface"'), "field and surface semantics use exclusive radio groups");
check(html.includes("Preview only · isolated from live") && html.includes("Live wall · living room"), "live and preview language is unmistakable");
check(`${html}\n${js}`.includes("Host-build simulation preview — not receiver framebuffer readback"), "native preview limitations are explicit");
check(`${html}\n${js}`.includes("Saved exactly") && `${html}\n${js}`.includes("Unsaved arrangement") && `${html}\n${js}`.includes("Wall changed elsewhere"), "saved, dirty, and drift states are represented");
check(html.includes("Save layout only"), "scene persistence boundary is visible");
check(html.includes("Point") && html.includes("Make a hole") && html.includes("Direction pad"), "point, hole, and D-pad interactions are represented");
check(html.includes("Paint pixels") && html.includes("Arrange emoji") && html.includes("Map leaves and globes"), "lower-fidelity making tools are reachable");
check(html.includes("Developer shelf"), "developer tools are reachable but secondary");
check(html.includes("Section 1") && html.includes("Section 4") && html.includes("verification incomplete"), "four-receiver expected-degraded state is represented");

check(!/https?:\/\//.test(`${html}\n${css}\n${js}`), "prototype has no network assets or URLs");
check(!/\b(fetch|XMLHttpRequest|WebSocket)\s*\(/.test(js), "prototype contains no backend/network calls");
check(!/text-overflow\s*:\s*ellipsis/.test(css), "CSS never ellipsizes names");
check(css.includes("@media (max-width: 760px)"), "phone breakpoint is implemented");
check(css.includes("prefers-reduced-motion: reduce"), "reduced motion is supported");
check(html.includes("skip-link") && html.includes("aria-live=\"polite\""), "keyboard skip and announced status feedback are present");
check(paradigm.includes("not a reskinned dashboard") && paradigm.includes("two transparent sheets"), "paradigm rationale documents the new grammar");

for (const result of checks) {
  console.log(`${result.condition ? "PASS" : "FAIL"}  ${result.message}`);
}

console.log(`\n${checks.filter(result => result.condition).length}/${checks.length} checks passed.`);
