# Lumen Loom: a spatial control paradigm

## Proposition

The living wall is not a monitor and its animations are not files. It is a room-scale light instrument partly covered by living material. Lumen Loom therefore borrows from a printmaker's light table, a weaver's loom, and an installation rehearsal—not from a dashboard, media library, or desktop file browser.

The primary noun is **material**. A preset is a strand of material; a component is the spool that made it; a scene is a two-layer weave; the global vibe is the room's climate; plant modifiers describe how light meets the installed foliage and rooting globes. The physical installation lives across a perceptual threshold that a rehearsal cannot accidentally cross.

## Spatial grammar

The interface is one left-to-right physical gesture:

1. **Atlas** — all 52 makers and all 292 preset strands occupy one continuous field. At overview scale, every preset is a colored hairline. Family and text lenses thin the field without pagination or a hidden remainder.
2. **Light table** — three 32:138 specimens run at useful poster scale. These loops are explicitly isolated local simulations. Provider, role, cadence, availability, and plant behavior remain attached to the material.
3. **Ghost wall** — a tall rehearsal image carries a background thread and an optional clock thread. Operators drag material into it, touch a point or hole, and directly move the overlay.
4. **Threshold** — live output is outside the work surface. Crossing requires a valid preflight, an old→new receipt, and a 1.2-second continuous hold.

This fixed geography makes safety learnable through muscle memory: exploration happens left of the threshold; physical consequence happens only at the far edge.

## The atlas is a field, not a list

A conventional catalog spends most of the display on repeated cards, then paginates or truncates. The atlas uses semantic zoom:

- Each component is a labeled horizontal spool.
- Each preset is always present as one touchable strand.
- Hover, focus, or touch widens a strand in place.
- Touching promotes it to the light table; it does not start or stage it.
- Search and family lenses preserve the total and visible counts.
- Readiness has a material mark: dashed for build-only, faded for unavailable, barred coral for quarantine.

The prototype deliberately generates the exact 52-component/292-preset corpus and asserts those counts at startup and in static checks. Provider identity is part of each preset ID (`host-python:…` or `receiver-native:…`), not inferred from its title.

## Rehearsal and comparison

The light table solves the wall's extreme aspect ratio instead of cropping it into landscape thumbnails. Three simultaneously moving specimens reveal vertical pacing, density, repetition, and how an image occupies the 138-LED height. They can be compared for as long as needed without a command path to the wall.

The prototype uses deterministic canvas simulations rather than live framebuffer data. This distinction is labeled on every relevant surface. A receiver-native specimen is a package preview, not a claim about the pixels currently visible in the room.

On touch devices, every specimen has an explicit “place” action. Desktop users can also drag a specimen or atlas strand onto the ghost wall. Unavailable, build-only, or quarantined specimens remain previewable but refuse staging.

## Scene composition as a weave

The scene has one background thread and an optional clock overlay thread. The background may be host-Python or receiver-native; the qualified provider stays visible. A full-scene material removes an incompatible overlay rather than silently composing an invalid scene.

The overlay thread exposes only its consequential spatial properties:

- opacity;
- directional placement and center;
- stale-data policy: hold, hide, or fallback;
- explicit fallback selection.

`Validate scene` checks readiness, role compatibility, and output routes. `Save layout only` persists the rehearsal model without applying it. These are separate verbs because saving authorship and changing a room are separate consequences.

## Independent climates

The belt below the work surface contains two controls that are visually adjacent but structurally independent:

- **Global Vibe** chooses Neutral, Quiet, Cozy, Vivid, or Celebration. It describes palette and motion character.
- **Plant Material** independently controls foliage hush, globe avoidance, edge halo, and vine current. It describes semantic relations with calibrated physical geometry.

Changing Vibe never resets a plant gesture. Plant values never masquerade as per-preset parameters. FPS ceiling and speed sit alongside them as installation-level tempo.

Global brightness remains in the wall ribbon because it is a direct, continuously legible physical-light control. Power and stop/start also remain attached to the observed live object, not the rehearsal scene.

## Truthful state and the threshold

Three state relationships are never collapsed into one ambiguous “saved” indicator:

| Relationship | Language |
| --- | --- |
| Rehearsal vs saved layout | `UNSAVED REHEARSAL` or `SAVED` |
| Rehearsal vs applied receipt | `LIVE MATCH` or `STAGED APART · n CHANGES` |
| Applied receipt vs observed wall | `WALL DRIFT · REAPPLY` |

The take-live dialog restates that previews were isolated, shows the physical scene and staged scene side by side, runs a preflight, and requires a continuous hold. Releasing early cancels. After a successful simulated take, saved layout, applied receipt, and observed wall converge.

This prototype never sends a network request and cannot mutate backend state or hardware.

## Plant and point interaction

The ghost wall carries seven globe silhouettes as semantic landmarks. Point, hole, and D-pad gestures operate directly on wall coordinates and report the corresponding strip/LED location. These are seeds for direct interaction rather than parameter-form stand-ins.

Painter, mask work, emoji toss, and game control live in a low, secondary tool drawer. Their entrances and touch-mode switching are represented; editing and persistence are intentionally not faked.

## Health as physical truth

Health is available from the live object or floor dock, not mixed into composition. It distinguishes:

- cadence and render cost;
- host-to-receiver frame flow;
- content readiness;
- complete telemetry from evidence that cannot exist on write-only paths.

Receivers 3–4 are labeled **Expected degraded** with `telemetry_complete=false` and `release_acceptance=false`. The interface explains that this is the installed expectation rather than turning it into either a false green success or a new red failure. A walkthrough control can simulate drift to exercise the reapply state.

## Phone posture

On a phone, the geography becomes a vertical ritual rather than a shrunken desktop:

- live truth stays pinned at the top;
- the atlas becomes a short overview well;
- specimens become a horizontally snapping strip of tall posters;
- the ghost wall moves ahead of its layer controls;
- the take-live threshold becomes a full-width bottom action;
- secondary tools remain in a bottom sheet.

The same safety boundary and state language survive the reflow.

## Secondary fidelity

The core walkthrough is implemented: atlas filtering, selection, animated comparison, direct placement, layer composition, overlay adjustment, point/hole/D-pad rehearsal, independent vibe/plants/tempo, validation, save-only, live diff, drift, and hold-to-take.

Painter persistence, editable calibration masks, emoji physics, games, receiver package operations, raw receipts, and diagnostic execution are honestly represented as lower-fidelity entrances. They are not implied to work.

## What would need production validation

- Map the material schema to the real component/preset API without losing provider-qualified identity.
- Use trusted packaged previews for receiver-native content; never execute receiver binaries in the dashboard.
- Run validation and apply against authoritative scene receipts, transactional receiver staging, and explicit display ownership.
- Bind direct wall gestures to the real sparse/point/hole protocols with bounded rates.
- Source health language from actual capabilities so “expected degraded” is policy-driven.
- Test the threshold under latency, disconnect, partial-stage, stale overlay, quarantine, and drift conditions.
- Conduct photographed physical acceptance after the final runtime restart; the prototype's canvas is not evidence about the installed wall.
