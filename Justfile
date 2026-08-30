set shell := ["bash", "-euo", "pipefail", "-c"]
set quiet := true

web_venv := ".venv-web"
python_env := "uv run --frozen --group test --group calibration"
captured := "python3 tools/deployment/run_captured.py --log-dir .deploy-logs"
ai_ssh_key := ".gpt-key"

# Create an ignored, repository-local identity for automated wall operations.
generate-ai-ssh-key key_path=ai_ssh_key:
	#!/usr/bin/env bash
	set -euo pipefail
	umask 077
	key="{{key_path}}"
	if [ -e "$key" ] || [ -e "$key.pub" ]; then
	  echo "Refusing to overwrite existing AI SSH key: $key or $key.pub" >&2
	  exit 1
	fi
	if [ ! -d "$(dirname -- "$key")" ]; then
	  echo "AI SSH key parent directory does not exist: $(dirname -- "$key")" >&2
	  exit 1
	fi
	ssh-keygen -q -t ed25519 -N "" -C "codex-ledgrid-poc" -f "$key"
	chmod 600 "$key"
	chmod 644 "$key.pub"
	target="${PI_HOST:-ledgridwall@ledgridwall.local}"
	echo "Generated dedicated AI SSH key: $key"
	echo "Authorize it once using your normal SSH handling:"
	printf '  ssh-copy-id -i %q %q\n' "$key.pub" "$target"
	echo "Then deploy without the SSH agent using:"
	printf '  SSH_KEY=%q just deploy\n' "$key"

# Run the complete local gate before a provision/firmware deployment.
# Set TEST=false for an explicitly requested fast deployment.
deploy:
	{{captured}} --phase deploy.full -- python3 tools/deployment/deploy_entrypoint.py run --mode full --policy clean

# Explicit development exception: deploy tracked edits plus safe untracked source.
deploy-dirty:
	{{captured}} --phase deploy.full -- python3 tools/deployment/deploy_entrypoint.py run --mode full --policy dirty

# Stream the clean deployment while retaining the same captured log and policy.
deploy-verbose:
	{{captured}} --verbose --phase deploy.full -- python3 tools/deployment/deploy_entrypoint.py run --mode full --policy clean --verbose

# Reconcile every attached receiver even when its recorded firmware identity matches.
deploy-force-firmware:
	{{captured}} --verbose --phase deploy.full -- python3 tools/deployment/deploy_entrypoint.py run --mode full --policy clean --verbose --force-firmware

# Read-only source accounting plus the authoritative coordinator sequence.
deploy-plan:
	python3 tools/deployment/deploy_entrypoint.py plan --mode full --policy plan

# Sync tracked application/plugin files without provisioning or flashing firmware.
deploy-python:
	{{captured}} --phase deploy.python -- python3 tools/deployment/deploy_entrypoint.py run --mode python --policy clean

deploy-python-dirty:
	{{captured}} --phase deploy.python -- python3 tools/deployment/deploy_entrypoint.py run --mode python --policy dirty

deploy-python-verbose:
	{{captured}} --verbose --phase deploy.python -- python3 tools/deployment/deploy_entrypoint.py run --mode python --policy clean --verbose

deploy-python-plan:
	python3 tools/deployment/deploy_entrypoint.py plan --mode python --policy plan

# Read-only, package-scoped source and action accounting for one native background.
native-plan plugin_id:
	{{python_env}} python tools/deployment/native_background_entrypoint.py \
		plan "{{plugin_id}}"

# Build, preview, validate, and retain one repository-owned native bundle locally.
native-build plugin_id:
	{{captured}} --phase receiver_background.build -- \
		{{python_env}} --group firmware python tools/deployment/native_background_entrypoint.py \
		build "{{plugin_id}}"

# Publish a managed local bundle (or first build a plugin ID) to shared Pi state.
# Requires an immutable current app release for the version-matched Pi helper.
# This never installs on receivers, changes display ownership, or restarts services.
native-publish bundle_or_plugin:
	{{captured}} --phase receiver_background.publish -- \
		{{python_env}} --group firmware python tools/deployment/native_background_entrypoint.py \
		publish "{{bundle_or_plugin}}"

