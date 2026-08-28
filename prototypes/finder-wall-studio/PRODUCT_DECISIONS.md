# Product decisions

## The paradigm

This is a content browser and show utility, not a dashboard. The Finder hierarchy does the scaling work:

**Source → category → component → preset → isolated preview / inspector**

The source list supplies stable places and smart folders. Column view preserves location and parentage while moving quickly through hundreds of presets. Outline view supports scanning and comparison by provider, role, count, and availability. Compare view changes the task from browsing to visual selection while retaining complete identities.

The physical wall is a persistent object above every workspace. Its identity and emergency controls never disappear behind content selection. This intentionally distinguishes “what I am looking at” from “what the installation is doing.”

## Why this resembles an excellent old Mac utility

- One sturdy window hierarchy: title bar, unified toolbar, live strip, path bar, content panes, status bar.
- Source list and crisp split panes instead of floating cards.
- Dimensional, bounded Aqua controls where action hierarchy benefits; standard wells, table headers, disclosure triangles, pop-up menus, and attached sheets elsewhere.
- Compact information density with strong typography and wrapping. Core names and descriptions are never ellipsized.
- Blue means selection; graphite means window/tool chrome; green and amber are reserved for operational meaning.
- Consequential actions use sheets attached to the current task. Safe Stop remains immediate.

The prototype deliberately avoids translucent glass, floating modules, oversized headlines, broad empty gutters, pill-everything controls, and decorative sci-fi telemetry.

## Workflow decisions

### Library and preview

Provider-qualified identity appears beside every component and again in the inspector. Receiver-native preview provenance says “host-build simulation”; no preview claims to be receiver framebuffer readback or current wall output. Unsupported/build-only/quarantined records remain visible for catalog honesty but are disabled in the performance browser. Their full inventory is also reachable under Developer.

Canvas previews use the exact 32:138 ratio. Local procedural pixels are illustrative fixtures, not claims of fidelity to shipped animation frames.

### Live output and safety

Stop acts immediately and explicitly leaves wall power on. Power-off requires a sheet because it blacks out the installation. Taking content or a scene live also requires a sheet naming the exact component, preset, provider, and state boundaries. The brightness fixture updates directly in the persistent strip.

### Scene composition

The outline contains exactly one Background and an optional Clock Overlay. Selecting a node changes the contextual inspector. Validation surfaces fallback and stale-policy evidence. Save language always says “layout,” and every save/apply sheet states that vibe, plant modifiers, power, and brightness are excluded.

Saved/current/dirty/drift are distinct labels. The fixture scene is saved, its preview is modified, and its live state differs.

### Vibe and plant material

Vibe uses an era-appropriate segmented selector. Plant strengths use checkboxes, pop-up menus, and sliders. Field and Surface exclusivity is enforced structurally by a single select for each group, making illegal simultaneous choices impossible. Support and fallback are shown against the previewed component.

Authored animation speed, vibe tempo, and operator speed remain conceptually separate. Operator speed and target FPS belong in a future device inspector rather than being crowded into the global-material pane.

### Operations

The opening statement is plain language, followed by compact metrics, then drill-down evidence. Receiver C’s reduced telemetry is amber and explained as a configured policy: playback agreement is verified while some evidence is intentionally unavailable. This avoids the common and inaccurate “red equals broken” shortcut.

### Responsive hierarchy

Desktop keeps the source list and multi-column context. Phone changes the interaction, not just the widths: a user drills in one full-width level at a time and returns through a native Back control. Compare candidates become a vertical list with tall previews. Secondary workspaces stack in reading order.

## Fixture assumptions

- Favorites, Recents, source counts, saved scene count, and preview-cache count are prototype fixture concepts; persistence is not implied.
- “Show-ready” contains 43 fixture components. Exact production availability should come from catalog compatibility and provider policy.
- Canvas art is locally generated and deterministic. Real preview posters/loops would replace it while preserving the same provenance caption.
- The current live output, performance metrics, receiver map, scene state, and saved/dirty/drift labels are representative fixture values.
- Hardware-connected mode is the default fixture. A production client must derive and display local-preview mode from backend status.
- Developer restriction is only information architecture in this prototype; it is not authentication or authorization.

## Intentional gaps

