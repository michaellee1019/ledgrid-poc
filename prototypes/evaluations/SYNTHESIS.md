# Studio Next UX synthesis

> **Historical design record — superseded integration guidance.** This synthesis
> preserves the design-tournament decision and the API assumptions used to build
> the first Studio Next slice. Sections describing direct Look/device takeover,
> bare scene mutation, preset Apply, or mask writing are not current contracts.
> Shipping activation is owned by Composer Check plus guarded activation; mask
> publication and selection are owned by the managed installation-profile flow.
> The separate emergency Stop and read-only compatibility boundaries remain
> intentional.

## Decision

Build `/studio-next` as a **room-first wall control surface** with one persistent, authoritative account of the physical wall. Use Wall Conductor's operational sequence and evidence model underneath Room Tune's plain language and phone hierarchy.

Do not combine the candidates' metaphors. The product is not simultaneously a conductor's desk, radio tuner, journey, Finder, atlas, loom, ghost wall, or threshold. The approved vocabulary is deliberately literal:

| Use | Meaning | Do not use as primary UI language |
| --- | --- | --- |
| **Live wall** | The last server-observed physical output state | On Air, performance, pulse |
| **Look** | A human-facing component + preset choice | Strand, material, recipe |
| **Preview** | Isolated work that cannot mutate the wall | Audition, rehearsal, ghost |
| **Compare** | Two or three pinned previews | Light table, deck |
| **Scene** | One background and an optional clock overlay | Score, weave, moment |
| **Background** | The scene's single base component | Thread, atmosphere sheet |
| **Clock overlay** | The one fixed optional overlay slot | Clock thread |
| **Room settings** | Global Vibe, plant behavior, brightness, target FPS, and operator speed | Climate, room layers |
| **Health** | Policy-aware operational evidence | Wall care, wall pulse |
| **Tools** | Painter, interaction, masks, calibration, and developer destinations | Workshop, maker shelf |
| **Save layout only** | Persist scene structure without changing the wall or globals | Save performance |
| **Take live** | Deliberately request a look or scene on the physical wall | Cross threshold, perform |
| **Stop output** | Black/stop active output without claiming hardware power state | Stop scene when any mode may be active |

Intent labels such as **Settle**, **Welcome**, **Focus**, and **Play** are filters into Looks. They are not saved objects, timed journeys, or a replacement for direct control.

## Evidence and consensus ranking

The weighted mean is shown as an arithmetic check, not as the selection rule. The final order also considers whether a defect is local to a prototype interaction or architectural at the physical-command boundary.

| Consensus | Candidate | Household rank / score | Artist rank / score | Ops rank / score | Rank sum | Score sum / mean | Convergence judgment |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | **Wall Conductor** | 2 / 86 | 1 / 98 | 1 / 86 | **4** | **270 / 90.0** | Operational foundation. Best identity, catalog, fixed scene, and evidence structure. Its phone live-action inconsistency, reversed-looking confirmation, unsupported scene enums, missing power control, and overclaimed refresh must be fixed, not copied. |
| 2 | **Room Tune** | 1 / 91 | 3 / 87 | 2 / 85 | **6** | **263 / 87.7** | Interaction and responsive foundation. Best household vocabulary, conventional Current → Proposed review, intent entry, preview provenance, and 390 px hierarchy. Its Stop/Power conflation, brightness gap, scene recovery dead end, and less exact API model prevent it from being the systems foundation. |
| 3 | **Lumen Path** | 3 / 80 | 2 / 92 | 5 / 66 | **10** | **238 / 79.3** | Keep purpose filters, private preview, and comparison. Do not use its journey runtime, live-state source, confirmation state, or Health architecture. Ops found stale old-state reviews after Stop/Power Off and active-presentation claims while powered off; timed journeys also lack backend contracts. |
| 4 | **Wall Studio** | 4 / 78 | 4 / 68 | 4 / 73 | **12** | **219 / 73.0** | Unanimous fourth. Keep its expert inspector patterns, Stop versus Power review, and route map only. Broken search, preloaded compare, inactive mobile workspaces, stale receiver identity, and a Build-only Take Live path are command-boundary failures. |
| 5 | **Lumen Loom** | 5 / 56 | 5 / 57 | 3 / 76 | **13** | **189 / 63.0** | Harvest the large three-up tall comparison and saved-not-live wording. Reject the overall spatial grammar: names disappear into strands, global consequence is ambiguous, native fallback validation is unsafe, hold is pointer-dependent, and phone operation is not reliable. |

This ranking reconciles, rather than averages, the persona disagreement:

- The artist reasonably valued Lumen Path's purpose and turn model. Ops demonstrated that its physical-state premise can be stale and that its timed execution has no backend. It is therefore a discovery influence, not the control architecture.
- Ops valued Lumen Loom's deliberate threshold. Household and artist could not reliably operate its final hold or phone layout. The preflight idea survives; the hold-only gesture and spatial navigation do not.
- Household observed Wall Conductor's phone Take Live as a no-op while artist and ops completed comparable phone paths. An inconsistent consequential action is itself release-blocking. Studio Next requires deterministic action state, error feedback, and automated 390 px coverage.
- Wall Studio was visually usable on phone for ops, but household saw stacked inactive workspaces and artist found them in the accessibility tree. Studio Next renders exactly one active phone workspace and removes all inactive work from focus and the accessibility tree.

## Cross-persona truths

