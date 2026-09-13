# Current Composer validation

Run `just test-composer-current` to exercise the supported local Composer journey. It is a maintenance gate for current behavior; `just test-demo` and `just deploy-precheck` keep their existing roles. This command neither contacts the wall nor adds a deployment prerequisite.

The command first creates the ignored, repository-local `native_aurora` managed bundle fixture with the pinned firmware environment (`uv run --frozen --group firmware python tools/deployment/native_background_entrypoint.py build native_aurora`). The fixture is deliberately prepared by the gate so a clean supported checkout does not rely on a pre-existing `run_state/native_background_builds` cache.

After the existing demo gate, the focused checks cover Composer selection, editable controls, local Preview, canonical activation and its stale-identity rejection, direct-live Stop followed by a newer edit, reconnect recovery, named Look save/reopen, installation-profile stale-update atomicity, and deployment release identity. Catalog activation coverage derives its members from the live component descriptors; it does not assert a fixed renderer count.

Historical suites that require retired Check-token activation or retired source-shape strings are intentionally outside this command. They remain available for compatibility-debt work and do not change the current product contract.
