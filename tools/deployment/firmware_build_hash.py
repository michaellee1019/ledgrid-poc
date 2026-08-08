#!/usr/bin/env python3
"""Compute the receiver firmware cache key from every build input."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Mapping


REQUIRED_BUILD_FILES = (
    "CMakeLists.txt",
    "dependencies.lock",
    "extra_script.py",
    "partitions.csv",
    "platformio.ini",
    "sdkconfig.defaults",
)
BUILD_DIRECTORIES = ("include", "src")
BUILD_ENVIRONMENT = (
    "DEBUG",
    "LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT",
    "LEDGRID_LOGICAL_DEVICE",
    "LEDGRID_TRUSTED_KEY_ID",
    "LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX",
)


def firmware_build_hash(
    firmware_root: str | Path,
    environment: Mapping[str, str] | None = None,
) -> str:
    root = Path(firmware_root)
    files = [root / relative for relative in REQUIRED_BUILD_FILES]
    for relative in BUILD_DIRECTORIES:
        directory = root / relative
        if not directory.is_dir():
            raise FileNotFoundError(f"missing firmware build directory: {directory}")
        files.extend(path for path in directory.rglob("*") if path.is_file())
    missing = [path for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing firmware build input: {missing[0]}")

    values = os.environ if environment is None else environment
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    for name in BUILD_ENVIRONMENT:
        encoded_name = name.encode("ascii")
        encoded_value = values.get(name, "").encode("utf-8")
        digest.update(len(encoded_name).to_bytes(2, "big"))
        digest.update(encoded_name)
        digest.update(len(encoded_value).to_bytes(4, "big"))
        digest.update(encoded_value)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit("usage: firmware_build_hash.py FIRMWARE_ROOT")
    print(firmware_build_hash(args[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
