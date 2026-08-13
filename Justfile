set shell := ["bash", "-euo", "pipefail", "-c"]
set quiet := true

web_venv := ".venv-web"
python_env := "uv run --frozen --group test --group calibration"
captured := "python3 tools/deployment/run_captured.py --log-dir .deploy-logs"

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

# Refresh the ignored, content-addressed dashboard preview catalog locally.
generate-previews:
	{{python_env}} python tools/generate_animation_previews.py --tracked-only

# Run the Mac-only software dashboard with 30 FPS contact-strip loops.
start-mac:
	{{python_env}} python tools/generate_animation_previews.py --tracked-only \
		--output run_state/mac_animation_previews \
		--public-prefix /preview-assets/generated --capture-fps 30 --capture-duration 4
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
	if rg -n 'FastLED|fastled' firmware/esp32/src firmware/esp32/include firmware/esp32/platformio.ini; then exit 1; fi

# Run deployment behavior tests and validate every maintained shell script.
test-deployment:
	{{python_env}} pytest -q \
		tests/unit/test_deploy_*.py \
		tests/unit/test_app_releases.py \
		tests/unit/test_firmware_reconciliation.py \
		tests/unit/test_gate_policy.py \
		tests/unit/test_preserve_deploy_settings.py
	for script in tools/deployment/*.sh; do bash -n "$script"; done

# Full local readiness gate.
preflight: test

# Required gate before a full deployment.
deploy-precheck: test

# Run the receiver-side timed hardware gates against one controller.
receiver-acceptance device="0" duration="60" min_fps="180":
	{{python_env}} python tools/benchmarks/receiver_acceptance.py --device {{device}} --duration {{duration}} --min-displayed-fps {{min_fps}} --animation rainbow

# Exercise every live plugin while checking host and receiver integrity counters.
live-animation-sweep seconds="2":
	{{python_env}} python tools/benchmarks/live_animation_sweep.py --seconds {{seconds}}

# Step physical output rates; visually note flashes and retain the highest clean rate.
output-rate-sweep seconds="15" rates="120,140,160,180,200":
	{{python_env}} python tools/benchmarks/output_rate_sweep.py --seconds {{seconds}} --rates {{rates}}

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