1. The physical wall needs one persistent truth surface. Selection, preview, scene drafts, saved layouts, and health evidence must never replace it.
2. A household intent is the fastest first step; a complete textual catalog is still required. **Settle** narrows Looks but never hides **Browse all**.
3. Complete component, preset, provider, role, readiness, and preview provenance must be readable without hover. Human names lead; qualified identity remains attached.
4. Previewability and executability are separate facts. Receiver-native preview is a host simulation unless the backend explicitly says otherwise; it is never framebuffer readback.
5. The useful comparison limit is three. A pinned set starts empty, never silently evicts an item, preserves the 32:138 ratio, and repeats full identity under every preview.
6. A scene is fixed: one background and zero or one clock overlay. Validation, Save layout only, and Take live are separate actions.
7. Vibe, plant behavior, brightness, FPS, operator speed, power, and output are global/device state. They are not owned or saved by looks or scenes.
8. Consequential reviews must be conventional **Current → Proposed** diffs. A gesture such as holding can supplement but never replace an ordinary keyboard/touch-operable confirmation.
9. An HTTP success or receiver-refresh 202 means accepted, not applied, observed, agreed, fresh, or healthy.
10. Expected-degraded evidence is policy, not a warning color. Transport, playback agreement, telemetry completeness, release acceptance, freshness, and visible-pixel verification are different claims.
11. Phone is a task hierarchy, not a compressed desktop. Live truth and Stop remain present, full names wrap, one workspace is active, and comparison/review becomes a sheet.
12. Enabled controls must work and acknowledge immediately. Silent no-ops, inert buttons that look active, non-updating sliders, and search fields that retain text without changing results are release failures.

## Contradictions the implementation must resolve

| Contradiction in the candidates | Studio Next decision |
| --- | --- |
| Stop means output black in some candidates and power off in Room Tune | **Stop output** is immediate and does not claim power changed. **Power Off** is separate and reviewed. Because current `/api/status` does not expose authoritative power distinct from stopped output, Power Off remains release-gated until that status field exists. |
| Hold feels deliberate but failed ordinary click/keyboard paths | The final control is a standard button. Optional pointer hold may be offered only as a redundant preference, never the sole path and never as evidence of backend application. |
| Journey/purpose framing is inspiring but timed execution and scheduling do not exist | Purpose is a reversible catalog filter. A ready look or valid scene can go directly live through existing endpoints. Timed routines are a gray/TODO surface and cannot block basic live control. |
| Some candidates retained old live identity after Stop or Power Off | Every live, scene, review, receipt, and Health surface reads one shared `LiveSnapshot` store. No workspace keeps its own live identity cache. Stopped state says **Stopped** and may show **Last output**, never **Live now**. |
| Catalog-visible or previewable content appeared stageable | One normalized readiness decision drives the row, detail CTA, comparison CTA, review entry, command serializer, and final backend validation. Any disagreement fails closed. |
| Global Apply looked atomic even though the backend uses separate endpoints | The review shows a multi-operation plan. Calls run serially and the receipt reports **accepted/observed/failed/not attempted** per property. The UI never says the group was atomic. |
| Health showed precise metrics without source, freshness, or consistency with stopped/powered-off state | Every evidence group shows source and observed time. Power/output state gates the headline. Accepted refresh is shown separately from a later observation. |
| Candidate provider strings varied (`host.python`, `host_python`, `host-python`) | Canonical client identity uses backend values `python` and `receiver_native`. UI labels them **Host Python** and **Receiver native**. No other provider spelling is persisted or compared. |
| Scene controls offered wrap, reject, hide, fallback, or stop-scene policies that the backend rejects | The UI exposes only `clip_to_wall`, stale `hold`, or `clear_after_lease` with `lease_ms`. It does not serialize invented options. |
| Exact power state was presented as fixture truth | Studio Next does not infer power from `is_running`. Until `/api/status.power` is authoritative, power is **Unknown** after reload and Power Off does not ship as a completed feature. |

## Information architecture

### Desktop

The shell has three invariant regions:

1. **Live wall bar, full width and sticky**
   - State: `LIVE WALL · RUNNING`, `STOPPED`, `POWERED OFF`, `CONNECTING`, or `STATE UNKNOWN`.
   - Full current identity: look or scene name, canonical provider, role, and preset where applicable.
   - Brightness, Vibe, saved/drift relation, and observation age.
   - Immediate **Stop output**.
   - **Room settings** shortcut.
   - Reviewed **Power Off** only after authoritative power status is added.
   - When stopped, identity is labeled **Last output**, not live.

2. **Left navigation, stable across workspaces**
   - **Now**
   - **Looks**
   - **Scene**
   - **Room**
   - **Health**
   - **Tools**

3. **One active workspace**
   - **Now:** current physical summary; Settle, Welcome, Focus, Play intent shortcuts; recent command receipts; resume only when the held/last identity is known.
   - **Looks:** a 320 px textual results column, one flexible large preview, and a collapsed compare tray. Search covers full names, descriptions, tags, provider, role, and IDs. Filters are intent, category, provider, role, and readiness. Counts always show `matching / total`.
   - **Compare:** an overlay/sheet containing two or three equal tall previews. It is entered only after the user pins candidates; it never auto-populates.
   - **Scene:** two-row structure editor on the left, exact-ratio whole-scene preview in the center, validation/state inspector on the right. Background and Clock overlay are the only rows.
   - **Room:** Vibe, plant behavior, brightness, target FPS, and operator speed. Each section shows observed, draft, and support state; one review summarizes the exact multi-call plan.
   - **Health:** conclusion first; four physical wall sections second; timing/transport/receiver evidence and refresh lifecycle below.
   - **Tools:** links or explicitly unavailable destinations for direct interaction, painter, masks, calibration, catalog exceptions, and developer operations.

### 390 px

- The sticky live bar is two rows and never horizontally scrolls.
  - Row 1: explicit state plus a minimum 48 × 48 px **Stop** button.
  - Row 2: full identity that wraps, provider/role, brightness, and observation age. Tapping identity opens a read-only live-detail sheet.
