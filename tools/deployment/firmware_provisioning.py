#!/usr/bin/env python3
"""Create and validate production receiver-animation provisioning state."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from firmware_animations.signing import generate_keypair


KEY_ID_RE = re.compile(r"key-[0-9a-f]{16}\Z")
PUBLIC_HEX_RE = re.compile(r"04[0-9a-f]{128}\Z")
PORT_RE = re.compile(r"/dev/serial/by-id/[A-Za-z0-9_.:+-]+\Z")
PORT_KEYS = tuple(f"LEDGRID_RECEIVER_{index}_PORT" for index in range(4))
REQUIRED_KEYS = (
    "LEDGRID_TRUSTED_KEY_ID",
    "LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX",
    *PORT_KEYS,
    "LEDGRID_LGA_TRUSTED_KEYS",
)
PINNED_PLATFORM = (
    "https://github.com/pioarduino/platform-espressif32/releases/download/"
    "55.03.39/platform-espressif32.zip"
)


@dataclass(frozen=True)
class Provisioning:
    key_id: str
    public_key_hex: str
    ports: tuple[str, str, str, str]
    host_trusted_keys: str

    def environment(self) -> dict[str, str]:
        values = {
            "LEDGRID_TRUSTED_KEY_ID": self.key_id,
            "LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX": self.public_key_hex,
            "LEDGRID_LGA_TRUSTED_KEYS": self.host_trusted_keys,
        }
        values.update(zip(PORT_KEYS, self.ports))
        return values


def _public_der(path: Path) -> bytes:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
        body = "".join(
            line.strip() for line in lines
            if line and not line.startswith("-----")
        )
        der = base64.b64decode(body, validate=True)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid public key PEM: {path}") from exc
    # P-256 SubjectPublicKeyInfo ends in the uncompressed SEC1 point.
    if len(der) < 65 or der[-65] != 0x04:
        raise ValueError("public key is not an uncompressed ECDSA P-256 key")
    return der


def public_identity(path: Path) -> tuple[str, str]:
    der = _public_der(path)
    return f"key-{hashlib.sha256(der).hexdigest()[:16]}", der[-65:].hex()


def parse_environment(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read provisioning file: {path}") from exc
    values: dict[str, str] = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name not in REQUIRED_KEYS or not value:
            raise ValueError(f"invalid provisioning line {number}")
        if name in values:
            raise ValueError(f"duplicate provisioning value: {name}")
        if any(character.isspace() or character in "'\"`$;\\" for character in value):
            raise ValueError(f"unsafe provisioning value: {name}")
        values[name] = value
    missing = set(REQUIRED_KEYS) - set(values)
    extra = set(values) - set(REQUIRED_KEYS)
    if missing or extra:
        raise ValueError(
            f"provisioning keys mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return values


def validate_environment(
    values: Mapping[str, str], *, public_key: Path | None = None
) -> Provisioning:
    key_id = values["LEDGRID_TRUSTED_KEY_ID"]
    public_hex = values["LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX"]
    ports = tuple(values[name] for name in PORT_KEYS)
    host_keys = values["LEDGRID_LGA_TRUSTED_KEYS"]
    if not KEY_ID_RE.fullmatch(key_id):
        raise ValueError("trusted key id must be key- plus 16 lowercase hex digits")
    if not PUBLIC_HEX_RE.fullmatch(public_hex):
        raise ValueError("trusted public key must be a 65-byte SEC1 P-256 point")
    if len(set(ports)) != 4 or any(not PORT_RE.fullmatch(port) for port in ports):
        raise ValueError("receiver ports must be four unique /dev/serial/by-id paths")
    expected_host_keys = f"{key_id}=run_state/firmware/public.pem"
    if host_keys != expected_host_keys:
        raise ValueError(
            f"LEDGRID_LGA_TRUSTED_KEYS must be {expected_host_keys}"
        )
    if public_key is not None:
        actual_id, actual_hex = public_identity(public_key)
        if (actual_id, actual_hex) != (key_id, public_hex):
            raise ValueError("provisioning values do not match public.pem")
    return Provisioning(key_id, public_hex, ports, host_keys)


def load_provisioning(path: Path, *, public_key: Path | None = None) -> Provisioning:
    return validate_environment(parse_environment(path), public_key=public_key)


def write_environment(path: Path, provisioning: Provisioning) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated receiver-animation provisioning. No private key is copied.",
        *(f"{name}={value}" for name, value in provisioning.environment().items()),
    ]
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def initialize(state_dir: Path, ports: list[str]) -> Provisioning:
    if len(ports) != 4:
        raise ValueError("exactly four --port values are required in logical order 0..3")
    state_dir.mkdir(parents=True, exist_ok=True)
    private_key = state_dir / "signing_private.pem"
    public_key = state_dir / "public.pem"
    if private_key.exists() != public_key.exists():
        raise ValueError("refusing partial signing-key state")
    if not private_key.exists():
        generate_keypair(private_key, public_key)
    key_id, public_hex = public_identity(public_key)
    provisioning = Provisioning(
        key_id,
        public_hex,
        tuple(ports),  # type: ignore[arg-type]
        f"{key_id}=run_state/firmware/public.pem",
    )
    validate_environment(provisioning.environment(), public_key=public_key)
    write_environment(state_dir / "deploy.env", provisioning)
    return provisioning


def render_platformio_config(
    provisioning: Provisioning, logical_device: int, build_dir: Path
) -> str:
    if logical_device not in range(4):
        raise ValueError("logical device must be 0..3")
    return f"""[platformio]
