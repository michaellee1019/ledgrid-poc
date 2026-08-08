"""Pi-authoritative persistent firmware-animation library."""

from __future__ import annotations

import json
import os
import tempfile
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import ActivePackageError, PackageValidationError
from .manifest import PACKAGE_ID_RE, canonical_json
from .package import ReceiverVerificationEnvelope, VerifiedPackage, inspect_package


@dataclass(frozen=True)
class InstalledPackage:
    package_id: str
    name: str
    version: str
    kind: str
    digest: str
    manifest: dict[str, Any]
    package_path: Path
    installed_at: str


class FirmwareAnimationLibrary:
    def __init__(
        self,
        root: str | Path,
        trusted_keys: Mapping[str, bytes | str | Path],
        *,
        active_id_provider: Callable[[], str | None] | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.root = Path(root)
        self.trusted_keys = dict(trusted_keys)
        self.active_id_provider = active_id_provider or (lambda: None)
        self._fault_injector = fault_injector or (lambda _stage: None)
        self.objects_dir = self.root / "objects"
        self.metadata_dir = self.root / "packages"
        self.staging_dir = self.root / ".staging"
        for directory in (self.root, self.objects_dir, self.metadata_dir, self.staging_dir):
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise PackageValidationError(f"library path is not a safe directory: {directory}")
        self.lock_path = self.root / ".library.lock"
        self.recover()

    @contextmanager
    def _locked(self, *, exclusive: bool):
        """Serialize publishers/recovery and keep readers on a stable snapshot."""
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(
                descriptor,
                fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
            )
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _metadata_path(self, package_id: str) -> Path:
        if not PACKAGE_ID_RE.fullmatch(package_id):
            raise PackageValidationError("invalid package id")
        return self.metadata_dir / f"{package_id}.json"

    def _object_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise PackageValidationError("invalid content digest")
        return self.objects_dir / f"{digest}.lga"

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_atomic(self, destination: Path, data: bytes) -> None:
        with tempfile.NamedTemporaryFile(dir=self.staging_dir, prefix="install-", suffix=".part", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        self._fsync_directory(destination.parent)

    def install(self, lga_path: str | Path | bytes) -> InstalledPackage:
        with self._locked(exclusive=True):
            self._recover_unlocked()
            package = inspect_package(lga_path, self.trusted_keys)
            previous = self._get_unlocked(package.manifest["id"])
            if previous is not None and previous.digest != package.digest:
                active = self.active_id_provider()
                if active in {previous.package_id, previous.digest}:
                    raise ActivePackageError(
                        f"cannot replace active package: {previous.package_id}"
                    )
            object_path = self._object_path(package.digest)
            if not object_path.exists():
                self._write_atomic(object_path, package.raw)
            self._fault_injector("after_object")
            manifest = package.manifest
            installed_at = datetime.now(timezone.utc).isoformat()
            metadata = {
                "digest": package.digest,
                "installed_at": installed_at,
                "manifest": manifest,
            }
            self._fault_injector("before_publish")
            self._write_atomic(self._metadata_path(manifest["id"]), canonical_json(metadata))
            installed = InstalledPackage(
                manifest["id"], manifest["name"], manifest["version"], manifest["kind"],
                package.digest, manifest, object_path, installed_at,
            )
            # Metadata is the sole visibility point. Only after its atomic publish
            # may an object from the prior version be reclaimed.
            if previous is not None and previous.digest != installed.digest:
                self._delete_object_if_unreferenced(previous.digest)
            return installed

    def _load_metadata(self, path: Path) -> InstalledPackage:
        try:
            raw = path.read_bytes()
            metadata = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageValidationError(f"corrupt library metadata: {path.name}") from exc
        if not isinstance(metadata, dict) or canonical_json(metadata) != raw or set(metadata) != {"digest", "installed_at", "manifest"}:
            raise PackageValidationError(f"non-canonical library metadata: {path.name}")
        manifest = metadata["manifest"]
        package_id = path.stem
        if not isinstance(manifest, dict) or manifest.get("id") != package_id:
            raise PackageValidationError(f"library metadata id mismatch: {path.name}")
        if not isinstance(metadata["digest"], str) or not isinstance(metadata["installed_at"], str):
            raise PackageValidationError(f"library metadata types are invalid: {path.name}")
        if any(not isinstance(manifest.get(field), str) for field in ("name", "version", "kind")):
            raise PackageValidationError(f"library manifest summary is invalid: {path.name}")
        object_path = self._object_path(metadata["digest"])
        if not object_path.is_file() or object_path.is_symlink():
            raise PackageValidationError(f"library object is missing: {metadata['digest']}")
        return InstalledPackage(
            package_id, manifest["name"], manifest["version"], manifest["kind"],
            metadata["digest"], manifest, object_path, metadata["installed_at"],
        )

    def _list_unlocked(self) -> list[InstalledPackage]:
        installed: list[InstalledPackage] = []
        for path in sorted(self.metadata_dir.glob("*.json")):
            if path.is_symlink():
                continue
            try:
                installed.append(self._load_metadata(path))
            except PackageValidationError:
                # Retain the exact file for diagnosis, but do not let one bad
                # entry take down the dashboard library listing.
                continue
        return installed

    def list(self) -> list[InstalledPackage]:
        with self._locked(exclusive=False):
            return self._list_unlocked()

    def _get_unlocked(self, package_id: str) -> InstalledPackage | None:
        path = self._metadata_path(package_id)
        if not path.exists():
            return None
        if path.is_symlink():
            return None
        try:
            return self._load_metadata(path)
        except PackageValidationError:
            return None

    def get(self, package_id: str) -> InstalledPackage | None:
        with self._locked(exclusive=False):
            return self._get_unlocked(package_id)

    def _verified_unlocked(self, package_id: str) -> VerifiedPackage:
        installed = self._get_unlocked(package_id)
        if installed is None:
            raise KeyError(package_id)
        package = inspect_package(installed.package_path, self.trusted_keys)
        if package.digest != installed.digest or package.manifest["id"] != package_id:
            raise PackageValidationError("installed object does not match library metadata")
        return package

    def verified(self, package_id: str) -> VerifiedPackage:
        with self._locked(exclusive=False):
            return self._verified_unlocked(package_id)

    def read_payload(self, package_id: str, device_index: int) -> bytes:
        return self.verified(package_id).payload_for_device(device_index)

    def verification_envelope(self, package_id: str, device_index: int) -> ReceiverVerificationEnvelope:
        return self.verified(package_id).verification_envelope(device_index)

    def delete(self, package_id: str) -> None:
        with self._locked(exclusive=True):
            installed = self._get_unlocked(package_id)
            if installed is None:
                return
            active = self.active_id_provider()
            if active in {package_id, installed.digest}:
                raise ActivePackageError(f"cannot delete active package: {package_id}")
            self._metadata_path(package_id).unlink()
            self._fsync_directory(self.metadata_dir)
            self._delete_object_if_unreferenced(installed.digest)

    def _reference_state(self) -> tuple[set[str], bool]:
        referenced: set[str] = set()
        corrupt = False
        for path in self.metadata_dir.glob("*.json"):
            if path.is_symlink():
                corrupt = True
                continue
            try:
                referenced.add(self._load_metadata(path).digest)
            except PackageValidationError:
                corrupt = True
        return referenced, corrupt

    def _delete_object_if_unreferenced(self, digest: str) -> None:
        referenced, corrupt = self._reference_state()
        # A corrupt entry might be the only surviving pointer to an object. In
        # that case retaining storage is safer than destroying diagnostic data.
        if corrupt or digest in referenced:
            return
        try:
            self._object_path(digest).unlink()
        except FileNotFoundError:
            return
        self._fsync_directory(self.objects_dir)

    def _recover_unlocked(self) -> None:
        for partial in self.staging_dir.glob("*.part"):
            if partial.is_file() or partial.is_symlink():
                partial.unlink(missing_ok=True)
        referenced, corrupt = self._reference_state()
        if corrupt:
            return
        for object_path in self.objects_dir.glob("*.lga"):
            if object_path.stem not in referenced:
                object_path.unlink(missing_ok=True)

    def recover(self) -> None:
        with self._locked(exclusive=True):
            self._recover_unlocked()
