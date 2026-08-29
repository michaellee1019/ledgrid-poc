#!/usr/bin/env python3
"""Run the portable browser matrix in an isolated, retained local evidence run."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from werkzeug.serving import BaseWSGIServer, make_server

from tools.browser_qualification.evidence import run_qualification
from tools.browser_qualification.fixture_server import create_fixture_server


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_ROOT = ROOT / "run_state" / "browser_qualification" / "evidence"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def create_run_directory(evidence_root: Path = DEFAULT_EVIDENCE_ROOT) -> Path:
    """Make a unique evidence directory, safe for concurrent worktrees/runs."""
    directory = evidence_root / f"rel01-{_utc_stamp()}-{uuid.uuid4().hex[:12]}"
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def write_index(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_clean_qualification(
    *, evidence_root: Path = DEFAULT_EVIDENCE_ROOT, timeout_ms: int = 180_000
) -> tuple[dict[str, Any] | None, Path]:
    """Serve the no-wall app on an OS-assigned loopback port and retain a run."""
    run_dir = create_run_directory(evidence_root)
    fixture_dir = run_dir / "fixture"
    evidence_path = run_dir / "rel01-browser-evidence.json"
    index_path = run_dir / "index.json"
    interface, _channel, profile_digest = create_fixture_server(fixture_dir, port=0)
    server: BaseWSGIServer = make_server("127.0.0.1", 0, interface.app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    index: dict[str, Any] = {
        "schema": "ledgrid.browser-qualification-run-index",
        "schema_version": 1,
        "base_url": base_url,
        "profile_digest": profile_digest,
        "evidence": "rel01-browser-evidence.json",
        "artifacts": "artifacts",
        "fixture_state": "fixture",
        "fixture_port": server.server_port,
        "status": "RUNNING",
    }
    write_index(index_path, index)
    result: dict[str, Any] | None = None
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def stop_on_sigterm(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt("browser qualification interrupted")

    signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        result = run_qualification(
            base_url=base_url,
            output_path=evidence_path,
            artifacts_dir=run_dir / "artifacts",
            timeout_ms=timeout_ms,
        )
        index["outcomes"] = result["outcomes"]
        index["status"] = "PASS" if result["outcomes"]["portable_browser_matrix"] == "PASS" else "FAIL"
    except BaseException as error:
        index["status"] = "ERROR"
        index["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=10)
        index["fixture_stopped"] = not server_thread.is_alive()
        write_index(index_path, index)
    return result, run_dir


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a clean, no-wall Composer browser qualification.")
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--timeout-ms", type=int, default=180_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result, run_dir = run_clean_qualification(
        evidence_root=args.evidence_root, timeout_ms=args.timeout_ms
    )
    print(json.dumps({"run_dir": os.fspath(run_dir), "outcomes": result["outcomes"]}, sort_keys=True))
    return 0 if result["outcomes"]["portable_browser_matrix"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
