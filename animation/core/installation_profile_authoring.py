"""Revisioned authoring for managed installation-profile geometry.

The immutable :class:`InstallationProfileLibrary` remains the only publication
authority.  This module stores one restart-safe editable draft per immutable
source digest, validates the canonical 32x138 authored surface, and compiles a
complete 33x138 LGIP artifact without selecting it for the live wall.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterator, Mapping, Sequence
import uuid

import numpy as np

from animation.core.installation_profile import (
    CALIBRATION_PIXEL_COUNT,
    CALIBRATION_STRIP_COUNT,
    GLOBAL_PIXEL_COUNT,
    GLOBAL_STRIP_COUNT,
    LEDS_PER_STRIP,
    InstallationProfile,
    encode_installation_profile,
)
from animation.core.installation_profile_library import (
    InstallationProfileLibrary,
    InstallationProfilePublishReceipt,
)
from animation.core.plant_awareness import (
    GLOBE_REGION_ORDER,
    _distance_and_normals,
    _inner_edge,
)
from animation.libraries.mask_effects import dilate_8


DRAFT_SCHEMA = "ledgrid.installation-profile-draft"
DRAFT_SCHEMA_VERSION = 1
DRAFTS_DIRECTORY = "drafts"
_LOCK_FILENAME = ".authoring.lock"
_DIGEST_LENGTH = 64


class InstallationProfileAuthoringError(ValueError):
    """A draft or managed authoring operation violates the frozen contract."""


class InstallationProfileDraftConflict(InstallationProfileAuthoringError):
    """The supplied expected revision no longer names the current draft."""

    def __init__(self, current_revision: str) -> None:
        super().__init__("installation-profile draft revision is stale")
        self.current_revision = current_revision


def _revision() -> str:
    return f"ipd-{uuid.uuid4().hex}"


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InstallationProfileAuthoringError(
            f"draft is not canonical JSON: {exc}"
        ) from exc


def _require_digest(value: object, label: str = "digest") -> str:
    if (
        not isinstance(value, str)
        or len(value) != _DIGEST_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise InstallationProfileAuthoringError(
            f"{label} must be exactly 64 lowercase hexadecimal characters"
        )
    return value


def _require_revision(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("ipd-")
        or len(value) != 36
        or any(character not in "0123456789abcdef" for character in value[4:])
    ):
        raise InstallationProfileAuthoringError("revision is not a valid opaque token")
    return value


def _validate_indices(value: object, label: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise InstallationProfileAuthoringError(f"{label} must be an array")
    result: list[int] = []
    previous = -1
    for position, index in enumerate(value):
        if type(index) is not int:
            raise InstallationProfileAuthoringError(
                f"{label}[{position}] must be an integer"
            )
        if index < 0 or index >= CALIBRATION_PIXEL_COUNT:
            raise InstallationProfileAuthoringError(
                f"{label}[{position}] is outside the 32x138 authored surface"
            )
        if index <= previous:
            raise InstallationProfileAuthoringError(
                f"{label} must be sorted, unique, and ascending"
            )
        result.append(index)
        previous = index
    return tuple(result)


def _draft_document(
    *,
    digest: str,
    revision: str,
    foliage: Sequence[int],
    globes: Mapping[str, Sequence[int]],
) -> dict[str, object]:
    return {
        "schema": DRAFT_SCHEMA,
        "schema_version": DRAFT_SCHEMA_VERSION,
        "digest": digest,
        "revision": revision,
        "led_info": {
            "strip_count": CALIBRATION_STRIP_COUNT,
            "leds_per_strip": LEDS_PER_STRIP,
            "total_leds": CALIBRATION_PIXEL_COUNT,
        },
        "masks": {
            "foliage": list(foliage),
            "globes": {
                name: list(globes[name]) for name in GLOBE_REGION_ORDER
            },
        },
    }


def validate_installation_profile_draft(
    value: object,
    *,
    expected_digest: str | None = None,
) -> dict[str, object]:
    """Strictly validate and normalize one complete public draft document."""

    if not isinstance(value, dict):
        raise InstallationProfileAuthoringError("draft must be an object")
    required = {"schema", "schema_version", "digest", "revision", "led_info", "masks"}
    if set(value) != required:
        raise InstallationProfileAuthoringError(
            "draft must contain exactly schema, schema_version, digest, revision, "
            "led_info, and masks"
        )
    if value.get("schema") != DRAFT_SCHEMA:
        raise InstallationProfileAuthoringError(f"draft.schema must be {DRAFT_SCHEMA!r}")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise InstallationProfileAuthoringError("draft.schema_version must be 1")
    digest = _require_digest(value.get("digest"))
    if expected_digest is not None and digest != _require_digest(expected_digest):
        raise InstallationProfileAuthoringError(
            "draft.digest does not match the immutable source profile"
        )
    revision = _require_revision(value.get("revision"))

    expected_geometry = {
        "strip_count": CALIBRATION_STRIP_COUNT,
        "leds_per_strip": LEDS_PER_STRIP,
        "total_leds": CALIBRATION_PIXEL_COUNT,
    }
    if value.get("led_info") != expected_geometry:
        raise InstallationProfileAuthoringError(
            "draft.led_info must be exactly the 32x138 authored geometry"
        )
    masks = value.get("masks")
    if not isinstance(masks, dict) or set(masks) != {"foliage", "globes"}:
        raise InstallationProfileAuthoringError(
            "draft.masks must contain exactly foliage and globes"
        )
    foliage = _validate_indices(masks.get("foliage"), "draft.masks.foliage")
    raw_globes = masks.get("globes")
    if not isinstance(raw_globes, dict):
        raise InstallationProfileAuthoringError("draft.masks.globes must be an object")
    # JSON object member order is not semantic and some clients sort keys.
    # Require the exact stable identities, then normalize them into the frozen
    # GLOBE_REGION_ORDER for storage, responses, and compilation.
    if set(raw_globes) != set(GLOBE_REGION_ORDER):
        raise InstallationProfileAuthoringError(
            "draft.masks.globes must use the stable seven-region identities"
        )
    globes = {
        name: _validate_indices(
            raw_globes[name], f"draft.masks.globes.{name}"
        )
        for name in GLOBE_REGION_ORDER
    }

    occupied = set(foliage)
    for name in GLOBE_REGION_ORDER:
        region = set(globes[name])
        overlap = occupied & region
        if overlap:
            raise InstallationProfileAuthoringError(
                f"semantic layers overlap at authored pixel {min(overlap)}"
            )
        occupied |= region
    return _draft_document(
        digest=digest,
        revision=revision,
        foliage=foliage,
        globes=globes,
    )


def _draft_from_profile(digest: str, profile: InstallationProfile) -> dict[str, object]:
    if profile.category.shape != (GLOBAL_STRIP_COUNT, LEDS_PER_STRIP):
        raise InstallationProfileAuthoringError(
            "immutable source profile does not use canonical 33x138 geometry"
        )
    if np.any(profile.category[CALIBRATION_STRIP_COUNT:]) or np.any(
        profile.globe_region[CALIBRATION_STRIP_COUNT:]
    ):
        raise InstallationProfileAuthoringError(
            "immutable source profile contains authored masks outside the 32x138 surface"
        )
    authored_category = profile.category[:CALIBRATION_STRIP_COUNT].ravel()
    authored_regions = profile.globe_region[:CALIBRATION_STRIP_COUNT].ravel()
    foliage = np.flatnonzero(authored_category == 1).tolist()
    globes = {
        name: np.flatnonzero(authored_regions == region_id).tolist()
        for region_id, name in enumerate(GLOBE_REGION_ORDER, start=1)
    }
    return _draft_document(
        digest=digest,
        revision=_revision(),
        foliage=foliage,
        globes=globes,
    )


def compile_installation_profile_draft(
    draft: object,
    *,
    clearance_radius: int,
) -> bytes:
    """Compile a validated 32x138 draft into one canonical 33x138 LGIP."""

    normalized = validate_installation_profile_draft(draft)
    if type(clearance_radius) is not int or not 0 <= clearance_radius <= 4:
        raise InstallationProfileAuthoringError(
            "clearance_radius must be an integer from 0 through 4"
        )
    masks = normalized["masks"]
    assert isinstance(masks, dict)
    foliage_indices = masks["foliage"]
    raw_globes = masks["globes"]
    assert isinstance(foliage_indices, list) and isinstance(raw_globes, dict)

    foliage = np.zeros(GLOBAL_PIXEL_COUNT, dtype=np.bool_)
    globes = np.zeros(GLOBAL_PIXEL_COUNT, dtype=np.bool_)
    globe_region = np.zeros(GLOBAL_PIXEL_COUNT, dtype=np.uint8)
    if foliage_indices:
        foliage[np.asarray(foliage_indices, dtype=np.intp)] = True
    for region_id, name in enumerate(GLOBE_REGION_ORDER, start=1):
        indices = raw_globes[name]
        if indices:
            positions = np.asarray(indices, dtype=np.intp)
            globes[positions] = True
            globe_region[positions] = region_id

    foliage = foliage.reshape(GLOBAL_STRIP_COUNT, LEDS_PER_STRIP)
    globes = globes.reshape(GLOBAL_STRIP_COUNT, LEDS_PER_STRIP)
    globe_region = globe_region.reshape(GLOBAL_STRIP_COUNT, LEDS_PER_STRIP)
    obstacle = foliage | globes
    clearance = obstacle.copy()
    for _ in range(clearance_radius):
        clearance = dilate_8(clearance)
    clearance[CALIBRATION_STRIP_COUNT:] = False
    distance, normal_x, normal_y = _distance_and_normals(obstacle)

    category = np.zeros(obstacle.shape, dtype=np.uint8)
    category[foliage] = 1
    category[globes] = 2
    calibration_digest = hashlib.sha256(
        _canonical_json(
            {
                "schema": DRAFT_SCHEMA,
                "schema_version": DRAFT_SCHEMA_VERSION,
                "led_info": normalized["led_info"],
                "masks": normalized["masks"],
            }
        )
    ).digest()

    def quantize(values: np.ndarray) -> np.ndarray:
        magnitude = np.floor(np.abs(values) * 127.0 + 0.5)
        return np.clip(np.sign(values) * magnitude, -127, 127).astype(np.int8)

    profile = InstallationProfile(
        global_strip_count=GLOBAL_STRIP_COUNT,
        leds_per_strip=LEDS_PER_STRIP,
        strip_origin=0,
        strip_count=GLOBAL_STRIP_COUNT,
        clearance_radius=clearance_radius,
        calibration_digest=calibration_digest,
        reversed_strip_order=False,
        category=category,
        clearance=clearance.astype(np.uint8),
        foliage_edge=_inner_edge(foliage).astype(np.uint8),
        globe_edge=_inner_edge(globes).astype(np.uint8),
        obstacle_edge=_inner_edge(obstacle).astype(np.uint8),
        globe_region=globe_region,
        distance=distance.astype(np.uint8),
        normal_x=quantize(normal_x),
        normal_y=quantize(normal_y),
    )
    return encode_installation_profile(profile)


class InstallationProfileAuthoring:
    """Durable compare-and-swap drafts over one immutable profile library."""

    def __init__(self, library: InstallationProfileLibrary, root: Path) -> None:
        if not isinstance(library, InstallationProfileLibrary):
            raise TypeError("library must be an InstallationProfileLibrary")
        self.library = library
        self.root = Path(root).resolve(strict=False)
        self.drafts_directory = self.root / DRAFTS_DIRECTORY
        self._thread_lock = threading.RLock()

    def _draft_path(self, digest: str) -> Path:
        return self.drafts_directory / f"{_require_digest(digest)}.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            self.root.mkdir(parents=True, exist_ok=True)
            self.drafts_directory.mkdir(exist_ok=True)
            lock_path = self.root / _LOCK_FILENAME
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    @staticmethod
    def _read(path: Path, digest: str) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InstallationProfileAuthoringError(
                f"managed draft is unreadable: {exc}"
            ) from exc
        return validate_installation_profile_draft(value, expected_digest=digest)

    @staticmethod
    def _write(path: Path, draft: Mapping[str, object]) -> None:
        # Preserve the semantic globe-region insertion order in the stored
        # document.  Canonical sorted JSON is used only for the compiled
        # calibration digest, where mapping order is intentionally immaterial.
        payload = json.dumps(
            draft,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _load_locked(self, digest: str, profile: InstallationProfile) -> dict[str, object]:
        path = self._draft_path(digest)
        if path.exists():
            return self._read(path, digest)
        draft = _draft_from_profile(digest, profile)
        self._write(path, draft)
        return draft

    def load(self, digest: str) -> dict[str, object]:
        """Load the exact immutable source artifact and its managed draft."""

        safe_digest = _require_digest(digest)
        resolved = self.library.resolve(safe_digest)
        with self._locked():
            return self._load_locked(safe_digest, resolved.global_profile)

    def update(
        self,
        digest: str,
        *,
        expected_revision: str,
        draft: object,
    ) -> dict[str, object]:
        """Atomically replace a draft only when its opaque revision matches."""

        safe_digest = _require_digest(digest)
        expected = _require_revision(expected_revision)
        resolved = self.library.resolve(safe_digest)
        with self._locked():
            current = self._load_locked(safe_digest, resolved.global_profile)
            if current["revision"] != expected:
                raise InstallationProfileDraftConflict(str(current["revision"]))
            normalized = validate_installation_profile_draft(
                draft, expected_digest=safe_digest
            )
            if normalized["revision"] != expected:
                raise InstallationProfileAuthoringError(
                    "draft.revision must match the If-Match revision"
                )
            masks = normalized["masks"]
            assert isinstance(masks, dict)
            updated = _draft_document(
                digest=safe_digest,
                revision=_revision(),
                foliage=masks["foliage"],  # type: ignore[arg-type]
                globes=masks["globes"],  # type: ignore[arg-type]
            )
            self._write(self._draft_path(safe_digest), updated)
            return updated

    def publish(
        self,
        digest: str,
        *,
        expected_revision: str,
    ) -> tuple[InstallationProfilePublishReceipt, dict[str, object]]:
        """Compile and atomically publish without mutating draft or selection."""

        safe_digest = _require_digest(digest)
        expected = _require_revision(expected_revision)
        resolved = self.library.resolve(safe_digest)
        with self._locked():
            current = self._load_locked(safe_digest, resolved.global_profile)
            if current["revision"] != expected:
                raise InstallationProfileDraftConflict(str(current["revision"]))
            encoded = compile_installation_profile_draft(
                current,
                clearance_radius=resolved.global_profile.clearance_radius,
            )
            receipt = self.library.publish(encoded)
            return receipt, current


__all__ = [
    "DRAFT_SCHEMA",
    "DRAFT_SCHEMA_VERSION",
    "InstallationProfileAuthoring",
    "InstallationProfileAuthoringError",
    "InstallationProfileDraftConflict",
    "compile_installation_profile_draft",
    "validate_installation_profile_draft",
]
