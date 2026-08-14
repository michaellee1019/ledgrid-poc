#!/usr/bin/env python3
"""Run one deployment phase quietly, retaining full output for diagnosis."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys


DEFAULT_TAIL_LINES = 40
LIVE_PROGRESS_PREFIX = "[deploy"


def _safe_phase_name(phase: str) -> str:
    value = "".join(char if char.isalnum() or char in "._-" else "-" for char in phase)
    return value.strip("-") or "deploy"


def run_captured(
    command: list[str],
    *,
    phase: str,
    log_dir: Path,
    verbose: bool = False,
    tail_lines: int = DEFAULT_TAIL_LINES,
    env: dict[str, str] | None = None,
) -> tuple[int, Path]:
    """Execute *command*, stream if verbose, and report concise failures."""
    if not command:
        raise ValueError("a command is required")
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    log_path = log_dir / f"{stamp}-{_safe_phase_name(phase)}.log"

    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        if verbose:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    sys.stdout.write(line)
                    sys.stdout.flush()
            finally:
                process.stdout.close()
            return_code = process.wait()
        else:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            assert process.stderr is not None
            try:
                for line in process.stderr:
                    log.write(line)
                    log.flush()
                    if line.startswith(LIVE_PROGRESS_PREFIX):
                        sys.stderr.write(line)
                        sys.stderr.flush()
            finally:
                process.stderr.close()
            return_code = process.wait()

    if return_code:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"[ERROR] {phase} failed (exit {return_code})", file=sys.stderr)
        if lines:
            print(f"--- last {min(tail_lines, len(lines))} log lines ---", file=sys.stderr)
            for line in lines[-tail_lines:]:
                print(line, file=sys.stderr)
        print(f"full log: {log_path}", file=sys.stderr)
    return return_code, log_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--log-dir", type=Path, default=Path(".deploy-logs"))
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--tail-lines", type=int, default=DEFAULT_TAIL_LINES)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return_code, _ = run_captured(
        command,
        phase=args.phase,
        log_dir=args.log_dir,
        verbose=args.verbose,
        tail_lines=max(0, args.tail_lines),
        env=dict(os.environ),
    )
    raise SystemExit(return_code)


if __name__ == "__main__":
    main()