- No backend requests, IPC, file writes, saved favorites, real media playback, or live wall mutation.
- Painter, foliage/globe mask editing, Emoji Arranger, the focused D-pad/game control surface, and developer actions are destination maps or catalog destinations only.
- Parameter fields are representative rather than generated from every component schema.
- Compare is visual and identity-focused; there is no synchronized playback timeline.
- There is no authentication, undo manager, keyboard shortcut command map, drag-to-resize panes, drag/drop scene assembly, or persisted window state.
- Tablet receives the compact desktop layout until the phone drill-in breakpoint. A production pass should test orientation-specific split behavior.
- Reduced-telemetry copy is representative and must ultimately be derived from the active receiver transport policy rather than hard-coded receiver identity.

## Historical backend mapping — superseded

> This unwired prototype preserves the route map used during evaluation. The map
> is not current integration guidance: direct animation/device takeover, bare
> scene mutation, preset-apply, and mask-writing routes are now fail-closed. A
> shipping implementation must hand activation to Composer Check and use the
> managed installation-profile workflow.

Its interaction boundaries mapped to the then-current contracts as follows:

| Prototype responsibility | Existing API contract | Integration note |
| --- | --- | --- |
| Component catalog and provider/role filters | `GET /api/v1/components` | Treat provider + component identity as qualified; use compatibility and policy fields to control availability. |
| Component presets | `GET /api/v1/components/<component_id>/presets` | The endpoint explicitly rejects ambiguous cross-provider discovery. |
| Legacy host detail/catalog | `GET /api/animations`, `GET /api/animations/<animation_name>` | Compatibility path only; the unified component catalog should drive the hierarchy. |
| Isolated component preview | `GET /api/preview/<animation_name>`, `POST /api/preview/<animation_name>/with_params` | Preserve `live_state_mutated: false` semantics and backend preview labels. |
| Whole-scene preview | `POST /api/v1/scene/preview` | Render backend provenance, especially host simulation for receiver-native backgrounds. |
| Read / validate / perform / stop scene | `GET /api/v1/scene`, `POST /api/v1/scene/validate`, `PUT|POST /api/v1/scene`, `DELETE /api/v1/scene` | Keep preview and validation separate from performance. |
| Update scene component | `PATCH /api/v1/scene/components/<target>` | Target is background or overlay; retain dirty/drift diagnostics. |
| Scene preset browse/save/apply/delete | `GET|POST /api/v1/scene-presets`, `POST /api/v1/scene-presets/<id>/apply`, `DELETE /api/v1/scene-presets/<id>` | API enforces layout-only: vibe, plant and output state are rejected. |
| Apply preset live | `POST /api/animations/<animation_name>/presets/<preset_id>/apply` | The confirmation sheet should use the resolved provider-qualified identity. |
| Atomic device change | `POST /api/device/state` | Matches deliberate power + brightness + animation + optional preset application. |
| Stop | `POST /api/stop` | Immediate safe action in the prototype. |
| Live output/status | `GET /api/status` | Populate identity, power, brightness, preset/scene, saved state, actual/target FPS, local mode, and hybrid evidence. |
| Vibe | `GET /api/v1/vibe`, `PUT|POST /api/v1/vibe` | Keep as independent global state and only claim semantic support reported by content. |
| Plant modifiers | `POST /api/config/plant-modifiers` | Serialize strengths and exclusive groups according to `PlantModifierState`; do not store in presets/scenes. |
| Brightness, target FPS, operator speed | `POST /api/config/brightness`, `POST /api/config/target-fps`, `POST /api/config/animation-speed` | Present as device/operator controls, distinct from authored and vibe tempo. |
| Health and performance | `GET /api/stats`, `GET /api/metrics`, `GET /api/hardware/stats` | Prefer plain-language summary then exact evidence. |
| Receiver refresh | `POST /api/v1/receivers/status/refresh` | Show accepted/pending status before refreshed evidence arrives. |
| Interaction and games | `POST /api/interaction`, `POST /api/hole`, `POST /api/dpad/<direction>` | Future focused control surface should translate screen points into logical wall coordinates. |
| Painter and masks | `POST /api/painter/updates`, `POST /api/painter/frame`, `POST /api/painter/clear`, `GET|POST /api/painter/masks`, painter preset endpoints | Keep sparse/full-frame and semantic layer mutations explicit. |
| Developer tools | `POST /api/reload/<animation_name>`, `POST /api/refresh` | Restrict to Developer source; show provider-qualified target and operation result. |

## Production questions

1. Which catalog fields are authoritative for show-ready, build-only, quarantined, and provider-policy availability?
2. Which status fields define saved/dirty/stale/drift for single content and scene output?
3. Should brightness changes remain immediate, or join a staged device-state inspector for atomic application?
4. What is the authoritative representation of Favorites, Recents, tags, and user collections, if these become real?
5. Which receiver telemetry gaps are expected under each transport policy, and what exact operator copy should explain them?
