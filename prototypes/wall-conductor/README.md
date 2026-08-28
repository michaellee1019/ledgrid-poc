# Wall Conductor

Wall Conductor is a dependency-free product prototype for operating the 32 × 138 living LED plant wall. It is intentionally isolated from the shipping frontend and uses fixture data. It does not call the backend or change the physical wall.

## Run it

From the repository root:

```sh
python3 -m http.server 8088 --directory prototypes/wall-conductor
```

Then open `http://localhost:8088`. Opening `index.html` directly also works. No install, build, or network access is required.

## Core paradigm

This is a conductor’s desk, not a dashboard:

1. The sticky **Live strip** is the single persistent account of physical reality.
2. **Find** is a full-name textual index feeding one large **Audition stage**. It is designed for keyboard-speed scanning of hundreds of items without turning every item into a tiny card.
3. A bounded **Compare set** holds up to three complete provider-qualified choices.
4. Parameter edits, scene edits, preview vibe overrides, and previews stay in a mint-colored isolated state.
5. A deliberate **Take live** handoff names both the preview source and the live output it will replace.
6. **Compose** is a two-track score: one background plus an optional clock overlay. It validates as a whole before performance.
7. **Room layers** hold independent global vibe and plant-material semantics; they do not masquerade as scene or preset parameters.
8. **Health** starts with an operational sentence, then unfolds into timing, receiver, transport, and device evidence.

See [PRODUCT_DECISIONS.md](PRODUCT_DECISIONS.md) for the scale, fidelity, state, responsive, and accessibility rationale.

## Information architecture

- **Now** — current physical output, its saved/modified state, direct stop, recent choices, and intent-led entry into the library.
- **Find** — 292 preset fixtures and 52 component fixtures, search, filters, tall audition, parameters, favorites, compare, and live handoff.
- **Compose** — fixed background + optional clock overlay score, layout controls, drift warning, isolated whole-scene preview, validate, save-layout-only, and take-live.
- **Touch** — interaction-focused live surface, logical coordinates, point/puncture interactions, and game controls.
- **Health** — plain-language health, performance evidence, four receiver policies, and manual refresh.
- **More** — lower-fidelity Painter/masks, Emoji arrangement, and Developer room entry points.
- **Room layers** — globally available from the live strip because vibe and plant semantics span every content surface.

## Key interactive walkthroughs

### Browse → audition → compare → live

1. Open **Find**, type `quiet`, `clock`, or `receiver native`, or use `/` to focus search.
2. Use the full-name index or the Up/Down keys. Switch between 292 Presets and 52 Components.
3. Change preview parameters; the preview remains explicitly isolated.
4. Press **C** or **Add to compare** for up to three choices. Choose **Review side by side** to open the real comparison surface with three tall previews, full provider/preset identities, and complete names. On phones it becomes a horizontally scrolling sequence.
5. Choose **Take this preset live**, review the source/replacement confirmation, and confirm. The prototype returns to Now with the new live identity.

### Compose a scene

1. Open **Compose** and inspect the background and clock-overlay tracks.
2. Toggle the overlay and adjust opacity/translation.
3. Validate, save layout only, or take the validated score live.
4. Notice that vibe, plants, power, and brightness are named as excluded from scene persistence.

### Change independent room material

1. Open **Room layers** in the live strip.
2. Select a vibe and change modifier strengths.
3. Apply the global layer set. These choices are described as following live output and future starts.

### Diagnose

1. Open **Health** and read the headline conclusion.
2. Inspect exact frame/render/send measures and receiver agreement.
3. Expand raw evidence. Receiver 3 demonstrates an expected limited return path without being labeled as a generic failure.

## Fixture assumptions and prototype-only proposals

- The catalog is deterministically expanded to exactly 52 component and 292 preset records so scale can be exercised without backend access.
- Recent history and favorites are local prototype proposals. Production would need persistence, ordering, and multi-client synchronization decisions.
- Compare is a client-side set of three items. A production comparison may request synchronized preview times or cached loops.
- Preview imagery is procedural canvas fixture art, not real poster/loop media or framebuffer data.
- Undo in Painter is a proposal requiring client-side history or a backend transaction model.
- Scene preview vibe override is represented as feedback rather than a complete editor.
- The prototype assumes one authenticated household/installation scope and does not model permissions.

