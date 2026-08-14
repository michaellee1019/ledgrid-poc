"""Immutable Pi-authoritative storage for canonical installation profiles.

The library accepts only complete, already-encoded global LGIP v1 artifacts.
Artifacts are addressed by the content digest embedded in the LGIP header and
are published as one atomically renamed directory.  Receiver transport routes
and host-frame strip direction remain outside profile semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from animation.core.installation_profile import (
    FORMAT_VERSION,
    GLOBAL_STRIP_COUNT,
    InstallationProfile,
    InstallationProfileError,
    decode_installation_profile,
    encode_installation_profile,
)
from animation.core.installation_profile_topology import (
    IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    InstallationProfileTopology,
    slice_installation_profile,
)


LIBRARY_SCHEMA_VERSION = 1
PROFILES_DIRECTORY = "profiles"
PROFILE_FILENAME = "profile.bin"
RECEIPT_FILENAME = "receipt.json"

_CONTENT_DIGEST_START = 68
_CONTENT_DIGEST_END = 100
_PROFILE_ID_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "profile_format_version",
        "id",
        "content_digest",
        "calibration_digest",
        "file_sha256",
        "size",
        "published_at",
    }
)


class InstallationProfileLibraryError(RuntimeError):
    """A managed profile entry is invalid, unsafe, corrupt, or unavailable."""


class InstallationProfileNotFoundError(InstallationProfileLibraryError):
    """The requested managed profile ID is not present in this library."""


def _require_lower_hex_digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _PROFILE_ID_PATTERN.fullmatch(value) is None:
        raise InstallationProfileLibraryError(
            f"{field} must be exactly 64 lowercase hexadecimal characters"
        )
    return value


def _require_exact_int(value: object, *, field: str, expected: int) -> int:
    if type(value) is not int or value != expected:
        raise InstallationProfileLibraryError(f"{field} must be {expected}")
    return value


def _require_non_negative_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise InstallationProfileLibraryError(
            f"{field} must be a non-negative integer"
        )
    return value


def _require_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise InstallationProfileLibraryError(
            "receipt published_at must be a UTC ISO-8601 timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise InstallationProfileLibraryError(
            "receipt published_at must be a UTC ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise InstallationProfileLibraryError(
            "receipt published_at must be a UTC ISO-8601 timestamp"
        )
    return value


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InstallationProfileLibraryError(
                f"receipt contains duplicate JSON field {key!r}"
            )
        result[key] = value
    return result


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _published_at(clock: Callable[[], datetime]) -> str:
    current = clock()
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise InstallationProfileLibraryError(
            "library clock must return a timezone-aware datetime"
        )
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_regular_immutable_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not metadata.st_mode & 0o222


@dataclass(frozen=True)
class InstallationProfilePublishReceipt:
    """Small immutable description of one published profile artifact.

    ``published_at`` records the runtime event but does not participate in the
    profile ID.  The embedded content digest is the sole artifact identity.
    """

    schema_version: int
    profile_format_version: int
    id: str
    content_digest: str
    calibration_digest: str
    file_sha256: str
    size: int
    published_at: str

    def __post_init__(self) -> None:
        _require_exact_int(
            self.schema_version,
            field="receipt schema_version",
            expected=LIBRARY_SCHEMA_VERSION,
        )
        _require_exact_int(
            self.profile_format_version,
            field="receipt profile_format_version",
            expected=FORMAT_VERSION,
        )
        profile_id = _require_lower_hex_digest(self.id, field="receipt id")
        content_digest = _require_lower_hex_digest(
            self.content_digest, field="receipt content_digest"
        )
        if content_digest != profile_id:
            raise InstallationProfileLibraryError(
                "receipt content_digest must equal its managed ID"
            )
        _require_lower_hex_digest(
            self.calibration_digest, field="receipt calibration_digest"
        )
        _require_lower_hex_digest(self.file_sha256, field="receipt file_sha256")
        _require_non_negative_int(self.size, field="receipt size")
        _require_timestamp(self.published_at)

    def to_dict(self) -> dict[str, object]:
        """Return the exact stable on-disk receipt schema."""

        return {
            "schema_version": self.schema_version,
            "profile_format_version": self.profile_format_version,
            "id": self.id,
            "content_digest": self.content_digest,
            "calibration_digest": self.calibration_digest,
            "file_sha256": self.file_sha256,
            "size": self.size,
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> "InstallationProfilePublishReceipt":
        """Strictly parse one receipt without accepting extension fields."""

        if not isinstance(value, dict) or set(value) != _RECEIPT_FIELDS:
            raise InstallationProfileLibraryError(
                "receipt must contain exactly the frozen v1 fields"
            )
        return cls(
            schema_version=value["schema_version"],
            profile_format_version=value["profile_format_version"],
            id=value["id"],
            content_digest=value["content_digest"],
            calibration_digest=value["calibration_digest"],
            file_sha256=value["file_sha256"],
            size=value["size"],
            published_at=value["published_at"],
        )  # type: ignore[arg-type]


@dataclass(frozen=True)
class ResolvedInstallationProfile:
    """Immutable artifact bytes and global/receiver semantic profile views."""

    receipt: InstallationProfilePublishReceipt
    encoded: bytes
    global_profile: InstallationProfile
    topology: InstallationProfileTopology
    receiver_profiles: Mapping[int, InstallationProfile]

    @property
    def id(self) -> str:
        """Return the frozen embedded content-digest identity."""

        return self.receipt.id

    @property
    def content_digest(self) -> str:
        """Return the embedded LGIP content digest as lowercase hexadecimal."""

        return self.receipt.content_digest


@dataclass(frozen=True)
class _CachedArtifact:
    encoded: bytes
    profile: InstallationProfile


SemanticTopologyKey = tuple[
    tuple[int, int, int, int], tuple[bool, bool, bool, bool]
]


class InstallationProfileLibrary:
    """Publish and resolve content-addressed profiles below one managed root.

    Production supplies the target-owned ``installation_profile_library`` path
    as ``root``.  The object does not create or mutate that path until a fully
    decoded, canonical global LGIP artifact is ready to publish.
    """

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root).resolve(strict=False)
        self.profiles_directory = self.root / PROFILES_DIRECTORY
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._artifact_cache: dict[str, _CachedArtifact] = {}
        self._receiver_cache: dict[
            tuple[str, SemanticTopologyKey], Mapping[int, InstallationProfile]
        ] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _validate_canonical_global(
        encoded: bytes,
    ) -> tuple[str, InstallationProfile]:
        if not isinstance(encoded, bytes):
            raise InstallationProfileLibraryError(
                "published installation profile must be immutable bytes"
            )
        try:
            profile = decode_installation_profile(encoded)
        except InstallationProfileError as exc:
            raise InstallationProfileLibraryError(
                f"published installation profile is invalid: {exc}"
            ) from exc
        if (
            profile.global_strip_count != GLOBAL_STRIP_COUNT
            or profile.strip_origin != 0
            or profile.strip_count != GLOBAL_STRIP_COUNT
            or profile.reversed_strip_order
        ):
            raise InstallationProfileLibraryError(
                "published installation profile must be the canonical "
                "non-reversed global view"
            )
        if encode_installation_profile(profile) != encoded:
            raise InstallationProfileLibraryError(
                "published installation profile is not canonically encoded"
            )
        profile_id = encoded[_CONTENT_DIGEST_START:_CONTENT_DIGEST_END].hex()
        _require_lower_hex_digest(profile_id, field="embedded content digest")
        return profile_id, profile

    @staticmethod
    def _semantic_topology_key(
        topology: InstallationProfileTopology,
    ) -> SemanticTopologyKey:
        return (
            topology.physical_lane_order,
            topology.reverse_native_strips_by_logical_receiver,
        )

    def _entry_directory(self, profile_id: str) -> Path:
        safe_id = _require_lower_hex_digest(profile_id, field="profile ID")
        candidate = self.profiles_directory / safe_id
        try:
            candidate.resolve(strict=False).relative_to(self.profiles_directory)
        except (OSError, RuntimeError, ValueError) as exc:
            raise InstallationProfileLibraryError(
                "profile ID resolves outside the managed library"
            ) from exc
        return candidate

    def artifact_path(self, profile_id: str) -> Path:
        """Validate a managed artifact, then return its contained filesystem path."""

        resolved = self.resolve(profile_id)
        return self._entry_directory(resolved.id) / PROFILE_FILENAME

    def _ensure_publish_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise InstallationProfileLibraryError(
                "installation-profile library root must be a real directory"
            )
        profiles_existed = self.profiles_directory.exists()
        if self.profiles_directory.is_symlink():
            raise InstallationProfileLibraryError(
                "managed profiles directory must not be a symbolic link"
            )
        self.profiles_directory.mkdir(exist_ok=True)
        if not self.profiles_directory.is_dir():
            raise InstallationProfileLibraryError(
                "managed profiles path must be a directory"
            )
        if not profiles_existed:
            _fsync_directory(self.root)

    @staticmethod
    def _receipt_for(
        profile_id: str,
        profile: InstallationProfile,
        encoded: bytes,
        published_at: str,
    ) -> InstallationProfilePublishReceipt:
        return InstallationProfilePublishReceipt(
            schema_version=LIBRARY_SCHEMA_VERSION,
            profile_format_version=FORMAT_VERSION,
            id=profile_id,
            content_digest=profile_id,
            calibration_digest=profile.calibration_digest.hex(),
            file_sha256=_sha256(encoded),
            size=len(encoded),
            published_at=published_at,
        )

    @staticmethod
    def _write_file(path: Path, payload: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        path.chmod(0o444)

    @staticmethod
    def _cleanup_staging_directory(path: Path) -> None:
        if not path.exists():
            return
        path.chmod(0o700)
        shutil.rmtree(path)

    def publish(self, encoded: bytes) -> InstallationProfilePublishReceipt:
        """Validate then atomically publish one canonical global LGIP artifact.

        Re-publishing identical bytes is idempotent and returns the original
        receipt.  An existing missing, conflicting, or corrupt entry is never
        repaired implicitly; it fails closed for operator inspection.
        """

        # Decoding and canonical re-encoding deliberately happen before mkdir,
        # temporary-file creation, or any other filesystem mutation.
        profile_id, profile = self._validate_canonical_global(encoded)

        with self._lock:
            destination = self._entry_directory(profile_id)
            if os.path.lexists(destination):
                existing = self._resolve_uncached(profile_id)
                if existing.encoded != encoded:
                    raise InstallationProfileLibraryError(
                        "managed profile entry conflicts with published bytes"
                    )
                return existing.receipt

            expected = self._receipt_for(
                profile_id,
                profile,
                encoded,
                _published_at(self._clock),
            )

            temporary: Path | None = None
            try:
                self._ensure_publish_directories()
                temporary = Path(
                    tempfile.mkdtemp(
                        prefix=f".{profile_id}.",
                        suffix=".tmp",
                        dir=self.profiles_directory,
                    )
                )
                self._write_file(temporary / PROFILE_FILENAME, encoded)
                receipt_payload = (
                    json.dumps(
                        expected.to_dict(), sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                    + b"\n"
                )
                self._write_file(temporary / RECEIPT_FILENAME, receipt_payload)
                _fsync_directory(temporary)
                temporary.chmod(0o555)
                try:
                    os.rename(temporary, destination)
                except OSError:
                    if os.path.lexists(destination):
                        existing = self._resolve_uncached(profile_id)
                        if existing.encoded == encoded:
                            return existing.receipt
                    raise
                _fsync_directory(self.profiles_directory)
            except OSError as exc:
                raise InstallationProfileLibraryError(
                    f"failed to publish installation profile {profile_id}: {exc}"
                ) from exc
            finally:
                if temporary is not None:
                    self._cleanup_staging_directory(temporary)

            resolved = self._resolve_uncached(profile_id)
            if resolved.receipt != expected or resolved.encoded != encoded:
                raise InstallationProfileLibraryError(
                    "atomically published profile does not match its validated input"
                )
            return resolved.receipt

    @staticmethod
    def _read_receipt(path: Path) -> InstallationProfilePublishReceipt:
        if not _is_regular_immutable_file(path):
            raise InstallationProfileLibraryError(
                "managed profile receipt must be an immutable regular file"
            )
        try:
            raw = path.read_text(encoding="utf-8")
            value = json.loads(raw, object_pairs_hook=_unique_json_object)
        except InstallationProfileLibraryError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InstallationProfileLibraryError(
                f"managed profile receipt is unreadable: {exc}"
            ) from exc
        return InstallationProfilePublishReceipt.from_dict(value)

    def _resolve_uncached(self, profile_id: str) -> ResolvedInstallationProfile:
        entry = self._entry_directory(profile_id)
        try:
            entry_metadata = entry.lstat()
        except FileNotFoundError as exc:
            raise InstallationProfileNotFoundError(
                f"installation profile is not published: {profile_id}"
            ) from exc
        except OSError as exc:
            raise InstallationProfileLibraryError(
                f"failed to inspect managed profile entry: {exc}"
            ) from exc
        if not stat.S_ISDIR(entry_metadata.st_mode) or entry_metadata.st_mode & 0o222:
            raise InstallationProfileLibraryError(
                "managed profile entry must be an immutable real directory"
            )
        try:
            children = {child.name for child in entry.iterdir()}
        except OSError as exc:
            raise InstallationProfileLibraryError(
                f"failed to inspect managed profile entry: {exc}"
            ) from exc
        if children != {PROFILE_FILENAME, RECEIPT_FILENAME}:
            raise InstallationProfileLibraryError(
                "managed profile entry contains missing or unexpected files"
            )

        receipt = self._read_receipt(entry / RECEIPT_FILENAME)
        if receipt.id != profile_id:
            raise InstallationProfileLibraryError(
                "managed profile receipt ID does not match its directory"
            )
        profile_path = entry / PROFILE_FILENAME
        if not _is_regular_immutable_file(profile_path):
            raise InstallationProfileLibraryError(
                "managed profile artifact must be an immutable regular file"
            )
        try:
            encoded = profile_path.read_bytes()
        except OSError as exc:
            raise InstallationProfileLibraryError(
                f"managed profile artifact is unreadable: {exc}"
            ) from exc
        if len(encoded) != receipt.size or _sha256(encoded) != receipt.file_sha256:
            raise InstallationProfileLibraryError(
                "managed profile artifact size or file SHA-256 is corrupt"
            )

        cached = self._artifact_cache.get(profile_id)
        if cached is not None and cached.encoded == encoded:
            profile = cached.profile
            embedded_id = encoded[_CONTENT_DIGEST_START:_CONTENT_DIGEST_END].hex()
        else:
            embedded_id, profile = self._validate_canonical_global(encoded)
            self._artifact_cache[profile_id] = _CachedArtifact(encoded, profile)
        if embedded_id != profile_id or receipt.content_digest != embedded_id:
            raise InstallationProfileLibraryError(
                "managed profile embedded content digest does not match its ID"
            )
        if receipt.calibration_digest != profile.calibration_digest.hex():
            raise InstallationProfileLibraryError(
                "managed profile calibration digest does not match its receipt"
            )
        if receipt.profile_format_version != FORMAT_VERSION:
            raise InstallationProfileLibraryError(
                "managed profile format version does not match the decoder"
            )

        identity_key = self._semantic_topology_key(
            IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
        )
        cache_key = (profile_id, identity_key)
        receiver_profiles = self._receiver_cache.get(cache_key)
        if receiver_profiles is None:
            receiver_profiles = MappingProxyType(
                slice_installation_profile(
                    profile, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
                )
            )
            self._receiver_cache[cache_key] = receiver_profiles
        return ResolvedInstallationProfile(
            receipt=receipt,
            encoded=encoded,
            global_profile=profile,
            topology=IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
            receiver_profiles=receiver_profiles,
        )

    def resolve(
        self,
        profile_id: str,
        topology: InstallationProfileTopology = IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    ) -> ResolvedInstallationProfile:
        """Resolve a safe ID and topology to validated immutable semantic views.

        Artifact bytes and the receipt are read and validated on every call.
        Decoded views may be reused only when those bytes are exactly equal to a
        previously validated immutable artifact.  Slice cache identity includes
        content digest, physical lane order, and receiver-native direction;
        transport routes and host-frame direction are deliberately inert.
        """

        safe_id = _require_lower_hex_digest(profile_id, field="profile ID")
        if not isinstance(topology, InstallationProfileTopology):
            raise TypeError("topology must be an InstallationProfileTopology")
        with self._lock:
            base = self._resolve_uncached(safe_id)
            semantic_key = self._semantic_topology_key(topology)
            cache_key = (safe_id, semantic_key)
            receiver_profiles = self._receiver_cache.get(cache_key)
            if receiver_profiles is None:
                receiver_profiles = MappingProxyType(
                    slice_installation_profile(base.global_profile, topology)
                )
                self._receiver_cache[cache_key] = receiver_profiles
            return ResolvedInstallationProfile(
                receipt=base.receipt,
                encoded=base.encoded,
                global_profile=base.global_profile,
                topology=topology,
                receiver_profiles=receiver_profiles,
            )


__all__ = [
    "InstallationProfileLibrary",
    "InstallationProfileLibraryError",
    "InstallationProfileNotFoundError",
    "InstallationProfilePublishReceipt",
    "LIBRARY_SCHEMA_VERSION",
    "PROFILE_FILENAME",
    "PROFILES_DIRECTORY",
    "RECEIPT_FILENAME",
    "ResolvedInstallationProfile",
]
