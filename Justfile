set shell := ["bash", "-euxo", "pipefail", "-c"]

web_venv := ".venv-web"
python_env := "uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' --with opencv-python-headless"

# Run the complete local gate before a provision/firmware deployment.
# Set TEST=false for an explicitly requested fast deployment.
deploy:
	case "${TEST:-true}" in false|FALSE|0|no|NO) echo "[INFO] Skipping tests (TEST=$TEST)" ;; *) just deploy-precheck ;; esac
	./tools/deployment/deploy.sh

# Sync tracked application/plugin files without provisioning or flashing firmware.
deploy-python:
	case "${TEST:-true}" in false|FALSE|0|no|NO) echo "[INFO] Skipping tests (TEST=$TEST)" ;; *) just test-unit test-rendering test-deployment ;; esac
	./tools/deployment/deploy_python.sh

# Compatibility name for the fast Python deployment.
deploy-no-firmware: deploy-python

# Fetch new Pi-saved presets without overwriting local curated files.
fetch-presets:
	./tools/deployment/fetch_presets.sh

# Refresh the ignored, content-addressed dashboard preview catalog locally.
generate-previews:
	{{python_env}} python tools/generate_animation_previews.py --tracked-only

# Create/refresh the lightweight virtualenv for serving the web controller locally.
setup-web:
	uv venv --allow-existing {{web_venv}}
	uv pip install --python {{web_venv}}/bin/python flask "werkzeug>=2.0.0"

# Prepare the deploy target for flashing ESP32 firmware and running the app.
setup:
	bash tools/deployment/setup.sh

# Run every local regression gate: Python, rendering performance, and firmware.
test: test-unit test-rendering test-firmware test-deployment

# Discover unit tests in both shared code and self-contained animation plugins.
test-unit:
	{{python_env}} --with pytest pytest -q tests animation

# Verify the host rendering pipeline and its performance budget.
test-rendering:
	{{python_env}} --with pytest pytest -q animation/core/tests/test_frame_pipeline.py tests/unit/test_spi_crc.py
	{{python_env}} python tools/benchmarks/animation_render.py --frames 100 --stress --check --max-p95-ms 4.0 --json

# Run native firmware tests, build the production target, and enforce dependencies.
test-firmware:
	uv run --with platformio pio test -d firmware/esp32 -e native
	uv run --with platformio pio run -d firmware/esp32 -e esp32-s3-devkitc-1
	if rg -n 'FastLED|fastled' firmware/esp32/src firmware/esp32/include firmware/esp32/platformio.ini; then exit 1; fi

# Run deployment behavior tests and validate every maintained shell script.
test-deployment:
	{{python_env}} --with pytest pytest -q tests/unit/test_deploy_*.py tests/unit/test_preserve_deploy_settings.py
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