# Install a published managed bundle on the exact configured receiver roster.
native-install plugin_or_digest:
	{{captured}} --phase receiver_background.install -- \
		{{python_env}} python tools/deployment/native_background_entrypoint.py \
		install "{{plugin_or_digest}}"

# Retired compatibility entrypoint: fails before target access and points to
# Composer Check + guarded activation.
native-start plugin_or_digest fallback="aurora_curtains":
	{{captured}} --phase receiver_background.activate -- \
		{{python_env}} python tools/deployment/native_background_entrypoint.py \
		start "{{plugin_or_digest}}" --fallback "{{fallback}}"

# Retired compatibility entrypoint: fails before build, publish, install, or
# target access and points to Composer Check + guarded activation.
native-run plugin_id fallback="aurora_curtains":
	{{captured}} --phase receiver_background.run -- \
		{{python_env}} --group firmware python tools/deployment/native_background_entrypoint.py \
		run "{{plugin_id}}" --fallback "{{fallback}}"

# Explicit recovery paths for the retained pre-cutover shell leaves.
deploy-legacy:
	{{captured}} --phase deploy.legacy.full -- python3 tools/deployment/deploy_entrypoint.py legacy --mode full --policy clean

deploy-legacy-dirty:
	{{captured}} --phase deploy.legacy.full -- python3 tools/deployment/deploy_entrypoint.py legacy --mode full --policy dirty

deploy-python-legacy:
	{{captured}} --phase deploy.legacy.python -- python3 tools/deployment/deploy_entrypoint.py legacy --mode python --policy clean

deploy-shadow:
	python3 tools/deployment/deploy_entrypoint.py shadow --mode full --policy plan

deploy-shadow-stage:
	python3 tools/deployment/deploy_entrypoint.py shadow --mode full --policy plan --target-stage

releases:
	python3 tools/deployment/deploy_entrypoint.py releases

rollback release_id:
	{{captured}} --phase deploy.rollback -- python3 tools/deployment/deploy_entrypoint.py \
		rollback "{{release_id}}"

# Compatibility name for the fast Python deployment.
deploy-no-firmware: deploy-python

# Refresh checked-in plant masks and fetch new Pi-saved animation presets.
fetch-wall-data:
	./tools/deployment/fetch_wall_data.sh

# Compatibility alias; this now refreshes masks and presets together.
fetch-presets: fetch-wall-data

# Rebuild the deterministic browser runtimes and their digest-pinned manifest.
browser-composer-assets:
	{{python_env}} python tools/build_browser_composer_assets.py

# Install the exact browser runner and engines committed beside the qualification.
browser-qualification-setup:
	cd tools/browser_qualification && npm ci
	cd tools/browser_qualification && npm run install-browsers

# Start an isolated no-wall fixture, use an OS-assigned loopback port, and retain
# a manifest-indexed local evidence run under ignored run_state/.
browser-qualification:
	{{python_env}} python -m tools.browser_qualification.run

# Run the Mac-only software dashboard with no controller process or LED hardware.
start-mac:
	{{python_env}} python scripts/start_mac_dashboard.py \
		--host "${HOST:-127.0.0.1}" --port "${PORT:-5000}"

# Create/refresh the lightweight virtualenv for serving the web controller locally.
setup-web:
	uv venv --allow-existing {{web_venv}}
	uv pip sync --python {{web_venv}}/bin/python requirements-pi.lock
	{{web_venv}}/bin/python tools/deployment/runtime_env.py smoke --root .

# Reproduce the complete local development environment from uv.lock.
setup-local:
	uv sync --frozen --all-groups

# Intentionally update all reproducible Python inputs after dependency review.
lock-dependencies:
	uv lock --python 3.10
	uv export --locked --no-default-groups --no-dev --no-emit-project \
		--no-annotate --no-header --output-file requirements-pi.lock
	uv export --locked --only-group firmware --no-emit-project \
		--no-annotate --no-header --output-file requirements-platformio.lock

# Prepare the deploy target for flashing ESP32 firmware and running the app.
setup:
	bash tools/deployment/setup.sh

# Run every local regression gate: Python, rendering performance, and firmware.
test: test-unit test-rendering test-firmware test-deployment

# Discover unit tests in both shared code and self-contained animation plugins.
test-unit:
	{{python_env}} pytest -q tests animation

