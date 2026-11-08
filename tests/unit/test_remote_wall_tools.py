"""Exercise the remote wall tools using local SSH and Pi command stubs."""

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def remote_shell(tmp_path, monkeypatch):
    def executable(name, content):
        script = tmp_path / name
        script.write_text("#!/bin/bash\nset -eu\n" + content)
        script.chmod(0o700)

    key = tmp_path / "wall key"
    key.write_text("test key")
    key.chmod(0o600)
    log = tmp_path / "commands.log"
    monkeypatch.setenv("SSH_KEY", str(key))
    monkeypatch.setenv("PI_HOST", "test@wall.local")
    monkeypatch.setenv("OUT_FILE", "")
    monkeypatch.setenv("PORT", "")
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("TEST_COMMAND_LOG", str(log))
    monkeypatch.setenv("TEST_WALL_KEY", str(key))
    monkeypatch.delenv("REMOTE", raising=False)
    executable(
        "ssh",
        '''
test "$1" = -i
test "$2" = "$TEST_WALL_KEY"
test "$3 $4" = "-o IdentitiesOnly=yes"
test "$5 $6" = "-F /dev/null"
test "$7 $8" = "-o IdentityAgent=none"
test "${9} ${10}" = "-o BatchMode=yes"
test "${15}" = test@wall.local
printf '%s\n' "${16}" >> "$TEST_COMMAND_LOG"
bash -c "${16}"
''',
    )
    executable("sudo", 'test "$1" = -n; shift; exec "$@"\n')
    executable(
        "systemctl",
        '''
printf 'systemctl %s\n' "$*" >> "$TEST_COMMAND_LOG"
case "$*" in
    "show ledgrid.service --property=Environment --value") echo "PORT=5012" ;;
esac
''',
    )
    executable("journalctl", 'printf "journalctl %s\\n" "$*" >> "$TEST_COMMAND_LOG"\n')
    executable("curl", 'printf "curl %s\\n" "$*" >> "$TEST_COMMAND_LOG"\n')
    return log


def run_script(path, *args):
    return subprocess.run(
        ["bash", str(ROOT / path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("action", ("stop", "restart", "status"))
def test_service_commands_use_systemd(remote_shell, action):
    result = run_script("tools/deployment/stop_remote.sh", action)
    assert result.returncode == 0, result.stderr
    assert f"systemctl {action}" in remote_shell.read_text()


@pytest.mark.parametrize("restart", ("0", "1"))
def test_diagnostics_use_current_telemetry(remote_shell, monkeypatch, restart):
    monkeypatch.setenv("RESTART_WEB", restart)
    result = run_script("tools/diagnostics/remote_diagnostics.sh")
    assert result.returncode == 0, result.stderr
    log = remote_shell.read_text()
    assert ("systemctl restart ledgrid.service" in log) == (restart == "1")
    assert "journalctl -u ledgrid.service" in log
    assert "http://127.0.0.1:5012/api/v1/composer/operations/telemetry" in log


def test_shell_tools_stop_before_ssh_if_key_missing(remote_shell, monkeypatch, tmp_path):
    monkeypatch.setenv("SSH_KEY", str(tmp_path / "missing"))
    result = run_script("tools/deployment/stop_remote.sh", "status")
    assert result.returncode == 1
    assert "Dedicated SSH key is missing" in result.stderr
    assert not remote_shell.exists()