- The bottom navigation has five destinations: **Now, Looks, Scene, Room, Health**. Tools is a clearly labeled link from Health, not a sixth crowded icon.
- Only the active route is mounted. Inactive desktop panes are absent from the DOM, focus order, and accessibility tree.
- Now shows the four intent buttons as a two-column grid followed by **Browse all looks**.
- Looks is a sequence, not columns: search/filter summary → results list → selected preview → actions. Selecting a result moves focus to the preview heading but does not scroll past the live bar.
- Compare is a full-height sheet with vertically stacked candidates and a sticky close/choice bar. It never obscures the results list underneath.
- Scene shows whole-scene Preview first, then Background, Clock overlay, validation, and actions. Editing a row opens a bottom sheet.
- Room uses native labeled controls. Changed values remain drafts until **Review room changes**.
- Health shows one sentence and four section rows before technical evidence.
- Sheets respect safe-area insets, trap focus while open, close on Escape, and return focus to the invoking control.
- There is no document-level horizontal overflow at 390 CSS px or 200% zoom.

## Exact core journeys

### 1. Orient and stop safely

1. Load `/studio-next`; the shell fetches `/api/status` before presenting any live claim.
2. The live bar shows source observation age, output state, and full provider-qualified identity. While unresolved it says **State unknown** and Take live actions remain disabled.
3. Selecting **Stop output** sends `POST /api/stop` immediately. Stop has no confirmation because delay is the greater hazard.
4. The receipt first says **Stop request accepted**. The client polls status until `is_running=false` and mode is not `scene`, `animation`, or `painter`; then it says **Stopped observed at [time]**. If no matching observation arrives, it says **Stop not yet observed** and keeps Stop available.
5. The bar may show the prior identity only as **Last output**. It must not infer whether hardware power remains on.

### 2. Find a calming look, preview, compare, and take it live

1. From Now select **Settle**. Looks opens with an intent filter and an honest `matching / total` count; **Clear Settle filter** and **Browse all** are adjacent.
2. Select a result. Full look name, component, preset, canonical provider, role, readiness, and preview provenance appear before any live action.
3. Select **Preview**. Only a preview endpoint may run. The plaque reads either **Isolated host preview — never changes the physical wall** or **Host simulation preview — not receiver framebuffer readback**.
4. Pin up to three looks with **Add to compare**. The tray starts empty and never evicts silently.
5. Select **Take live** on an executable look. The client opens a fresh Current → Proposed review; it does not require a journey or schedule.
6. Select the standard button **Take [full look name] live**. A final status re-read must still match the review's current fingerprint. Otherwise submission is blocked and the draft is preserved.
7. The UI shows accepted, then separately polls for an observed match. It never turns the live bar optimistic.

### 3. Inspect receiver-native or unavailable content

1. A result remains discoverable with separate **Receiver native**, role, readiness, and preview-provenance fields.
2. Preview is allowed only when the catalog/preview response provides a safe preview. Its plaque always says **Host simulation preview — not receiver framebuffer readback** and `live_state_mutated=false`.
3. Build-only, unavailable, quarantined, and developer-only content has no Take live path. The disabled reason is specific and an expert remediation link is shown when known.
4. A receiver-native background is executable only when the backend catalog says it is scene-compatible and the current rollout policy permits it. In the current backend that means only `receiver_native:compiled_rainbow` with both required rollout flags. Catalog visibility alone never grants execution.

### 4. Compose and save a scene, then optionally take it live

1. Scene starts from either the current scene or a new draft. It records the base scene revision and current live fingerprint.
2. Choose exactly one Background. Add or remove the optional `clock_overlay`. For a receiver-native background, a known Host Python fallback is mandatory.
3. Edit clock opacity, integer strip/LED translation, and stale policy. The whole-scene Preview remains isolated.
4. Any edit increments `draftRevision` and clears prior validation. **Take live** is disabled until `validatedRevision === draftRevision`.
5. Select **Validate** to call `POST /api/v1/scene/validate`. Validation errors stay attached to the relevant row.
6. Select **Save layout only**. The review states that Vibe, plant behavior, brightness, target FPS, operator speed, power, and live output are excluded. Saving calls `POST /api/v1/scene-presets` and returns **Layout saved; wall unchanged**.
7. If the live fingerprint or base scene revision changes, show **Wall changed elsewhere** with **Reload current wall** and **Rebase draft**. Neither action discards edits without a second explicit choice.
8. Select **Take scene live** for a fresh Current → Proposed review. On acceptance, poll until the observed scene identity/revision matches or time out to Unknown.

### 5. Change independent room settings

1. Room loads observed Vibe, plant modifiers, brightness, target FPS, and operator speed from status and the relevant GET endpoints.
2. Editing native controls changes only `RoomDraft`. The current scene/look identity does not change.
3. Select **Review room changes**. Show a property-by-property old → new diff and explicitly say **Looks and scene layouts will not be saved or replaced**.
4. Apply serially in this order: Vibe, plant modifiers, brightness, target FPS, operator speed. Do not send the next call until the prior property is observed. A rejection or observation timeout stops the remaining calls; this also prevents rapid commands from overwriting one another on the single-file control channel.
5. The receipt lists each field group as **Observed**, **Accepted; awaiting observation**, **Failed**, or **Not attempted**. Offer **Retry remaining** and **Restore observed starting values**; restoration is another reviewed best-effort operation, not an undo guarantee.

### 6. Diagnose receiver evidence and refresh it

1. Health derives its headline from current output state before receiver metrics.
2. For running output, show transport sent, playback agreement, telemetry completeness, release acceptance, evidence age, and visible verification as separate rows per wall section.
3. A configured one-way path is **Playing; verification incomplete as expected**, never generic Healthy or Failed.
4. Select **Request fresh evidence**. `POST /api/v1/receivers/status/refresh` returning 202 creates an **Accepted** receipt with `request_id` and `command_id`.
5. Poll `/api/status`. Only a newer source observation can update the evidence view. Because current status does not echo the refresh `request_id`, label it **Newer evidence observed; request correlation unavailable** rather than **Refresh complete**.

