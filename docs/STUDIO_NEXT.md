# Studio Next

Studio Next is an alternate, room-first control surface for the living LED wall. It is intentionally separate from the current dashboard while the interaction model is evaluated.

## Route and files

- UI: `GET /studio-next`
- Bootstrap: `GET /api/v1/studio-next/bootstrap`
- Physical activation handoff: `GET /composer`, followed by Composer's server
  Check and guarded scene-activation transaction
- Retired compatibility aliases: `POST /api/v1/studio-next/take-look` and
  `POST /api/v1/studio-next/take-scene` fail closed with `428` and write no
  controller command
- Template: `web/templates/studio_next.html`
- Styles: `web/static/css/studio_next.css`
- Client: `web/static/js/studio_next.js`

The client is dependency-free semantic HTML, namespaced CSS, and vanilla JavaScript. It loads no fixture catalog or live identity. The first physical-wall claim comes from the bootstrap/status APIs; failed or stale status disables consequential draft actions.

## Functional slice

Studio Next currently provides:

- A sticky, server-observed Live wall bar with output state, full identity, source age, Brightness, Vibe, and one-activation **Stop output**.
- Status polling every two seconds. One client store owns live state; selection, previews, scene drafts, and room drafts never update it optimistically.
- **Now** intent filters: Settle, Welcome, Focus, and Play, plus an unfiltered catalog entry.
- **Looks** search and filters across complete names, descriptions, tags, provider, role, IDs, and server execution state. Counts reflect the live catalog (currently 53 components and 293 presets; these are not hard-coded).
- A large exact 32:138 isolated renderer preview with provider/readiness/provenance details, an explicit empty-to-three compare set with no automatic eviction, and an accessible compare dialog/sheet. No server poster or loop asset is published.
- Provider-qualified Look discovery, isolated preview, and comparison. Physical activation is a **Check & activate in Composer** handoff; Studio Next has no direct Look execution path.
- A fixed Scene draft editor: exactly one ready, loaded Host Python background and an optional `clock_overlay`. It serializes only `clip_to_wall`, `hold`, or `clear_after_lease` with a valid lease. Validation, a backend-rendered isolated frame preview, **Save layout only**, and the Composer activation handoff are distinct.
- Independent Room drafts for Vibe, representative plant behavior, output brightness, target FPS, and operator speed. Application is a visible serial plan, never described as atomic. A failure or observation timeout stops remaining calls and leaves per-property receipts.
- State-first Health copy, four receiver sections, evidence source/age/policy, and receiver refresh as **Accepted → newer observation**. Because status does not echo the refresh request, the client says correlation is unavailable.
- Desktop navigation and a 390 px task hierarchy with a compact Live bar, 48 px Stop and receipt targets, five-item bottom navigation, one active workspace (`hidden` and `inert` for the rest), wrapping names, and review/compare sheets.
- Existing-tool links for direct interaction.

## Safety model

Preview and live command modules are separate. Procedural or asset previews never call a live endpoint. Receiver-native previews are labeled **Host simulation preview — not receiver framebuffer readback**.

Studio Next never serializes a Look or Scene takeover. Its visible activation
actions link to Composer, where a short-lived server Check binds the exact scene,
globals, runtime/profile identities, and controller session/revision. Composer's
durable activation resource keeps Pending separate from a later correlated
Active observation. The two retired Studio Next command aliases remain only as
fail-closed compatibility boundaries.

Room-setting requests use a conventional **Current → Proposed** review. The
client stores the observed consequence-bearing fingerprint and refetches
immediately before its explicitly serial, non-atomic plan. A `2xx` changes the
per-setting receipt to **Accepted; awaiting observation**; it does not change the
Live bar. A newer matching observation is **Observed**, a mismatch is **Observed
conflict**, and a timeout remains **Accepted; outcome not observed**.

Stop is immediate because delay is the greater hazard. Acceptance and an observed stopped mode remain distinct, and Stop never claims hardware power changed.

Room calls run in this order: Vibe, plant behavior, brightness, target FPS, operator speed. The next call waits for a newer matching observation. Receipts show **Observed**, **Accepted; awaiting observation**, **Failed**, or **Not attempted** per property.

## Honest TODO and disabled surfaces

- **Power Off:** disabled because `/api/status` does not yet expose authoritative power distinct from stopped output.
- **Timed routines:** runtime, persistence, cancellation, progress, and restart contracts do not exist.
- **Arbitrary receiver-native live:** visible/previewable catalog content is not assumed executable. Studio Next offers no direct activation route; Composer enforces provider/runtime/profile readiness during Check and activation.
- **Full parameter editor:** requires a complete provider-qualified schema and reviewed execution path.
- **Embedded Painter, masks, and calibration:** initial slice links to existing tools or shows a disabled destination; no editor is simulated.
- **Developer actions:** require authorization, exact targets, and receipts.
- **Room-setting compare-and-swap:** Studio Next's serial room-setting preflight cannot eliminate a race after its final status read. Scene/animation takeover is instead owned by Composer's server/controller compare-and-swap transaction.
- **Refresh completion correlation:** newer evidence can be observed, but not attributed to a particular refresh request.
- **Camera-visible acceptance:** transport evidence cannot prove visible LEDs, wiring, power delivery, foliage occlusion, or color accuracy.
- **Scene rebase UI:** drift preserves and disables the draft; a complete deliberate rebase flow is still required.
- **Room restoration:** starting values return as a new reviewed best-effort draft, not an undo guarantee.

## Verification

Run the focused server contract tests and syntax/static checks:

```sh
uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' python -m unittest tests.unit.test_studio_next -v
node --check web/static/js/studio_next.js
git diff --check -- web/templates/studio_next.html web/static/css/studio_next.css web/static/js/studio_next.js docs/STUDIO_NEXT.md
```

Before replacing the current dashboard, also test at 390 × 844 and 200% zoom, keyboard-only, VoiceOver/Safari, reduced motion, delayed/mismatching status observations, and each backend rejection case. The prototype is functional, but physical-wall validation should be deliberate and supervised; automated UI tests must stub mutation endpoints rather than command the installation.