## Historical backend wiring map — superseded

> This map records the APIs available during the design tournament. It is not
> current integration guidance: direct animation/device takeover, bare scene
> mutation, preset-apply, and mask-writing routes are now fail-closed. A shipping
> implementation must use Composer Check plus guarded activation and the managed
> installation-profile workflow. The prototype itself remains fixture-only and
> makes no network requests.

| Prototype surface | Existing API(s) to wire | Notes |
| --- | --- | --- |
| Live strip and Now | `GET /api/status`, `GET /api/frame`, `POST /api/device/state`, `POST /api/stop` | `/api/device/state` supports atomic power + hardware brightness + animation + optional preset. |
| Catalog index | `GET /api/v1/components`, `GET /api/animations` | Prefer the versioned catalog for provider-qualified identity, role, compatibility, and availability. Search/filter/favorites remain client concerns. |
| Component and preset detail | `GET /api/animations/<animation_name>`, `GET /api/animations/<animation_name>/presets`, `GET /api/v1/components/<component_id>/presets` | The production adapter must preserve provider identity; a bare component ID is not sufficient when providers collide. |
| Isolated audition | `GET /api/preview/<animation_name>`, `POST /api/preview/<animation_name>/with_params` | Surface preview provenance from the response; receiver-native simulation must never be presented as framebuffer readback. |
| Take preset live | `POST /api/device/state`, `POST /api/start/<animation_name>`, `POST /api/animations/<animation_name>/presets/<preset_id>/apply` | The confirmation favors the atomic device-state call where applicable. |
| Live parameter update | `POST /api/parameters` | Keep authored animation speed distinct from global operator speed and vibe tempo. |
| Scene score | `GET/PUT/POST/DELETE /api/v1/scene`, `POST /api/v1/scene/validate`, `PATCH /api/v1/scene/components/<target>`, `POST /api/v1/scene/preview` | The score maps directly to background and `clock_overlay`; preview can carry isolated vibe and plant modifiers. |
| Scene preset save/load/apply | `GET/POST /api/v1/scene-presets`, `GET/DELETE /api/v1/scene-presets/<preset_id>`, `POST /api/v1/scene-presets/<preset_id>/apply` | Persist layout/components only and render returned preset diagnostics/drift. |
| Room vibe | `GET/PUT/POST /api/v1/vibe` | Independent global layer. Preview overrides belong in preview requests, not this endpoint. |
| Plant material | `POST /api/config/plant-aware`, `POST /api/config/plant-modifiers` | Client enforces one field and one surface modifier while server remains authoritative. |
| Touch/game | `POST /api/interaction`, `POST /api/hole`, `POST /api/dpad/<direction>`, `POST /api/preview/<animation_name>/interaction` | Live and preview interaction targets must remain visually and technically separate. |
| Performance and receivers | `GET /api/status`, `GET /api/metrics`, `GET /api/hardware/stats`, `POST /api/v1/receivers/status/refresh` | Interpret telemetry completeness and release acceptance under the transport policy instead of using a single red/green status. |
| Global device controls | `POST /api/config/brightness`, `POST /api/config/target-fps`, `POST /api/config/animation-speed` | These belong in expert/contextual surfaces, not authored parameter forms. |
| Painter/masks | `POST /api/painter/updates`, `POST /api/painter/frame`, `POST /api/painter/clear`, `GET/POST /api/painter/masks`, `GET/POST /api/painter/presets` | Lower fidelity in this prototype. |
| Developer room | `POST /api/reload/<animation_name>`, `POST /api/refresh` | Deliberately outside the normal household path. |

## Files

- `index.html` — semantic application shell, persistent live strip, global room drawer, live confirmation, and accessible comparison dialog.
- `styles.css` — visual system, tall-preview fidelity, desktop/tablet/mobile layouts, focus and reduced-motion handling.
- `app.js` — fixture catalog, application state, all view rendering, interactions, and procedural previews.
- `PRODUCT_DECISIONS.md` — rationale and explicit differences from a dashboard/card-grid approach.
- `checks.mjs` — dependency-free structural checks.

## Checks

```sh
node --check prototypes/wall-conductor/app.js
node prototypes/wall-conductor/checks.mjs
```
