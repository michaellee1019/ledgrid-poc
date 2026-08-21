"""Canonical, path-safe stored ZIP primitives for native bundles."""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Mapping

from .constants import (
    MAX_ARCHIVE_MEMBERS,
    MAX_BUNDLE_BYTES,
    MAX_UNCOMPRESSED_BYTES,
)
from .errors import NativeBundleError

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def safe_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or path.is_absolute()
        or name.endswith("/")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise NativeBundleError(f"unsafe bundle member path: {name!r}")


def read_archive_source(
    source: str | Path | bytes | bytearray | BinaryIO,
) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    elif hasattr(source, "read"):
        data = source.read(MAX_BUNDLE_BYTES + 1)
    else:
        path = Path(source)
        try:
            if path.is_symlink() or not path.is_file():
                raise NativeBundleError("bundle path must be a regular non-symlink file")
            if path.stat().st_size > MAX_BUNDLE_BYTES:
                raise NativeBundleError("bundle exceeds its 3 MiB size limit")
            data = path.read_bytes()
        except NativeBundleError:
            raise
        except OSError as exc:
            raise NativeBundleError(f"cannot read native bundle: {exc}") from exc
    if not isinstance(data, bytes) or not data or len(data) > MAX_BUNDLE_BYTES:
        raise NativeBundleError("bundle is empty or exceeds its 3 MiB size limit")
    return data


def read_safe_zip(data: bytes) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise NativeBundleError("native bundle is not a valid ZIP archive") from exc
    members: dict[str, bytes] = {}
    total = 0
    try:
        infos = archive.infolist()
        if not 1 <= len(infos) <= MAX_ARCHIVE_MEMBERS:
            raise NativeBundleError("native bundle has an invalid member count")
        for info in infos:
            safe_member_name(info.filename)
            if info.filename in members:
                raise NativeBundleError(f"duplicate bundle member: {info.filename}")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode) or info.is_dir():
                raise NativeBundleError(f"non-regular bundle member: {info.filename}")
            if info.flag_bits & 0x1:
                raise NativeBundleError("encrypted bundle members are unsupported")
            if info.compress_type != zipfile.ZIP_STORED:
                raise NativeBundleError("native bundle members must use canonical stored ZIP encoding")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise NativeBundleError("native bundle exceeds its uncompressed limit")
            with archive.open(info, "r") as handle:
                payload = handle.read(info.file_size + 1)
            if len(payload) != info.file_size:
                raise NativeBundleError(f"bundle member size mismatch: {info.filename}")
            members[info.filename] = payload
    except NativeBundleError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise NativeBundleError("cannot safely decode native bundle") from exc
    finally:
        archive.close()
    return members


def deterministic_zip(members: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_STORED,
        strict_timestamps=True,
    ) as archive:
        for name in sorted(members):
            safe_member_name(name)
            payload = members[name]
            if not isinstance(payload, bytes):
                raise NativeBundleError(f"bundle member {name!r} is not bytes")
            info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.flag_bits = 0
            archive.writestr(info, payload, compress_type=zipfile.ZIP_STORED)
    data = output.getvalue()
    if len(data) > MAX_BUNDLE_BYTES:
        raise NativeBundleError("built native bundle exceeds its 3 MiB size limit")
    return data
