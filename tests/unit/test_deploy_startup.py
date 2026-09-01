"""Regression tests for production startup against relocated runtime venvs."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
START_SCRIPT = ROOT / "scripts" / "start_systemd.sh"


class SystemdStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        self.start_script = scripts / "start_systemd.sh"
        shutil.copyfile(START_SCRIPT, self.start_script)
        self.start_script.chmod(self.start_script.stat().st_mode | stat.S_IXUSR)
        (self.root / "venv" / "bin").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary_dir.cleanup()

    @staticmethod
    def _write_executable(path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def test_relocated_runtime_ignores_stale_activation_path(self) -> None:
        trace = self.root / "python-invocations.log"
        bash_env = self.root / "bash-env"
        bash_env.write_text(
            'wait() { if [ "${1:-}" = -n ]; then shift; builtin wait "$1"; '
            'else builtin wait "$@"; fi; }\n',
            encoding="utf-8",
        )
        self._write_executable(
            self.root / "venv" / "bin" / "activate",
            "#!/bin/bash\nreturn 97 2>/dev/null || exit 97\n",
        )
        self._write_executable(
            self.root / "venv" / "bin" / "python",
            """#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >> "$LEDGRID_TEST_PYTHON_TRACE"
if [ "${1:-}" = "-" ]; then
    program=$(cat)
    case "$program" in
        *resolve_active_release_id*) printf '%s\n' "${LEDGRID_TEST_RELEASE_ID:-}" ;;
        *default_strip_count*) printf '32\n' ;;
        *DEFAULT_LEDS_PER_STRIP*) printf '138\n' ;;
        *DEFAULT_ANIMATION_SPEED_SCALE*) printf '1.0\n' ;;
        *) exit 64 ;;
    esac
    exit 0
fi
if [ "${1:-}" = "scripts/start_server.py" ]; then
    case " $* " in
        *" --mode controller "*) sleep 0.2 ;;
    esac
    exit 0
fi
exit 65
""",
        )

        completed = subprocess.run(
            ["/bin/bash", os.fspath(self.start_script)],
            cwd=self.root,
            env={
                **os.environ,
                "BASH_ENV": os.fspath(bash_env),
                "LEDGRID_TEST_PYTHON_TRACE": os.fspath(trace),
            },
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        invocations = trace.read_text(encoding="utf-8").splitlines()
        self.assertEqual(invocations.count("-"), 3)
        self.assertEqual(sum(line.startswith("- ") for line in invocations), 1)
        self.assertEqual(
            sum(
                "scripts/start_server.py --mode controller" in line
                for line in invocations
            ),
            1,
        )
        self.assertEqual(
            sum("scripts/start_server.py --mode web" in line for line in invocations),
            1,
        )

    def test_verified_release_identity_is_passed_to_both_processes(self) -> None:
        release_id = "e" * 64
        trace = self.root / "python-invocations.log"
        bash_env = self.root / "bash-env"
        bash_env.write_text(
            'wait() { if [ "${1:-}" = -n ]; then shift; builtin wait "$1"; '
            'else builtin wait "$@"; fi; }\n',
            encoding="utf-8",
        )
        self._write_executable(
            self.root / "venv" / "bin" / "python",
            """#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >> "$LEDGRID_TEST_PYTHON_TRACE"
if [ "${1:-}" = "-" ]; then
    program=$(cat)
    case "$program" in
        *resolve_active_release_id*) printf '%s\n' "$LEDGRID_TEST_RELEASE_ID" ;;
        *default_strip_count*) printf '32\n' ;;
        *DEFAULT_LEDS_PER_STRIP*) printf '138\n' ;;
        *DEFAULT_ANIMATION_SPEED_SCALE*) printf '1.0\n' ;;
        *) exit 64 ;;
    esac
    exit 0
fi
if [ "${1:-}" = "scripts/start_server.py" ]; then
    case " $* " in
        *" --mode controller "*) sleep 0.2 ;;
    esac
    exit 0
fi
exit 65
""",
        )

        completed = subprocess.run(
            ["/bin/bash", os.fspath(self.start_script)],
            cwd=self.root,
            env={
                **os.environ,
                "BASH_ENV": os.fspath(bash_env),
                "LEDGRID_TEST_PYTHON_TRACE": os.fspath(trace),
                "LEDGRID_TEST_RELEASE_ID": release_id,
            },
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        server_invocations = [
            line for line in trace.read_text(encoding="utf-8").splitlines()
            if line.startswith("scripts/start_server.py")
        ]
        self.assertEqual(len(server_invocations), 2)
        for invocation in server_invocations:
            self.assertIn(f"--release-id {release_id}", invocation)

    def test_failed_child_logs_are_copied_to_the_service_journal(self) -> None:
        bash_env = self.root / "bash-env"
        bash_env.write_text(
            'wait() { if [ "${1:-}" = -n ]; then shift; builtin wait "$1"; '
            'else builtin wait "$@"; fi; }\n',
            encoding="utf-8",
        )
        self._write_executable(
            self.root / "venv" / "bin" / "python",
            """#!/bin/bash
set -euo pipefail
if [ "${1:-}" = "-" ]; then
    program=$(cat)
    case "$program" in
        *resolve_active_release_id*) printf '%s\n' "${LEDGRID_TEST_RELEASE_ID:-}" ;;
        *default_strip_count*) printf '33\n' ;;
        *DEFAULT_LEDS_PER_STRIP*) printf '138\n' ;;
        *DEFAULT_ANIMATION_SPEED_SCALE*) printf '1.0\n' ;;
        *) exit 64 ;;
    esac
    exit 0
fi
case " $* " in
    *" --mode controller "*) printf 'controller traceback sentinel\n'; exit 23 ;;
    *" --mode web "*) sleep 1 ;;
esac
exit 65
""",
        )

        completed = subprocess.run(
            ["/bin/bash", os.fspath(self.start_script)],
            cwd=self.root,
            env={
                **os.environ,
                "BASH_ENV": os.fspath(bash_env),
                "LEDGRID_TEST_RELEASE_ID": "f" * 64,
            },
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(completed.returncode, 23)
        self.assertIn("ledgrid child exited with status 23", completed.stderr)
        self.assertIn("controller traceback sentinel", completed.stderr)

    def test_missing_runtime_interpreter_fails_without_system_python_fallback(
        self,
    ) -> None:
        fallback_called = self.root / "system-python-called"
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        self._write_executable(
            fake_bin / "python",
            f"#!/bin/bash\ntouch {fallback_called!s}\n",
        )

        completed = subprocess.run(
            ["/bin/bash", os.fspath(self.start_script)],
            cwd=self.root,
            env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Runtime Python not found or not executable", completed.stderr)
        self.assertFalse(fallback_called.exists())


if __name__ == "__main__":
    unittest.main()
