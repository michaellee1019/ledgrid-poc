"""Temporarily install a radio fixture and always restore verified original flash."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import zlib
import struct


def bounded_test(command, timeout=600):
    # Stop uv and its Python child before reopening the serial port to restore.
    process = subprocess.Popen(command, start_new_session=True)
    try:
        result = process.wait(timeout=timeout)
        if result:
            raise subprocess.CalledProcessError(result, command)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # A grandchild may ignore TERM even after the launcher has exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        raise


def validate(board):
    data = Path(board["backup"]).read_bytes()
    if len(data) != board["size"] or hashlib.sha256(data).hexdigest() != board["sha256"]:
        raise ValueError("Incomplete or changed full-flash backup: " + board["name"])
    if not board["read_flash_digest_verified"]:
        raise ValueError("Backup transfer was not digest verified")
    entries = board["ota_entries"]
    valid = [entry for entry in entries if entry["sequence"] not in (0, 0xFFFFFFFF)
             and entry["state"] not in (3, 4)
             and entry["crc"] == zlib.crc32(struct.pack("<I", entry["sequence"]), 0xFFFFFFFF)]
    if not valid or (max(valid, key=lambda entry: entry["sequence"])["sequence"] - 1) % 2:
        raise ValueError("This bounded fixture expects the inspected app0 OTA selection")
    if board["app_offset"] != 0x10000 or data[0x10000] != 0xE9:
        raise ValueError("Unexpected original app offset or image")


def run(args):
    boards = json.loads(args.manifest.read_text())["boards"]
    for board in boards:
        validate(board)
    chosen = next(board for board in boards if board["name"] == args.board)
    image = args.image.resolve()
    if image.stat().st_size > chosen["app_size"] or image.read_bytes()[:1] != b"\xe9":
        raise ValueError("Fixture image does not fit the inspected original app slot")
    tool = [str(args.tool_python), str(args.esptool), "--chip", "esp32s3",
            "--port", chosen["port"], "--baud", "57600"]
    log_root = args.manifest.parent

    def flash(offset, path, phase):
        check = subprocess.run([*tool, "read_mac"], capture_output=True, text=True, check=True, timeout=30)
        if ("mac: " + chosen["base_mac"].lower()) not in check.stdout.lower():
            raise RuntimeError("USB target identity changed; refusing flash")
        log = log_root / (chosen["name"] + "-" + phase + ".log")
        print(phase + ": " + chosen["name"], flush=True)
        with log.open("w") as output:
            subprocess.run([*tool, "write_flash", "-z", "--flash_mode", "keep", "--flash_freq", "keep",
                            "--flash_size", "keep", hex(offset), str(path)],
                           stdout=output, stderr=subprocess.STDOUT, check=True, timeout=600)
        if "Hash of data verified" not in log.read_text():
            raise RuntimeError("Flash completed without the required digest verification")

    # Mark restoration necessary before the first possible modifying operation.
    try:
        flash(chosen["app_offset"], image, "fixture-install")
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        bounded_test(command)
    finally:
        flash(0, Path(chosen["backup"]), "original-restore")
        print("RESTORED_AND_DIGEST_VERIFIED " + chosen["name"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--board", choices=("four-screen", "epaper"), required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--tool-python", type=Path, default=Path("/opt/homebrew/opt/platformio/libexec/bin/python"))
    parser.add_argument("--esptool", type=Path, default=Path.home() / ".platformio/packages/tool-esptoolpy/esptool.py")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or args.command == ["--"]:
        sys.exit("An explicit bounded test command is required")
    run(args)