## Authoritative client state model

There is one application store. Workspaces select from it; they do not copy or own live state.

| State object | Required fields | Authority and mutation rule |
| --- | --- | --- |
| `LiveSnapshot` | `source`, `sourceObservedAt`, `receivedAt`, `lastCommandId`, `isRunning`, `mode`, `power`, `identity`, `scene`, `brightness`, `vibe`, `plantModifiers`, `targetFps`, `operatorSpeed`, `fingerprint`, `freshness` | Replaced only by normalized `/api/status` and `/api/v1/scene` responses. Never optimistically modified. `power` is `unknown` until backend status supplies it. |
| `CatalogRecord` | `key`, `provider`, `pluginId`, `role`, `displayName`, `compatibility`, `readiness`, `previewCapability`, `executionCapability`, `reason` | Normalized from `/api/v1/components` plus provider-safe preset sources. Key is `${provider}:${pluginId}`. |
| `PresetRecord` | `key`, `componentKey`, `presetId`, `displayName`, `parameters`, `readiness` | Key is `${provider}:${pluginId}:${presetId}`. A bare component or preset ID is never sufficient for equality, caching, review, or receipts. |
| `PreviewDraft` | `targetKey`, `parameters`, `elapsed`, `previewIdentity`, `provenance`, `status` | Local/backend preview state only. It has no method that can call a live endpoint. |
| `CompareSet` | ordered unique list of 0–3 `PresetRecord.key` values | Client-local and explicit. Fourth add is refused until the user removes one; no automatic eviction. |
| `SceneDraft` | `baseSceneRevision`, `baseLiveFingerprint`, canonical `scene`, `draftRevision`, `validatedRevision`, `validation`, `savedPresetId`, `dirty`, `drift` | Edits are local. Validation applies to exactly one draft revision. Saving never updates `LiveSnapshot`. |
| `RoomDraft` | observed and draft values for each global/device property plus per-property support | Local until reviewed Apply. Never attached to `SceneDraft` or a preset save payload. |
| `CommandAttempt` | `attemptId`, `kind`, `reviewedOldFingerprint`, `proposedFingerprint`, `endpoint`, `requestedAt`, `acceptedAt`, `serverCommandId`, `observation`, `status`, `error` | Append-only receipt record. HTTP response changes it to Accepted/Rejected; only later status evidence can change it to Observed/Conflict/Unknown. |
| `ReceiverEvidence` | `source`, `sourceObservedAt`, `sourceSceneRevision`, per-lane policy and evidence, refresh attempt | Derived from the same status observation as the live headline. A refresh request does not mutate evidence values. |

Canonical identity rules:

- Provider values are exactly `python` and `receiver_native`; labels are presentation only.
- A live host look identity is provider + component/plugin + preset ID, with full names as display data.
- A live scene identity includes scene revision, background qualified identity, overlay qualified identity or absence, and overlay layout.
- `LiveSnapshot.fingerprint` is a stable hash of consequence-bearing values: power when available; `is_running`; mode; current qualified content/preset; canonical scene; brightness; Vibe ID/revision; plant modifiers; target FPS; and operator speed. Observation timestamps are not part of the fingerprint.
- `sourceObservedAt` uses controller `updated_at`, then status-file `written_at`. A response-time fallback may be shown as **Received at**, but cannot establish source freshness.
- If `/api/status` and `/api/v1/scene` disagree about mode, identity, or scene revision, state is **Conflicting evidence** and Take live is blocked.

## Safety boundaries

| Action | Boundary | Required result |
| --- | --- | --- |
| Search, filter, select, compare | No confirmation | No live endpoint is callable from these state modules. |
| Preview look/scene or preview interaction | No confirmation; permanent Preview plaque | Response must preserve `live_state_mutated=false` where provided. Network tests reject any live endpoint from preview actions. |
| Save layout only | Scope review, not physical confirmation | Receipt repeats **wall unchanged** and excluded globals. |
| Stop output | Immediate, one activation | Accepted then observed receipt; no success claim from HTTP alone. |
| Power Off | Reviewed consequence and observed power state | Not enabled for release until `/api/status` distinguishes power from stopped output. |
| Take look/scene live | Fresh Current → Proposed review plus ordinary final button | Re-fetch and compare current fingerprint immediately before send; drift blocks and preserves draft. |
| Apply room settings | Exact old → new property review | Per-call, partial-failure-aware receipt. No atomic claim. |
| Send direct interaction | Only inside an explicitly Live interaction mode; otherwise Preview → Review send | Persistent target, coordinates, strength/radius, immediate Stop, and rate limits. This is outside the initial slice. |
| Build-only/quarantined/unavailable/developer-only | No command boundary exists | Disabled at row, detail, comparison, review, serializer, and backend. |

The UI may prevent stale submission by re-reading state, but the current backend does not expose compare-and-swap for live commands. Studio Next must describe its check accurately and must not claim race-free application. Server-enforced expected revision is a follow-up contract, not a reason to route ordinary control through nonexistent timed journeys.

## Current → Proposed confirmation contract

Every look, scene, power, and room-setting review uses the same structure:

1. **Target:** the named physical installation.
2. **Current:** source-observed time and age; power if authoritative; running/stopped; complete provider-qualified content or scene identity; scene revision; relevant globals.
3. **Proposed:** complete provider-qualified identity and exact changed values.
4. **Unchanged:** values deliberately outside command scope. Look/scene review explicitly lists Vibe, plant behavior, brightness, FPS, speed, and power as unchanged unless the chosen endpoint payload really includes one.
5. **Safety evidence:** readiness decision, scene validation revision, native fallback, and preview provenance.
6. **Final action:** one standard `<button>` whose accessible name includes the destination, such as **Take Quiet Tidal Silk live**. Cancel is adjacent; Escape cancels.

