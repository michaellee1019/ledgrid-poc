"""Hardware-lab recovery must finish before original-board firmware resumes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zlib

import pytest

from tests.hardware.cribbage_ble import lab


@pytest.fixture
def bench(tmp_path, monkeypatch):
    data = bytearray(0x11000)
    data[0x10000] = 0xE9
    backup = tmp_path / "original.bin"
    backup.write_bytes(data)
    image = tmp_path / "fixture.bin"
    image.write_bytes(b"\xe9fixture")
    board = {"name": "four-screen", "port": "test-port", "base_mac": "98:3D:AE:EA:17:BC",
             "backup": str(backup), "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
             "read_flash_digest_verified": True, "app_offset": 0x10000, "app_size": 0x1000,
             "ota_entries": [{"sequence": 1, "state": 0xFFFFFFFF,
                               "crc": zlib.crc32((1).to_bytes(4, "little"), 0xFFFFFFFF)}]}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"boards": [board]}))
    args = argparse.Namespace(manifest=manifest, board="four-screen", image=image,
                              tool_python=Path(sys.executable), esptool=Path("fake-esptool"),
                              command=["--", "test-command"])
    writes = []

    def tool(command, **kwargs):
        if command[-1] == "read_mac":
            return subprocess.CompletedProcess(command, 0, "MAC: " + board["base_mac"])
        assert "write_flash" in command
        writes.append((int(command[-2], 16), Path(command[-1])))
        kwargs["stdout"].write("Hash of data verified\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(lab.subprocess, "run", tool)
    return args, board, writes


@pytest.mark.parametrize("failure", [None, RuntimeError("failed test"), subprocess.TimeoutExpired("test", 600)])
def test_original_restored_after_success_failure_or_timeout(bench, monkeypatch, failure, capsys):
    args, board, writes = bench

    def test(_command):
        if failure:
            raise failure

    monkeypatch.setattr(lab, "bounded_test", test)
    if failure:
        with pytest.raises(type(failure)):
            lab.run(args)
    else:
        lab.run(args)
    assert writes == [(0x10000, args.image.resolve()), (0, Path(board["backup"]))]
    assert "RESTORED_AND_DIGEST_VERIFIED four-screen" in capsys.readouterr().out


def test_changed_backup_prevents_every_flash(bench):
    args, board, writes = bench
    Path(board["backup"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Incomplete or changed"):
        lab.run(args)
    assert not writes


def test_wrong_usb_identity_prevents_flash_and_restore_to_wrong_board(bench, monkeypatch):
    args, _board, writes = bench
    monkeypatch.setattr(lab.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, "MAC: 00:00:00:00:00:01"))
    with pytest.raises(RuntimeError, match="identity changed"):
        lab.run(args)
    assert not writes


def test_absent_flash_digest_never_reports_restoration_success(bench, monkeypatch, capsys):
    args, _board, _writes = bench

    def tool(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "MAC: 98:3D:AE:EA:17:BC")

    monkeypatch.setattr(lab.subprocess, "run", tool)
    with pytest.raises(RuntimeError, match="digest verification"):
        lab.run(args)
    assert "RESTORED_AND_DIGEST_VERIFIED" not in capsys.readouterr().out


def test_timeout_stops_test_child_before_restoration(tmp_path):
    pid_file = tmp_path / "child.pid"
    code = ("import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c',"
            "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)']); "
            "open(sys.argv[1],'w').write(str(p.pid)); time.sleep(60)")
    with pytest.raises(subprocess.TimeoutExpired):
        lab.bounded_test([sys.executable, "-c", code, str(pid_file)], timeout=.5)
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(.02)
    else:
        pytest.fail("The timed-out test left a child holding hardware resources")
