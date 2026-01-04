set shell := ["bash", "-euxo", "pipefail", "-c"]

web_venv := ".venv-web"

# Deploy to the Raspberry Pi using the existing deployment script.
deploy:
	./tools/deployment/deploy.sh

# Deploy app + server bits without flashing ESP32 firmware.
deploy-no-firmware:
	SKIP_FIRMWARE=1 ./tools/deployment/deploy.sh

# Save current animation state, sync plugins, and restart the remote server.
iterate:
	./tools/dev/iterate.sh

# Create/refresh the lightweight virtualenv for serving the web controller locally.
setup-web:
	if [ ! -d {{web_venv}} ]; then python3 -m venv {{web_venv}}; fi
	{{web_venv}}/bin/pip install --upgrade pip
	{{web_venv}}/bin/pip install --upgrade flask "werkzeug>=2.0.0"

# Prepare the deploy target for flashing ESP32 firmware and running the app.
setup: setup-web
	bash tools/deployment/setup.sh

# Run the current (non-legacy) tests.
test:
	python3 -m unittest discover -s tests -p 'test_*.py'

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
		echo "web controller venv missing; run 'just setup' first" >&2; \
		exit 1; \
	fi; \
	HOST="${HOST:-127.0.0.1}"; \
	PORT="${PORT:-5000}"; \
	ARGS=(--mode web --host "$HOST" --port "$PORT"); \
	if [ -n "${DEBUG+x}" ] && [ "$DEBUG" != "0" ]; then ARGS+=("--debug"); fi; \
	exec {{web_venv}}/bin/python scripts/start_server.py "${ARGS[@]}"