Opening the review fetches a fresh snapshot. Pressing the final button fetches again. If the consequence-bearing fingerprint changed, no command is serialized. The review updates to **Wall changed since review opened**, shows previous current → new current → proposed, and offers **Review against current wall**.

## Accepted → observed receipt lifecycle

The lifecycle is exact and shared by all command types:

`draft → reviewing → sending → accepted | rejected → observed_match | observed_conflict | observation_timeout`

- **Sending:** request is in flight; duplicate submission is disabled and the live bar remains unchanged.
- **Accepted:** HTTP 2xx only. Show endpoint, request time, client attempt ID, and backend `command_id`/`request_id` when returned. Never use **Applied**, **Live**, **Healthy**, or **Current** at this stage.
- **Rejected:** show HTTP error and retain the draft/review.
- **Observed match:** a later controller-sourced status observation is newer than the preflight and matches the proposed consequence-bearing identity. If `last_command_id` matches the response command ID, label **Command-correlated observation**. Otherwise label **Matching state observed after request; command correlation unavailable**.
- **Observed conflict:** a newer observation contains a different identity or value. Show both; do not retry automatically.
- **Observation timeout:** poll at 250 ms, 500 ms, 1 s, then every 2 s through 15 s. Keep the receipt as **Accepted; outcome not observed** and provide **Check again**. Do not roll the live bar forward.

`POST /api/device/state`, `POST /api/stop`, and legacy preset Apply currently do not return a command ID from the web route even though the control channel has one. The client must use a generated `attemptId` and weaker observation copy until those routes return `command_id`. Scene start/stop/update and Vibe do return command IDs. Receiver refresh returns both request and command IDs but lacks request-correlated completion evidence.

## Readiness, native execution, and provenance

Normalize backend compatibility into these client states; never derive them from styling or gallery placement:

| State | Detail/preview | Compare | Take live | Required copy |
| --- | --- | --- | --- | --- |
| `ready` + executable route | Yes | Yes | Yes | Provider, role, and route-backed readiness |
| `ready` but no executable provider-qualified route | Yes | Yes | No | **Ready in catalog; live route unavailable in Studio Next** |
| `build_only` | Yes when a safe preview exists | Yes | No | Missing artifact/build prerequisite |
| `unavailable` | Detail; preview only if explicitly supplied | Yes when preview exists | No | Reason and compatible alternative when known |
| `quarantined` | Maintainer-visible detail; safe preview only | Optional | No | Quarantine reason/review owner when available |
| `developer_only` | Tools only | No in household compare | No in initial slice | **Developer-only; not executable from Studio Next** |

Execution rules:

- Host Python background content can use direct preset/device endpoints only after component existence, role, preset existence, and readiness are verified.
- Overlay content never uses `/api/device/state` or legacy start. It enters only the `clock_overlay` scene slot.
- Receiver-native preview is always labeled **Host simulation preview — not receiver framebuffer readback** when `framebuffer_readback=false`.
- Receiver-native scene execution is limited by current backend policy to `compiled_rainbow` with both rollout flags. Every other native record is visible/previewable according to evidence but lacks Take live.
- `/api/v1/components/<component_id>/presets` can return 409 when providers collide. The adapter must not drop provider or choose one. Use a provider-safe source where available; otherwise show a provider-collision diagnostic and disable preset actions for that record.
- A backend 4xx readiness/validation rejection is final even if the client thought the content was executable. The receipt becomes Rejected and the client invalidates the cached readiness decision.

## Strict scene/global separation

The only persisted Scene fields are those accepted by `normalize_scene_payload`:

| Scene part | Allowed contract |
| --- | --- |
| Background | Provider-qualified background component, preset/fingerprint and parameter overrides; exactly one |
| Known fallback | Provider-qualified **Host Python** background; mandatory for a receiver-native background |
| Overlay | Zero or one slot with `slot_id="clock_overlay"` and the `clock_overlay` component |
| Opacity | Integer byte 0–255; UI may display a derived percentage |
| Placement | Signed integer `strip_translation`, signed integer `led_translation`, fixed `clip_policy="clip_to_wall"` |
| Stale behavior | `{"policy":"hold"}` or `{"policy":"clear_after_lease","lease_ms":1..4294967295}` |
| Revision | Unsigned scene revision carried through validation/review; not presented as a server CAS guarantee |

Scene Preview may receive Vibe and plant modifiers as explicit **Preview context**. Those values are not written into `SceneDraft.scene`, are not submitted to scene-preset save, and are labeled **Preview only; not saved or applied**.

Room/global state is separate and uses separate endpoints:

| Global/device property | Endpoint | UI rule |
| --- | --- | --- |
| Vibe | `GET /api/v1/vibe`, then `PUT` or `POST` to `/api/v1/vibe` | Stable values Neutral, Quiet, Cozy, Vivid, Celebration; old → new review |
| Plant behavior | `POST /api/config/plant-modifiers` | Many light behaviors may coexist; at most one field (`attractor`, `repulsor`, `slow_zone`) and at most one surface (`obstacle`, `portal`, `bumper`, `hazard`, `habitat`) |
| Brightness | `POST /api/config/brightness` or atomic device request when paired with a host look | Integer 0–255 with displayed percent; draft then Apply |
| Target FPS | `POST /api/config/target-fps` | Integer 1–200; advanced disclosure |
| Operator speed | `POST /api/config/animation-speed` | Positive finite multiplier; distinguish from authored animation speed and Vibe tempo |
| Power | `POST /api/device/state` | Separate reviewed action; release-gated until observable in status |

`POST /api/v1/scene-presets` already rejects `vibe`, `plant_modifiers`, or `output`; Studio Next also strips and tests for those keys before sending.

## Policy-aware Health

Health computes a headline from the following order; later evidence cannot override an earlier physical-state constraint:

