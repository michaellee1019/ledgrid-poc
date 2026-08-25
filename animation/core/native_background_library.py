"""Immutable Pi-authoritative storage for trusted native backgrounds.

Bundles are validated by :mod:`animation.native.bundle` before this module
creates any filesystem state.  The complete canonical bundle remains the
authoritative bundle identity, while extracted executable payloads are shared
by payload digest so manifest/preview-only rebuilds reuse one immutable object.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
from typing import Any, Callable, Iterator, Mapping, Sequence


LIBRARY_SCHEMA_VERSION = 1
BUNDLES_DIRECTORY = "bundles"
PAYLOADS_DIRECTORY = "payloads"
BUNDLE_FILENAME = "bundle.zip"
PAYLOAD_FILENAME = "module.so"
RECEIPT_FILENAME = "receipt.json"
LOCK_FILENAME = ".library.lock"

_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "package_id",
        "bundle_digest",
        "payload_digest",
        "bundle_size",
        "payload_size",
        "published_at",
    }
)


class NativeBackgroundLibraryError(RuntimeError):
    """A native bundle library entry is invalid, unsafe, or unavailable."""


class NativeBackgroundNotFoundError(NativeBackgroundLibraryError):
    """The requested native bundle is not present in the managed library."""


def _require_digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise NativeBackgroundLibraryError(
            f"{field} must be exactly 64 lowercase hexadecimal characters"
        )
    return value


def _require_non_negative_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise NativeBackgroundLibraryError(f"{field} must be a non-negative integer")
    return value


def _parse_utc_timestamp(value: object) -> datetime:
    """Parse one frozen receipt timestamp into a comparable UTC instant."""

    if (
        not isinstance(value, str)
        or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None
    ):
        raise NativeBackgroundLibraryError(
            "receipt published_at must be a UTC ISO-8601 timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise NativeBackgroundLibraryError(
            "receipt published_at must be a UTC ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise NativeBackgroundLibraryError(
            "receipt published_at must be a UTC ISO-8601 timestamp"
        )
    return parsed.astimezone(timezone.utc)


def _require_timestamp(value: object) -> str:
    _parse_utc_timestamp(value)
    assert isinstance(value, str)  # Narrowed by the parser above.
    return value


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NativeBackgroundLibraryError(
                f"receipt contains duplicate JSON field {key!r}"
            )
        result[key] = value
    return result


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _is_immutable_regular_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not metadata.st_mode & 0o222


def _published_at(clock: Callable[[], datetime]) -> str:
    current = clock()
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise NativeBackgroundLibraryError(
            "library clock must return a timezone-aware datetime"
        )
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _inspect_bundle(source: bytes | Path):
    try:
        from animation.native.bundle import PAYLOAD_PATH, inspect_bundle
    except (ImportError, AttributeError) as exc:  # pragma: no cover - integration guard
        raise NativeBackgroundLibraryError(
            "native bundle validator is unavailable"
        ) from exc
    try:
        verified = inspect_bundle(source)
        payload = verified.members[PAYLOAD_PATH]
    except Exception as exc:
        # The validator owns the detailed format vocabulary; the managed library
        # exposes one stable domain error without weakening that validation.
        raise NativeBackgroundLibraryError(f"native bundle is invalid: {exc}") from exc
    if not isinstance(verified.raw, bytes) or not isinstance(payload, bytes):
        raise NativeBackgroundLibraryError(
            "native bundle validator returned mutable or malformed bytes"
        )
    _require_digest(verified.bundle_digest, field="bundle digest")
    _require_digest(verified.payload_digest, field="payload digest")
    if _sha256(verified.raw) != verified.bundle_digest:
        raise NativeBackgroundLibraryError(
            "native bundle digest does not identify the complete bundle bytes"
        )
    if _sha256(payload) != verified.payload_digest:
        raise NativeBackgroundLibraryError(
            "native payload digest does not identify the executable bytes"
        )
    manifest = verified.manifest
    package_id = manifest.get("plugin_id") if isinstance(manifest, Mapping) else None
    if not isinstance(package_id, str) or not package_id:
        raise NativeBackgroundLibraryError("native bundle manifest has no plugin_id")
    return verified, payload, package_id


@dataclass(frozen=True)
class NativeBackgroundPublishReceipt:
    """Exact immutable receipt stored beside a published bundle."""

    schema_version: int
    package_id: str
    bundle_digest: str
    payload_digest: str
    bundle_size: int
    payload_size: int
    published_at: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != LIBRARY_SCHEMA_VERSION:
            raise NativeBackgroundLibraryError(
                f"receipt schema_version must be {LIBRARY_SCHEMA_VERSION}"
            )
        if not isinstance(self.package_id, str) or not self.package_id:
            raise NativeBackgroundLibraryError("receipt package_id must be non-empty")
        _require_digest(self.bundle_digest, field="receipt bundle_digest")
        _require_digest(self.payload_digest, field="receipt payload_digest")
        _require_non_negative_int(self.bundle_size, field="receipt bundle_size")
        _require_non_negative_int(self.payload_size, field="receipt payload_size")
        _require_timestamp(self.published_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "bundle_digest": self.bundle_digest,
            "payload_digest": self.payload_digest,
            "bundle_size": self.bundle_size,
            "payload_size": self.payload_size,
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> "NativeBackgroundPublishReceipt":
        if not isinstance(value, dict) or set(value) != _RECEIPT_FIELDS:
            raise NativeBackgroundLibraryError(
                "native bundle receipt must contain exactly the frozen v1 fields"
            )
        return cls(**value)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ResolvedNativeBackground:
    """Revalidated immutable bundle and shared payload object."""

    receipt: NativeBackgroundPublishReceipt
    verified: Any
    payload: bytes
    bundle_path: Path
    payload_path: Path

    @property
    def bundle_digest(self) -> str:
        return self.receipt.bundle_digest

    @property
    def payload_digest(self) -> str:
        return self.receipt.payload_digest

    @property
    def bundle(self) -> bytes:
        return self.verified.raw

class NativeBackgroundLibrary:
    """Publish and resolve native bundles beneath one target-owned root."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        # Keep the lexical root so a caller-supplied symlink cannot be hidden by
        # eager resolution before the safety checks below.
        self.root = Path(root).expanduser().absolute()
        self.bundles_directory = self.root / BUNDLES_DIRECTORY
        self.payloads_directory = self.root / PAYLOADS_DIRECTORY
        self.lock_path = self.root / LOCK_FILENAME
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._thread_lock = threading.RLock()

    def _safe_child(self, parent: Path, digest: str, *, suffix: str = "") -> Path:
        safe_digest = _require_digest(digest, field="managed digest")
        candidate = parent / f"{safe_digest}{suffix}"
        try:
            candidate.resolve(strict=False).relative_to(parent.resolve(strict=False))
        except (OSError, RuntimeError, ValueError) as exc:
            raise NativeBackgroundLibraryError(
                "managed digest resolves outside the native library"
            ) from exc
        return candidate

    def _bundle_directory(self, bundle_digest: str) -> Path:
        return self._safe_child(self.bundles_directory, bundle_digest)

    def _payload_path(self, payload_digest: str) -> Path:
        return self._safe_child(
            self.payloads_directory, payload_digest, suffix=".so"
        )

    def _ensure_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise NativeBackgroundLibraryError(
                "native library root must be a real directory"
            )
        for directory in (self.bundles_directory, self.payloads_directory):
            existed = directory.exists()
            if directory.is_symlink():
                raise NativeBackgroundLibraryError(
                    f"native library path must not be a symbolic link: {directory.name}"
                )
            directory.mkdir(exist_ok=True)
            if not directory.is_dir():
                raise NativeBackgroundLibraryError(
                    f"native library path must be a directory: {directory.name}"
                )
            if not existed:
                _fsync_directory(self.root)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            self._ensure_directories()
            descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise NativeBackgroundLibraryError(
                        "native library lock must be a regular file"
                    )
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    @staticmethod
    def _write_immutable(path: Path, payload: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fchmod(stream.fileno(), 0o444)
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)

    def _publish_payload(self, digest: str, payload: bytes) -> tuple[Path, bool]:
        destination = self._payload_path(digest)
        if os.path.lexists(destination):
            if not _is_immutable_regular_file(destination):
                raise NativeBackgroundLibraryError(
                    "managed native payload must be an immutable regular file"
                )
            if destination.read_bytes() != payload:
                raise NativeBackgroundLibraryError(
                    "managed native payload conflicts with its content digest"
                )
            return destination, False
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}.", suffix=".tmp", dir=self.payloads_directory
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fchmod(stream.fileno(), 0o444)
                os.fsync(stream.fileno())
            # The hard-link claim is atomic and never replaces a destination
            # created after the initial fail-closed existence check.
            os.link(temporary, destination, follow_symlinks=False)
            _fsync_directory(self.payloads_directory)
        except FileExistsError as exc:
            raise NativeBackgroundLibraryError(
                "managed native payload appeared during publication"
            ) from exc
        except OSError as exc:
            raise NativeBackgroundLibraryError(
                f"failed to publish native payload {digest}: {exc}"
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)
        return destination, True

    def publish(self, source: bytes | Path) -> NativeBackgroundPublishReceipt:
        """Validate and atomically publish a complete native bundle."""

        verified, payload, package_id = _inspect_bundle(source)
        bundle_digest = verified.bundle_digest
        payload_digest = verified.payload_digest
        expected = NativeBackgroundPublishReceipt(
            schema_version=LIBRARY_SCHEMA_VERSION,
            package_id=package_id,
            bundle_digest=bundle_digest,
            payload_digest=payload_digest,
            bundle_size=len(verified.raw),
            payload_size=len(payload),
            published_at=_published_at(self._clock),
        )

        with self._locked():
            destination = self._bundle_directory(bundle_digest)
            if os.path.lexists(destination):
                return self._resolve_unlocked(bundle_digest).receipt

            payload_path, payload_created = self._publish_payload(
                payload_digest, payload
            )
            temporary: Path | None = None
            try:
                temporary = Path(
                    tempfile.mkdtemp(
                        prefix=f".{bundle_digest}.",
                        suffix=".tmp",
                        dir=self.bundles_directory,
                    )
                )
                self._write_immutable(temporary / BUNDLE_FILENAME, verified.raw)
                receipt_bytes = (
                    json.dumps(
                        expected.to_dict(), sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                    + b"\n"
                )
                self._write_immutable(temporary / RECEIPT_FILENAME, receipt_bytes)
                _fsync_directory(temporary)
                temporary.chmod(0o555)
                _fsync_directory(temporary)
                os.rename(temporary, destination)
                _fsync_directory(self.bundles_directory)
            except OSError as exc:
                if payload_created:
                    payload_path.unlink(missing_ok=True)
                    _fsync_directory(self.payloads_directory)
                raise NativeBackgroundLibraryError(
                    f"failed to publish native background {bundle_digest}: {exc}"
                ) from exc
            finally:
                if temporary is not None and temporary.exists():
                    temporary.chmod(0o700)
                    shutil.rmtree(temporary)

            resolved = self._resolve_unlocked(bundle_digest)
            if resolved.receipt != expected or resolved.bundle != verified.raw:
                raise NativeBackgroundLibraryError(
                    "published native background does not match validated input"
                )
            return resolved.receipt

    @staticmethod
    def _read_receipt(path: Path) -> NativeBackgroundPublishReceipt:
        if not _is_immutable_regular_file(path):
            raise NativeBackgroundLibraryError(
                "native bundle receipt must be an immutable regular file"
            )
        try:
            value = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_object,
            )
        except NativeBackgroundLibraryError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise NativeBackgroundLibraryError(
                f"native bundle receipt is unreadable: {exc}"
            ) from exc
        return NativeBackgroundPublishReceipt.from_dict(value)

    def _resolve_unlocked(self, bundle_digest: str) -> ResolvedNativeBackground:
        entry = self._bundle_directory(bundle_digest)
        try:
            metadata = entry.lstat()
        except FileNotFoundError as exc:
            raise NativeBackgroundNotFoundError(
                f"native background is not published: {bundle_digest}"
            ) from exc
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o222:
            raise NativeBackgroundLibraryError(
                "managed native bundle entry must be an immutable real directory"
            )
        if {path.name for path in entry.iterdir()} != {
            BUNDLE_FILENAME,
            RECEIPT_FILENAME,
        }:
            raise NativeBackgroundLibraryError(
                "managed native bundle entry contains missing or unexpected files"
            )

        receipt = self._read_receipt(entry / RECEIPT_FILENAME)
        if receipt.bundle_digest != bundle_digest:
            raise NativeBackgroundLibraryError(
                "managed native receipt digest does not match its directory"
            )
        bundle_path = entry / BUNDLE_FILENAME
        if not _is_immutable_regular_file(bundle_path):
            raise NativeBackgroundLibraryError(
                "managed native bundle must be an immutable regular file"
            )
        raw = bundle_path.read_bytes()
        verified, payload, package_id = _inspect_bundle(raw)
        if (
            verified.bundle_digest != receipt.bundle_digest
            or verified.payload_digest != receipt.payload_digest
            or package_id != receipt.package_id
            or len(raw) != receipt.bundle_size
            or len(payload) != receipt.payload_size
        ):
            raise NativeBackgroundLibraryError(
                "managed native bundle does not match its immutable receipt"
            )

        payload_path = self._payload_path(receipt.payload_digest)
        if not _is_immutable_regular_file(payload_path):
            raise NativeBackgroundLibraryError(
                "managed native payload object must be an immutable regular file"
            )
        object_bytes = payload_path.read_bytes()
        if object_bytes != payload or _sha256(object_bytes) != receipt.payload_digest:
            raise NativeBackgroundLibraryError(
                "managed native payload object is corrupt or conflicts with its bundle"
            )
        return ResolvedNativeBackground(
            receipt=receipt,
            verified=verified,
            payload=payload,
            bundle_path=bundle_path,
            payload_path=payload_path,
        )

    def resolve(self, bundle_digest: str) -> ResolvedNativeBackground:
        """Revalidate a managed bundle, receipt, and shared payload object."""

        safe_digest = _require_digest(bundle_digest, field="bundle digest")
        if not self.root.exists():
            raise NativeBackgroundNotFoundError(
                f"native background is not published: {safe_digest}"
            )
        with self._locked():
            return self._resolve_unlocked(safe_digest)

    def list(self) -> tuple[ResolvedNativeBackground, ...]:
        """Return every revalidated managed bundle in deterministic order.

        Catalog discovery never trusts directory names or receipts alone. A
        corrupt entry therefore fails the complete listing instead of quietly
        presenting a partial library whose apparent "latest" package could
        differ between processes.
        """

        if not self.root.exists():
            return ()
        with self._locked():
            entries = []
            for path in sorted(self.bundles_directory.iterdir(), key=lambda item: item.name):
                if path.name.startswith("."):
                    continue
                digest = _require_digest(path.name, field="managed bundle directory")
                entries.append(self._resolve_unlocked(digest))
            return tuple(entries)

    def resolve_package(
        self, package_id: str, *, bundle_digest: str | None = None
    ) -> ResolvedNativeBackground:
        """Resolve an exact package build, or its newest published build."""

        if not isinstance(package_id, str) or not package_id:
            raise NativeBackgroundLibraryError("native package_id must be non-empty")
        if bundle_digest is not None:
            resolved = self.resolve(bundle_digest)
            if resolved.receipt.package_id != package_id:
                raise NativeBackgroundNotFoundError(
                    f"native bundle {bundle_digest} does not provide {package_id}"
                )
            return resolved
        matches = [
            resolved for resolved in self.list()
            if resolved.receipt.package_id == package_id
        ]
        if not matches:
            raise NativeBackgroundNotFoundError(
                f"native background package is not published: {package_id}"
            )
        return max(
            matches,
            key=lambda resolved: (
                _parse_utc_timestamp(resolved.receipt.published_at),
                resolved.receipt.bundle_digest,
            ),
        )


__all__ = [
    "BUNDLE_FILENAME",
    "BUNDLES_DIRECTORY",
    "LIBRARY_SCHEMA_VERSION",
    "NativeBackgroundLibrary",
    "NativeBackgroundLibraryError",
    "NativeBackgroundNotFoundError",
    "NativeBackgroundPublishReceipt",
    "PAYLOAD_FILENAME",
    "PAYLOADS_DIRECTORY",
    "RECEIPT_FILENAME",
    "ResolvedNativeBackground",
]
