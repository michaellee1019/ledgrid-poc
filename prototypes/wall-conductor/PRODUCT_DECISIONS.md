# Product decisions

## Why this is genuinely different

The unit of navigation is a **place in an operational sequence**, not an admin page and not a content-card collection. “Now → Find → Audition → Compare → Take live” mirrors the operator’s changing confidence. Persistent live reality never disappears, while the working surface changes from observation to selection to performance.

The library is a **reading index plus one visual stage**. Hundreds of small cards would make the unusually tall previews decorative and force long titles into truncation. Here the index gives complete searchable language, the selected item earns most of the screen, and only three deliberate candidates enter comparison.

Scene construction is a **score**, not another generalized settings form. The background and overlay are the two contractually allowed tracks. Role, provider, fallback, overlay translation, clipping, stale policy, validation, and drift stay attached to those tracks. This makes the fixed scene model easy to understand without pretending it is an unlimited layer editor.

Interaction is a **takeover mode**. Once a game or point-reactive animation is active, browsing gives way to a large representation of the physical wall and large controls. This avoids forcing direct manipulation through a tiny preview surrounded by unrelated configuration.

Operations are an **evidence ladder**. The first answer is “Can tonight’s show continue?” Exact receiver and timing detail is progressively disclosed. Expected incomplete telemetry is explained within transport policy, not collapsed into a frightening red failure.

## How the model handles scale

- One search scans complete preset/component names, descriptions, category, provider, and role.
- Preset and component modes make the 292-to-52 relationship explicit.
- Filters split show-ready exploration from lab/diagnostic access.
- Every row wraps. No CSS ellipsis is used for names or descriptions.
- Keyboard `/`, Up/Down, and `C` support rapid serial evaluation.
- The compare set is intentionally capped at three to preserve useful preview size and decision clarity. Review opens an accessible modal with three true 32:138 canvases; phones retain the same complete content in a horizontally snapping sequence.
- Provider-qualified identifiers are visible in detail, while human names lead.
- Recent and favorite organization are identified as proposals rather than implied backend facts.

The current fixture count is deliberately exact: 32 components have six presets and 20 have five, producing 292 presets across 52 components.

## Preview fidelity and provenance

Every wall canvas uses the exact CSS `aspect-ratio: 32 / 138`. The audition view allocates height to one large preview rather than repeating small poster thumbnails. On a phone, the tall preview and wrapped description remain side by side where space allows, then the index occupies a bounded scan region above them.

Mint means isolated preview; coral means physical live. Each canvas carries a redundant text label, not color alone. Receiver-native choices explicitly say “Host-built simulation — not receiver framebuffer readback or exact live output.” A live confirmation names both the draft entering the room and the output being replaced.

Production media should prefer returned poster/loop metadata and synchronized comparison time. This prototype’s procedural canvas art proves format and interaction hierarchy only; it makes no claim of content accuracy.

## State model

| State | Visual treatment | Mutation rule |
| --- | --- | --- |
| Physical live | Coral label and persistent top strip | Stop and take-live are explicit actions. |
| Isolated draft/preview | Mint border and label | Parameters, scene score, and preview context can change freely. |
| Saved | Neutral saved badge | Preset/scene identity remains visible. |
| Dirty or drifted | Amber warning with explanatory text | Never silently reconciled or hidden. |
| Build-only/quarantined/unsupported | Visible, subdued index row and explicit status | Cannot take live. |
| Global room layers | Amber “independent” context | Applied separately from presets and scene persistence. |

## Hierarchy and typography

Product nouns are concrete: Live, Preview, Preset, Component, Background, Clock overlay, Vibe, Plant material, Receiver. Internal IDs are secondary evidence. Titles and descriptions use normal wrapping and flexible columns; the prototype never uses `text-overflow: ellipsis`. Core action labels have enough room to wrap where necessary.

## Responsive operation

- Desktop uses a stable place rail and a three-part Find workbench: index, audition, compare.
- Tablet keeps index and audition side by side while turning compare into a floating bounded set.
- Phone moves navigation to a six-place bottom bar, stacks the library index above the audition, preserves the 32:138 preview, and keeps stop/room access in the live strip.
- Minimum control height is generally 44px; direct game buttons are larger.
- Focus rings, semantic landmarks, labels, keyboard shortcuts, and `prefers-reduced-motion` are supported.

## Deliberate boundaries

- Painter, emoji arrangement, and developer tools establish access and hierarchy but are lower fidelity than the primary workflows.
- Favorites/recent history, compare, and Painter undo need production persistence/state decisions.
- The prototype does not emulate real animation schemas exhaustively or attempt server validation.
- Global device tuning (FPS, operator speed, brightness) is represented in state/readouts and API mapping but does not receive a full editor, so ordinary content choice stays calm.
- Authentication, multi-operator races, error retries, offline recovery, and command acknowledgement timing are not modeled.