1. No controller-sourced observation or observation older than 10 seconds: **Wall state is unknown; evidence is stale.**
2. Authoritative `power=false`: **Wall is powered off.** Receiver playback evidence is historical and cannot be summarized as active.
3. `is_running=false`: **Output is stopped.** Receiver observations may describe the last run; no active-presentation claim is allowed.
4. Running with unexpected transport/driver error or unexpected loss of required evidence: **Playback needs attention**, naming affected sections and the failed claim.
5. Running with policy-declared incomplete telemetry or release acceptance but operational transport/playback evidence: **Playing; verification incomplete as expected.** Show the exact unverified sections and a visual-check instruction.
6. Running with fresh complete evidence: **Playback evidence agrees across all sections.** Still state that transport/receiver evidence does not prove visible LEDs, wiring, power delivery, foliage occlusion, or color accuracy.

Freshness uses controller `updated_at` or status-file `written_at`, never the browser receive time. At the current 0.5 s status-write default, evidence is **Fresh** through 3 seconds, **Aging** from 3–10 seconds, and **Stale** after 10 seconds. If deployments change the write cadence, the backend should expose it and the client should use `max(3 seconds, 6 × cadence)` for Fresh and `max(10 seconds, 20 × cadence)` for Stale.

Each wall section exposes separate facts:

- transport operational;
- source scene revision/context digest;
- playback/frame agreement;
- telemetry complete true/false;
- release acceptance true/false;
- configured transport policy;
- last source observation time;
- visual verification: confirmed, needed, or unavailable.

Colors supplement text. Expected degraded is not yellow merely because data is missing; it is a named policy result. Four matching stale receiver identities are not agreement with the current live wall.

## Backend integration map

| Studio Next responsibility | Existing endpoint(s) | Concrete adapter rule / gap |
| --- | --- | --- |
| Live snapshot | `GET /api/status`; `GET /api/v1/scene` | Poll every 2 s and after commands. Normalize `updated_at`/`written_at`, `last_command_id`, mode, qualified current preset/scene, globals, and receiver evidence. Status currently lacks authoritative power. |
| Catalog | `GET /api/v1/components?provider=&role=` | Preserve `provider + plugin_id`, role, compatibility, preview/build data, and rollout policy. Client-side intent/search filters do not change identity. |
| Presets | `GET /api/v1/components/<component_id>/presets`; host compatibility `GET /api/animations/<name>/presets` | Treat 409 as a provider-collision state. Never fall back to a same-named provider. A provider-qualified preset route remains a backend gap. |
| Look preview | `GET /api/preview/<animation_name>`; `POST /api/preview/<animation_name>/with_params` | Host Python only unless response/catalog explicitly supplies a safe native simulation. Preserve provenance and no-live-mutation semantics. |
| Scene preview | `POST /api/v1/scene/preview` | Use returned `preview_identity`, `preview_label`, `background_provider`, `live_state_mutated`, and `framebuffer_readback`. |
| Direct ready host look | Prefer `POST /api/device/state` with `power`, `brightness` if intentionally included, `animation`, and `preset`; compatibility `POST /api/animations/<animation>/presets/<preset>/apply` | Direct live control requires no journey backend. Device-state route is atomic for its supported fields but currently omits response `command_id`. Do not use it for overlay or arbitrary receiver-native content. |
| Stop | `POST /api/stop`; scene-specific `DELETE /api/v1/scene` | Main Stop uses `/api/stop` so it covers the active output mode. Observe through status. |
| Power | `POST /api/device/state` with Boolean `power` | Command exists; persistent observation does not. Add `/api/status.power` before releasing distinct Power UI. |
| Scene read/validate/start/stop/update | `GET /api/v1/scene`; `POST /api/v1/scene/validate`; `PUT`, `POST`, or `DELETE /api/v1/scene`; `PATCH /api/v1/scene/components/<target>` | Prefer whole-scene validate and start for reviewed drafts. `target` is background or overlay. Response command IDs mean accepted only. Current HTTP boundary has no CAS. |
| Scene layouts | `GET` or `POST /api/v1/scene-presets`; `GET` or `DELETE /api/v1/scene-presets/<id>`; `POST /api/v1/scene-presets/<id>/apply` | Save scene only. Apply enters the same Current → Proposed and receipt path as an unsaved draft. |
| Vibe | `GET /api/v1/vibe`; `PUT` or `POST /api/v1/vibe` | Independent call with command ID; observe selected Vibe revision/state through status. |
| Plant behavior | `POST /api/config/plant-modifiers` | Serialize `PlantModifierState`; server remains authoritative for exclusivity and strengths 0–1. Route lacks command ID. |
| Brightness/FPS/speed | `POST /api/config/brightness`; `POST /api/config/target-fps`; `POST /api/config/animation-speed` | Separate calls, separate receipt rows; no atomic global claim. |
| Health | `GET /api/status`; `GET /api/stats`; `GET /api/metrics`; `GET /api/hardware/stats` | Status is the identity/time anchor. Metrics never override stopped/power state or freshness. |
| Receiver evidence refresh | `POST /api/v1/receivers/status/refresh` | 202 = Accepted. Poll status for a newer observation; request-correlated completion is a backend gap. |
| Interaction | `POST /api/interaction`; `POST /api/hole`; `POST /api/dpad/<direction>`; preview interaction endpoint | Link from Tools in the initial slice; a later live surface must show target and Stop persistently. |
| Painter/masks | Painter update/frame/clear/mask/preset endpoints; existing `/painter` | Initial slice links to the existing tool and labels the context switch. No fake embedded editor. |
| Developer reload/refresh | `POST /api/reload/<animation_name>`; `POST /api/refresh` | Tools only, provider-qualified target, explicit operation receipt; not part of household navigation. |

Required server-side work for the initial slice is deliberately small:

