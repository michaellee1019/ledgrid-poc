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

# Stop, copy/build, flash the explicitly mapped receivers, start, and check HTTP.
deploy:
	{{captured}} --phase deploy.full -- python3 tools/deployment/deploy_entrypoint.py run --mode full

deploy-dirty: deploy

deploy-verbose: deploy

deploy-force-firmware: deploy

deploy-plan:
	python3 tools/deployment/deploy_entrypoint.py plan --mode full

# Application-only deployment uses the same persistent data and startup path.
deploy-python:
	{{captured}} --phase deploy.python -- python3 tools/deployment/deploy_entrypoint.py run --mode python

deploy-python-dirty: deploy-python

deploy-python-verbose: deploy-python

deploy-python-plan:
	python3 tools/deployment/deploy_entrypoint.py plan --mode python

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
test: test-composer-current test-firmware test-deployment

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
	if rg -n 'FastLED|fastled' firmware/esp32/src firmware/esp32/include firmware/esp32/platformio.ini; then exit 1; fi
	if rg -n 'stable/platform-espressif32' firmware/esp32/platformio.ini; then exit 1; fi

# Current deployment/data behavior and maintained shell syntax.
test-deployment:
	{{python_env}} pytest -q tests/unit/test_deploy_simple.py tests/unit/test_deploy_settings.py tests/unit/test_deploy_captured.py tests/unit/test_configure_spi.py tests/unit/test_server_startup.py
	for script in tools/deployment/*.sh scripts/start_systemd.sh; do bash -n "$script"; done

# Full local readiness gate.
preflight: test

# Fast command, ordering and host-output regressions.
test-scene-fast:
    {{python_env}} pytest -q tests/unit/test_host_full_controller.py tests/unit/test_controller_command_queue.py tests/unit/test_composer_simple_playback.py

# Current phone/desktop Composer and retained browser renderer contracts.
test-composer-current:
    {{python_env}} pytest -q tests/unit/test_receiver_status.py tests/unit/test_receiver_status_integrity.py tests/unit/test_browser_scene_contract.py tests/unit/test_spi_fec_envelope.py tests/unit/test_spi_crc.py tests/unit/test_composer_async_stop.py tests/unit/test_composer_simple_playback.py tests/unit/test_browser_composer_asset_publication.py tests/unit/test_composer_runtime_preview.py tests/unit/test_component_catalog.py tests/unit/test_media_composer.py tests/unit/test_composer_slice.py tests/unit/test_host_browser_rendering.py tests/unit/test_host_full_controller.py tests/unit/test_controller_command_queue.py tests/unit/test_retained_simulations.py

test-demo: test-composer-current test-deployment

deploy-precheck: test-demo

# Read-only controller rate samples; no scene identity/display-proof claim.
output-rate-observation seconds="15":
    {{python_env}} python tools/benchmarks/output_rate_sweep.py --seconds "{{seconds}}"

# Diagnose the deploy host (API + logs). Outputs to diagnostics/remote_diagnostics.out.
diagnose-remote:
	mkdir -p diagnostics
	OUT_FILE=diagnostics/remote_diagnostics.out tools/diagnostics/remote_diagnostics.sh

# Diagnose the deploy host and restart the web server if needed.
diagnose-remote-restart:
	mkdir -p diagnostics
	OUT_FILE=diagnostics/remote_diagnostics.out RESTART_WEB=1 tools/diagnostics/remote_diagnostics.sh

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