default_envs = receiver-{logical_device}
build_dir = {build_dir}

[env:receiver-{logical_device}]
platform = {PINNED_PLATFORM}
board = esp32-s3-devkitc1-n16r8
framework = espidf
board_build.flash_mode = dio
board_build.f_flash = 80000000L
board_build.f_cpu = 240000000L
board_build.partitions = partitions.csv
build_flags =
    -O2
    -std=gnu++17
extra_scripts = pre:extra_script.py
upload_speed = 460800
monitor_speed = 115200
monitor_raw = yes
"""


def render_sdkconfig(
    defaults: str, provisioning: Provisioning, logical_device: int
) -> str:
    if logical_device not in range(4):
        raise ValueError("logical device must be 0..3")
    managed = (
        "CONFIG_LEDGRID_TRUSTED_KEY_ID",
        "CONFIG_LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX",
        "CONFIG_LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT",
        "CONFIG_LEDGRID_LOGICAL_DEVICE",
    )
    retained = [
        line for line in defaults.splitlines()
        if not any(name in line for name in managed)
    ]
    retained.extend((
        f'CONFIG_LEDGRID_TRUSTED_KEY_ID="{provisioning.key_id}"',
        f'CONFIG_LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX="{provisioning.public_key_hex}"',
        "# CONFIG_LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT is not set",
        f"CONFIG_LEDGRID_LOGICAL_DEVICE={logical_device}",
    ))
    return "\n".join(retained) + "\n"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--state-dir", type=Path, required=True)
    init.add_argument("--port", action="append", default=[])
    init.add_argument("--ports", help="comma-separated logical receiver ports 0..3")
    check = commands.add_parser("check")
    check.add_argument("--config", type=Path, required=True)
    check.add_argument("--public-key", type=Path, required=True)
    config = commands.add_parser("platformio-config")
    config.add_argument("--config", type=Path, required=True)
    config.add_argument("--public-key", type=Path, required=True)
    config.add_argument("--logical-device", type=int, required=True)
    config.add_argument("--build-dir", type=Path, required=True)
    config.add_argument("--output", type=Path, required=True)
    config.add_argument("--sdkconfig-defaults", type=Path, required=True)
    config.add_argument("--sdkconfig-output", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            ports = list(args.port)
            if args.ports:
                if ports:
                    raise ValueError("use either --port or --ports, not both")
                ports = args.ports.split(",")
            provisioning = initialize(args.state_dir, ports)
            print(json.dumps({"key_id": provisioning.key_id, "ports": provisioning.ports}))
        else:
            provisioning = load_provisioning(
                args.config, public_key=args.public_key
            )
            if args.command == "platformio-config":
                rendered = render_platformio_config(
                    provisioning, args.logical_device, args.build_dir
                )
                args.output.parent.mkdir(parents=True, exist_ok=True)
                temporary = args.output.with_suffix(args.output.suffix + ".part")
                temporary.write_text(rendered, encoding="utf-8")
                temporary.replace(args.output)
                sdkconfig = render_sdkconfig(
                    args.sdkconfig_defaults.read_text(encoding="utf-8"),
                    provisioning,
                    args.logical_device,
                )
                sdk_temporary = args.sdkconfig_output.with_suffix(".part")
                sdk_temporary.write_text(sdkconfig, encoding="utf-8")
                sdk_temporary.replace(args.sdkconfig_output)
            else:
                print(json.dumps({"key_id": provisioning.key_id, "ports": provisioning.ports}))
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