# Verify the host rendering pipeline and its performance budget.
test-rendering:
	{{python_env}} pytest -q animation/core/tests/test_frame_pipeline.py tests/unit/test_spi_crc.py
	{{python_env}} python tools/benchmarks/animation_render.py --frames 100 --stress --scenes --check --max-p95-ms 4.0 --json

# Run native firmware tests, build the production target, and enforce dependencies.
test-firmware:
	uv run --frozen --group firmware pio test -d firmware/esp32 -e native
	uv run --frozen --group firmware pio run -d firmware/esp32 -e esp32-s3-devkitc-1
	uv run --frozen --group firmware pio run -d firmware/esp32 -e esp32-s3-devkitc-1-local-canary
	uv run --frozen --group firmware pio run -d firmware/esp32 -e esp32-s3-devkitc-1-native-canary
	if rg -n 'FastLED|fastled' firmware/esp32/src firmware/esp32/include firmware/esp32/platformio.ini; then exit 1; fi
	if rg -n 'stable/platform-espressif32' firmware/esp32/platformio.ini; then exit 1; fi

# Run deployment behavior tests and validate every maintained shell script.
test-deployment:
	{{python_env}} pytest -q \
		tests/unit/test_deploy_*.py \
		tests/unit/test_app_releases.py \
		tests/unit/test_configure_spi.py \
		tests/unit/test_firmware_reconciliation.py \
		tests/unit/test_receiver_firmware_inventory.py \
		tests/unit/test_gate_policy.py \
		tests/unit/test_preserve_deploy_settings.py \
		tests/unit/test_receiver_hybrid_config.py
	for script in tools/deployment/*.sh; do bash -n "$script"; done

# Full local readiness gate.
preflight: test

# Required gate before a full deployment.
deploy-precheck: test

# Run the receiver-side timed hardware gates against one controller.
receiver-acceptance expected_scene_digest device="0" duration="60" min_fps="150" target_fps="160":
	expected_scene_digest="{{expected_scene_digest}}"; device="{{device}}"; duration="{{duration}}"; min_fps="{{min_fps}}"; target_fps="{{target_fps}}"; \
	device="${device#device=}"; duration="${duration#duration=}"; \
	min_fps="${min_fps#min_fps=}"; target_fps="${target_fps#target_fps=}"; \
	{{python_env}} python tools/benchmarks/receiver_acceptance.py --expected-scene-digest "$expected_scene_digest" --device "$device" --duration "$duration" --min-displayed-fps "$min_fps" --target-fps "$target_fps" --animation rainbow

# Run the dense streamed-frame gate against the complete installed topology.
receiver-streamed-wall-acceptance expected_scene_digest duration="60" min_fps="150" target_fps="160":
	expected_scene_digest="{{expected_scene_digest}}"; duration="{{duration}}"; min_fps="{{min_fps}}"; target_fps="{{target_fps}}"; \
	duration="${duration#duration=}"; min_fps="${min_fps#min_fps=}"; \
	target_fps="${target_fps#target_fps=}"; \
	{{python_env}} python tools/benchmarks/receiver_acceptance.py \
		--expected-scene-digest "$expected_scene_digest" \
		--device 0 --device 1 --device 2 --device 3 --device 4 \
		--duration "$duration" --min-displayed-fps "$min_fps" \
		--target-fps "$target_fps" --animation rainbow

# Observe the exact guarded activation and immutable release for the complete
# WALL-02 soak. This never activates, restores, restarts, or otherwise mutates.
guarded-wall-soak activation_id expected_scene_digest expected_release_id expected_basis_digest duration="1800" sample_interval="5" target="ledgridwall.local" min_fps="150" target_fps="150":
	activation_id="{{activation_id}}"; expected_scene_digest="{{expected_scene_digest}}"; expected_release_id="{{expected_release_id}}"; expected_basis_digest="{{expected_basis_digest}}"; \
	duration="{{duration}}"; sample_interval="{{sample_interval}}"; target="{{target}}"; min_fps="{{min_fps}}"; target_fps="{{target_fps}}"; \
	duration="${duration#duration=}"; sample_interval="${sample_interval#sample_interval=}"; target="${target#target=}"; \
	min_fps="${min_fps#min_fps=}"; target_fps="${target_fps#target_fps=}"; \
	{{python_env}} python tools/benchmarks/guarded_wall_soak.py \
		"$activation_id" --expected-scene-digest "$expected_scene_digest" \
		--expected-release-id "$expected_release_id" \
		--expected-basis-digest "$expected_basis_digest" --target "$target" \
		--duration "$duration" --sample-interval "$sample_interval" \
		--min-displayed-fps "$min_fps" --target-fps "$target_fps"

# Collect H2 binding/topology/skew/drift supporting evidence. Transaction
# injection, restart/lease repair, streamed capacity, and the Python sweep remain
# explicit companion subgates. The default is a real 30-minute evidence run.
receiver-native-h2-evidence expected_scene_digest selector="aurora_curtains_native" duration="1800" sample_interval="5" target="ledgridwall.local":
	expected_scene_digest="{{expected_scene_digest}}"; selector="{{selector}}"; duration="{{duration}}"; sample_interval="{{sample_interval}}"; target="{{target}}"; \
	selector="${selector#selector=}"; duration="${duration#duration=}"; \
	sample_interval="${sample_interval#sample_interval=}"; target="${target#target=}"; \
	{{python_env}} python tools/benchmarks/receiver_native_physical_acceptance.py \
		"$selector" --expected-scene-digest "$expected_scene_digest" --gate H2 --target "$target" --duration "$duration" \
		--sample-interval "$sample_interval"

# H4 supporting soak at authored defaults; this intentionally defaults to 1800 s.
receiver-native-h4-default-soak expected_scene_digest selector="aurora_curtains_native" duration="1800" sample_interval="5" target="ledgridwall.local":
	expected_scene_digest="{{expected_scene_digest}}"; selector="{{selector}}"; duration="{{duration}}"; sample_interval="{{sample_interval}}"; target="{{target}}"; \
	selector="${selector#selector=}"; duration="${duration#duration=}"; \
	sample_interval="${sample_interval#sample_interval=}"; target="${target#target=}"; \
	{{python_env}} python tools/benchmarks/receiver_native_physical_acceptance.py \
		"$selector" --expected-scene-digest "$expected_scene_digest" --gate H4-default --target "$target" --duration "$duration" \
		--sample-interval "$sample_interval"

# Separate H4 maximum-work supporting soak; this also defaults to 1800 s.
receiver-native-h4-maximum-soak expected_scene_digest selector="aurora_curtains_native" duration="1800" sample_interval="5" target="ledgridwall.local":
	expected_scene_digest="{{expected_scene_digest}}"; selector="{{selector}}"; duration="{{duration}}"; sample_interval="{{sample_interval}}"; target="{{target}}"; \
	selector="${selector#selector=}"; duration="${duration#duration=}"; \
	sample_interval="${sample_interval#sample_interval=}"; target="${target#target=}"; \
	{{python_env}} python tools/benchmarks/receiver_native_physical_acceptance.py \
		"$selector" --expected-scene-digest "$expected_scene_digest" --gate H4-maximum --target "$target" --duration "$duration" \
		--sample-interval "$sample_interval"

# Temporary installed-wall exception: require full receiver telemetry on SPI0,
# prove outbound host traffic on write-only SPI1, and require visual inspection.
receiver-streamed-wall-acceptance-degraded-spi1 expected_scene_digest duration="60" min_fps="150" target_fps="160":
	expected_scene_digest="{{expected_scene_digest}}"; duration="{{duration}}"; min_fps="{{min_fps}}"; target_fps="{{target_fps}}"; \
	duration="${duration#duration=}"; min_fps="${min_fps#min_fps=}"; \
	target_fps="${target_fps#target_fps=}"; \
	{{python_env}} python tools/benchmarks/receiver_acceptance.py \
		--expected-scene-digest "$expected_scene_digest" \
		--device 0 --device 1 --device 2 --device 3 \
		--allow-degraded-spi1-return-path \
		--duration "$duration" --min-displayed-fps "$min_fps" \
		--target-fps "$target_fps" --animation rainbow

# Verify deployed Phase 3A status/ownership/identity without changing display state.
receiver-phase3a-status:
	{{python_env}} python tools/benchmarks/receiver_acceptance.py --phase3a-status-only

# Temporary installed-wall status proof: strict on SPI0, exact no-return state on SPI1.
receiver-phase3a-status-degraded-spi1:
	{{python_env}} python tools/benchmarks/receiver_acceptance.py \
		--phase3a-status-only --allow-degraded-spi1-return-path

# Require local/background context capability on the deliberately flashed canary.
receiver-phase3a-canary-status device:
	{{python_env}} python tools/benchmarks/receiver_acceptance.py --phase3a-status-only --local-canary-device {{device}}

# Run with ledgrid.service already stopped; this recipe never manages services or flashes.
receiver-phase3a-physical-canary bus device logical_id disconnect_seconds="60":
	{{python_env}} python tools/benchmarks/phase3a_single_receiver_canary.py \
		--bus {{bus}} --device {{device}} --logical-id {{logical_id}} \
		--disconnect-seconds {{disconnect_seconds}}

# Run with ledgrid.service already stopped and one readable receiver deliberately
# flashed with the named local-canary image. This never manages services or flashes.
receiver-phase3b-physical-canary bus device logical_id disconnect_seconds="5":
	{{python_env}} python tools/benchmarks/phase3b_single_receiver_canary.py \
		--bus {{bus}} --device {{device}} --logical-id {{logical_id}} \
		--disconnect-seconds {{disconnect_seconds}}

# Explicitly incomplete four-wall product showcase for the documented SPI1 return
# fault. The operator must supply an exact prior complete frame/state and a fresh
# nonce response; this never installs, uploads, flashes, or claims release acceptance.
receiver-phase3b-degraded-showcase desired_state restore_frame challenge response duration="15":
	{{python_env}} python tools/benchmarks/phase3b_degraded_showcase.py \
		--desired-display-state {{desired_state}} --restore-frame-npy {{restore_frame}} \
		--confirmation-challenge {{challenge}} --confirmation-response {{response}} \
		--duration {{duration}}

# Retired compatibility recipe: fails before network/wall changes and points to
# per-scene guarded activation plus receipt-bound observation.
live-animation-sweep seconds="2":
	seconds="{{seconds}}"; seconds="${seconds#seconds=}"; \
	{{python_env}} python tools/benchmarks/live_animation_sweep.py --seconds "$seconds"

# Retired degraded compatibility recipe; also fails before network/wall changes.
live-animation-sweep-degraded-spi1 seconds="2":
	seconds="{{seconds}}"; seconds="${seconds#seconds=}"; \
	{{python_env}} python tools/benchmarks/live_animation_sweep.py \
		--allow-degraded-spi1-return-path --seconds "$seconds"

# Observe one exact pre-activated scene/rate; never changes target FPS or scene.
output-rate-observation expected_scene_digest seconds="15" rate="160":
	expected_scene_digest="{{expected_scene_digest}}"; seconds="{{seconds}}"; rate="{{rate}}"; \
	seconds="${seconds#seconds=}"; rate="${rate#rate=}"; \
	{{python_env}} python tools/benchmarks/output_rate_sweep.py --expected-scene-digest "$expected_scene_digest" --seconds "$seconds" --rate "$rate"

# Diagnose the deploy host (API + logs). Outputs to diagnostics/remote_diagnostics.out.
diagnose-remote:
	mkdir -p diagnostics
	OUT_FILE=diagnostics/remote_diagnostics.out tools/diagnostics/remote_diagnostics.sh

# Diagnose the deploy host and restart the web server if needed.
diagnose-remote-restart:
	mkdir -p diagnostics
	OUT_FILE=diagnostics/remote_diagnostics.out KILL_PORT=1 RESTART_WEB=1 tools/diagnostics/remote_diagnostics.sh

# Run the web controller locally (defaults to HOST=127.0.0.1, PORT=5000).
start:
	if [ ! -x {{web_venv}}/bin/python ]; then \
		echo "web controller venv missing; run 'just setup-web' first" >&2; \
		exit 1; \
	fi; \
	HOST="${HOST:-127.0.0.1}"; \
	PORT="${PORT:-5000}"; \
	ARGS=(--mode web --host "$HOST" --port "$PORT"); \
	if [ -n "${DEBUG+x}" ] && [ "$DEBUG" != "0" ]; then ARGS+=("--debug"); fi; \
	exec {{web_venv}}/bin/python scripts/start_server.py "${ARGS[@]}"