- Put one catalog-derived execution decision in front of `/api/device/state`, legacy start/preset Apply, and scene start/apply. It must reject Build-only, Unavailable, Quarantined, Developer-only, incompatible-role, unsupported-provider, and rollout-disabled content. Client gating is redundant safety, not the final authority.
- Return the control-channel `command_id` from Stop, device-state, legacy preset Apply, plant, brightness, FPS, and speed routes. Studio Next can ship before this with weaker correlation copy, but it may not claim command-correlated observation for those routes.
- Add authoritative `power` to `/api/status` before enabling Power Off. Provider-qualified preset lookup, receiver-refresh completion correlation, and server CAS remain later contract work because the initial UI can fail closed or state the limitation without blocking direct ready Host Python control.

## Deliberately bounded functional slice for `/studio-next`

Implement the first release in the existing dependency-free Flask/vanilla-JS stack:

- Add `/studio-next` as a separate route and template; do not replace `/` during evaluation.
- Add one namespaced stylesheet and one ES module/client file. Do not import prototype fixture state or candidate source.
- Implement the persistent Live wall bar, shared state normalizer/store, 2 s polling, Stop, and receipt drawer.
- Implement Now with Settle/Welcome/Focus/Play filters and Browse all.
- Implement Looks with real catalog data, provider-safe host presets, working search/filters/counts, one isolated preview, explicit pinning, three-up Compare, and direct Take live for route-proven ready Host Python backgrounds.
- Add the shared server-side execution gate used by every initial live route; do not treat a disabled client button as sufficient enforcement.
- Implement receiver-native detail/preview provenance. Enable receiver-native Take live only for a validated `compiled_rainbow` scene when current catalog rollout compatibility permits it.
- Implement the fixed Scene editor, validation, isolated preview, Save layout only, drift display, and reviewed Take live.
- Implement Room for Vibe, plant modifiers, brightness, target FPS, and operator speed with serial per-property receipts.
- Implement Health from real status/metrics plus the Accepted → newer-observation refresh lifecycle.
- Implement responsive 390 px routes and the accessibility rules below as part of the slice, not a later styling pass.
- Link existing Painter/Emoji/control destinations from Tools with clear **Opens existing tool** labels.

The slice explicitly excludes timed journeys/routines, scheduling, favorites/recents persistence, synchronized preview clocks, arbitrary receiver-native deployment, build/quarantine remediation, embedded painter/mask/calibration editors, permissions, offline command queues, and server-enforced CAS. None may be simulated as working.

## Gray and TODO surfaces

Gray surfaces are read-only rows, links, or disabled controls with literal status text. They must not use normal enabled button styling.

| Surface | Initial treatment | Exit condition |
| --- | --- | --- |
| Timed routines / scheduled arcs | **Not available — runtime and persistence API required** in Tools; not shown in the direct live journey | Durable schema, execution, cancellation, progress, restart, and observed receipt contracts exist |
| Power Off | Disabled with **Power state is not observable yet** | `/api/status` reports authoritative power and tests distinguish stopped/powered-off after reload |
| Provider-colliding preset source | Detail visible; preset actions disabled with provider list from 409 | Provider-qualified preset route exists |
| Arbitrary receiver-native live content | Preview/detail only | Catalog policy and executable provider route explicitly support it |
| Favorites and Recents | Omitted, not fake-local | Persistence, ordering, and multi-client semantics exist |
| Embedded Painter/masks/calibration | Links to existing tools or read-only destination descriptions | Draft/save/apply/acceptance flows are implemented and tested |
| Developer-only actions | Tools-only, disabled unless actually connected | Authorization, exact target, backend result, and receipt UI exist |
| Refresh completion correlation | Show Accepted, then **newer evidence observed; correlation unavailable** | Status echoes refresh request/command identity and observation time |
| Server CAS | Client preflight freshness check with honest limitation | Live/scene/global mutation endpoints accept expected revision/fingerprint and reject conflicts atomically |
| Camera-visible acceptance | **Visual check required** | Calibrated camera evidence with observation time and target identity is integrated |

## Accessibility rules

- All functionality is available through native semantic controls. No `div`/canvas click is the only action path.
- Stop, Take live, Power, and Apply have unique accessible names containing action and target. Never concatenate duplicate visible/ARIA labels.
- A hold gesture is never required. If retained as an option, Space/Enter on the standard button completes the same reviewed action without timing.
- Minimum touch target is 44 × 44 CSS px; Stop is at least 48 × 48 px on phone. Adjacent targets have at least 8 px separation.
- Full names wrap. No essential identity, readiness, reason, or state uses ellipsis, hover-only disclosure, or color alone.
- Live, Preview, Saved, Drift, Accepted, Observed, and Expected degraded are literal text in addition to color/icon treatment.
- Search has a visible label, result count, clear action, and zero-result recovery. Updating search announces the count in a polite live region.
- Consequential request failures use an assertive live region; accepted/observed progress uses a polite region. Focus moves to the review heading and to receipt error headings when intervention is required.
- Dialog/sheet focus is trapped; Escape cancels before sending; focus returns to the invoking control. Sending cannot be dismissed into ambiguity; it may be minimized to the persistent receipt drawer.
- Sliders expose name, current draft value, observed value, min/max/step, and keyboard arrows. Changing a slider is not announced as applied.
- Result lists use list semantics and a roving active option only if arrow-key behavior is implemented completely. Tab order remains sufficient without shortcuts.
- Exact-ratio canvas previews have an adjacent accessible text summary. Canvas pixels are decorative when that summary is present.
- `prefers-reduced-motion: reduce` pauses automatic preview motion by default and offers a labeled Play preview button. Comparison remains usable with static frames.
- At 390 px, inactive workspaces are unmounted or `hidden` + `inert`; they are not merely moved off-screen.
- Meet WCAG 2.2 AA contrast and focus appearance. Validate at 200% zoom, system text enlargement, keyboard only, VoiceOver/Safari, and reduced motion.

