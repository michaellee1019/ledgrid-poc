# Incremental Local Build and Deployment Improvements

## Summary

Keep `just` as the operator interface, `uv` and PlatformIO as the real build
tools, systemd as the runtime supervisor, and shell for stable platform-specific
leaf operations. `just` is explicitly a command runner, which is the right role.
See the [just overview](https://just.systems/man/en/) and
[quiet-recipe documentation](https://just.systems/man/en/quiet-recipes.html).

The cold reviews identified genuine over-engineering in the earlier proposal.
Remove or defer:

- The generic `ArtifactProvider`, `ArtifactDescriptor`, `ActionDescriptor`, and
  `GroupedActivation` framework.
- Fake receiver-package transactions before the native backend passes physical
  acceptance.
- A wholesale shell-to-Python rewrite.
- Gate caching before timing data demonstrates value.
- Migrating releases, provisioning, firmware, history, caching, and rollback
  together.
- Treating generated previews as a separate deployment domain; they belong to
  the host release.

Deliver six independently useful increments. Each must ship and remain usable
before the next begins.

## Increment 1: Quiet and Explicit Deployment UX

Make deployment quiet without changing remote behavior or file selection.

- Enable global `just` quiet mode and remove `bash -x`.
- Wrap deployment commands with a small local runner that:
  - Captures stdout and stderr to an ignored log.
  - Shows one erasable TTY status line while running.
  - Produces zero stdout after success.
  - On failure, prints the failed command, concise cause, relevant log tail, and
    log path.
  - Streams normally with `DEBUG=1` or an explicit verbose command.
- Make source policy explicit:
  - `just deploy` refuses any dirty or untracked working-tree state.
  - `just deploy-dirty` uses today's tracked-plus-safe-untracked manifest
    behavior.
  - `just deploy-plan` explains selected files, excluded files, full versus
    Python-only behavior, test policy, and whether firmware or provisioning
    would run.
  - Record the base commit, diff digest, and included untracked paths in the log
    header.
- Preserve current deployment helpers, ordering, protected runtime paths,
  `PI_HOST`, `DEPLOY_DIR`, and compatibility flags during this increment.

### Acceptance

- Full and Python-only deployments invoke the same commands in the same order
  as today.
- Runtime presets, `run_state`, logs, environments, and firmware hashes retain
  their existing protections.
- Successful execution leaves no terminal output.
- Failure output is sufficient to diagnose the phase without rerunning
  verbosely.
- `deploy-plan` accounts for every included untracked file.

### Stop boundary

Do not change remote layout, dependency installation, health semantics,
provisioning, firmware behavior, or systemd configuration.

## Increment 2: Reproducible Dependencies and Toolchains

Make build inputs reproducible without rewriting deployment.

- Add `pyproject.toml` and `uv.lock` with runtime, test/development, and
  calibration groups. Use frozen local sync rather than repeated inline
  `uv run --with` lists. `uv` supports exact synchronization from the lockfile;
  see [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/).
- Pin PlatformIO Core through the development lock.
- Replace the mutable pioarduino `stable` URL with the currently validated
  immutable `55.03.39` release. PlatformIO continues to own firmware package
  resolution and builds; see
  [PlatformIO dependency management](https://docs.platformio.org/en/latest/librarymanager/dependencies.html).
- Export a fully pinned Pi runtime requirements lock from `uv.lock`.
- Build a fresh Pi virtual environment keyed by the lock digest, install into
  that empty environment, smoke-test imports, and reuse it while the digest and
  Pi Python identity remain unchanged.
- Do not require `uv` on the Pi unless it proves simpler than installing the
  exported lock into a new venv.

### Acceptance

- A clean checkout completes frozen dependency setup and every non-hardware
  gate.
- Local Python 3.10 and Pi Python 3.13 resolve the intended dependency sets.
- A fresh Pi environment imports the runtime modules and starts the web and
  controller entrypoints.
- Changing any dependency or pinned toolchain changes its recorded digest.
- Repeating setup with unchanged locks performs no installation.

### Stop boundary

Keep existing shell deployment orchestration and mutable target layout.

## Increment 3: Thin Python Coordinator and Receipts

Move only policy and sequencing into a testable Python coordinator.

- Retain `rsync`, SSH and SPI helpers, firmware flashing, and systemd operations
  as leaf commands.
- Introduce only:
  - `DeployContext`: source policy, target, mode, flags, paths, and redaction
    rules.
  - `Step`: stable namespaced ID, mutating flag, and callable operation.
  - `StepResult`: timing, result, log reference, and opaque artifact metadata.
  - `DeployReceipt`: attempt identity, source identity, completed steps, health
    result, and outcome.
  - An injectable subprocess and SSH runner using argument arrays and no
    `shell=True`.
- Use procedural step construction rather than an action DSL. Initial IDs
  include:
  - `source.validate`
  - `tests.run`
  - `app.stage`
  - `host.provision`
  - `receiver.firmware_build`
  - `receiver.firmware_flash`
  - `host.restart`
  - `health.readiness`
- Persist append-only, atomic JSON receipts locally and remotely containing
  deployment ID, timestamps, target, requested mode, Git revision or dirty
  digest, dependency/toolchain/manifest digests, completed steps, timings,
  artifacts, outcome, and health result.
- Artifact receipt entries use the deliberately small shape
  `{kind, id, digest, version, target_id?}`.
- Never serialize environment values. Redact private-key arguments, sensitive
  paths, and configured secret names.
- Record failed and interrupted attempts as well as successes.
- Fix health checking here:
  - Require controller status newer than restart.
  - Check systemd state, expected geometry and device topology, and stable fresh
    samples.
  - Record success and `deploy_timestamp` only after readiness passes.

### Acceptance

- Failure-injection tests cover every phase, fail-fast ordering, interruption,
  state preservation, stale status, dirty-source policy, and diagnostics.
- Receipts remain valid after success, failure, and interruption.
- Private or trusted key material cannot appear in commands, receipts, or logs.
- Existing `just deploy`, `deploy-dirty`, and Python-only commands remain
  compatible.
- Legacy shell entrypoints remain available until coordinator parity is proven
  on the wall.

### Stop boundary

No generic provider system, deployment database, UI, test cache, release
symlink, or receiver-package support.

## Increment 4: App-Only Staged Releases and Rollback

Pilot immutable releases only for the Python and web deployment lane.

- Put runtime application files and generated previews in
  `releases/<content-digest>`.
- Keep venvs, presets, `run_state`, logs, calibration data, firmware artifacts,
  and future receiver-package libraries outside releases.
- Point systemd at an atomic `current` symlink.
- Validate imports and static release structure before activation.
- On activation:
  1. Preserve settings.
  2. Switch `current`.
  3. Restart systemd.
  4. Restore settings.
  5. Require fresh desired-release health.
- If health fails, immediately restore the previous symlink, restart it, verify
  the previous health, and record both the candidate failure and successful
  restoration.
- Add:
  - `just releases`
  - `just rollback [release-id]`
- Rollback changes only the host application release. It never provisions,
  reboots, builds, flashes firmware, or mutates receiver state.
- Do not add automatic release garbage collection yet.

### Acceptance

- Two successive releases contain no stale code.
- Target-owned state survives both releases.
- An injected unhealthy candidate automatically returns to the previous
  healthy API.
- Explicit rollback works without building, syncing, provisioning, or flashing.
- A receiver-package-library fixture placed under shared state survives
  application deployment and rollback.

### Stop boundary

- Full provisioning and firmware deployment continues through the existing
  lane.
- Do not claim whole-system atomic rollback.

## Increment 5: Measure Before Adding Gate Caching

Use receipts to determine whether caching is worth maintaining.

- Collect timings for unit, rendering, deployment, preview, firmware-test, and
  firmware-build steps across normal iteration for at least twenty attempts.
- Add content-hash caching only for a deterministic local gate that:
  - Regularly consumes at least five seconds.
  - Has a complete, reviewable input set.
  - Has no hardware or external-state dependency.
  - Produces a meaningful improvement in the observed workflow.
- A cache key must include selected source contents, dirty manifest, lockfile,
  interpreter and platform identity, toolchain identity, command arguments, and
  an explicit gate-version constant.
- Never cache health, provisioning discovery, firmware flashing, receiver
  readiness, or physical acceptance.
- `just test` always forces the complete gate regardless of cached deploy
  results.

### Acceptance

- Deliberate changes to every declared input invalidate the corresponding
  entry.
- Corrupt or missing cache state safely reruns the gate.
- Receipts distinguish executed, cached, and explicitly skipped work.

### Stop/go criterion

If no eligible gate meets the measured threshold, ship no build cache.
Content-addressed previews and dependency or firmware hashes remain sufficient.

## Increment 6: Robust Fully Automatic Provisioning and Firmware

After receipts and app rollback are proven, make ordinary `just deploy` fully
automatic as requested.

- The coordinator compares desired and observed host state for packages,
  permissions, SPI boot settings, systemd definitions, Python environment,
  application release, and receiver firmware.
- Apply only necessary changes.
- If provisioning changes require a reboot:
  - Persist the transaction phase.
  - Reboot automatically.
  - Wait for SSH.
  - Rediscover target state.
  - Resume idempotently.
- Compute firmware identity from all source, configuration, provisioning
  identity, logical-device identity, pinned platform and toolchain, partitions,
  and build flags.
- Treat firmware as four explicit logical-device images addressed through
  stable provisioned device paths.
- Build images before downtime and flash only changed images.
- Any missing, duplicate, unexpected, or failed receiver makes flashing fail
  nonzero.
- On partial flash failure:
  - Do not activate the candidate host release.
  - Leave the service stopped.
  - Retain prior and candidate images, receipts, and device logs.
  - Require explicit firmware recovery; do not claim automatic whole-system
    rollback.
- After firmware success, activate the staged host release and use its automatic
  app-only health restoration if application readiness fails.

### Acceptance

- An unchanged deploy performs no provisioning, reboot, dependency work,
  release activation, or flash.
- Package, SPI, unit, dependency, application, and firmware changes each produce
  the expected distinct plan.
- Automatic reboot resumes exactly once and cannot loop indefinitely.
- Partial firmware failure is clearly reported per device and never records
  success.
- A successful full deployment updates the receipt and existing API deployment
  timestamp only after fresh readiness.

## Minimum Future Native-Animation Seam

The `native-animations` branch remains speculative; port none of it in these
increments.

Preserve only these extension points now:

- Stable namespaced step IDs.
- Opaque artifact receipt metadata.
- An explicit distinction between build, install or prepare, and activate
  steps.
- Shared state outside application releases.
- A coordinator capable of running a domain-owned ordered step list.

Reserve the future UX without adding dead recipes:

- `just native-run <source>`: build, host-preview, validate, sign, install,
  stage all four receivers, and start.
- `just native-build <source>`
- `just native-install <package>`
- `just native-start <package-id>`

Future native source iteration:

- Operates on the selected animation's working-tree digest without requiring
  the entire repository to be clean.
- Does not rebuild the host release, recreate the Python environment, restart
  systemd, reboot, or flash loader firmware.
- Keeps the private signing key workstation-only.
- Places signed packages in a Pi-authoritative shared library.
- Owns its protocol-specific `probe -> stage -> verify -> activate/compensate`
  transaction.
- Must never leave a mixed wall; if activation becomes partial, that domain
  restores prior playback while retaining evidence.

Do not implement a generic grouped-activation abstraction now. Add the
receiver-specific transaction only after:

- The documented SPI1 wiring fault is repaired.
- All four receivers return fresh identity and required capability telemetry.
- One production-signed package completes physical install, start, parameter
  update, streamed-frame switchback, and failure-recovery acceptance.

Only generalize a provider or transaction interface after two accepted
implementations demonstrate genuinely shared lifecycle semantics.

## Assumptions

- Everything remains local: one Mac, one Pi, four receivers, no hosted CI,
  artifact registry, deployment service, or telemetry backend.
- Plain `just deploy` eventually provisions and reboots automatically.
- Plain deployment requires a clean tree; `just deploy-dirty` is explicit.
- Successful deployment output is ephemeral only.
- Application health failure automatically restores the previous host release.
- Firmware failure remains in place with explicit recovery.
- Future receiver-package activation preserves the no-mixed-wall guarantee
  inside its own domain.
