# Current UX acceptance criteria

## Scope and release rule

These criteria apply to the current product at `/`, `/control`, `/painter`, and
the canonical Emoji workspace `/emoji`. Prototype pages are out of scope.

The redesign is accepted only when every P0 and P1 criterion below passes in the
running application at desktop and narrow mobile widths. A criterion is not
accepted from source inspection alone when it describes interactive behavior.

## Interaction contract

- **Draft**: private selection or edits that cannot change the wall.
- **Preview**: isolated visual feedback that cannot change the wall.
- **Live**: content actively sent to the wall. Entering this state always requires
  an explicit action whose label contains **Take live**, **Take scene live**, or
  **Mirror to wall**. Updating content that is already live is labeled **Update live**.
- **Stop / Return to draft**: an always-visible way to end this tool's live output
  without destroying the draft.

## Acceptance matrix

| ID | Priority | Acceptance criterion | Required evidence and pass threshold |
|---|---|---|---|
| LIVE-01 | P0 | Opening Studio, Advanced, Painter, or Emoji Arranger never takes over the wall. | Begin with a known live animation. Load each page separately. The live animation and its parameters remain unchanged; the page sends no start, stop, parameter, painter-frame, or scene-mutation request as a side effect of loading. Status reads and isolated preview requests are allowed. |
| LIVE-02 | P0 | Browsing and editing are private by default. | On every workspace, selecting an animation or preset, opening Adjust, loading a scene, editing a painter cell, or moving an emoji leaves the known live animation unchanged until an explicit live action is invoked. The UI identifies the work as Draft or Preview. |
| LIVE-03 | P0 | Going live is explicit and accurately reflected. | The only actions that replace live output contain **Take live**, **Take scene live**, or **Mirror to wall** in their visible labels. A subsequent publish may say **Update live**. After activation, the persistent live status identifies the new live item; failures leave the prior state intact and show an error. |
| LIVE-04 | P0 | Live output is reversible without losing work. | While Painter or Emoji is live, **Stop / Return to draft** is visible without scrolling. The global bar always exposes **Stop live output**. Activating either ends live output, returns the editor UI to Draft, and retains the current canvas/arrangement. |
| LIVE-05 | P1 | Live state remains understandable across navigation and refresh. | A persistent live bar is visible in every workspace and exposes Stop. It reconciles with server status after refresh instead of displaying stale local state. |
| IA-01 | P1 | The product is organized around present-day tasks rather than accumulated controls. | Studio exposes Library, Now Playing / Adjust, Compose, and System as named task areas. Painter and Emoji are named creation workspaces. Advanced is clearly labeled for diagnostics and direct system control. Each area is reachable within one navigation interaction. |
| IA-02 | P1 | The initial Studio view is scannable and does not behave as one giant catch-all page. | At 1280×800, initial document height is at most 3,000 CSS pixels; only the active task panel is displayed; opening Adjust brings the relevant controls into view. The left navigation does not create a second, viewport-height scrolling region. |
| IA-03 | P1 | Studio and Advanced have distinct jobs rather than duplicating competing dashboards. | Studio supports routine discovery, contextual adjustment, scene composition/component selection, global wall controls, and guided test/calibration launch. Advanced is the expert direct-control workspace for arbitrary animation inspection, full-precision parameters, performance/receiver diagnostics, raw status, and specialty controls such as Tetris. Equivalent live actions use the same interaction contract. |
| DISC-01 | P1 | The library scales through search. | Search matches animation names, preset names, categories, and helpful metadata case-insensitively. Results and their count update without taking the wall live; clearing restores the prior browse set. |
| DISC-02 | P1 | Category, item-type, and saved-view filters are composable and understandable. | Search, category, and type can be combined with either the Favorites or Recent saved view. The count and empty state describe the active intersection; resetting every filter is one clear action. |
| DISC-03 | P1 | Browsing is progressively disclosed. | Initial Studio renders no more than 24 library cards. **Show more** reveals another bounded batch without resetting search/filter state. At 390×844, the library has no horizontal document overflow. |
| DISC-04 | P1 | Favorites are available and durable. | Every library item can be favorited without taking it live. The Favorite state survives a reload in the same browser, the Favorites filter returns exactly those items, and removing a favorite updates the result immediately. |
| DISC-05 | P1 | Recent work is easy to resume. | A deliberate item interaction (open Adjust, Preview, or Take live) adds the item to Recent; page load alone does not. Recent is ordered newest first, de-duplicated, capped at 12 items, survives reload in the same browser, and can be filtered without changing live output. |
| PARAM-01 | P0 | Parameter controls preserve schema precision. | For each numeric capability the control uses its declared min, max, and step. A step of `0.02` is displayed and changed at `0.02`, never rounded to `0.1`; submitted values remain within bounds. |
| PARAM-02 | P0 | Machine-oriented values do not leak into routine controls. | Studio and ordinary Advanced forms contain no `[object Object]`, serialized object, raw asset path, or unlabeled complex value. Unsupported complex/path parameters are omitted or represented by a purpose-built control. Raw JSON is confined to the explicitly labeled diagnostics area. |
| PARAM-03 | P1 | Live tuning cannot silently target the wrong animation. | Adjust initially changes only draft values. Live parameter updates are enabled only when the adjusted animation is the live animation and the UI shows that relationship; otherwise the person must use Take live first. |
| PHYS-01 | P0 | Emoji Arranger represents the physical wall faithfully. | The preview is a tall 33×138 coordinate surface and states that the current Emoji hardware profile uses the first 8 of 33 horizontal columns, while visually dimming inactive columns. Layout offsets use the same horizontal/vertical axes as the surface. |
| PHYS-02 | P0 | Painter's canvas difference is explicit rather than misleading. | Painter identifies its editable canvas as 32×138 and explains its relationship to the 33×138 physical installation. Its displayed aspect is tall, and pointer mapping reaches the expected grid edges. |
| CAP-01 | P0 | Existing functionality remains reachable after the reorganization. | The running UX exposes animation/preset launch, contextual adjustment, animation speed/wall mood/plant behavior, scene create/update/delete/take-live, painter drawing/masks/presets/mirror, emoji composition/take-live, component catalog/tests/calibration, performance and receiver diagnostics, raw status, and Tetris controls. No feature requires visiting a prototype. |
| A11Y-01 | P1 | Task navigation and primary controls work by keyboard. | Studio task tabs expose tab semantics and visible focus. Arrow Left/Right, Home, and End change the active tab; Tab reaches primary actions in a logical order; every icon-only control has an accessible name. |
| A11Y-02 | P1 | State is not communicated by color alone. | Draft, Preview, Live, selected filters, errors, and disabled actions have persistent text or an equivalent accessible name/state in addition to color. |
| RESP-01 | P1 | Core workflows remain usable on a phone-sized viewport. | At 390×844 there is no horizontal document overflow on all four pages. Live status, the live/stop action, task navigation, and the active workspace remain reachable and controls do not overlap. |
| CONS-01 | P1 | Shared concepts use consistent language and hierarchy. | The same visible terms—Draft, Preview, Take live, Stop / Return to draft, Now Playing, and Advanced—mean the same thing across workspaces. Primary actions are visually distinct from secondary selection/editing actions. |
| CONS-02 | P1 | Live wall settings use one plain-language control model. | The only visible terms are **Animation speed**, **Wall mood**, and **Plant behavior**. Speed shows its multiplier, 0.1×–10× range, and numeric preset values. Mood explains its palette/energy/brightness effect. Plant behavior shows only capabilities supported by the current live animation, states Field/Surface exclusivity, and displays a percentage beside every strength slider. |
| CONS-03 | P0 | Every mutation has one clear owner and no competing duplicate action. | Studio Now Playing is the sole owner of animation preset load/save/delete. Advanced links there instead of repeating those actions. The persistent live bar is the sole general Stop control. Loading an animation preset, mask preset, emoji preset, or scene preset changes only a draft; it never invokes a live/apply endpoint. Each slider has a visible label and visible current value. |
| TEXT-01 | P1 | User-facing labels and values remain complete at every supported width. | At desktop and 390×844, names, descriptions, task subtitles, button labels, preset names, hashes, digests, and diagnostics wrap and remain readable. User-facing CSS contains no ellipsis or line-clamp truncation and does not hide task subtitles. No value is shortened with an appended ellipsis. |
| QUAL-01 | P1 | Normal use is free of broken or misleading feedback. | The acceptance journey produces no uncaught browser errors, broken controls, or success message before server confirmation. Loading, empty, disabled, error, draft, and live states are visibly distinguishable. |

