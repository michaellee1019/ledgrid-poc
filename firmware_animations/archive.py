"""Safe deterministic ZIP primitives used by the package verifier and builder."""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Mapping

from .constants import (
    MAX_ARCHIVE_MEMBERS,
    MAX_COMPRESSION_RATIO,
    MAX_PACKAGE_BYTES,
    MAX_UNCOMPRESSED_BYTES,
)
from .errors import PackageValidationError

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _safe_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or name.endswith("/")
    ):
        raise PackageValidationError(f"unsafe archive member path: {name!r}")


def read_archive_source(source: str | Path | bytes | bytearray | BinaryIO) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    elif hasattr(source, "read"):
        data = source.read(MAX_PACKAGE_BYTES + 1)
    else:
        path = Path(source)
        try:
            if path.stat().st_size > MAX_PACKAGE_BYTES:
                raise PackageValidationError("package exceeds the 16 MiB upload limit")
            data = path.read_bytes()
        except OSError as exc:
            raise PackageValidationError(f"cannot read package: {exc}") from exc
    if not isinstance(data, bytes) or len(data) > MAX_PACKAGE_BYTES:
        raise PackageValidationError("package exceeds the 16 MiB upload limit")
    return data


def read_safe_zip(source: str | Path | bytes | bytearray | BinaryIO) -> tuple[bytes, dict[str, bytes]]:
    data = read_archive_source(source)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageValidationError("package is not a valid ZIP archive") from exc
    members: dict[str, bytes] = {}
    total = 0
    try:
        infos = archive.infolist()
        if not 1 <= len(infos) <= MAX_ARCHIVE_MEMBERS:
            raise PackageValidationError("package has an invalid member count")
        for info in infos:
            _safe_member_name(info.filename)
            if info.filename in members:
                raise PackageValidationError(f"duplicate archive member: {info.filename}")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise PackageValidationError(f"symlink archive member is forbidden: {info.filename}")
            if info.flag_bits & 0x1:
                raise PackageValidationError("encrypted archive members are unsupported")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise PackageValidationError("unsupported ZIP compression method")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise PackageValidationError("package expands beyond the safe uncompressed limit")
            if info.file_size and info.file_size > max(1024, info.compress_size * MAX_COMPRESSION_RATIO):
                raise PackageValidationError("package member has a suspicious compression ratio")
            with archive.open(info, "r") as handle:
                payload = handle.read(info.file_size + 1)
            if len(payload) != info.file_size:
                raise PackageValidationError(f"archive member size mismatch: {info.filename}")
            members[info.filename] = payload
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise PackageValidationError("cannot safely decode package ZIP") from exc
    finally:
        archive.close()
    return data, members


def deterministic_zip(members: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True) as archive:
        for name in sorted(members):
            _safe_member_name(name)
            if name in seen:
                raise PackageValidationError(f"duplicate archive member: {name}")
            seen.add(name)
            payload = members[name]
            if not isinstance(payload, bytes):
                raise PackageValidationError(f"member {name!r} is not bytes")
            info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.flag_bits = 0
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    data = output.getvalue()
    if len(data) > MAX_PACKAGE_BYTES:
        raise PackageValidationError("built package exceeds the 16 MiB upload limit")
    return data
