"""Thread-safe host runtime views of managed installation profiles.

The managed library remains the authority for artifact validation and receiver
topology.  This module converts a resolved global artifact once into the
read-only geometry contract consumed by Python animations, then swaps that
small view atomically when the selected content digest changes.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import threading
from types import MappingProxyType
from typing import Any

import numpy as np

from animation.core.installation_profile import FORMAT_VERSION
from animation.core.installation_profile_library import (
    InstallationProfileLibrary,
    InstallationProfileLibraryError,
    ResolvedInstallationProfile,
)
from animation.core.installation_profile_topology import (
    IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    InstallationProfileTopology,
)
from animation.core.plant_awareness import GLOBE_REGION_ORDER, PlantMaskGeometry


EMPTY_INSTALLATION_PROFILE_DIGEST = "0" * 64

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class InstallationProfileRuntimeError(RuntimeError):
    """A managed installation profile cannot be selected for runtime use."""


TopologyIdentity = tuple[
    tuple[int, int, int, int],
    tuple[bool, bool, bool, bool],
]


def _topology_identity(topology: InstallationProfileTopology) -> TopologyIdentity:
    """Return only topology fields that change semantic receiver geometry."""

    return (
        topology.physical_lane_order,
        topology.reverse_native_strips_by_logical_receiver,
    )


def _readonly_array(value: Any, *, dtype: np.dtype[Any] | None = None) -> np.ndarray:
    """Return one owned, C-contiguous, non-writeable runtime array."""

    result = np.array(value, dtype=dtype, order="C", copy=True)
    result.setflags(write=False)
    return result


def _runtime_plant_masks(resolved: ResolvedInstallationProfile) -> PlantMaskGeometry:
    profile = resolved.global_profile
    foliage = _readonly_array(profile.category == 1, dtype=np.dtype(np.bool_))
    globes = _readonly_array(profile.category == 2, dtype=np.dtype(np.bool_))
    obstacle = _readonly_array(profile.category != 0, dtype=np.dtype(np.bool_))
    clearance = _readonly_array(profile.clearance != 0, dtype=np.dtype(np.bool_))
    foliage_edge = _readonly_array(
        profile.foliage_edge != 0, dtype=np.dtype(np.bool_)
    )
    globe_edge = _readonly_array(profile.globe_edge != 0, dtype=np.dtype(np.bool_))
    obstacle_edge = _readonly_array(
        profile.obstacle_edge != 0, dtype=np.dtype(np.bool_)
    )
    distance = _readonly_array(profile.distance, dtype=np.dtype(np.float32))
    # The portable profile freezes normals as signed Q0.7.  Python animations
    # consume unit-ish float vectors, so dequantize once at the context boundary.
    normal_x = _readonly_array(
        profile.normal_x.astype(np.float32) / np.float32(127.0),
        dtype=np.dtype(np.float32),
    )
    normal_y = _readonly_array(
        profile.normal_y.astype(np.float32) / np.float32(127.0),
        dtype=np.dtype(np.float32),
    )
    region_masks = MappingProxyType(
        {
            region_name: _readonly_array(
                profile.globe_region == region_id, dtype=np.dtype(np.bool_)
            )
            for region_id, region_name in enumerate(GLOBE_REGION_ORDER, start=1)
        }
    )
    return PlantMaskGeometry(
        foliage=foliage,
        globes=globes,
        obstacle=obstacle,
        clearance=clearance,
        foliage_flat=foliage.reshape(-1),
        globes_flat=globes.reshape(-1),
        obstacle_flat=obstacle.reshape(-1),
        clearance_flat=clearance.reshape(-1),
        foliage_count=int(np.count_nonzero(foliage)),
        globe_count=int(np.count_nonzero(globes)),
        globe_regions=len(GLOBE_REGION_ORDER),
        foliage_edge=foliage_edge,
        globe_edge=globe_edge,
        obstacle_edge=obstacle_edge,
        distance=distance,
        normal_x=normal_x,
        normal_y=normal_y,
        globe_region_masks=region_masks,
        error="",
    )


def _validate_runtime_geometry(
    geometry: PlantMaskGeometry, *, width: int, height: int
) -> None:
    """Reject public construction around mutable or malformed array surfaces."""

    logical_shape = (width, height)
    flat_shape = (width * height,)
    array_contracts = {
        "foliage": (logical_shape, np.dtype(np.bool_)),
        "globes": (logical_shape, np.dtype(np.bool_)),
        "obstacle": (logical_shape, np.dtype(np.bool_)),
        "clearance": (logical_shape, np.dtype(np.bool_)),
        "foliage_flat": (flat_shape, np.dtype(np.bool_)),
        "globes_flat": (flat_shape, np.dtype(np.bool_)),
        "obstacle_flat": (flat_shape, np.dtype(np.bool_)),
        "clearance_flat": (flat_shape, np.dtype(np.bool_)),
        "foliage_edge": (logical_shape, np.dtype(np.bool_)),
        "globe_edge": (logical_shape, np.dtype(np.bool_)),
        "obstacle_edge": (logical_shape, np.dtype(np.bool_)),
        "distance": (logical_shape, np.dtype(np.float32)),
        "normal_x": (logical_shape, np.dtype(np.float32)),
        "normal_y": (logical_shape, np.dtype(np.float32)),
    }
    for name, (shape, dtype) in array_contracts.items():
        value = getattr(geometry, name)
        if not isinstance(value, np.ndarray):
            raise TypeError(f"plant_masks.{name} must be a numpy array")
        if value.shape != shape or value.dtype != dtype:
            raise InstallationProfileRuntimeError(
                f"plant_masks.{name} must have shape {shape} and dtype {dtype}"
            )
        if value.flags.writeable:
            raise InstallationProfileRuntimeError(
                f"plant_masks.{name} must be non-writeable"
            )

    if not isinstance(geometry.globe_region_masks, MappingProxyType):
        raise InstallationProfileRuntimeError(
            "plant_masks.globe_region_masks must be an immutable mapping"
        )
    if tuple(geometry.globe_region_masks) != GLOBE_REGION_ORDER:
        raise InstallationProfileRuntimeError(
            "plant_masks.globe_region_masks must use the stable region order"
        )
    for name, value in geometry.globe_region_masks.items():
        if (
            not isinstance(value, np.ndarray)
            or value.shape != logical_shape
            or value.dtype != np.dtype(np.bool_)
            or value.flags.writeable
        ):
            raise InstallationProfileRuntimeError(
                f"plant_masks.globe_region_masks[{name!r}] must be a "
                "non-writeable logical boolean array"
            )


@dataclass(frozen=True)
class InstallationProfileRuntimeView:
    """Compact identity plus immutable global geometry for Python rendering."""

    profile_digest: str
    calibration_digest: str
    format_version: int
    global_width: int
    height: int
    pixel_count: int
    clearance_radius: int
    topology: InstallationProfileTopology
    plant_masks: PlantMaskGeometry

    @classmethod
    def from_resolved(
        cls, resolved: ResolvedInstallationProfile
    ) -> "InstallationProfileRuntimeView":
        """Build the global host view after managed-library resolution succeeds."""

        if not isinstance(resolved, ResolvedInstallationProfile):
            raise TypeError("resolved must be a ResolvedInstallationProfile")
        profile = resolved.global_profile
        return cls(
            profile_digest=resolved.content_digest,
            calibration_digest=profile.calibration_digest.hex(),
            format_version=FORMAT_VERSION,
            global_width=profile.global_strip_count,
            height=profile.leds_per_strip,
            pixel_count=profile.global_strip_count * profile.leds_per_strip,
            clearance_radius=profile.clearance_radius,
            topology=resolved.topology,
            plant_masks=_runtime_plant_masks(resolved),
        )

    def __post_init__(self) -> None:
        for field_name in ("profile_digest", "calibration_digest"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise InstallationProfileRuntimeError(
                    f"{field_name} must be a lowercase SHA-256 digest"
                )
        if self.profile_digest == EMPTY_INSTALLATION_PROFILE_DIGEST:
            raise InstallationProfileRuntimeError(
                "an empty digest cannot identify a resolved runtime view"
            )
        if self.format_version != FORMAT_VERSION:
            raise InstallationProfileRuntimeError(
                f"format_version must be {FORMAT_VERSION}"
            )
        if not isinstance(self.topology, InstallationProfileTopology):
            raise TypeError("topology must be an InstallationProfileTopology")
        if not isinstance(self.plant_masks, PlantMaskGeometry):
            raise TypeError("plant_masks must be a PlantMaskGeometry")
        if (self.global_width, self.height, self.pixel_count) != (
            32,
            138,
            32 * 138,
        ):
            raise InstallationProfileRuntimeError(
                "runtime profile view must describe the global 32x138 wall"
            )
        _validate_runtime_geometry(
            self.plant_masks, width=self.global_width, height=self.height
        )

    @property
    def content_digest(self) -> str:
        """Alias the content-addressed profile identity used by the library."""

        return self.profile_digest

    @property
    def geometry(self) -> PlantMaskGeometry:
        """PlantMaskGeometry-compatible global host geometry."""

        return self.plant_masks

    @property
    def topology_identity(self) -> TopologyIdentity:
        return _topology_identity(self.topology)

    @property
    def presentation_identity(self) -> tuple[Any, ...]:
        """Compact stable identity; deliberately excludes every geometry array."""

        return (
            self.profile_digest,
            self.calibration_digest,
            self.format_version,
            self.global_width,
            self.height,
            self.clearance_radius,
            self.topology_identity,
        )

    @property
    def compact_identity(self) -> tuple[Any, ...]:
        return self.presentation_identity

    def status(self) -> dict[str, Any]:
        """Return JSON-safe metadata without copying or serializing geometry."""

        topology = self.topology
        return {
            "profile_digest": self.profile_digest,
            "content_digest": self.content_digest,
            "calibration_digest": self.calibration_digest,
            "format_version": self.format_version,
            "global_width": self.global_width,
            "height": self.height,
            "pixel_count": self.pixel_count,
            "clearance_radius": self.clearance_radius,
            "topology": {
                "logical_to_transport_routes": [
                    list(route) for route in topology.logical_to_transport_routes
                ],
                "physical_lane_order": list(topology.physical_lane_order),
                "reverse_host_strips_by_logical_receiver": list(
                    topology.reverse_host_strips_by_logical_receiver
                ),
                "reverse_native_strips_by_logical_receiver": list(
                    topology.reverse_native_strips_by_logical_receiver
                ),
            },
        }


def _normalize_selection_digest(value: str | None) -> str:
    if value is None or value == EMPTY_INSTALLATION_PROFILE_DIGEST:
        return EMPTY_INSTALLATION_PROFILE_DIGEST
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise InstallationProfileRuntimeError(
            "installation profile digest must be None or a lowercase SHA-256 digest"
        )
    return value


class InstallationProfileSelection:
    """Atomic managed-profile selection shared by render and control threads."""

    def __init__(
        self,
        library: InstallationProfileLibrary | None = None,
        topology: InstallationProfileTopology | None = None,
        selected_digest: str | None = None,
    ) -> None:
        if library is not None and not isinstance(library, InstallationProfileLibrary):
            raise TypeError("library must be an InstallationProfileLibrary or None")
        if topology is not None and not isinstance(topology, InstallationProfileTopology):
            raise TypeError("topology must be an InstallationProfileTopology or None")
        self._library = library
        self._topology = topology or IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
        self._lock = threading.RLock()
        self._selected_digest = EMPTY_INSTALLATION_PROFILE_DIGEST
        self._revision = 0
        self._view: InstallationProfileRuntimeView | None = None
        self._resolved: ResolvedInstallationProfile | None = None

        initial = _normalize_selection_digest(selected_digest)
        if initial != EMPTY_INSTALLATION_PROFILE_DIGEST:
            self.select(initial)

    @property
    def selected_digest(self) -> str:
        with self._lock:
            return self._selected_digest

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def view(self) -> InstallationProfileRuntimeView | None:
        with self._lock:
            return self._view

    @property
    def resolved(self) -> ResolvedInstallationProfile | None:
        with self._lock:
            return self._resolved

    def select(self, profile_digest: str | None) -> bool:
        """Resolve then atomically select a digest; return whether state changed."""

        candidate = _normalize_selection_digest(profile_digest)
        with self._lock:
            if candidate == self._selected_digest:
                return False
            if candidate == EMPTY_INSTALLATION_PROFILE_DIGEST:
                self._selected_digest = candidate
                self._view = None
                self._resolved = None
                self._revision += 1
                return True
            if self._library is None:
                raise InstallationProfileRuntimeError(
                    "a managed InstallationProfileLibrary is required to select "
                    "a nonempty profile"
                )

            # Keep every externally observable field unchanged until both strict
            # managed resolution and construction of immutable geometry succeed.
            try:
                resolved = self._library.resolve(candidate, self._topology)
                view = InstallationProfileRuntimeView.from_resolved(resolved)
            except (InstallationProfileLibraryError, TypeError, ValueError) as exc:
                raise InstallationProfileRuntimeError(
                    f"failed to resolve managed installation profile {candidate}: {exc}"
                ) from exc

            self._resolved = resolved
            self._view = view
            self._selected_digest = candidate
            self._revision += 1
            return True

    def status(self) -> dict[str, Any]:
        """Return one JSON-safe snapshot protected from mixed concurrent reads."""

        with self._lock:
            return {
                "selected_digest": self._selected_digest,
                "revision": self._revision,
                "selected": self._view is not None,
                "view": None if self._view is None else self._view.status(),
            }


__all__ = [
    "EMPTY_INSTALLATION_PROFILE_DIGEST",
    "InstallationProfileRuntimeError",
    "InstallationProfileRuntimeView",
    "InstallationProfileSelection",
]