## Required acceptance journey

1. Start a known animation and record its server status.
2. Visit each workspace, browse/select/edit, and prove status is unchanged.
3. In Studio, search and combine category, type, Favorites, and Recent filters;
   verify progressive disclosure and persistence across reload.
4. Open Adjust for an item that is not live, change a draft value, then explicitly
   take it live and verify the exact schema step and status transition.
5. Load a scene preset and prove it only updates the draft; then create or edit a
   scene, take it live explicitly, and verify all scene operations.
6. Draw in Painter and arrange Emoji while the prior animation remains live; then
   mirror/take live, stop/return to draft, and prove the drafts remain.
7. Visit Advanced and verify diagnostics, calibration, receiver/performance data,
   raw status, component tests, and Tetris are still reachable.
8. Repeat the navigation and primary actions with a keyboard and at 390×844.

## Traceability to the original review

| Original finding or recommendation | Acceptance coverage |
|---|---|
| Editors and selection can unexpectedly take over the wall | LIVE-01 through LIVE-05, PARAM-03 |
| Dashboard is a long catch-all with an independently scrolling sidebar | IA-01, IA-02, DISC-03, RESP-01 |
| Dashboard and Control duplicate one another with inconsistent controls | IA-03, PARAM-01 through PARAM-03, CONS-01 |
| Discovery does not scale; search, categories, Favorites, and Recent are missing | DISC-01 through DISC-05 |
| Emoji and Painter previews misrepresent the physical installation | PHYS-01, PHYS-02 |
| Persistent live status and an obvious Stop action | LIVE-04, LIVE-05 |
| Task navigation for Library, Now Playing, Compose, Create, and System / Advanced | IA-01 through IA-03 |
| Private draft/preview plus explicit publish contract | LIVE-01 through LIVE-05 |
| Contextual speed, mood/vibe, plant behavior, presets, and modifiers | CAP-01, CONS-01 through CONS-03 |
| Preserve scenes, tests, calibration, diagnostics, raw status, Tetris, Painter masks/presets, and Emoji composition | CAP-01 |
| Schema/capability-driven controls with no raw paths/objects and exact precision | PARAM-01 through PARAM-03 |
| Complete labels and values with no ellipsis or clipped text | TEXT-01 |
| Responsive and accessible operation | A11Y-01, A11Y-02, RESP-01, TEXT-01, QUAL-01 |
