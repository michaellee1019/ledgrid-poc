"""Topology-only receiver views of a canonical installation profile.

The installation-profile payload deliberately contains no transport or host
frame wiring policy.  This module keeps those coordinate domains independently
named while applying only the two transforms that define a receiver profile:
the physical lane assigned to a logical receiver and that receiver's native
local-strip direction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from animation.core.installation_profile import InstallationProfile, SECTION_NAMES


RECEIVER_COUNT = 5
RECEIVER_IDS = tuple(range(RECEIVER_COUNT))
STRIPS_PER_RECEIVER = 8
RECEIVER_STRIP_COUNTS = (8, 8, 8, 8, 1)
CANONICAL_GLOBAL_STRIP_COUNT = sum(RECEIVER_STRIP_COUNTS)


class InstallationProfileTopologyError(ValueError):
    """The topology or a set of receiver profile views is inconsistent."""


def _fixed_sequence(value: object, *, field: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise InstallationProfileTopologyError(f"{field} must be a sequence")
    normalized = tuple(value)
    if len(normalized) != RECEIVER_COUNT:
        raise InstallationProfileTopologyError(
            f"{field} must contain exactly {RECEIVER_COUNT} entries"
        )
    return normalized


def _normalize_transport_routes(
    value: object,
) -> tuple[tuple[int, int], ...]:
    routes = _fixed_sequence(value, field="logical_to_transport_routes")
    normalized: list[tuple[int, int]] = []
    for route in routes:
        if isinstance(route, (str, bytes)) or not isinstance(route, Sequence):
            raise InstallationProfileTopologyError(
                "each logical_to_transport_routes entry must be a (bus, device) pair"
            )
        pair = tuple(route)
        if len(pair) != 2 or any(type(item) is not int for item in pair):
            raise InstallationProfileTopologyError(
                "each logical_to_transport_routes entry must be a (bus, device) "
                "integer pair"
            )
        bus, device = pair
        if bus < 0 or device < 0:
            raise InstallationProfileTopologyError(
                "logical_to_transport_routes bus and device values must be "
                "non-negative"
            )
        normalized.append((bus, device))
    if len(set(normalized)) != RECEIVER_COUNT:
        raise InstallationProfileTopologyError(
            f"logical_to_transport_routes must contain {RECEIVER_COUNT} unique routes"
        )
    return tuple(normalized)


def _normalize_lane_order(
    value: object,
) -> tuple[int, ...]:
    lane_order = _fixed_sequence(value, field="physical_lane_order")
    if any(type(logical_id) is not int for logical_id in lane_order):
        raise InstallationProfileTopologyError(
            "physical_lane_order values must be integer logical receiver IDs"
        )
    if set(lane_order) != set(RECEIVER_IDS):
        raise InstallationProfileTopologyError(
            "physical_lane_order must be a permutation of logical receivers "
            + ",".join(str(item) for item in RECEIVER_IDS)
        )
    return lane_order  # type: ignore[return-value]


def _normalize_directions(
    value: object, *, field: str
) -> tuple[bool, ...]:
    directions = _fixed_sequence(value, field=field)
    if any(type(reversed_order) is not bool for reversed_order in directions):
        raise InstallationProfileTopologyError(
            f"{field} values must be booleans indexed by logical receiver ID"
        )
    return directions  # type: ignore[return-value]


def _normalize_strip_counts(value: object) -> tuple[int, ...]:
    strip_counts = _fixed_sequence(
        value, field="strip_counts_by_logical_receiver"
    )
    if any(type(width) is not int or width <= 0 for width in strip_counts):
        raise InstallationProfileTopologyError(
            "strip_counts_by_logical_receiver values must be positive integers"
        )
    if sum(strip_counts) != CANONICAL_GLOBAL_STRIP_COUNT:
        raise InstallationProfileTopologyError(
            "strip_counts_by_logical_receiver must partition the canonical "
            f"{CANONICAL_GLOBAL_STRIP_COUNT}-strip wall"
        )
    return strip_counts  # type: ignore[return-value]


@dataclass(frozen=True)
class InstallationProfileTopology:
    """Validated separation of the installation's coordinate domains.

    ``physical_lane_order`` is indexed by physical lane and contains logical
    receiver IDs.  The other three fields are indexed by logical receiver ID.
    Transport and host-strip direction are retained for domain separation but
    never influence installation-profile slices.
    """

    logical_to_transport_routes: tuple[tuple[int, int], ...]
    physical_lane_order: tuple[int, ...]
    reverse_host_strips_by_logical_receiver: tuple[bool, ...]
    reverse_native_strips_by_logical_receiver: tuple[bool, ...]
    strip_counts_by_logical_receiver: tuple[int, ...] = RECEIVER_STRIP_COUNTS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "logical_to_transport_routes",
            _normalize_transport_routes(self.logical_to_transport_routes),
        )
        object.__setattr__(
            self,
            "physical_lane_order",
            _normalize_lane_order(self.physical_lane_order),
        )
        object.__setattr__(
            self,
            "reverse_host_strips_by_logical_receiver",
            _normalize_directions(
                self.reverse_host_strips_by_logical_receiver,
                field="reverse_host_strips_by_logical_receiver",
            ),
        )
        object.__setattr__(
            self,
            "reverse_native_strips_by_logical_receiver",
            _normalize_directions(
                self.reverse_native_strips_by_logical_receiver,
                field="reverse_native_strips_by_logical_receiver",
            ),
        )
        object.__setattr__(
            self,
            "strip_counts_by_logical_receiver",
            _normalize_strip_counts(self.strip_counts_by_logical_receiver),
        )

    def physical_lane_for_logical_receiver(self, logical_id: int) -> int:
        """Return the left-to-right physical lane for one logical receiver."""

        if type(logical_id) is not int or logical_id not in RECEIVER_IDS:
            raise InstallationProfileTopologyError(
                f"logical receiver ID must be an integer from 0 through "
                f"{RECEIVER_COUNT - 1}"
            )
        return self.physical_lane_order.index(logical_id)

    def strip_origin_for_logical_receiver(self, logical_id: int) -> int:
        """Return the canonical global origin of one physical receiver view."""

        lane = self.physical_lane_for_logical_receiver(logical_id)
        return sum(
            self.strip_counts_by_logical_receiver[receiver_id]
            for receiver_id in self.physical_lane_order[:lane]
        )


IDENTITY_INSTALLATION_PROFILE_TOPOLOGY = InstallationProfileTopology(
    logical_to_transport_routes=((0, 0), (0, 1), (1, 0), (1, 1), (1, 2)),
    physical_lane_order=(0, 1, 2, 3, 4),
    reverse_host_strips_by_logical_receiver=(False,) * RECEIVER_COUNT,
    reverse_native_strips_by_logical_receiver=(False,) * RECEIVER_COUNT,
)

INSTALLED_INSTALLATION_PROFILE_TOPOLOGY = InstallationProfileTopology(
    logical_to_transport_routes=((0, 0), (0, 1), (1, 1), (1, 0), (1, 2)),
    physical_lane_order=(0, 1, 3, 2, 4),
    reverse_host_strips_by_logical_receiver=(False, False, True, True, False),
    reverse_native_strips_by_logical_receiver=(False, False, True, True, False),
)


def _require_topology(topology: object) -> InstallationProfileTopology:
    if not isinstance(topology, InstallationProfileTopology):
        raise TypeError("topology must be an InstallationProfileTopology")
    return topology


def _require_profile(profile: object, *, field: str) -> InstallationProfile:
    if not isinstance(profile, InstallationProfile):
        raise TypeError(f"{field} must be an InstallationProfile")
    return profile


def _receiver_profile(
    source: InstallationProfile,
    *,
    strip_origin: int,
    strip_count: int,
    reversed_strip_order: bool,
) -> InstallationProfile:
    stop = strip_origin + strip_count
    sections: dict[str, np.ndarray] = {}
    for name in SECTION_NAMES:
        rows = getattr(source, name)[strip_origin:stop]
        if reversed_strip_order:
            rows = rows[::-1]
        sections[name] = np.ascontiguousarray(rows)
    return InstallationProfile(
        global_strip_count=source.global_strip_count,
        leds_per_strip=source.leds_per_strip,
        strip_origin=strip_origin,
        strip_count=strip_count,
        clearance_radius=source.clearance_radius,
        calibration_digest=source.calibration_digest,
        reversed_strip_order=reversed_strip_order,
        **sections,
    )


def slice_installation_profile(
    profile: InstallationProfile,
    topology: InstallationProfileTopology,
) -> dict[int, InstallationProfile]:
    """Slice one canonical global profile into logical receiver views.

    Every derived section is sliced from the global bytes.  Geometry is never
    recomputed at receiver boundaries.  Transport routes and host-frame strip
    reversal are intentionally absent from the transform.
    """

    source = _require_profile(profile, field="profile")
    topology = _require_topology(topology)
    if (
        source.global_strip_count != CANONICAL_GLOBAL_STRIP_COUNT
        or source.strip_origin != 0
        or source.strip_count != CANONICAL_GLOBAL_STRIP_COUNT
        or source.reversed_strip_order
    ):
        raise InstallationProfileTopologyError(
            "profile slicing requires the canonical non-reversed global "
            f"{CANONICAL_GLOBAL_STRIP_COUNT}-strip source"
        )

    receiver_profiles: dict[int, InstallationProfile] = {}
    for logical_id in topology.physical_lane_order:
        receiver_profiles[logical_id] = _receiver_profile(
            source,
            strip_origin=topology.strip_origin_for_logical_receiver(logical_id),
            strip_count=topology.strip_counts_by_logical_receiver[logical_id],
            reversed_strip_order=(
                topology.reverse_native_strips_by_logical_receiver[logical_id]
            ),
        )
    return receiver_profiles


def _validated_receiver_slices(
    slices: Mapping[int, InstallationProfile],
    topology: InstallationProfileTopology,
) -> dict[int, InstallationProfile]:
    if not isinstance(slices, Mapping):
        raise TypeError("slices must be a mapping indexed by logical receiver ID")
    keys = tuple(slices.keys())
    if (
        len(keys) != RECEIVER_COUNT
        or any(type(logical_id) is not int for logical_id in keys)
        or set(keys) != set(RECEIVER_IDS)
    ):
        raise InstallationProfileTopologyError(
            "slices must contain each logical receiver ID "
            + ",".join(str(item) for item in RECEIVER_IDS)
            + " exactly once"
        )

    profiles = {
        logical_id: _require_profile(
            slices[logical_id], field=f"slices[{logical_id}]"
        )
        for logical_id in RECEIVER_IDS
    }
    origins = tuple(profile.strip_origin for profile in profiles.values())
    if len(set(origins)) != RECEIVER_COUNT:
        raise InstallationProfileTopologyError(
            "receiver profile strip origins must be unique"
        )

    reference = profiles[0]
    for logical_id, profile in profiles.items():
        if (
            profile.global_strip_count != CANONICAL_GLOBAL_STRIP_COUNT
            or profile.leds_per_strip != reference.leds_per_strip
            or profile.strip_count
            != topology.strip_counts_by_logical_receiver[logical_id]
        ):
            raise InstallationProfileTopologyError(
                f"receiver profile {logical_id} has mismatched geometry"
            )
        if profile.calibration_digest != reference.calibration_digest:
            raise InstallationProfileTopologyError(
                f"receiver profile {logical_id} has a mismatched calibration digest"
            )
        if profile.clearance_radius != reference.clearance_radius:
            raise InstallationProfileTopologyError(
                f"receiver profile {logical_id} has a mismatched clearance radius"
            )

        expected_origin = topology.strip_origin_for_logical_receiver(logical_id)
        if profile.strip_origin != expected_origin:
            raise InstallationProfileTopologyError(
                f"receiver profile {logical_id} has strip origin "
                f"{profile.strip_origin}; expected {expected_origin}"
            )
        expected_reversal = (
            topology.reverse_native_strips_by_logical_receiver[logical_id]
        )
        if profile.reversed_strip_order is not expected_reversal:
            raise InstallationProfileTopologyError(
                f"receiver profile {logical_id} has mismatched native strip direction"
            )
    return profiles


def reassemble_installation_profile(
    slices: Mapping[int, InstallationProfile],
    topology: InstallationProfileTopology,
) -> InstallationProfile:
    """Invert validated receiver views into canonical global strip order."""

    topology = _require_topology(topology)
    profiles = _validated_receiver_slices(slices, topology)
    reference = profiles[0]
    sections = {
        name: np.empty(
            (CANONICAL_GLOBAL_STRIP_COUNT, reference.leds_per_strip),
            dtype=getattr(reference, name).dtype,
        )
        for name in SECTION_NAMES
    }

    for logical_id, profile in profiles.items():
        start = profile.strip_origin
        stop = start + profile.strip_count
        for name in SECTION_NAMES:
            rows = getattr(profile, name)
            if profile.reversed_strip_order:
                rows = rows[::-1]
            sections[name][start:stop] = rows

    return InstallationProfile(
        global_strip_count=CANONICAL_GLOBAL_STRIP_COUNT,
        leds_per_strip=reference.leds_per_strip,
        strip_origin=0,
        strip_count=CANONICAL_GLOBAL_STRIP_COUNT,
        clearance_radius=reference.clearance_radius,
        calibration_digest=reference.calibration_digest,
        reversed_strip_order=False,
        **sections,
    )
