"""Deterministic Phase 3C installation-profile compiler and binary codec.

Version 1 deliberately describes canonical global strip-major geometry only.
Transport routing and host/native strip-direction policies belong to the
topology adapter, not to the calibration artifact compiled here.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

import numpy as np

from animation.core.plant_awareness import (
    GLOBE_REGION_ORDER,
    _distance_and_normals,
    _inner_edge,
)
from animation.libraries.mask_effects import dilate_8


MAGIC = b"LGIP"
FORMAT_VERSION = 1
FIXED_HEADER_BYTES = 112
SECTION_ENTRY_BYTES = 24
SECTION_COUNT = 9
PROFILE_HEADER_BYTES = FIXED_HEADER_BYTES + SECTION_COUNT * SECTION_ENTRY_BYTES
MAX_PROFILE_BYTES = 65_535
GLOBAL_STRIP_COUNT = 33
LEDS_PER_STRIP = 138
GLOBAL_PIXEL_COUNT = GLOBAL_STRIP_COUNT * LEDS_PER_STRIP
# The photographed calibration remains valid evidence for the original 32
# columns.  The finalized wall adds one independent physical column.  Keep the
# evidence geometry explicit so compilation can append an intentionally open
# (unmasked) strip instead of silently pretending the camera observed it.
CALIBRATION_STRIP_COUNT = 32
CALIBRATION_PIXEL_COUNT = CALIBRATION_STRIP_COUNT * LEDS_PER_STRIP

ENCODING_UNSIGNED_ENUM = 1
ENCODING_UNSIGNED_BOOLEAN = 2
ENCODING_UNSIGNED_BYTE = 3
ENCODING_SIGNED_BYTE = 4

SECTION_NAMES = (
    "category",
    "clearance",
    "foliage_edge",
    "globe_edge",
    "obstacle_edge",
    "globe_region",
    "distance",
    "normal_x",
    "normal_y",
)
_SECTION_ENCODINGS = (
    ENCODING_UNSIGNED_ENUM,
    ENCODING_UNSIGNED_BOOLEAN,
    ENCODING_UNSIGNED_BOOLEAN,
    ENCODING_UNSIGNED_BOOLEAN,
    ENCODING_UNSIGNED_BOOLEAN,
    ENCODING_UNSIGNED_ENUM,
    ENCODING_UNSIGNED_BYTE,
    ENCODING_SIGNED_BYTE,
    ENCODING_SIGNED_BYTE,
)
_SECTION_DTYPES = (
    np.dtype(np.uint8),
    np.dtype(np.uint8),
    np.dtype(np.uint8),
    np.dtype(np.uint8),
    np.dtype(np.uint8),
    np.dtype(np.uint8),
    np.dtype(np.uint8),
    np.dtype(np.int8),
    np.dtype(np.int8),
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FOLIAGE_PATH = _REPOSITORY_ROOT / "config/plant_pixel_map_32x138.json"
DEFAULT_GLOBES_PATH = _REPOSITORY_ROOT / "config/plant_globe_map_32x138.json"
DEFAULT_REGIONS_PATH = _REPOSITORY_ROOT / "config/plant_globe_regions_32x138.json"
DEFAULT_WALL_PATH = _REPOSITORY_ROOT / "config/webcam_wall_calibration.json"


class InstallationProfileError(ValueError):
    """Raised when calibration input or profile bytes violate the v1 contract."""


def _require_int(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InstallationProfileError(f"{label} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" through {maximum}" if maximum is not None else " or greater"
        raise InstallationProfileError(f"{label} must be {minimum}{suffix}")
    return value


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise InstallationProfileError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise InstallationProfileError(f"{label} must be an array")
    return value


def _freeze_array(
    value: Any,
    name: str,
    dtype: np.dtype[Any],
    shape: Tuple[int, int],
) -> np.ndarray:
    if not isinstance(value, np.ndarray):
        raise InstallationProfileError(f"{name} must be a numpy array")
    if value.shape != shape:
        raise InstallationProfileError(f"{name} must have shape {shape}, got {value.shape}")
    if value.dtype != dtype:
        raise InstallationProfileError(f"{name} must have dtype {dtype}, got {value.dtype}")
    frozen = np.array(value, dtype=dtype, order="C", copy=True)
    frozen.setflags(write=False)
    return frozen


@dataclass(frozen=True)
class InstallationProfile:
    """Validated immutable semantic view of one v1 profile or receiver slice."""

    global_strip_count: int
    leds_per_strip: int
    strip_origin: int
    strip_count: int
    clearance_radius: int
    calibration_digest: bytes
    reversed_strip_order: bool
    category: np.ndarray
    clearance: np.ndarray
    foliage_edge: np.ndarray
    globe_edge: np.ndarray
    obstacle_edge: np.ndarray
    globe_region: np.ndarray
    distance: np.ndarray
    normal_x: np.ndarray
    normal_y: np.ndarray

    def __post_init__(self) -> None:
        global_strips = _require_int(self.global_strip_count, "global_strip_count", maximum=0xFFFF)
        leds_per_strip = _require_int(
            self.leds_per_strip,
            "leds_per_strip",
            minimum=1,
            maximum=0xFFFF,
        )
        if global_strips != GLOBAL_STRIP_COUNT or leds_per_strip != LEDS_PER_STRIP:
            raise InstallationProfileError(
                f"v1 geometry must be {GLOBAL_STRIP_COUNT}x{LEDS_PER_STRIP}"
            )
        origin = _require_int(self.strip_origin, "strip_origin", maximum=0xFFFF)
        strip_count = _require_int(self.strip_count, "strip_count", minimum=1, maximum=0xFFFF)
        if origin + strip_count > global_strips:
            raise InstallationProfileError("represented strip range exceeds global geometry")
        _require_int(self.clearance_radius, "clearance_radius", maximum=4)
        if not isinstance(self.calibration_digest, bytes) or len(self.calibration_digest) != 32:
            raise InstallationProfileError("calibration_digest must be exactly 32 bytes")
        if not isinstance(self.reversed_strip_order, bool):
            raise InstallationProfileError("reversed_strip_order must be boolean")

        shape = (strip_count, leds_per_strip)
        for name, dtype in zip(SECTION_NAMES, _SECTION_DTYPES):
            object.__setattr__(self, name, _freeze_array(getattr(self, name), name, dtype, shape))
        self._validate_values()

    @property
    def pixel_count(self) -> int:
        return self.strip_count * self.leds_per_strip

    @property
    def foliage(self) -> np.ndarray:
        return self.category == 1

    @property
    def globes(self) -> np.ndarray:
        return self.category == 2

    @property
    def obstacle(self) -> np.ndarray:
        return self.category != 0

    @property
    def safe(self) -> np.ndarray:
        return self.clearance == 0

    @property
    def globe_region_masks(self) -> Mapping[str, np.ndarray]:
        return {
            name: self.globe_region == region_id
            for region_id, name in enumerate(GLOBE_REGION_ORDER, start=1)
        }

    def _validate_values(self) -> None:
        if np.any(self.category > 2):
            raise InstallationProfileError("category values must be in range 0 through 2")
        for name in ("clearance", "foliage_edge", "globe_edge", "obstacle_edge"):
            if np.any(getattr(self, name) > 1):
                raise InstallationProfileError(f"{name} values must be 0 or 1")
        if np.any(self.globe_region > len(GLOBE_REGION_ORDER)):
            raise InstallationProfileError("globe_region values must be in range 0 through 7")
        if np.any(self.normal_x == -128) or np.any(self.normal_y == -128):
            raise InstallationProfileError(
                "normal values must be signed Q0.7 in range -127 through 127"
            )

        foliage = self.category == 1
        globes = self.category == 2
        obstacle = foliage | globes
        if np.any((self.globe_region != 0) != globes):
            raise InstallationProfileError(
                "every globe must have exactly one known region and no other "
                "pixel may have one"
            )
        if np.any((self.clearance != 0) < obstacle):
            raise InstallationProfileError("clearance must contain every obstacle pixel")
        if np.any((self.foliage_edge != 0) & ~foliage):
            raise InstallationProfileError("foliage_edge must be contained by foliage")
        if np.any((self.globe_edge != 0) & ~globes):
            raise InstallationProfileError("globe_edge must be contained by globes")
        if np.any((self.obstacle_edge != 0) & ~obstacle):
            raise InstallationProfileError("obstacle_edge must be contained by obstacles")
        if np.any((self.distance == 0) != obstacle):
            raise InstallationProfileError("distance must be zero exactly at obstacle pixels")


def _reject_json_constant(value: str) -> None:
    raise InstallationProfileError(f"non-finite JSON number is not allowed: {value}")


def _unique_json_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InstallationProfileError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _read_json(path: Path, role: str) -> Mapping[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(
            raw,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except InstallationProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallationProfileError(f"failed to read {role} calibration {path}: {exc}") from exc
    return _require_mapping(payload, f"{role} calibration root")


def _validate_geometry(payload: Mapping[str, Any], role: str) -> None:
    geometry = _require_mapping(payload.get("geometry"), f"{role}.geometry")
    strip_count = _require_int(geometry.get("strip_count"), f"{role}.geometry.strip_count")
    leds_per_strip = _require_int(geometry.get("leds_per_strip"), f"{role}.geometry.leds_per_strip")
    total_leds = _require_int(geometry.get("total_leds"), f"{role}.geometry.total_leds")
    if (strip_count, leds_per_strip, total_leds) != (
        CALIBRATION_STRIP_COUNT,
        LEDS_PER_STRIP,
        CALIBRATION_PIXEL_COUNT,
    ):
        raise InstallationProfileError(
            f"{role}.geometry must be exactly "
            f"{CALIBRATION_STRIP_COUNT}x{LEDS_PER_STRIP} calibration evidence"
        )
    formula = geometry.get("index_formula")
    if formula is not None and formula != "strip * leds_per_strip + led":
        raise InstallationProfileError(
            f"{role}.geometry.index_formula is not canonical strip-major order"
        )


def _validate_index_list(payload: Mapping[str, Any], key: str, role: str) -> Tuple[int, ...]:
    values = _require_list(payload.get(key), f"{role}.{key}")
    result = tuple(
        _require_int(
            value,
            f"{role}.{key}[{position}]",
            maximum=CALIBRATION_PIXEL_COUNT - 1,
        )
        for position, value in enumerate(values)
    )
    if any(left >= right for left, right in zip(result, result[1:])):
        raise InstallationProfileError(f"{role}.{key} must be sorted, unique, and ascending")
    return result


def _validate_pixel_coordinates(record: Mapping[str, Any], label: str, index: int) -> None:
    strip = _require_int(
        record.get("strip"),
        f"{label}.strip",
        maximum=CALIBRATION_STRIP_COUNT - 1,
    )
    led = _require_int(record.get("led"), f"{label}.led", maximum=LEDS_PER_STRIP - 1)
    if strip * LEDS_PER_STRIP + led != index:
        raise InstallationProfileError(f"{label} coordinates do not match its strip-major index")


def _validate_foliage(payload: Mapping[str, Any]) -> Tuple[int, ...]:
    _validate_geometry(payload, "foliage")
    covered = _validate_index_list(payload, "covered_indices", "foliage")
    covered_count = _require_int(payload.get("covered_count"), "foliage.covered_count")
    if covered_count != len(covered):
        raise InstallationProfileError("foliage.covered_count does not match covered_indices")

    occluded = _validate_index_list(payload, "occluded_indices", "foliage")
    occluded_count = _require_int(payload.get("occluded_count"), "foliage.occluded_count")
    if occluded_count != len(occluded) or occluded != covered:
        raise InstallationProfileError("foliage occlusion metadata does not match covered_indices")

    records = _require_list(payload.get("pixels"), "foliage.pixels")
    observed_count = _require_int(payload.get("observed_count"), "foliage.observed_count")
    if observed_count != len(records) or observed_count != CALIBRATION_PIXEL_COUNT:
        raise InstallationProfileError(
            "foliage.pixels must contain one measured record per wall pixel"
        )
    record_indices = []
    record_occluded = []
    for position, raw_record in enumerate(records):
        label = f"foliage.pixels[{position}]"
        record = _require_mapping(raw_record, label)
        index = _require_int(
            record.get("index"),
            f"{label}.index",
            maximum=CALIBRATION_PIXEL_COUNT - 1,
        )
        if index != position:
            raise InstallationProfileError("foliage.pixels must be sorted, unique, and complete")
        _validate_pixel_coordinates(record, label, index)
        observed = record.get("observed")
        occluded_flag = record.get("occluded")
        if not isinstance(observed, bool) or not observed:
            raise InstallationProfileError(f"{label}.observed must be true")
        if not isinstance(occluded_flag, bool):
            raise InstallationProfileError(f"{label}.occluded must be boolean")
        record_indices.append(index)
        if occluded_flag:
            record_occluded.append(index)
    if (
        tuple(record_indices) != tuple(range(CALIBRATION_PIXEL_COUNT))
        or tuple(record_occluded) != covered
    ):
        raise InstallationProfileError("foliage pixel records do not agree with covered_indices")
    return covered


def _validate_region_definitions(
    payload: Mapping[str, Any], role: str
) -> Tuple[Mapping[str, int], ...]:
    if payload.get("region_shape") not in (None, "circle_in_8x8_led_box"):
        raise InstallationProfileError(f"{role}.region_shape is unsupported")
    records = _require_list(payload.get("regions"), f"{role}.regions")
    if len(records) != len(GLOBE_REGION_ORDER):
        raise InstallationProfileError(f"{role}.regions must contain exactly seven definitions")
    result = []
    occupied: set[Tuple[int, int]] = set()
    for position, expected_id in enumerate(GLOBE_REGION_ORDER):
        label = f"{role}.regions[{position}]"
        record = _require_mapping(records[position], label)
        if record.get("id") != expected_id:
            raise InstallationProfileError(f"{role}.regions must use the stable globe-region order")
        strip_start = _require_int(
            record.get("strip_start"),
            f"{label}.strip_start",
            maximum=CALIBRATION_STRIP_COUNT - 1,
        )
        led_start = _require_int(
            record.get("led_start"),
            f"{label}.led_start",
            maximum=LEDS_PER_STRIP - 1,
        )
        width = _require_int(
            record.get("width"),
            f"{label}.width",
            minimum=1,
            maximum=CALIBRATION_STRIP_COUNT,
        )
        height = _require_int(
            record.get("height"),
            f"{label}.height",
            minimum=1,
            maximum=LEDS_PER_STRIP,
        )
        if width != 8 or height != 8:
            raise InstallationProfileError(f"{label} must define the canonical 8x8 globe box")
        # Authored boxes at the right wall edge are intentionally clipped.  Only
        # their in-wall portions participate in overlap and membership checks.
        in_wall = {
            (strip, led)
            for strip in range(
                strip_start,
                min(strip_start + width, CALIBRATION_STRIP_COUNT),
            )
            for led in range(led_start, min(led_start + height, LEDS_PER_STRIP))
        }
        if occupied & in_wall:
            raise InstallationProfileError(f"{role}.regions contain overlapping definitions")
        occupied |= in_wall
        result.append({
            "id": expected_id,
            "strip_start": strip_start,
            "led_start": led_start,
            "width": width,
            "height": height,
        })
    return tuple(result)


def _validate_globes(
    payload: Mapping[str, Any],
    expected_regions: Tuple[Mapping[str, int], ...],
) -> Tuple[Tuple[int, ...], Tuple[Tuple[int, str], ...]]:
    _validate_geometry(payload, "globes")
    regions = _validate_region_definitions(payload, "globes")
    if regions != expected_regions:
        raise InstallationProfileError("globe and region calibration definitions disagree")
    region_count = _require_int(payload.get("region_count"), "globes.region_count")
    if region_count != len(GLOBE_REGION_ORDER):
        raise InstallationProfileError("globes.region_count must be exactly seven")

    globe_indices = _validate_index_list(payload, "globe_indices", "globes")
    covered_indices = _validate_index_list(payload, "covered_indices", "globes")
    if globe_indices != covered_indices:
        raise InstallationProfileError("globes.globe_indices and covered_indices disagree")
    for key in ("globe_count", "covered_count"):
        if _require_int(payload.get(key), f"globes.{key}") != len(globe_indices):
            raise InstallationProfileError(f"globes.{key} does not match globe_indices")

    raw_counts = _require_mapping(payload.get("region_pixel_counts"), "globes.region_pixel_counts")
    if set(raw_counts) != set(GLOBE_REGION_ORDER):
        raise InstallationProfileError(
            "globes.region_pixel_counts must name exactly the seven known regions"
        )
    declared_counts = {
        name: _require_int(raw_counts[name], f"globes.region_pixel_counts.{name}", minimum=1)
        for name in GLOBE_REGION_ORDER
    }

    records = _require_list(payload.get("pixels"), "globes.pixels")
    if len(records) != len(globe_indices):
        raise InstallationProfileError("globes.pixels count does not match globe_indices")
    region_by_name = {record["id"]: record for record in regions}
    assignments = []
    actual_counts = {name: 0 for name in GLOBE_REGION_ORDER}
    previous_index = -1
    for position, raw_record in enumerate(records):
        label = f"globes.pixels[{position}]"
        record = _require_mapping(raw_record, label)
        index = _require_int(
            record.get("index"),
            f"{label}.index",
            maximum=CALIBRATION_PIXEL_COUNT - 1,
        )
        if index <= previous_index:
            raise InstallationProfileError("globes.pixels must be sorted, unique, and ascending")
        previous_index = index
        if index != globe_indices[position]:
            raise InstallationProfileError("globes.pixels membership does not match globe_indices")
        _validate_pixel_coordinates(record, label, index)
        region_name = record.get("region")
        if region_name not in region_by_name:
            raise InstallationProfileError(f"{label}.region must be one of the seven known regions")
        definition = region_by_name[str(region_name)]
        strip, led = divmod(index, LEDS_PER_STRIP)
        if not (
            definition["strip_start"] <= strip < definition["strip_start"] + definition["width"]
            and definition["led_start"] <= led < definition["led_start"] + definition["height"]
        ):
            raise InstallationProfileError(f"{label} lies outside its declared region")
        assignments.append((index, str(region_name)))
        actual_counts[str(region_name)] += 1
    if actual_counts != declared_counts or sum(declared_counts.values()) != len(globe_indices):
        raise InstallationProfileError("globes.region_pixel_counts do not match pixel membership")
    return globe_indices, tuple(assignments)


def _validate_wall(payload: Mapping[str, Any]) -> None:
    measured = _require_mapping(payload.get("measured_layout"), "wall.measured_layout")
    strip_count = _require_int(measured.get("strip_count"), "wall.measured_layout.strip_count")
    leds_per_strip = _require_int(
        measured.get("leds_per_strip"),
        "wall.measured_layout.leds_per_strip",
    )
    if (strip_count, leds_per_strip) != (
        CALIBRATION_STRIP_COUNT,
        LEDS_PER_STRIP,
    ):
        raise InstallationProfileError(
            "wall measured_layout must be exactly "
            f"{CALIBRATION_STRIP_COUNT}x{LEDS_PER_STRIP} camera evidence"
        )
    if measured.get("verification_status") != "camera_verified":
        raise InstallationProfileError("wall measured_layout must be camera_verified")


def _canonical_calibration_digest(
    foliage: Mapping[str, Any],
    globes: Mapping[str, Any],
    regions: Mapping[str, Any],
    wall: Mapping[str, Any],
) -> bytes:
    canonical = json.dumps(
        {"foliage": foliage, "globes": globes, "regions": regions, "wall": wall},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).digest()


def _quantize_normal(values: np.ndarray) -> np.ndarray:
    magnitude = np.floor(np.abs(values) * 127.0 + 0.5)
    quantized = np.sign(values) * magnitude
    return np.clip(quantized, -127, 127).astype(np.int8)


def compile_installation_profile(
    foliage_path: Path | None = None,
    globes_path: Path | None = None,
    regions_path: Path | None = None,
    wall_path: Path | None = None,
    *,
    clearance_radius: int = 1,
) -> InstallationProfile:
    """Compile the four canonical calibration inputs into the global profile."""

    radius = _require_int(clearance_radius, "clearance_radius", maximum=4)
    foliage_payload = _read_json(Path(foliage_path or DEFAULT_FOLIAGE_PATH), "foliage")
    globes_payload = _read_json(Path(globes_path or DEFAULT_GLOBES_PATH), "globes")
    regions_payload = _read_json(Path(regions_path or DEFAULT_REGIONS_PATH), "regions")
    wall_payload = _read_json(Path(wall_path or DEFAULT_WALL_PATH), "wall")

    foliage_indices = _validate_foliage(foliage_payload)
    _validate_geometry(regions_payload, "regions")
    region_definitions = _validate_region_definitions(regions_payload, "regions")
    globe_indices, globe_assignments = _validate_globes(globes_payload, region_definitions)
    _validate_wall(wall_payload)

    foliage = np.zeros(GLOBAL_PIXEL_COUNT, dtype=bool)
    globes = np.zeros(GLOBAL_PIXEL_COUNT, dtype=bool)
    foliage[np.fromiter(foliage_indices, dtype=np.intp, count=len(foliage_indices))] = True
    globes[np.fromiter(globe_indices, dtype=np.intp, count=len(globe_indices))] = True
    # Globes have explicit precedence over overlapping foliage evidence.
    foliage &= ~globes
    foliage = foliage.reshape(GLOBAL_STRIP_COUNT, LEDS_PER_STRIP)
    globes = globes.reshape(GLOBAL_STRIP_COUNT, LEDS_PER_STRIP)
    obstacle = foliage | globes

    clearance = obstacle.copy()
    for _ in range(radius):
        clearance = dilate_8(clearance)
    # Strip 32 has no camera evidence yet.  It is deliberately represented as
    # open space, even when dilation from observed strip 31 would otherwise
    # infer a mask into the new column.
    clearance[CALIBRATION_STRIP_COUNT:] = False
    distance, normal_x, normal_y = _distance_and_normals(obstacle)

    category = np.zeros(obstacle.shape, dtype=np.uint8)
    category[foliage] = 1
    category[globes] = 2
    globe_region = np.zeros(obstacle.shape, dtype=np.uint8)
    for index, region_name in globe_assignments:
        globe_region.ravel()[index] = GLOBE_REGION_ORDER.index(region_name) + 1

    return InstallationProfile(
        global_strip_count=GLOBAL_STRIP_COUNT,
        leds_per_strip=LEDS_PER_STRIP,
        strip_origin=0,
        strip_count=GLOBAL_STRIP_COUNT,
        clearance_radius=radius,
        calibration_digest=_canonical_calibration_digest(
            foliage_payload, globes_payload, regions_payload, wall_payload
        ),
        reversed_strip_order=False,
        category=category,
        clearance=clearance.astype(np.uint8),
        foliage_edge=_inner_edge(foliage).astype(np.uint8),
        globe_edge=_inner_edge(globes).astype(np.uint8),
        obstacle_edge=_inner_edge(obstacle).astype(np.uint8),
        globe_region=globe_region,
        distance=distance.astype(np.uint8),
        normal_x=_quantize_normal(normal_x),
        normal_y=_quantize_normal(normal_y),
    )


def encode_installation_profile(profile: InstallationProfile) -> bytes:
    """Encode a validated profile using the frozen bounded v1 binary format."""

    if not isinstance(profile, InstallationProfile):
        raise InstallationProfileError("profile must be an InstallationProfile")
    total_bytes = PROFILE_HEADER_BYTES + SECTION_COUNT * profile.pixel_count
    if total_bytes > MAX_PROFILE_BYTES:
        raise InstallationProfileError("complete profile exceeds 65,535 bytes")

    encoded = bytearray(total_bytes)
    flags = 1 if profile.reversed_strip_order else 0
    struct.pack_into(
        ">4sHHIHHHHIBBHHHI32s32s12s",
        encoded,
        0,
        MAGIC,
        FORMAT_VERSION,
        FIXED_HEADER_BYTES,
        flags,
        profile.global_strip_count,
        profile.leds_per_strip,
        profile.strip_origin,
        profile.strip_count,
        profile.pixel_count,
        profile.clearance_radius,
        len(GLOBE_REGION_ORDER),
        SECTION_COUNT,
        SECTION_ENTRY_BYTES,
        0,
        total_bytes,
        profile.calibration_digest,
        bytes(32),
        bytes(12),
    )
    offset = PROFILE_HEADER_BYTES
    for section_id, (name, encoding) in enumerate(
        zip(SECTION_NAMES, _SECTION_ENCODINGS), start=1
    ):
        payload = getattr(profile, name).tobytes(order="C")
        length = len(payload)
        struct.pack_into(
            ">HBBIIIII",
            encoded,
            FIXED_HEADER_BYTES + (section_id - 1) * SECTION_ENTRY_BYTES,
            section_id,
            encoding,
            1,
            profile.pixel_count,
            offset,
            length,
            zlib.crc32(payload) & 0xFFFFFFFF,
            0,
        )
        encoded[offset:offset + length] = payload
        offset += length
    if offset != total_bytes:
        raise InstallationProfileError("internal profile size mismatch")
    encoded[68:100] = hashlib.sha256(encoded).digest()
    return bytes(encoded)


def decode_installation_profile(data: bytes) -> InstallationProfile:
    """Validate and decode a complete v1 installation profile."""

    if not isinstance(data, bytes):
        raise InstallationProfileError("profile data must be bytes")
    if len(data) < PROFILE_HEADER_BYTES:
        raise InstallationProfileError("profile is truncated before its section table")
    if len(data) > MAX_PROFILE_BYTES:
        raise InstallationProfileError("complete profile exceeds 65,535 bytes")

    (
        magic,
        version,
        fixed_header_bytes,
        flags,
        global_strip_count,
        leds_per_strip,
        strip_origin,
        strip_count,
        pixel_count,
        clearance_radius,
        region_count,
        section_count,
        section_entry_bytes,
        reserved,
        total_bytes,
        calibration_digest,
        content_digest,
        reserved_tail,
    ) = struct.unpack_from(">4sHHIHHHHIBBHHHI32s32s12s", data, 0)

    if magic != MAGIC:
        raise InstallationProfileError("profile magic must be LGIP")
    if version != FORMAT_VERSION:
        raise InstallationProfileError("unsupported installation-profile version")
    if fixed_header_bytes != FIXED_HEADER_BYTES:
        raise InstallationProfileError("fixed-header size must be 112 bytes")
    if flags & ~1:
        raise InstallationProfileError("profile contains unknown flag bits")
    if reserved != 0 or reserved_tail != bytes(12):
        raise InstallationProfileError("profile header reserved bytes must be zero")
    if region_count != len(GLOBE_REGION_ORDER):
        raise InstallationProfileError("profile globe-region count must be seven")
    if section_count != SECTION_COUNT or section_entry_bytes != SECTION_ENTRY_BYTES:
        raise InstallationProfileError("profile section-table layout is not v1")
    if total_bytes != len(data):
        raise InstallationProfileError("complete-profile byte count does not match input")
    if pixel_count != strip_count * leds_per_strip:
        raise InstallationProfileError("represented pixel count does not match geometry")
    digest_input = bytearray(data)
    digest_input[68:100] = bytes(32)
    if not hashlib.sha256(digest_input).digest() == content_digest:
        raise InstallationProfileError("profile content SHA-256 does not match")

    arrays: Dict[str, np.ndarray] = {}
    expected_offset = PROFILE_HEADER_BYTES
    for position, (name, expected_encoding, dtype) in enumerate(
        zip(SECTION_NAMES, _SECTION_ENCODINGS, _SECTION_DTYPES)
    ):
        entry_offset = FIXED_HEADER_BYTES + position * SECTION_ENTRY_BYTES
        (
            section_id,
            encoding,
            element_width,
            element_count,
            offset,
            length,
            crc32,
            entry_reserved,
        ) = struct.unpack_from(">HBBIIIII", data, entry_offset)
        if section_id != position + 1:
            raise InstallationProfileError("profile sections must use exact ascending IDs")
        if encoding != expected_encoding or element_width != 1:
            raise InstallationProfileError(f"{name} section encoding is not canonical")
        if element_count != pixel_count or length != pixel_count:
            raise InstallationProfileError(f"{name} section count or length does not match pixels")
        if offset != expected_offset:
            raise InstallationProfileError(
                "profile section payloads must be contiguous and ordered"
            )
        if entry_reserved != 0:
            raise InstallationProfileError(f"{name} section reserved field must be zero")
        end = offset + length
        if end < offset or end > len(data):
            raise InstallationProfileError(f"{name} section lies outside the profile")
        payload = data[offset:end]
        if zlib.crc32(payload) & 0xFFFFFFFF != crc32:
            raise InstallationProfileError(f"{name} section CRC-32 does not match")
        arrays[name] = np.frombuffer(payload, dtype=dtype).reshape(strip_count, leds_per_strip)
        expected_offset = end
    if expected_offset != len(data):
        raise InstallationProfileError("profile contains trailing or unreferenced payload bytes")

    return InstallationProfile(
        global_strip_count=global_strip_count,
        leds_per_strip=leds_per_strip,
        strip_origin=strip_origin,
        strip_count=strip_count,
        clearance_radius=clearance_radius,
        calibration_digest=calibration_digest,
        reversed_strip_order=bool(flags & 1),
        **arrays,
    )


__all__ = [
    "DEFAULT_FOLIAGE_PATH",
    "DEFAULT_GLOBES_PATH",
    "DEFAULT_REGIONS_PATH",
    "DEFAULT_WALL_PATH",
    "ENCODING_SIGNED_BYTE",
    "ENCODING_UNSIGNED_BOOLEAN",
    "ENCODING_UNSIGNED_BYTE",
    "ENCODING_UNSIGNED_ENUM",
    "FIXED_HEADER_BYTES",
    "FORMAT_VERSION",
    "GLOBAL_STRIP_COUNT",
    "CALIBRATION_STRIP_COUNT",
    "CALIBRATION_PIXEL_COUNT",
    "InstallationProfile",
    "InstallationProfileError",
    "LEDS_PER_STRIP",
    "MAGIC",
    "MAX_PROFILE_BYTES",
    "PROFILE_HEADER_BYTES",
    "SECTION_COUNT",
    "SECTION_ENTRY_BYTES",
    "SECTION_NAMES",
    "compile_installation_profile",
    "decode_installation_profile",
    "encode_installation_profile",
]