## Release-blocking acceptance tests

### Physical truth and receipts

- On cold load with delayed/failed status, no fixture identity or Healthy claim appears; Take live remains disabled until a controller-sourced snapshot exists.
- Stop from every route and at 390 px in one activation. Verify the UI says Accepted before a newer observed stopped state and never infers power.
- When Power Off is enabled in a future change, stop with power retained, then power off, reload, and prove all live/scene/review/health surfaces distinguish the two from authoritative status.
- Delay status observation after a 2xx command. The receipt must remain **Accepted; awaiting observation** and the live bar must retain its prior observed state.
- Return a newer mismatching status. The receipt becomes Conflict, not success, and no automatic retry occurs.
- Open a take-live review, change live content from another client, then confirm. No live command is serialized; the UI shows previous current → new current → proposed and preserves the draft.

### Catalog, readiness, and provenance

- With the contract fixture, show exactly 52 components and 292 presets. Search/filter counts reconcile to the displayed set and clearing filters restores 292.
- Long component/preset names, provider, role, readiness, and reason are complete at desktop, 390 px, and 200% zoom.
- A provider collision returning 409 never silently resolves to another provider and cannot create a Take live action.
- Attempt execution from every Build-only, Unavailable, Quarantined, Developer-only, unsupported-role, and route-unavailable entry through row, keyboard, detail, compare, review URL/state injection, and command serializer. Zero live requests may be emitted.
- Call each live backend route directly with those same forbidden identities. Every request must return 4xx before a control-channel command is written.
- Receiver-native previews display **Host simulation preview — not receiver framebuffer readback** beside the specimen and in Compare. Preview actions emit no live endpoint requests.
- Receiver-native scene execution fails closed unless the component is `compiled_rainbow` and both rollout gates are present in authoritative compatibility.

### Direct control and scene separation

- Starting from only existing direct-control endpoints, Settle → ready Host Python look → Preview → Take live completes without a journey, schedule, or timed-runtime API.
- Compare starts empty, accepts at most three explicit pins, never auto-seeds or evicts, and retains full identity for all candidates.
- Scene serialization accepts exactly one background, at most `clock_overlay`, byte opacity, integer translations, `clip_to_wall`, and only `hold` or `clear_after_lease`; invalid UI/state injection is rejected before request and by backend.
- A receiver-native scene without a known Host Python fallback cannot validate or open Take live.
- Edit after validation; Take live must disable until the exact new draft revision validates.
- Save a scene and prove the request omits Vibe, plant modifiers, brightness, FPS, operator speed, power, and output. Verify live status and all globals remain unchanged and the receipt says **wall unchanged**.
- Simulate base/live drift. Reload and Rebase are visible, preserve the draft, and never clear drift merely because Save succeeded.

### Globals and Health

- Review Room changes with exact old → new values. Force the third endpoint to fail; the receipt accurately marks the first two observed/accepted, third failed, and remainder not attempted. It never calls the group atomic.
- Plant controls cannot serialize more than one field or more than one surface modifier; the server rejection path is also tested.
- Brightness, FPS, and speed controls respond to keyboard and display changed draft values before Apply.
- While stopped, Health cannot say presenting frames, ready to play, or Healthy even when cached receiver evidence was formerly good.
- Under an expected one-way receiver policy, Health says **Playing; verification incomplete as expected**, identifies affected sections, and does not infer release acceptance or visible pixels.
- Four stale receiver records that agree with each other but not current live identity are labeled stale/conflicting, never **all agree**.
- Receiver refresh 202 shows Accepted with IDs. It cannot say refreshed/current until a newer observation; without request echo it must retain the correlation-unavailable qualifier.

### Phone and accessibility

- At 390 × 844, identify output state and full identity, Stop in one activation, Settle, inspect provider/readiness, preview, compare, complete Current → Proposed review, and reach the observed receipt without overlap or horizontal scrolling.
- Exactly one phone workspace is in the DOM/accessibility tree. Bottom navigation and compare/review sheets do not cover the final action or Stop.
- Complete the same core journey with keyboard only. No hold, drag, hover, canvas precision, or horizontal wheel gesture is required.
- Run automated accessible-name/role checks plus VoiceOver smoke tests for live bar, results, compare, review, receipt, Room controls, and Health evidence.
- With reduced motion, previews do not autoplay and all comparison/confirmation information remains available.
- Every enabled consequential action immediately enters Sending, Accepted, or Rejected. No enabled action can be a silent no-op.

## Verification of this synthesis

- Source coverage: the evaluation brief, all three persona evaluations, and README plus PARADIGM/PRODUCT_DECISIONS for all five candidates were read completely. Backend claims were checked against `web/app.py`, `ipc/scene_contract.py`, `ipc/control_channel.py`, `animation/core/manager.py`, `animation/core/plant_awareness.py`, and the relevant route/contract tests.
- Arithmetic: all 15 reported weighted totals were recomputed from the eight category scores and the brief's weights; all match their evaluation files.
- Candidate score sums are Wall Conductor `86 + 98 + 86 = 270`, Room Tune `91 + 87 + 85 = 263`, Lumen Path `80 + 92 + 66 = 238`, Wall Studio `78 + 68 + 73 = 219`, and Lumen Loom `56 + 57 + 76 = 189`.
- Rank sums are respectively `4`, `6`, `10`, `12`, and `13`; the consensus order matches those sums, while the implementation decision separately discounts architecturally unsafe live-state and nonexistent-runtime assumptions.
- Completeness: this report defines a consensus ranking, cross-persona truths, contradictions, one vocabulary, desktop and 390 px IA, direct core journeys, authoritative state, command and evidence lifecycles, readiness/native/provenance rules, strict scene/global separation, policy-aware Health, accessibility, endpoint mapping, bounded `/studio-next` scope, honest gray surfaces, and release-blocking tests.
