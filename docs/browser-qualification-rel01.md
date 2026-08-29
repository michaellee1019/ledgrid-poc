# REL-01 portable browser qualification

This lane retains machine-readable Chromium, Firefox, and WebKit evidence for
the portable Composer contract. It cannot qualify physical iPhone Safari, iOS
installed standalone mode, VoiceOver, physical wall output,
controller/receiver performance, or electrical safety. Those results belong in
separate evidence records and remain required before REL-01 can be closed.

## Acceptance contract

[`tools/browser_qualification/rel01_manifest.json`](../tools/browser_qualification/rel01_manifest.json)
is the versioned source of truth. Every required engine must execute all eight
journeys and retain its reported engine identity and version, Playwright
version, timestamps, viewport observations, and individual assertion outcomes.

- `core_no_mutation` chooses the activation-ready Color Gradient background, tunes it,
  proves Undo/Redo and the Clock toggle, runs a local Check, saves to the
  authoring library, proves the assigned preset identity invalidates that Check,
  reruns Check for the saved identity, and cancels guarded activation review.
- `offline_reconnect` seeds a real `v19` cache on the fixture origin, proves the
  verified `v20` promotion removes it, blocks the dynamic bootstrap endpoint,
  and proves the versioned static catalog still starts a Python renderer with
  Play enabled and zero wall-state reads. It then prepares the pinned offline
  runtime, proves the active worker retains the bundled catalog and exact
  profile, reloads and edits while offline, proves a stale Check cannot enable
  review, runs a new Check, exports, reconnects, refreshes digest-compatible
  server capabilities, and verifies the local draft did not change.
  Chromium uses browser-native offline emulation. Because Playwright Firefox 153
  and WebKit 26.5 reject navigation before dispatching to an active service
  worker under that flag, they use the no-wall fixture's origin-outage gate:
  every origin request returns 503, the fixture retains the rejected count and
  path, and `/composer` must still reload from verified cache before the gate is
  removed for reconnect.
  Evidence labels these strategies `native_network_offline` and
  `fixture_origin_outage` respectively. Before and after the offline Check, the
  runner compares the complete Python runtime cache inventory by URL, byte
  count, SHA-256, and recorded metadata; any new, missing, or changed runtime
  asset fails the journey.
- `worker_recovery` captures a nested Aurora managed-native draft, records a labeled
  synthetic Worker error immediately followed by real `Worker.terminate()`,
  requires exactly one bounded restart, compares the exact restored draft,
  runs a recovered Check, and detects later stale overwrites.
- `responsive_layouts` observes 375×667, 390×844, 430×932, 768×1024, and
  1440×1000. Each viewport must have no horizontal overflow, reachable
  navigation and actions, and at least 44×44 primary targets.
- `keyboard_only_desktop` uses only Tab, Shift+Tab, Enter, Space, arrow,
  Home/End, and declared keyboard shortcuts after navigation. It follows the
  skip link, filters and chooses Color Gradient, tunes with Undo/Redo, operates
  the inspector tablist and Clock switch, completes Check and library Save,
  then proves native dialog focus containment, Escape cancellation, exact
  focus return, and zero wall mutation.
- `global_controls` exercises all five vibes, brightness, speed, FPS, and all
  fourteen plant-modifier classes, then opens and cancels review.
- `profile_masks` uses the managed profile draft to paint foliage plus all
  seven named globe regions. It binds the exact eight painted cell identities
  to the saved profile, publishes an immutable candidate, cancels
  profile-selection review, reloads, and compares the exact saved masks. Each
  engine uses a distinct declared LED row so a single fresh fixture can run the
  three-engine matrix without one engine's saved draft turning the next run
  into a no-op.
- `python_native_clock` runs Clock checks on the activation-ready Color Gradient
  background and the browser-previewable Aurora managed-native background. It also
  requires the native card's exact declared host-activation ineligibility reason
  so browser preview evidence cannot be mistaken for receiver readiness. A
  missing pinned Python runtime is an honest blocker, not a reason to skip this
  journey.

Browser console errors and any non-authoring API mutation fail the relevant
journey. Missing engines, missing or duplicate assertions, engine identity
mismatches, missing versions, viewport mismatches, probe crashes, Playwright
version drift, and dirty working trees all fail closed. A passing portable
matrix still records REL-01 as `PENDING_EXTERNAL_EVIDENCE`.

## No-wall fixture

Use the repository fixture rather than a production server. It binds only to a
loopback host, serves the real `AnimationWebInterface` and Composer assets,
seeds a temporary `InstallationProfileLibrary` from the checked-in profile
fixture, builds and publishes the checked-in Aurora package into a temporary
managed-native library with the required scene-policy gates, keeps Check and
guarded review available for components that declare activation readiness,
attaches no controller consumer, and retains then rejects every attempted wall
command. The real Aurora descriptor still fails closed if the product marks its
host implementation unloaded; the fixture does not override that declaration.
The fixture gives its web and synthetic controller surfaces one identical
SHA-256 release identity derived from the checked-out Git commit, publishes a
canonical active-state digest, and records both identities plus the source
commit in fixture status. Retained evidence rejects inconsistent identities, an
identity not derived from that commit, or a fixture commit different from the
clean commit recorded after the matrix.
Native build output is content-addressed under the ignored
`run_state/browser_qualification_native_builds` directory; authoritative library
state remains inside the selected fixture state directory.

The committed browser tooling has an npm lockfile. From a fresh worktree, install
the locked package and all three supported engines once:

```bash
just browser-qualification-setup
```

Then run the complete no-wall matrix:

```bash
just browser-qualification
```

