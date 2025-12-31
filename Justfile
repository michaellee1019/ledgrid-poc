set shell := ["bash", "-euxo", "pipefail", "-c"]

web_venv := ".venv-web"

# Deploy to the Raspberry Pi using the existing deployment script.
deploy:
	./tools/deployment/deploy.sh

# Create/refresh the lightweight virtualenv for serving the web controller locally.
setup:
	if [ ! -d {{web_venv}} ]; then python3 -m venv {{web_venv}}; fi
	{{web_venv}}/bin/pip install --upgrade pip
	{{web_venv}}/bin/pip install --upgrade flask "werkzeug>=2.0.0"

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
