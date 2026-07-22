# Operational tools

Tools are executable support code outside the application runtime:

- `benchmarks/`: headless render, live animation, receiver, and physical output
  acceptance checks
- `deployment/`: Pi setup, application sync, service control, preset retrieval,
  and firmware flashing
- `diagnostics/`: SPI tests, remote health collection, payload inspection, and
  offline simulation helpers

Application entry points belong in `scripts/`; reusable Python code belongs in
the package that owns it. Do not add one-off migration, rescue, or historical
debug scripts here. If a diagnostic remains useful, give it stable CLI help and
exercise its reusable logic from tests.