The runner starts its own fixture on an OS-assigned loopback port, shuts it down
on success, failure, or interruption, and writes an ignored run directory under
`run_state/browser_qualification/evidence/`. Its `index.json` names the retained
evidence JSON, fixture state, Playwright traces, and videos. Each run has a UUID
in its directory name, so simultaneous worktrees and local runs do not collide.
The fixture status is available at `/__qualification__/status` while the runner
is active. A nonzero
`wall_mutation_attempts` count or any line in
`wall-mutation-attempts.jsonl` is a qualification failure.
`network_outage_blocks` is separate: WebKit offline qualification requires a
positive count including `/composer`, proving the cached navigation did not
reach a usable origin while the outage gate was active.
This desktop WebKit fixture result is not physical iPhone Safari, installed iOS
standalone, or a claim about full-device network behavior.

## Running and retaining evidence

For a previously started fixture, the evidence module resolves the local locked
tooling package directly; no absolute module path or environment variable is
needed:

```bash
uv run --frozen python -m tools.browser_qualification.evidence \
  --base-url http://127.0.0.1:8765 \
  --output run_state/browser_qualification/evidence/manual-rel01.json
```

The command exits successfully only when all three real engines execute every
required portable assertion from a clean commit. It always writes JSON,
including launch, runtime, or journey failures, so an unavailable engine can
never be mistaken for a pass. The output location is operator-selected and the
record is not committed automatically.

`manifest_sha256` is the SHA-256 of canonical JSON (sorted keys with compact
separators), not the byte digest of the pretty-printed manifest file. This keeps
the evidence binding stable across whitespace-only formatting while still
changing for every semantic manifest edit.

## Physical iPhone, installed mode, and VoiceOver evidence

The external lane has a separate fail-closed validator. It validates evidence;
it does not drive an iPhone, infer observations, or turn external evidence into
the complete REL-01 release result by itself:

```bash
uv run python -m tools.browser_qualification.external_iphone_evidence \
  --write-template /absolute/path/to/rel01-iphone-capture.json

uv run python -m tools.browser_qualification.external_iphone_evidence \
  --input /absolute/path/to/rel01-iphone-capture.json \
  --output /absolute/path/to/retained/rel01-iphone-evidence.json
```

The generated worksheet contains the exact current journey, assertion, and
viewport structure, but is deliberately `NOT_EXECUTED`, has no device claims,
and cannot pass until an operator replaces every placeholder with observed
evidence.

The input must use schema `ledgrid.rel01-external-iphone-capture`, version `1`,
and declare `disposition: EXECUTED`. `OPERATOR_WAIVED`, simulator, Continuity
Camera-only, unavailable, unpaired, or non-inspectable devices cannot pass. The
device record must retain its name, model name and identifier, hardware UDID,
iOS version/build, Safari version, WebKit version, available CoreDevice state,
and USB or network transport. The physical-UDID shape deliberately excludes a
Simulator UUID.

The capture has exactly three sessions: `safari`, `installed_standalone`, and
`voiceover`. Normal Safari must report `navigator_standalone: false` with Safari
chrome visible. Installed mode must report `navigator_standalone: true`, no
Safari chrome, and a present Home Screen installation. The VoiceOver session
must identify which of those surfaces it used, prove VoiceOver was enabled, and
pass every declared spoken name/role/state, state-change, live-region,
navigation-order, modal-containment, focus-return, and unlabeled-control
observation.

Every session must retain the exact current eight-journey and assertion set from
`rel01_manifest.json`, including the complete responsive viewport observation
set. Capture objects are strict: missing, additional, or duplicate JSON fields,
duplicate session/journey/assertion/viewport identifiers, and a session order
other than Safari, installed standalone, then VoiceOver fail closed.

Session and artifact timestamps must be explicit UTC `Z` timestamps. Sessions
cannot overlap, run longer than six hours, finish more than seven days before
validation, or exceed five minutes of future clock skew. Every media timestamp
and every HAR entry timestamp must fall inside its owning session.

Each session supplies a relative, session-bound HAR path plus its byte count and
SHA-256. The validator rereads and hashes the HAR, rejects duplicate JSON keys,
parses every entry, derives the request count, and independently classifies
mutating API requests using the same authoring-only exceptions as the portable
probe. Operator-supplied request counts or mutation summaries are not accepted.
The retained record contains the derived facts.

Media records likewise use relative paths and explicitly name their owning
session, byte count, SHA-256, type, and format. The validator hashes and parses
PNG/JPEG images, WAV audio, and MOV/MP4/M4A streams locally, checks the declared
type and format against the parsed artifact, and requires a visual artifact for
every session. VoiceOver requires a parsed audio stream; an
`audio_included: true` claim is not evidence. Paths and digests cannot be reused
across sessions, so each session has distinct retained artifacts. Missing,
changed, malformed, path-escaping, reused, or mislabeled files fail closed.

The capture is bound to all of the following rather than merely labeled with
them:

- the current clean 40-character Git commit and its derived fixture release ID;
- the canonical current manifest SHA-256;
- the checked-in installation-profile, Python runtime, and managed-native
  browser-runtime digests;
- before/after fixture status schema version `2`, including matching source,
  web/controller release identity, profile and native bundle/payload identities;
- `wall_consumer_attached: false` and `wall_mutation_attempts: 0` before and
  after every session.

A passing retained record sets `external_evidence_satisfied: true` but always
keeps `release_gate_satisfied: false`; this tool never claims the overall
release passed. REL-01 may be closed only by a separate release decision that
also verifies the clean portable Chromium/Firefox/WebKit record.
The current no-wall fixture remains loopback-only. Safari Web Inspector and
`rvictl` do not forward a physical device to that origin; a separately reviewed,
secure device-local tunnel is therefore a prerequisite for a physical run and
must not be replaced by a public/LAN bind of the no-wall fixture.
