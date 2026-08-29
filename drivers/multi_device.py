#!/usr/bin/env python3
"""
Multi-Device LED Grid Controller - SPI version
Controls multiple ESP32 devices via SPI with different CS pins
"""

import hashlib
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple, Optional

import numpy as np

from drivers.spi_controller import (
    CAPABILITY_ALIGNED_ENVELOPE_V1,
    CAPABILITY_EXPLICIT_BASE_OWNERSHIP,
    CAPABILITY_PRESENTATION_CONTEXT_V1,
    CAPABILITY_STATIC_LOCAL_BACKGROUND,
    CAPABILITY_STATUS_V3,
    CAPABILITY_SPARSE_OVERLAY_V1,
    CAPABILITY_SPARSE_OVERLAY_BATCH_V1,
    CAPABILITY_STATUS_V6,
    CAPABILITY_NATIVE_MODULE_V2,
    CAPABILITY_NATIVE_CACHE_V1,
    CAPABILITY_NATIVE_TYPED_PARAMETERS_V1,
    CAPABILITY_NATIVE_QUARANTINE_V1,
    CAPABILITY_NATIVE_GUARDED_LOADER_V1,
    LEDController,
    MAX_NATIVE_CHUNK_BYTES,
    MAX_RGBA_PIXELS_PER_BATCH_SPAN,
    OVERLAY_LOCAL_PIXELS,
    OVERLAY_FORMAT_PREMULTIPLIED_RGBA8,
    OVERLAY_UPDATE_DELTA,
    OVERLAY_UPDATE_FULL_SNAPSHOT,
    SPI_RESPONSE_QUEUE_DEPTH,
    TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS,
    SPI_BUS,
    SPI_MODE,
    SPI_SPEED,
)
from drivers.led_layout import (
    DEFAULT_LEDS_PER_STRIP,
    STRIPS_PER_DEVICE,
    WALL_PHYSICAL_LANE_ORDER,
    WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER,
    WALL_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER,
    DeviceMapEntry,
    logical_strip_count,
    wall_device_map,
)

LOCAL_BACKGROUND_REQUIRED_CAPABILITIES = (
    CAPABILITY_ALIGNED_ENVELOPE_V1
    | CAPABILITY_STATIC_LOCAL_BACKGROUND
    | CAPABILITY_PRESENTATION_CONTEXT_V1
    | CAPABILITY_STATUS_V3
    | CAPABILITY_EXPLICIT_BASE_OWNERSHIP
)
SPARSE_OVERLAY_REQUIRED_CAPABILITIES = (
    LOCAL_BACKGROUND_REQUIRED_CAPABILITIES
    | CAPABILITY_SPARSE_OVERLAY_V1
    | CAPABILITY_SPARSE_OVERLAY_BATCH_V1
)
NATIVE_BACKGROUND_REQUIRED_CAPABILITIES = (
    CAPABILITY_ALIGNED_ENVELOPE_V1
    | CAPABILITY_STATUS_V6
    | CAPABILITY_NATIVE_MODULE_V2
    | CAPABILITY_NATIVE_CACHE_V1
    | CAPABILITY_NATIVE_TYPED_PARAMETERS_V1
    | CAPABILITY_NATIVE_QUARANTINE_V1
    | CAPABILITY_NATIVE_GUARDED_LOADER_V1
    | CAPABILITY_EXPLICIT_BASE_OWNERSHIP
    | CAPABILITY_PRESENTATION_CONTEXT_V1
)


class MultiDeviceLEDController:
    """Multi-device LED controller that manages multiple ESP32 devices"""

    def with_receiver_hybrid_transport_policy(
        self, transport_policy, *, physical_lane_order=WALL_PHYSICAL_LANE_ORDER,
        reverse_strips_by_logical_receiver=(
            WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER
        ),
        reverse_native_strips_by_logical_receiver=(
            WALL_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER
        ),
    ):
        """Return the controller facade selected by one explicit policy.

        Ordinary host streaming and the strict all-readable receiver path keep
        this controller unchanged.  The installed degraded SPI1 exception is
        isolated in its own facade so strict transaction methods never acquire
        implicit write-only branches.
        """

        if transport_policy in (None, "", "off", "strict_all_readable_v1"):
            return self
        from drivers.degraded_receiver_hybrid import (
            DEGRADED_SPI1_TRANSPORT_POLICY,
            DegradedReceiverHybridController,
        )

        if transport_policy != DEGRADED_SPI1_TRANSPORT_POLICY:
            raise ValueError(
                f"unsupported receiver hybrid transport policy: "
                f"{transport_policy!r}"
            )
        return DegradedReceiverHybridController(
            self,
            physical_lane_order=physical_lane_order,
            reverse_strips_by_logical_receiver=(
                reverse_strips_by_logical_receiver
            ),
            reverse_native_strips_by_logical_receiver=(
                reverse_native_strips_by_logical_receiver
            ),
        )

    def _controller_lock(self):
        """Lazily initialize orchestration state for legacy/test construction."""
        if not hasattr(self, "_transport_lock"):
            self._transport_lock = threading.RLock()
        if not hasattr(self, "receiver_strip_counts"):
            devices = tuple(getattr(self, "devices", ()))
            if devices and all(
                type(getattr(device, "strip_count", None)) is int
                and getattr(device, "strip_count") > 0
                for device in devices
            ):
                widths = tuple(device.strip_count for device in devices)
            else:
                widths = (getattr(self, "strips_per_device", 8),) * int(
                    getattr(self, "num_devices", len(devices) or 1)
                )
            offsets = []
            running = 0
            for width in widths:
                offsets.append(running)
                running += width
            self.receiver_strip_counts = widths
            self.receiver_global_strip_offsets = tuple(offsets)
            leds_per_strip = int(getattr(self, "leds_per_strip", 138))
            self.receiver_pixel_counts = tuple(
                width * leds_per_strip for width in widths
            )
            self.receiver_pixel_offsets = tuple(
                offset * leds_per_strip for offset in offsets
            )
            if not hasattr(self, "strip_count"):
                self.strip_count = running
            if not hasattr(self, "total_leds"):
                self.total_leds = running * leds_per_strip
        if not hasattr(self, "reverse_native_strips_by_logical_receiver"):
            self.reverse_native_strips_by_logical_receiver = (
                False,
            ) * len(self.receiver_strip_counts)
        if not hasattr(self, "reverse_host_strips_by_logical_receiver"):
            self.reverse_host_strips_by_logical_receiver = (
                False,
            ) * len(self.receiver_strip_counts)
        if not hasattr(self, "receiver_lane_masks"):
            self.receiver_lane_masks = tuple(
                (1 << width) - 1 if width < 8 else 0xFF
                for width in self.receiver_strip_counts
            )
        if not hasattr(self, "_local_background_active"):
            self._local_background_active = False
            self._local_background_context_digest = None
            self._local_background_parameters = {}
            self._local_background_status = {
                "state": "host_full_scene", "operation": "legacy_initialize"
            }
        if not hasattr(self, "_display_ownership_known"):
            # Objects constructed before Phase 3A are already host-frame-only;
            # preserve their partial-update behavior.
            self._display_ownership_known = True
        if not hasattr(self, "_sparse_overlay_session_id"):
            self._sparse_overlay_session_id = None
            self._sparse_overlay_generation = 0
            self._sparse_overlay_snapshot_digest = None
        if not hasattr(self, "_installation_profile_wall"):
            self._installation_profile_wall = None
            self._installation_profile_status = {
                "state": "idle",
                "operation": "legacy_initialize",
                "rollout_enabled": False,
            }
        if not hasattr(self, "_receiver_geometry_profile_enabled"):
            # Legacy/test instances predate the rollout gate and therefore
            # retain the product-safe, command-silent default.
            self._receiver_geometry_profile_enabled = False
        if not hasattr(self, "_receiver_native_modules_enabled"):
            self._receiver_native_modules_enabled = False
            self._native_background_active = False
            self._native_background_binding = None
            self._native_background_descriptor = None
            self._native_background_parameters = {}
            self._native_background_candidate = None
            self._native_background_context = None
            self._native_background_profile_digest = None
            self._native_background_parameter_set = None
            self._native_background_status = {
                "state": "idle",
                "operation": "legacy_initialize",
                "rollout_enabled": False,
                "progress": 0.0,
            }
        return self._transport_lock
    
    def __init__(self, 
                 num_devices: int = 1,
                 bus: int = SPI_BUS,
                 speed: int = SPI_SPEED,
                 mode: int = SPI_MODE,
                 strips_per_device: int = STRIPS_PER_DEVICE,
                 strip_count: Optional[int] = None,
                 leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
                 debug: bool = False,
                 parallel: bool = True,
                 device_map: Optional[List[DeviceMapEntry]] = None,
                 receiver_geometry_profile: bool = False,
                 receiver_native_modules: bool = False,
                 reverse_host_strips_by_logical_receiver: tuple[bool, ...] | None = None,
                 reverse_native_strips_by_logical_receiver: tuple[bool, ...] | None = None,
                 receiver_lane_masks: tuple[int, ...] | None = None,
                 receiver_strip_counts: tuple[int, ...] | None = None,
                 receiver_global_strip_offsets: tuple[int, ...] | None = None,
                 receiver_spi_speeds_hz: tuple[int, ...] | None = None,
                 fec_receiver_ids: tuple[int, ...] = ()):
        """
        Initialize multi-device LED controller
        
        Args:
            num_devices: Number of ESP32-S3 receivers (default: 1)
            bus: SPI bus number (default: 0)
            speed: SPI speed in Hz (default: SPI_SPEED, currently 20 MHz)
            mode: SPI mode (default: SPI_MODE, currently mode 0)
            strips_per_device: LED strips per receiver (default: 8)
            strip_count: Visible strips. May be smaller than device capacity when
                the last receiver drives fewer than eight lanes.
            leds_per_strip: LEDs per strip (installed default: 138)
            debug: Enable debug output
            parallel: Send data to devices in parallel using threads
            device_map: Optional list of (bus, device) tuples for each device
            receiver_geometry_profile: Explicit host rollout gate for receiver
                profile traffic. Defaults off, so ordinary deployments never
                emit commands 0x40 through 0x47.
            receiver_native_modules: Explicit host rollout gate for trusted
                repository-native module traffic. Defaults off, so ordinary
                streaming never emits commands 0x50 through 0x5c.
            reverse_host_strips_by_logical_receiver: Host-streamed frame lane
                direction by logical receiver. This affects only Pi-authored RGB
                and overlay pixels; it never changes receiver-native coordinates.
            reverse_native_strips_by_logical_receiver: Native renderer lane
                direction by logical receiver. This is separate from host-frame
                strip reversal and defaults false everywhere.
            receiver_strip_counts: Exact local strip width by logical receiver.
                Defaults to the legacy uniform ``strips_per_device`` layout.
            receiver_global_strip_offsets: Exact global strip origin by logical
                receiver. Defaults to contiguous origins in logical-ID order.
            receiver_spi_speeds_hz: Optional exact SPI clock by logical
                receiver. When omitted, the legacy per-bus environment override
                remains authoritative.
            receiver_lane_masks: Exact physical output-lane mask by logical
                receiver. Defaults to the lowest ``local_strip_count`` lanes;
                installed callers pass an explicit all-lane broadcast mask for
                the dedicated one-strip tail receiver.
            fec_receiver_ids: Explicit logical receivers allowed to negotiate
                aligned-envelope v7 for complete SET_ALL frames. Empty keeps
                every receiver on aligned-envelope v1.
        """
        if type(receiver_geometry_profile) is not bool:
            raise TypeError("receiver_geometry_profile must be a boolean")
        if type(receiver_native_modules) is not bool:
            raise TypeError("receiver_native_modules must be a boolean")
        if type(num_devices) is not int or not 1 <= num_devices <= 0xFF:
            raise ValueError("num_devices must be an integer from 1 through 255")
        if (
            type(fec_receiver_ids) is not tuple
            or any(type(value) is not int for value in fec_receiver_ids)
            or len(set(fec_receiver_ids)) != len(fec_receiver_ids)
            or any(not 0 <= value < num_devices for value in fec_receiver_ids)
        ):
            raise ValueError(
                "fec_receiver_ids must be a tuple of unique configured logical IDs"
            )
        if receiver_strip_counts is None:
            visible_strips = logical_strip_count(
                num_devices, strips_per_device, strip_count
            )
            receiver_strip_counts = tuple(
                min(strips_per_device, visible_strips - index * strips_per_device)
                for index in range(num_devices)
                if visible_strips - index * strips_per_device > 0
            )
            if len(receiver_strip_counts) != num_devices:
                raise ValueError(
                    "strip_count must assign at least one visible strip to "
                    "every configured receiver"
                )
        if (
            type(receiver_strip_counts) is not tuple
            or len(receiver_strip_counts) != num_devices
            or any(type(width) is not int or not 1 <= width <= 8
                   for width in receiver_strip_counts)
        ):
            raise TypeError(
                "receiver_strip_counts must be a tuple of one integer from 1 "
                "through 8 per receiver"
            )
        if receiver_global_strip_offsets is None:
            running_offset = 0
            calculated_offsets = []
            for width in receiver_strip_counts:
                calculated_offsets.append(running_offset)
                running_offset += width
            receiver_global_strip_offsets = tuple(calculated_offsets)
        if (
            type(receiver_global_strip_offsets) is not tuple
            or len(receiver_global_strip_offsets) != num_devices
            or any(type(offset) is not int or not 0 <= offset <= 0xFFFF
                   for offset in receiver_global_strip_offsets)
        ):
            raise TypeError(
                "receiver_global_strip_offsets must be a tuple of one uint16 "
                "origin per receiver"
            )
        occupied_strips = []
        for receiver_id, (offset, width) in enumerate(zip(
            receiver_global_strip_offsets, receiver_strip_counts
        )):
            occupied_strips.extend((strip, receiver_id)
                                   for strip in range(offset, offset + width))
        strip_ids = [strip for strip, _receiver_id in occupied_strips]
        if len(set(strip_ids)) != len(strip_ids) or set(strip_ids) != set(
            range(max(strip_ids) + 1)
        ):
            raise ValueError(
                "receiver strip ranges must partition one contiguous global wall"
            )
        direction_fields = (
            (
                "reverse_host_strips_by_logical_receiver",
                reverse_host_strips_by_logical_receiver,
            ),
            (
                "reverse_native_strips_by_logical_receiver",
                reverse_native_strips_by_logical_receiver,
            ),
        )
        directions = {}
        for field, value in direction_fields:
            if value is None:
                value = (False,) * num_devices
            if type(value) is not tuple:
                raise TypeError(
                    f"{field} must be a tuple of one boolean per receiver"
                )
            if (
                len(value) != num_devices
                or any(type(reverse) is not bool for reverse in value)
            ):
                raise TypeError(
                    f"{field} must be a tuple of {num_devices} booleans "
                    "(one per receiver)"
                )
            directions[field] = value
        if receiver_lane_masks is None:
            receiver_lane_masks = tuple(
                (1 << width) - 1 if width < 8 else 0xFF
                for width in receiver_strip_counts
            )
        if (
            type(receiver_lane_masks) is not tuple
            or len(receiver_lane_masks) != num_devices
            or any(type(mask) is not int or not 1 <= mask <= 0xFF
                   for mask in receiver_lane_masks)
        ):
            raise TypeError(
                "receiver_lane_masks must be a tuple of one nonzero byte per receiver"
            )
        if any(
            mask.bit_count() < width
            for mask, width in zip(receiver_lane_masks, receiver_strip_counts)
        ):
            raise ValueError(
                "receiver_lane_masks cannot expose fewer physical lanes than "
                "the receiver's logical strip width"
            )
        self.num_devices = num_devices
        self.fec_receiver_ids = fec_receiver_ids
        self.strips_per_device = strips_per_device
        self.receiver_strip_counts = receiver_strip_counts
        self.receiver_global_strip_offsets = receiver_global_strip_offsets
        self.receiver_lane_masks = receiver_lane_masks
        if receiver_spi_speeds_hz is not None and (
            type(receiver_spi_speeds_hz) is not tuple
            or len(receiver_spi_speeds_hz) != num_devices
            or any(type(value) is not int or value <= 0
                   for value in receiver_spi_speeds_hz)
        ):
            raise TypeError(
                "receiver_spi_speeds_hz must be a tuple of one positive integer "
                "per receiver"
            )
        self.receiver_spi_speeds_hz = receiver_spi_speeds_hz
        self.leds_per_strip = leds_per_strip
        self.debug = debug
        self.parallel = parallel
        self._executor = None
        
        # Semantic receiver ranges are the geometry authority. If a caller also
        # supplied a visible strip count, fail closed on any disagreement.
        geometry_strip_count = max(
            offset + width for offset, width in zip(
                self.receiver_global_strip_offsets, self.receiver_strip_counts
            )
        )
        if strip_count is not None and strip_count != geometry_strip_count:
            raise ValueError(
                "strip_count does not match receiver strip ranges: "
                f"{strip_count} != {geometry_strip_count}"
            )
        self.strip_count = geometry_strip_count
        self.total_leds = self.strip_count * leds_per_strip
        # Retained for legacy callers on uniform layouts. Internal routing uses
        # the exact per-receiver pixel counts below.
        self.leds_per_device = strips_per_device * leds_per_strip
        self.receiver_pixel_counts = tuple(
            width * leds_per_strip for width in self.receiver_strip_counts
        )
        self.receiver_pixel_offsets = tuple(
            offset * leds_per_strip for offset in self.receiver_global_strip_offsets
        )
        self._logical_frames_sent = 0
        self._logical_wall_frame_sequence = 0
        self._transport_lock = threading.RLock()
        self._local_background_active = False
        self._local_background_context_digest = None
        self._local_background_parameters: Dict[str, int] = {}
        self._local_background_status: Dict[str, Any] = {
            "state": "stopped", "operation": "initialize"
        }
        self._receiver_status_refresh: Dict[str, Any] = {
            "request_id": None,
            "completed_at": None,
            "passed": False,
            "errors": ["no explicit receiver-status refresh has completed"],
        }
        self._display_ownership_known = False
        self._sparse_overlay_session_id = None
        self._sparse_overlay_generation = 0
        self._sparse_overlay_snapshot_digest = None
        self._installation_profile_wall = None
        self._receiver_geometry_profile_enabled = receiver_geometry_profile
        self._receiver_native_modules_enabled = receiver_native_modules
        self.reverse_host_strips_by_logical_receiver = directions[
            "reverse_host_strips_by_logical_receiver"
        ]
        self.reverse_native_strips_by_logical_receiver = (
            directions["reverse_native_strips_by_logical_receiver"]
        )
        self._installation_profile_status: Dict[str, Any] = {
            "state": "idle",
            "operation": "initialize",
            "rollout_enabled": receiver_geometry_profile,
        }
        self._native_background_active = False
        self._native_background_binding = None
        self._native_background_descriptor = None
        self._native_background_parameters: Dict[str, Any] = {}
        self._native_background_candidate = None
        self._native_background_context = None
        self._native_background_profile_digest = None
        self._native_background_parameter_set = None
        self._native_background_status: Dict[str, Any] = {
            "state": "idle",
            "operation": "initialize",
            "rollout_enabled": receiver_native_modules,
            "progress": 0.0,
        }
        self._monotonic_ns = time.monotonic_ns
        
        # For compatibility with animation system
        self.inline_show = True
        self.current_brightness = None
        
        if self.debug:
            print("Multi-Device LED Controller")
            print(f"  Devices: {num_devices}")
            print(f"  Strips per device: {strips_per_device}")
            print(f"  LEDs per strip: {leds_per_strip}")
            print(f"  Total strips: {self.strip_count}")
            print(f"  Total LEDs: {self.total_leds}")
            print(f"  Parallel mode: {parallel}")
        
        # Build device map (auto-detects SPI1 fallback if needed)
        if device_map is None:
            self.device_map = self._build_device_map(num_devices, bus)
        else:
            if (
                not isinstance(device_map, (list, tuple))
                or len(device_map) != num_devices
                or any(
                    not isinstance(entry, (list, tuple))
                    or len(entry) != 2
                    or any(type(value) is not int or value < 0 for value in entry)
                    for entry in device_map
                )
            ):
                raise ValueError(
                    "device_map must contain exactly one nonnegative "
                    "(bus, chip_select) pair per receiver"
                )
            self.device_map = [tuple(entry) for entry in device_map]
        self._devices_by_bus = {}
        for device_id, (device_bus, _chip_select) in enumerate(self.device_map):
            self._devices_by_bus.setdefault(device_bus, []).append(device_id)
        if parallel and len(self._devices_by_bus) > 1:
            self._executor = ThreadPoolExecutor(
                max_workers=len(self._devices_by_bus),
                thread_name_prefix="led-spi-bus",
            )
        map_parts = []
        for idx, entry in enumerate(self.device_map):
            bus, dev = entry
            map_parts.append(f"dev{idx}=spidev{bus}.{dev}")
        print(f"[LEDGRID] SPI device map ({num_devices} devices): {', '.join(map_parts)}")
        if (
            self.strip_count == 33
            and self.receiver_strip_counts[-1] == 1
            and self.receiver_lane_masks[-1] == 0xFF
        ):
            print(
                "[LEDGRID] Extra strip 32 -> dev4 one logical strip, "
                "broadcast across physical lanes (SPI1 CE2)"
            )
        
        # Initialize individual device controllers
        self.devices: List[LEDController] = []
        for device_index, (device_bus, device_id) in enumerate(self.device_map):
            if self.debug:
                print(f"\nInitializing Device {device_index} on /dev/spidev{device_bus}.{device_id}")
            
            device = LEDController(
                bus=device_bus,
                device=device_id,  # CE0, CE1, etc.
                speed=(
                    self.receiver_spi_speeds_hz[device_index]
                    if self.receiver_spi_speeds_hz is not None
                    else self._resolve_speed(device_bus, speed)
                ),
                mode=self._resolve_mode(device_bus, mode),
                strips=self.receiver_strip_counts[device_index],
                leds_per_strip=leds_per_strip,
                debug=debug,
                logical_device_id=device_index,
                reverse_native_strip_order=(
                    self.reverse_native_strips_by_logical_receiver[device_index]
                ),
                global_strip_offset=(
                    self.receiver_global_strip_offsets[device_index]
                ),
                fec_transport=device_index in self.fec_receiver_ids,
            )
            self.devices.append(device)

        # All bus/controller objects must exist before observability I/O begins.
        # Confirmed v3 receivers get their physical logical identity immediately;
        # older receivers retain the exact legacy five-byte CONFIG packet.
        self._initialize_receiver_identity_observability()
        
        if self.debug:
            print(f"\n✓ All {num_devices} devices initialized\n")

    def _initialize_receiver_identity_observability(self):
        """Best-effort topology provisioning without blocking legacy streaming."""
        for index, device in enumerate(self.devices):
            try:
                # The ESP32 slave keeps a two-deep response queue: a query
                # clocks out one older response before its new snapshot can be
                # observed. Drain depth+1 before deciding whether CONFIG may
                # use the explicit identity/topology form.
                # Two identical boot-time queue entries may precede the first
                # receiver-owned packet-counter advance. Drain enough samples
                # to prove three fresh capability observations before CONFIG.
                for _ in range(
                    SPI_RESPONSE_QUEUE_DEPTH
                    + TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS
                ):
                    device.query_receiver_status()
                device.logical_device_id = index
                device.configure()
                device.set_lane_mask(self.receiver_lane_masks[index])
                if int(device.get_stats().get("receiver_status_version", 0) or 0) >= 3:
                    status = None
                    # CONFIG and SET_LANE_MASK each enqueue status behind
                    # earlier replies. Require a causally post-command sample
                    # before accepting the installed topology.
                    for _ in range(SPI_RESPONSE_QUEUE_DEPTH + 1):
                        status = device.query_receiver_status()
                    expected_topology = {
                        "receiver_logical_device": index,
                        "receiver_active_strips": self.receiver_strip_counts[index],
                        "receiver_global_strip_offset": (
                            self.receiver_global_strip_offsets[index]
                        ),
                        "receiver_lane_mask": self.receiver_lane_masks[index],
                    }
                    mismatches = [
                        f"{field}={status.get(field)!r}, expected {expected!r}"
                        for field, expected in expected_topology.items()
                        if status.get(field) != expected
                    ]
                    if mismatches:
                        raise RuntimeError(
                            "reported topology mismatch after CONFIG/lane-mask: "
                            + "; ".join(mismatches)
                        )
            except Exception as exc:
                print(
                    f"[LEDGRID] Receiver {index} topology observability unavailable: {exc}; "
                    "continuing with ordinary host streaming",
                    file=sys.stderr,
                )

    def refresh_receiver_status(self, request_id: str) -> Dict[str, Any]:
        """Clock out and record a fresh serialized status snapshot on every board."""
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError("receiver-status request_id must be 1..128 characters")

        errors = []
        with self._controller_lock():
            for index, device in enumerate(self.devices):
                try:
                    before = int(
                        device.get_stats().get("receiver_status_responses", 0) or 0
                    )
                    status = None
                    # Two snapshots may already be queued in the slave. A query
                    # consumes one old response and only then queues a new one,
                    # so depth+1 transfers are required for the final parsed
                    # snapshot to causally follow this explicit request.
                    for _ in range(SPI_RESPONSE_QUEUE_DEPTH + 1):
                        status = device.query_receiver_status()
                    after = int(
                        device.get_stats().get("receiver_status_responses", 0) or 0
                    )
                    if not isinstance(status, dict) or after <= before:
                        raise RuntimeError("no new receiver status response was parsed")
                except Exception as exc:
                    errors.append({"logical_device": index, "error": str(exc)})

            self._receiver_status_refresh = {
                "request_id": request_id,
                "completed_at": time.time(),
                "passed": not errors,
                "errors": errors,
            }
            return dict(self._receiver_status_refresh)

    def installation_profile_wall(self):
        """Return the stable real-receiver facade in logical receiver order."""

        from drivers.installation_profile_receiver import SpiInstallationProfileWall
        from animation.core.installation_profile_transaction import RECEIVER_COUNT

        with self._controller_lock():
            if not self._receiver_geometry_profile_enabled:
                raise RuntimeError(
                    "receiver_geometry_profile rollout gate is disabled"
                )
            if self.num_devices != RECEIVER_COUNT or len(self.devices) != RECEIVER_COUNT:
                raise RuntimeError(
                    "installation-profile transactions require exactly "
                    f"{RECEIVER_COUNT} receivers"
                )
            if self._installation_profile_wall is None:
                self._installation_profile_wall = SpiInstallationProfileWall(
                    self.devices, enabled=True
                )
            return self._installation_profile_wall

    def install_installation_profile(self, candidate):
        """Run one display-inert, all-board profile cache transaction.

        The controller-wide lock prevents complete frames, sparse traffic, and
        profile chunks from interleaving on SPI. Receivers retain their current
        display base throughout; this method never changes ownership state.
        """

        from animation.core.installation_profile_transaction import (
            InstallationProfileTransaction,
        )

        with self._controller_lock():
            wall = self.installation_profile_wall()
            result = InstallationProfileTransaction(wall).install(candidate)
            self._installation_profile_status = {
                "state": (
                    "active"
                    if result.success
                    else (
                        "compensated" if result.compensated else "degraded"
                    )
                ),
                "operation": "install",
                "rollout_enabled": True,
                "success": result.success,
                "changed": result.changed,
                "profile_id": result.profile_id,
                "failed_phase": (
                    result.failed_phase.value if result.failed_phase else None
                ),
                "failed_receiver_id": result.failed_receiver_id,
                "compensated": result.compensated,
                "error": result.error,
                "wall_health": result.wall_status.health.value,
                "active_profile_id": result.wall_status.active_profile_id,
            }
            return result

    def _managed_native_background(self, resolved):
        from animation.core.native_background_operation import (
            NativeReceiverTopology,
            managed_native_background,
        )

        topology = tuple(
            NativeReceiverTopology(
                logical_receiver_id=receiver_id,
                global_strip_offset=self.receiver_global_strip_offsets[receiver_id],
                local_strips=self.receiver_strip_counts[receiver_id],
                reverse_local_strip_order=(
                    self.reverse_native_strips_by_logical_receiver[receiver_id]
                ),
            )
            for receiver_id in range(self.num_devices)
        )
        candidate = managed_native_background(resolved, topology)
        if (
            candidate.global_strips != self.strip_count
            or candidate.leds_per_strip != self.leds_per_strip
        ):
            raise RuntimeError(
                "managed native background does not match controller geometry"
            )
        return candidate

    def _require_native_enabled(self):
        if not self._receiver_native_modules_enabled:
            raise RuntimeError("receiver_native_modules rollout gate is disabled")

    @staticmethod
    def _native_status_binding(status, prefix):
        from animation.core.native_background_operation import (
            NativeBackgroundBinding,
            NativeBackgroundOperationError,
        )

        bundle = status.get(f"receiver_native_{prefix}_bundle_digest")
        payload = status.get(f"receiver_native_{prefix}_payload_digest")
        if bundle is None and payload is None:
            return None
        if bundle is None or payload is None:
            raise NativeBackgroundOperationError(
                f"receiver reported incomplete {prefix} native binding"
            )
        return NativeBackgroundBinding(bundle, payload)

    def _fresh_native_status(self, receiver_id, *, require_capabilities=True):
        from animation.core.native_background_operation import (
            NativeBackgroundOperationError,
        )

        if type(receiver_id) is not int or not 0 <= receiver_id < len(self.devices):
            raise ValueError("receiver_id is outside the configured roster")
        device = self.devices[receiver_id]
        status = None
        try:
            # The first response can only discover status-v6. Two queued older
            # snapshots plus one negotiated extension require four transfers
            # for a causally fresh result.
            for _ in range(SPI_RESPONSE_QUEUE_DEPTH + 2):
                status = device.query_receiver_status()
        except Exception as exc:
            raise NativeBackgroundOperationError(
                f"receiver {receiver_id} native status refresh failed: {exc}"
            ) from exc
        if not isinstance(status, dict):
            raise NativeBackgroundOperationError(
                f"receiver {receiver_id} returned no native status"
            )
        capabilities = int(status.get("receiver_capabilities", 0) or 0)
        if (
            int(status.get("receiver_status_version", 0) or 0) < 6
            or capabilities & CAPABILITY_STATUS_V6 != CAPABILITY_STATUS_V6
        ):
            raise NativeBackgroundOperationError(
                f"receiver {receiver_id} returned no coherent native status-v6"
            )
        if require_capabilities and (
            capabilities & NATIVE_BACKGROUND_REQUIRED_CAPABILITIES
            != NATIVE_BACKGROUND_REQUIRED_CAPABILITIES
        ):
            raise NativeBackgroundOperationError(
                f"receiver {receiver_id} lacks required native-module capabilities"
            )
        if status.get("receiver_logical_device") != receiver_id:
            raise NativeBackgroundOperationError(
                f"receiver {receiver_id} reported logical identity "
                f"{status.get('receiver_logical_device')!r}"
            )
        flags = status.get("receiver_native_flags")
        if type(flags) is not int or not 0 <= flags <= 0xFF:
            raise NativeBackgroundOperationError(
                f"receiver {receiver_id} reported invalid native flags"
            )
        if require_capabilities and (
            not status.get("receiver_native_ready")
            or not status.get("receiver_native_cache_integrity_ok")
        ):
            raise NativeBackgroundOperationError(
                f"receiver {receiver_id} native cache/loader is not ready"
            )
        for prefix, bit in (("active", 0x08), ("staged", 0x10), ("rollback", 0x20)):
            binding = self._native_status_binding(status, prefix)
            if bool(flags & bit) != (binding is not None):
                raise NativeBackgroundOperationError(
                    f"receiver {receiver_id} reported inconsistent {prefix} binding"
                )
        return status

    def _fresh_native_statuses(self, *, require_capabilities=True):
        return tuple(
            self._fresh_native_status(
                receiver_id, require_capabilities=require_capabilities
            )
            for receiver_id in range(len(self.devices))
        )

    @staticmethod
    def _require_native_ack(status, operation, receiver_id):
        if not isinstance(status, dict):
            raise RuntimeError(f"receiver {receiver_id} returned no {operation} status")
        if int(status.get("receiver_status_version", 0) or 0) < 6:
            raise RuntimeError(
                f"receiver {receiver_id} returned pre-v6 {operation} status"
            )
        result = int(status.get("receiver_native_result", 0) or 0)
        if result != 1:
            raise RuntimeError(
                f"receiver {receiver_id} rejected {operation}: "
                f"{status.get('receiver_native_result_name', result)}"
            )
        return status

    @staticmethod
    def _native_snapshots(statuses):
        return tuple({
            "active": MultiDeviceLEDController._native_status_binding(status, "active"),
            "staged": MultiDeviceLEDController._native_status_binding(status, "staged"),
            "rollback": MultiDeviceLEDController._native_status_binding(status, "rollback"),
        } for status in statuses)

    def _restore_native_snapshots(self, snapshots):
        errors = []
        for receiver_id, (device, snapshot) in enumerate(zip(self.devices, snapshots)):
            try:
                current = self._fresh_native_status(receiver_id)
                status = device.native_restore(
                    expected_generation=current["receiver_native_state_generation"],
                    active_binding=snapshot["active"],
                    staged_binding=snapshot["staged"],
                    rollback_binding=snapshot["rollback"],
                )
                self._require_native_ack(status, "native restore", receiver_id)
            except Exception as exc:
                errors.append({"logical_device": receiver_id, "error": str(exc)})
        return errors

    def _native_capability_report(self, statuses):
        return {
            "required_capabilities": NATIVE_BACKGROUND_REQUIRED_CAPABILITIES,
            "devices": [
                {
                    "logical_device": receiver_id,
                    "capabilities": int(status.get("receiver_capabilities", 0) or 0),
                    "local_strip_count": self.receiver_strip_counts[receiver_id],
                    "global_strip_offset": self.receiver_global_strip_offsets[receiver_id],
                    "bus": self.device_map[receiver_id][0],
                    "chip_select": self.device_map[receiver_id][1],
                    "reverse_host_strip_order": (
                        self.reverse_host_strips_by_logical_receiver[receiver_id]
                    ),
                    "reverse_native_strip_order": (
                        self.reverse_native_strips_by_logical_receiver[receiver_id]
                    ),
                    "physical_output_lane_mask": self.receiver_lane_masks[receiver_id],
                }
                for receiver_id, status in enumerate(statuses)
            ],
        }

    def _native_agreement(self, statuses):
        return {
            "exact_roster": True,
            "verified_receiver_ids": list(range(self.num_devices)),
            "state_generations": [
                int(status["receiver_native_state_generation"])
                for status in statuses
            ],
        }

    @staticmethod
    def _require_native_probe_identity(status, candidate, receiver_id, operation):
        from animation.core.native_background_operation import (
            NativeBackgroundOperationError,
        )

        echoed = status.get("receiver_native_last_probe_payload_digest")
        if echoed != candidate.binding.payload_digest:
            raise NativeBackgroundOperationError(
                f"receiver {receiver_id} {operation} echoed payload digest "
                f"{echoed!r}, expected {candidate.binding.payload_digest!r} "
                f"for bundle {candidate.binding.bundle_digest}"
            )
        return status

    def probe_native_background(self, resolved):
        """Probe every exact receiver cache for one managed executable payload."""
        candidate = self._managed_native_background(resolved)
        with self._controller_lock():
            self._require_native_enabled()
            statuses = self._fresh_native_statuses()
            devices = []
            for receiver_id, device in enumerate(self.devices):
                status = self._require_native_probe_identity(
                    self._require_native_ack(
                        device.native_probe(
                            payload_digest=candidate.binding.payload_digest
                        ),
                        "native probe",
                        receiver_id,
                    ),
                    candidate,
                    receiver_id,
                    "native probe",
                )
                devices.append({
                    "logical_device": receiver_id,
                    "found": bool(status.get("receiver_native_probe_found")),
                    "payload_digest": status.get(
                        "receiver_native_last_probe_payload_digest"
                    ),
                })
            result = {
                "state": "present" if all(item["found"] for item in devices) else "missing",
                "operation": "probe",
                "rollout_enabled": True,
                "progress": 1.0,
                "bundle_digest": candidate.binding.bundle_digest,
                "payload_digest": candidate.binding.payload_digest,
                "devices": devices,
                "capability_report": self._native_capability_report(statuses),
            }
            self._native_background_status = result
            return dict(result)

    def install_native_background(self, resolved, *, retries=3):
        """Preflight, resumably stage, and verify one binding on every receiver."""
        from animation.core.native_background_operation import (
            NativeBackgroundOperationError,
        )

        if type(retries) is not int or not 1 <= retries <= 10:
            raise ValueError("retries must be an integer from 1 through 10")
        candidate = self._managed_native_background(resolved)
        descriptors = tuple(
            candidate.descriptor_for(receiver_id)
            for receiver_id in range(self.num_devices)
        )
        for descriptor in descriptors:
            LEDController.serialize_native_preflight(**descriptor)
            LEDController.serialize_native_begin(preflight_token=1, **descriptor)

        with self._controller_lock():
            self._require_native_enabled()
            before = self._fresh_native_statuses()
            snapshots = self._native_snapshots(before)
            report = self._native_capability_report(before)
            self._native_background_status = {
                "state": "preflight",
                "operation": "install",
                "rollout_enabled": True,
                "progress": 0.0,
                "bundle_digest": candidate.binding.bundle_digest,
                "payload_digest": candidate.binding.payload_digest,
                "capability_report": report,
            }
            begun = []
            try:
                plans = []
                for receiver_id, (device, descriptor) in enumerate(zip(self.devices, descriptors)):
                    status = self._require_native_ack(
                        device.native_preflight(**descriptor),
                        "native preflight",
                        receiver_id,
                    )
                    token = status.get("receiver_native_preflight_token")
                    if (
                        status.get("receiver_native_transfer_state") != 1
                        or type(token) is not int
                        or token <= 0
                    ):
                        raise NativeBackgroundOperationError(
                            f"receiver {receiver_id} returned dishonest native preflight status"
                        )
                    plans.append(token)

                sent = 0
                total = len(candidate.payload) * self.num_devices
                for receiver_id, (device, descriptor, token) in enumerate(
                    zip(self.devices, descriptors, plans)
                ):
                    begun.append(receiver_id)
                    status = self._require_native_ack(
                        device.native_begin(preflight_token=token, **descriptor),
                        "native begin",
                        receiver_id,
                    )
                    if (
                        self._native_status_binding(status, "staged")
                        == candidate.binding
                    ):
                        sent += len(candidate.payload)
                        continue
                    if status.get("receiver_native_transfer_state") != 2:
                        raise NativeBackgroundOperationError(
                            f"receiver {receiver_id} did not enter native receiving state"
                        )
                    offset = int(status.get("receiver_native_received_bytes", -1))
                    if not 0 <= offset <= len(candidate.payload):
                        raise NativeBackgroundOperationError(
                            f"receiver {receiver_id} reported an invalid resume offset"
                        )
                    sent += offset
                    while offset < len(candidate.payload):
                        chunk = candidate.payload[offset : offset + MAX_NATIVE_CHUNK_BYTES]
                        error = None
                        for _attempt in range(retries):
                            try:
                                status = self._require_native_ack(
                                    device.native_chunk(offset=offset, data=chunk),
                                    "native chunk",
                                    receiver_id,
                                )
                                if status.get("receiver_native_received_bytes") != offset + len(chunk):
                                    raise NativeBackgroundOperationError(
                                        f"receiver {receiver_id} did not acknowledge exact chunk end"
                                    )
                                error = None
                                break
                            except (OSError, RuntimeError) as exc:
                                error = exc
                        if error is not None:
                            raise error
                        offset += len(chunk)
                        sent += len(chunk)
                        self._native_background_status["state"] = "staging"
                        self._native_background_status["progress"] = (
                            sent / total if total else 1.0
                        )
                    status = self._require_native_ack(
                        device.native_finalize(
                            bundle_digest=candidate.binding.bundle_digest,
                            payload_digest=candidate.binding.payload_digest,
                        ),
                        "native finalize",
                        receiver_id,
                    )
                    if self._native_status_binding(status, "staged") != candidate.binding:
                        raise NativeBackgroundOperationError(
                            f"receiver {receiver_id} finalized the wrong native binding"
                        )

                for receiver_id, device in enumerate(self.devices):
                    status = self._require_native_ack(
                        device.native_verify(
                            bundle_digest=candidate.binding.bundle_digest,
                            payload_digest=candidate.binding.payload_digest,
                        ),
                        "native verify",
                        receiver_id,
                    )
                    if self._native_status_binding(status, "staged") != candidate.binding:
                        raise NativeBackgroundOperationError(
                            f"receiver {receiver_id} did not verify the staged native binding"
                        )
            except Exception as exc:
                abort_errors = []
                for receiver_id in reversed(begun):
                    try:
                        self._require_native_ack(
                            self.devices[receiver_id].native_abort(),
                            "native abort",
                            receiver_id,
                        )
                    except Exception as abort_exc:
                        abort_errors.append({
                            "logical_device": receiver_id, "error": str(abort_exc)
                        })
                restore_errors = self._restore_native_snapshots(snapshots)
                self._native_background_status.update({
                    "state": "degraded" if restore_errors else "compensated",
                    "error": str(exc),
                    "abort_errors": abort_errors,
                    "compensation_errors": restore_errors,
                })
                raise NativeBackgroundOperationError(
                    f"native install failed; compensated={not restore_errors}: {exc}"
                ) from exc

            self._native_background_status.update({
                "state": "ready",
                "progress": 1.0,
                "error": None,
            })
            return dict(self._native_background_status)

    @staticmethod
    def _required_profile_digest(value):
        if value is None:
            return None
        if not isinstance(value, str) or len(value) != 64 or value != value.lower():
            raise ValueError(
                "installation_profile_digest must be a lowercase SHA-256 digest"
            )
        try:
            if bytes.fromhex(value).hex() != value:
                raise ValueError
        except ValueError as exc:
            raise ValueError(
                "installation_profile_digest must be a lowercase SHA-256 digest"
            ) from exc
        return value

    def _verify_native_active(
        self, statuses, candidate, parameter_set, context, profile_digest
    ):
        self._validate_presentation_agreement(statuses, context)
        for receiver_id, status in enumerate(statuses):
            descriptor = candidate.descriptor_for(receiver_id)
            expected = {
                "receiver_base_mode": 1,
                "receiver_active_context_digest": context.context_digest.hex(),
                "receiver_active_session_id": context.controller_session_id.hex(),
                "receiver_scene_epoch": context.scene_epoch,
                "receiver_native_active_bundle_digest": candidate.binding.bundle_digest,
                "receiver_native_active_payload_digest": candidate.binding.payload_digest,
                "receiver_native_active_schema_revision": parameter_set.schema_revision,
                "receiver_native_active_cadence_hz": descriptor["cadence_hz"],
                "receiver_native_active_local_strips": descriptor["local_strips"],
                "receiver_native_active_target": descriptor["target"],
                "receiver_native_active_global_strips": descriptor["global_strips"],
                "receiver_native_active_leds_per_strip": descriptor["leds_per_strip"],
                "receiver_native_active_global_strip_offset": descriptor[
                    "global_strip_offset"
                ],
                "receiver_native_active_parameter_size": len(parameter_set.blob),
                "receiver_native_active_parameter_digest": parameter_set.digest,
                "receiver_native_executing": True,
            }
            if profile_digest is not None:
                expected["receiver_profile_active_global_digest"] = profile_digest
            for key, value in expected.items():
                if status.get(key) != value:
                    raise RuntimeError(
                        f"receiver {receiver_id} reported unexpected {key}: "
                        f"{status.get(key)!r}, expected {value!r}"
                    )
            if self._native_status_binding(status, "active") != candidate.binding:
                raise RuntimeError(
                    f"receiver {receiver_id} did not activate the exact native binding"
                )

    def activate_native_background(
        self,
        resolved,
        *,
        context,
        parameters=None,
        installation_profile_digest=None,
        deterministic_seed=0,
    ):
        """Commit context and activate one verified managed binding everywhere."""
        from animation.core.native_background_operation import (
            NativeBackgroundOperationError,
        )
        from animation.core.receiver_presentation import ReceiverPresentationContext

        if not isinstance(context, ReceiverPresentationContext):
            raise TypeError("context must be a ReceiverPresentationContext")
        seed = LEDController._bounded_uint(
            "deterministic_seed", deterministic_seed, 0xFFFFFFFF
        )
        profile_digest = self._required_profile_digest(installation_profile_digest)
        candidate = self._managed_native_background(resolved)
        parameter_set = candidate.encode_parameters(parameters)
        for receiver_id in range(self.num_devices):
            descriptor = candidate.descriptor_for(receiver_id)
            LEDController.serialize_native_activate(
                expected_generation=0,
                bundle_digest=candidate.binding.bundle_digest,
                payload_digest=candidate.binding.payload_digest,
                scene_epoch=context.scene_epoch,
                deterministic_seed=seed,
                parameter_blob=parameter_set.blob,
            )
            if descriptor["parameter_schema_revision"] != parameter_set.schema_revision:
                raise NativeBackgroundOperationError(
                    "native parameter schema revision drifted from its descriptor"
                )

        with self._controller_lock():
            self._require_native_enabled()
            before = self._fresh_native_statuses()
            snapshots = self._native_snapshots(before)
            capability_report = self._native_capability_report(before)
            for receiver_id, status in enumerate(before):
                if self._native_status_binding(status, "staged") != candidate.binding:
                    raise NativeBackgroundOperationError(
                        f"receiver {receiver_id} does not hold the verified staged binding"
                    )
            prior_context = self._native_background_context
            activated = []
            self._native_background_status = {
                "state": "activating",
                "operation": "activate",
                "rollout_enabled": True,
                "progress": 0.0,
                "bundle_digest": candidate.binding.bundle_digest,
                "payload_digest": candidate.binding.payload_digest,
                "capability_report": capability_report,
            }
            try:
                for operation, method_name in (
                    ("presentation begin", "begin_presentation_context"),
                    ("presentation set", "set_presentation_context"),
                ):
                    for receiver_id, device in enumerate(self.devices):
                        status = getattr(device, method_name)(context)
                        self._require_ack(status, operation, receiver_id)
                self._commit_presentation_contexts(context)
                committed = self._fresh_native_statuses()
                self._validate_presentation_agreement(committed, context)
                if profile_digest is not None:
                    for receiver_id, status in enumerate(committed):
                        if status.get("receiver_profile_active_global_digest") != profile_digest:
                            raise NativeBackgroundOperationError(
                                f"receiver {receiver_id} has the wrong installation profile"
                            )

                for receiver_id, device in enumerate(self.devices):
                    current = self._fresh_native_status(receiver_id)
                    status = self._require_native_ack(
                        device.native_activate(
                            expected_generation=current[
                                "receiver_native_state_generation"
                            ],
                            bundle_digest=candidate.binding.bundle_digest,
                            payload_digest=candidate.binding.payload_digest,
                            scene_epoch=context.scene_epoch,
                            deterministic_seed=seed,
                            parameter_blob=parameter_set.blob,
                        ),
                        "native activation",
                        receiver_id,
                    )
                    activated.append(receiver_id)
                    self._native_background_status["progress"] = (
                        len(activated) / self.num_devices
                    )
                active = self._fresh_native_statuses()
                self._verify_native_active(
                    active, candidate, parameter_set, context, profile_digest
                )
            except Exception as exc:
                restore_errors = self._restore_native_snapshots(snapshots)
                context_errors = []
                host_fallback = False
                if prior_context is not None:
                    try:
                        for operation, method_name in (
                            ("presentation rollback begin", "begin_presentation_context"),
                            ("presentation rollback set", "set_presentation_context"),
                        ):
                            for receiver_id, device in enumerate(self.devices):
                                status = getattr(device, method_name)(prior_context)
                                self._require_ack(status, operation, receiver_id)
                        self._commit_presentation_contexts(prior_context)
                    except Exception as context_exc:
                        context_errors.append({"error": str(context_exc)})
                else:
                    # A first activation has no controller-owned presentation
                    # context to replay. Once the candidate context is committed,
                    # restoring only the native cache ledger would leave an
                    # untracked context authoritative. An exact black SET_ALL
                    # takes full RGB ownership back on the configured roster.
                    try:
                        self._display_ownership_known = False
                        self.set_all_pixels([(0, 0, 0)] * self.total_leds)
                        host_fallback = (
                            self._display_ownership_known
                            and self._local_background_status.get("state")
                            == "host_full_scene"
                        )
                        if not host_fallback:
                            raise RuntimeError(
                                "full-scene host fallback did not reach the exact roster"
                            )
                    except Exception as context_exc:
                        context_errors.append({
                            "operation": "host_full_scene_fallback",
                            "error": str(context_exc),
                        })
                if host_fallback:
                    self._native_background_active = False
                    self._local_background_active = False
                    self._native_background_binding = None
                    self._native_background_candidate = None
                    self._native_background_context = None
                    self._native_background_parameter_set = None
                    self._native_background_parameters = {}
                    self._native_background_status = {
                        "state": "fallback",
                        "operation": "activation_failed_host_fallback",
                        "rollout_enabled": self._receiver_native_modules_enabled,
                        "progress": 0.0,
                        "error": str(exc),
                        "host_full_scene_authority": True,
                        "restore_warnings": restore_errors,
                        "compensation_errors": [],
                    }
                    return False
                if restore_errors or context_errors:
                    for receiver_id, device in enumerate(self.devices):
                        try:
                            self._require_native_ack(
                                device.native_stop(), "native fallback stop", receiver_id
                            )
                        except Exception:
                            pass
                self._native_background_active = bool(restore_errors or context_errors)
                self._native_background_status.update({
                    "state": "degraded" if self._native_background_active else "compensated",
                    "error": str(exc),
                    "compensation_errors": restore_errors + context_errors,
                })
                return False

            self._native_background_active = True
            self._local_background_active = True
            self._display_ownership_known = True
            self._native_background_binding = candidate.binding
            self._native_background_descriptor = tuple(
                candidate.descriptor_for(receiver_id)
                for receiver_id in range(self.num_devices)
            )
            self._native_background_candidate = candidate
            self._native_background_context = context
            self._native_background_profile_digest = profile_digest
            self._native_background_parameter_set = parameter_set
            self._native_background_parameters = dict(parameter_set.values)
            self._native_background_status.update({
                "state": "active", "progress": 1.0, "error": None,
                "parameter_digest": parameter_set.digest,
                "effective_parameters": dict(parameter_set.values),
                "context_digest": context.context_digest.hex(),
                "installation_profile_digest": profile_digest,
                "capability_report": capability_report,
                "agreement": self._native_agreement(active),
            })
            return True

    def adopt_native_background(
        self,
        resolved,
        *,
        context,
        parameters=None,
        installation_profile_digest=None,
    ):
        """Adopt retained execution only after exact unanimous state proof."""
        from animation.core.receiver_presentation import ReceiverPresentationContext

        if not isinstance(context, ReceiverPresentationContext):
            raise TypeError("context must be a ReceiverPresentationContext")
        candidate = self._managed_native_background(resolved)
        parameter_set = candidate.encode_parameters(parameters)
        profile_digest = self._required_profile_digest(installation_profile_digest)
        with self._controller_lock():
            try:
                self._require_native_enabled()
                statuses = self._fresh_native_statuses()
                self._verify_native_active(
                    statuses, candidate, parameter_set, context, profile_digest
                )
                capability_report = self._native_capability_report(statuses)
            except Exception as exc:
                self._native_background_active = False
                self._native_background_status = {
                    "state": "adoption_rejected",
                    "operation": "adopt",
                    "rollout_enabled": self._receiver_native_modules_enabled,
                    "progress": 0.0,
                    "error": str(exc),
                }
                return False
            self._native_background_active = True
            self._local_background_active = True
            self._display_ownership_known = True
            self._native_background_binding = candidate.binding
            self._native_background_candidate = candidate
            self._native_background_context = context
            self._native_background_profile_digest = profile_digest
            self._native_background_parameter_set = parameter_set
            self._native_background_parameters = dict(parameter_set.values)
            self._native_background_status = {
                "state": "active",
                "operation": "adopt",
                "rollout_enabled": True,
                "progress": 1.0,
                "bundle_digest": candidate.binding.bundle_digest,
                "payload_digest": candidate.binding.payload_digest,
                "parameter_digest": parameter_set.digest,
                "effective_parameters": dict(parameter_set.values),
                "context_digest": context.context_digest.hex(),
                "installation_profile_digest": profile_digest,
                "foreground_snapshot_required": True,
                "capability_report": capability_report,
                "agreement": self._native_agreement(statuses),
            }
            return True

    def update_native_background_parameters(self, parameters):
        """Apply one canonical parameter set everywhere or restore the prior set."""
        with self._controller_lock():
            self._require_native_enabled()
            if not self._native_background_active or self._native_background_candidate is None:
                raise RuntimeError("native background is not active")
            candidate = self._native_background_candidate
            prior = self._native_background_parameter_set
            if prior is None:
                raise RuntimeError("active native parameter authority is unavailable")
            updated = candidate.encode_parameters(parameters)
            LEDController.serialize_native_parameters(
                bundle_digest=candidate.binding.bundle_digest,
                payload_digest=candidate.binding.payload_digest,
                parameter_schema_revision=updated.schema_revision,
                parameter_blob=updated.blob,
            )
            changed = []
            try:
                for receiver_id, device in enumerate(self.devices):
                    status = self._require_native_ack(
                        device.native_parameters(
                            bundle_digest=candidate.binding.bundle_digest,
                            payload_digest=candidate.binding.payload_digest,
                            parameter_schema_revision=updated.schema_revision,
                            parameter_blob=updated.blob,
                        ),
                        "native parameter update",
                        receiver_id,
                    )
                    changed.append(receiver_id)
                statuses = self._fresh_native_statuses()
                for receiver_id, status in enumerate(statuses):
                    if (
                        status.get("receiver_native_active_parameter_digest")
                        != updated.digest
                        or status.get("receiver_native_active_parameter_size")
                        != len(updated.blob)
                    ):
                        raise RuntimeError(
                            f"receiver {receiver_id} did not apply exact native parameters"
                        )
            except Exception as exc:
                rollback_errors = []
                for receiver_id in reversed(changed):
                    try:
                        self._require_native_ack(
                            self.devices[receiver_id].native_parameters(
                                bundle_digest=candidate.binding.bundle_digest,
                                payload_digest=candidate.binding.payload_digest,
                                parameter_schema_revision=prior.schema_revision,
                                parameter_blob=prior.blob,
                            ),
                            "native parameter rollback",
                            receiver_id,
                        )
                    except Exception as rollback_exc:
                        rollback_errors.append({
                            "logical_device": receiver_id,
                            "error": str(rollback_exc),
                        })
                if rollback_errors:
                    for receiver_id, device in enumerate(self.devices):
                        try:
                            self._require_native_ack(
                                device.native_stop(), "native parameter fallback", receiver_id
                            )
                        except Exception:
                            pass
                    self._native_background_active = True
                self._native_background_status.update({
                    "state": "degraded" if rollback_errors else "active",
                    "operation": "parameter_rollback",
                    "error": str(exc),
                    "rollback_errors": rollback_errors,
                })
                return False
            self._native_background_parameter_set = updated
            self._native_background_parameters = dict(updated.values)
            self._native_background_status.update({
                "state": "active",
                "operation": "parameter_update",
                "error": None,
                "parameter_digest": updated.digest,
                "effective_parameters": dict(updated.values),
            })
            return True

    def stop_native_background(self):
        """Stop execution everywhere and prove compiled-fallback ownership."""
        with self._controller_lock():
            self._require_native_enabled()
            command_errors = []
            for receiver_id, device in enumerate(self.devices):
                try:
                    self._require_native_ack(
                        device.native_stop(), "native stop", receiver_id
                    )
                except Exception as exc:
                    command_errors.append({
                        "logical_device": receiver_id, "error": str(exc)
                    })
            try:
                statuses = self._fresh_native_statuses()
                for receiver_id, status in enumerate(statuses):
                    if (
                        status.get("receiver_native_executing")
                        or int(status.get("receiver_base_mode", -1)) != 0
                    ):
                        raise RuntimeError(
                            f"receiver {receiver_id} did not enter compiled fallback"
                        )
            except Exception as exc:
                command_errors.append({"logical_device": -1, "error": str(exc)})
            if command_errors:
                self._native_background_active = True
                self._native_background_status.update({
                    "state": "degraded",
                    "operation": "stop",
                    "error": "could not prove unanimous native stop",
                    "command_errors": command_errors,
                })
                return False
            self._native_background_active = False
            self._local_background_active = False
            self._native_background_binding = None
            self._native_background_candidate = None
            self._native_background_context = None
            self._native_background_profile_digest = None
            self._native_background_parameter_set = None
            self._native_background_parameters = {}
            self._native_background_status = {
                "state": "fallback",
                "operation": "stop",
                "rollout_enabled": True,
                "progress": 1.0,
                "error": None,
            }
            return True

    def recover_native_background_to_host(self, colors):
        """Use the universal complete-RGB kill path and prove host ownership."""
        self.set_all_pixels(colors)
        with self._controller_lock():
            try:
                statuses = self._fresh_native_statuses(require_capabilities=False)
                for receiver_id, status in enumerate(statuses):
                    if int(status.get("receiver_base_mode", -1)) != 2:
                        raise RuntimeError(
                            f"receiver {receiver_id} did not enter HostFullScene"
                        )
                    if status.get("receiver_native_executing"):
                        raise RuntimeError(
                            f"receiver {receiver_id} retained native execution"
                        )
            except Exception as exc:
                self._native_background_active = True
                self._native_background_status.update({
                    "state": "degraded",
                    "operation": "host_recovery",
                    "error": str(exc),
                })
                return False
            return not self._native_background_active

    def remove_native_background(self, resolved):
        """Remove one inactive binding everywhere and prove payload absence."""
        from animation.core.native_background_operation import (
            NativeBackgroundOperationError,
        )

        candidate = self._managed_native_background(resolved)
        with self._controller_lock():
            self._require_native_enabled()
            statuses = self._fresh_native_statuses()
            for receiver_id, status in enumerate(statuses):
                protected = tuple(
                    self._native_status_binding(status, prefix)
                    for prefix in ("active", "staged", "rollback")
                )
                if any(
                    binding is not None
                    and binding.payload_digest == candidate.binding.payload_digest
                    for binding in protected
                ):
                    raise NativeBackgroundOperationError(
                        f"receiver {receiver_id} protects the payload through an "
                        "active/staged/rollback binding"
                    )
            removed = []
            errors = []
            for receiver_id, device in enumerate(self.devices):
                try:
                    self._require_native_ack(
                        device.native_remove(
                            bundle_digest=candidate.binding.bundle_digest,
                            payload_digest=candidate.binding.payload_digest,
                        ),
                        "native removal",
                        receiver_id,
                    )
                    removed.append(receiver_id)
                except Exception as exc:
                    errors.append({
                        "logical_device": receiver_id, "error": str(exc)
                    })
            remaining = []
            for receiver_id, device in enumerate(self.devices):
                try:
                    status = self._require_native_probe_identity(
                        self._require_native_ack(
                            device.native_probe(
                                payload_digest=candidate.binding.payload_digest
                            ),
                            "native removal probe",
                            receiver_id,
                        ),
                        candidate,
                        receiver_id,
                        "native removal probe",
                    )
                    if status.get("receiver_native_probe_found"):
                        remaining.append(receiver_id)
                except Exception as exc:
                    errors.append({
                        "logical_device": receiver_id, "error": str(exc)
                    })
            self._native_background_status = {
                "state": "removed" if not errors and not remaining else "degraded",
                "operation": "remove",
                "rollout_enabled": True,
                "progress": 1.0,
                "bundle_digest": candidate.binding.bundle_digest,
                "payload_digest": candidate.binding.payload_digest,
                "removed_devices": removed,
                "remaining_devices": remaining,
                "errors": errors,
            }
            return not errors and not remaining

    def clear_native_background_quarantine(self, resolved):
        """Clear one exact quarantined payload across the configured roster.

        The command is intentionally separate from installation. Clearing a
        quarantine is an explicit operator recovery decision; a caller may
        retry installation only after this method proves that no receiver
        retains either the requested or a conflicting quarantine identity.
        """
        from animation.core.native_background_operation import (
            NativeBackgroundOperationError,
        )

        candidate = self._managed_native_background(resolved)
        target = candidate.binding.payload_digest
        with self._controller_lock():
            self._require_native_enabled()
            before = self._fresh_native_statuses()
            report = self._native_capability_report(before)
            quarantined = []
            conflicts = []
            for receiver_id, status in enumerate(before):
                digest = status.get("receiver_native_quarantine_payload_digest")
                if digest is None:
                    continue
                if digest == target:
                    quarantined.append(receiver_id)
                else:
                    conflicts.append({
                        "logical_device": receiver_id,
                        "payload_digest": digest,
                    })
            if conflicts:
                raise NativeBackgroundOperationError(
                    "receiver quarantine identities do not unanimously match the "
                    f"managed payload: {conflicts!r}"
                )

            cleared = []
            errors = []
            for receiver_id in quarantined:
                try:
                    self._require_native_ack(
                        self.devices[receiver_id].native_quarantine_clear(
                            payload_digest=target
                        ),
                        "native quarantine clear",
                        receiver_id,
                    )
                    cleared.append(receiver_id)
                except Exception as exc:
                    errors.append({
                        "logical_device": receiver_id,
                        "error": str(exc),
                    })

            remaining = []
            verification_errors = []
            try:
                after = self._fresh_native_statuses()
                for receiver_id, status in enumerate(after):
                    digest = status.get(
                        "receiver_native_quarantine_payload_digest"
                    )
                    if digest is not None:
                        remaining.append({
                            "logical_device": receiver_id,
                            "payload_digest": digest,
                        })
            except Exception as exc:
                after = ()
                verification_errors.append(str(exc))

            succeeded = not errors and not verification_errors and not remaining
            result = {
                "state": "ready" if succeeded else "degraded",
                "operation": "clear_quarantine",
                "rollout_enabled": True,
                "progress": 1.0,
                "bundle_digest": candidate.binding.bundle_digest,
                "payload_digest": target,
                "quarantined_devices": quarantined,
                "cleared_devices": cleared,
                "remaining_quarantines": remaining,
                "errors": errors,
                "verification_errors": verification_errors,
                "capability_report": report,
            }
            if succeeded:
                result["agreement"] = self._native_agreement(after)
            self._native_background_status = result
            return dict(result)
    
    def _host_local_pixels(self, receiver_id: int, pixels):
        """Map one canonical global slice into physical host-output strip order."""

        if not self.reverse_host_strips_by_logical_receiver[receiver_id]:
            return pixels
        width = self.receiver_strip_counts[receiver_id]
        height = self.leds_per_strip
        if isinstance(pixels, np.ndarray):
            shape = (width, height) + pixels.shape[1:]
            return np.ascontiguousarray(pixels.reshape(shape)[::-1]).reshape(
                pixels.shape
            )
        strips = [
            pixels[start:start + height]
            for start in range(0, width * height, height)
        ]
        return [pixel for strip in reversed(strips) for pixel in strip]

    def _receiver_local_dirty_ranges(self, receiver_id: int, dirty_ranges):
        """Map global pixel intervals into sorted receiver-local intervals."""

        global_first = self.receiver_pixel_offsets[receiver_id]
        global_last = global_first + self.receiver_pixel_counts[receiver_id]
        width = self.receiver_strip_counts[receiver_id]
        height = self.leds_per_strip
        reverse = self.reverse_host_strips_by_logical_receiver[receiver_id]
        mapped = []
        for start, end in sorted(dirty_ranges or ()):
            cursor = max(global_first, max(0, int(start)))
            clipped_end = min(global_last, min(self.total_leds, int(end)))
            while cursor < clipped_end:
                global_strip = cursor // height
                global_strip_end = min(clipped_end, (global_strip + 1) * height)
                logical_local_strip = global_strip - (
                    self.receiver_global_strip_offsets[receiver_id]
                )
                physical_local_strip = (
                    width - 1 - logical_local_strip
                    if reverse else logical_local_strip
                )
                led_first = cursor % height
                led_last = led_first + (global_strip_end - cursor)
                local_first = physical_local_strip * height + led_first
                mapped.append((local_first, physical_local_strip * height + led_last))
                cursor = global_strip_end
        mapped.sort()
        merged = []
        for start, end in mapped:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def _split_frame(self, colors: List[Tuple[int, int, int]]) -> List[List[Tuple[int, int, int]]]:
        """
        Split full frame into per-device chunks
        
        Args:
            colors: Full frame of (r,g,b) tuples for all pixels
            
        Returns:
            List of color lists, one per device
        """
        if isinstance(colors, np.ndarray):
            total_needed = self.total_leds
            if colors.shape[0] < total_needed:
                colors = np.concatenate([colors, np.zeros((total_needed - colors.shape[0], 3), dtype=np.uint8)])
            device_frames = []
            for device_id in range(self.num_devices):
                start = self.receiver_pixel_offsets[device_id]
                count = self.receiver_pixel_counts[device_id]
                device_frames.append(self._host_local_pixels(
                    device_id, colors[start:start + count]
                ))
            return device_frames

        device_frames = []
        for device_id in range(self.num_devices):
            device_colors = []
            for local_strip in range(self.receiver_strip_counts[device_id]):
                global_strip = (
                    self.receiver_global_strip_offsets[device_id] + local_strip
                )
                start_idx = global_strip * self.leds_per_strip
                end_idx = start_idx + self.leds_per_strip

                if start_idx < len(colors):
                    strip_pixels = colors[start_idx:end_idx]
                else:
                    strip_pixels = []

                if len(strip_pixels) < self.leds_per_strip:
                    strip_pixels = list(strip_pixels) + [(0, 0, 0)] * (self.leds_per_strip - len(strip_pixels))

                device_colors.extend(strip_pixels[:self.leds_per_strip])

            device_frames.append(self._host_local_pixels(device_id, device_colors))

        return device_frames
    
    def _claim_logical_wall_frame_sequence(self):
        """Reserve one sequence shared by every receiver send for a wall frame."""
        sequence = getattr(self, "_logical_wall_frame_sequence", 0)
        self._logical_wall_frame_sequence = sequence + 1
        return sequence

    def _send_to_device(
        self,
        device_id: int,
        colors: List[Tuple[int, int, int]],
        wall_frame_sequence=None,
    ):
        """Send frame data to a specific device"""
        try:
            if wall_frame_sequence is None:
                self.devices[device_id].set_all_pixels(colors)
            else:
                self.devices[device_id].set_all_pixels(
                    colors, wall_frame_sequence=wall_frame_sequence
                )
            return True
        except Exception as e:
            if self.debug:
                print(f"✗ Error sending to device {device_id}: {e}")
            return False

    def _send_bus_frames(
        self, device_ids, device_frames, wall_frame_sequence=None
    ):
        """Serialize chip selects on one bus while independent buses overlap."""
        successful = True
        for device_id in device_ids:
            successful = self._send_to_device(
                device_id, device_frames[device_id], wall_frame_sequence
            ) and successful
        return successful

    def _send_bus_partial(
        self,
        device_ids,
        device_frames,
        device_ranges,
        wall_frame_sequence=None,
    ):
        for device_id in device_ids:
            ranges = device_ranges.get(device_id)
            if not ranges:
                continue
            try:
                dirty_pixels = sum(end - start for start, end in ranges)
                if dirty_pixels > self.receiver_pixel_counts[device_id] * 0.35:
                    if wall_frame_sequence is None:
                        self.devices[device_id].set_all_pixels(
                            device_frames[device_id]
                        )
                    else:
                        self.devices[device_id].set_all_pixels(
                            device_frames[device_id],
                            wall_frame_sequence=wall_frame_sequence,
                        )
                else:
                    self.devices[device_id].set_partial_frame(device_frames[device_id], ranges)
            except Exception as exc:
                if self.debug:
                    print(f"✗ Error partially sending to device {device_id}: {exc}")

    def set_all_pixels(self, colors: List[Tuple[int, int, int]]):
        """
        Set all pixels across all devices
        
        Args:
            colors: List of (r,g,b) tuples for entire grid
        """
        with self._controller_lock():
            was_local = self._local_background_active
            was_native = self._native_background_active
            had_sparse_authority = (
                getattr(self, "_sparse_overlay_session_id", None) is not None
            )
            # Split frame into per-device chunks. A complete SET_ALL is the
            # protocol's universal takeover path; no local STOP is required.
            device_frames = self._split_frame(colors)
            wall_frame_sequence = self._claim_logical_wall_frame_sequence()
            successful = True

            if self._executor is not None:
                futures = [
                    self._executor.submit(
                        self._send_bus_frames,
                        device_ids,
                        device_frames,
                        wall_frame_sequence,
                    )
                    for device_ids in self._devices_by_bus.values()
                ]
                for future in futures:
                    successful = bool(future.result()) and successful
            else:
                # Keep the same topology-owned per-bus order when bus overlap
                # is disabled.
                for device_ids in self._devices_by_bus.values():
                    successful = self._send_bus_frames(
                        device_ids, device_frames, wall_frame_sequence
                    ) and successful
            self._logical_frames_sent += 1
            if successful and (
                was_local or was_native or had_sparse_authority
                or not self._display_ownership_known
            ):
                try:
                    statuses = []
                    for device in self.devices:
                        status = None
                        for _ in range(SPI_RESPONSE_QUEUE_DEPTH):
                            status = device.query_receiver_status()
                        statuses.append(status)
                    versions = [
                        int(status.get("receiver_status_version", 0) or 0)
                        if isinstance(status, dict) else 0
                        for status in statuses
                    ]
                    if all(version >= 3 for version in versions):
                        for index, status in enumerate(statuses):
                            if int(status.get("receiver_base_mode", -1)) != 2:
                                raise RuntimeError(
                                    f"receiver {index} did not enter HostFullScene"
                                )
                    else:
                        for index, (version, status) in enumerate(zip(versions, statuses)):
                            if (version >= 3
                                    and int(status.get("receiver_base_mode", -1)) != 2):
                                raise RuntimeError(
                                    f"receiver {index} did not enter HostFullScene"
                                )
                except Exception as exc:
                    successful = False
                    self._local_background_status = {
                        "state": "degraded",
                        "operation": "set_all_takeover_verify",
                        "error": str(exc),
                    }
            if successful:
                self._display_ownership_known = True
                self._local_background_active = False
                self._native_background_active = False
                self._native_background_binding = None
                self._native_background_descriptor = None
                self._native_background_parameters = {}
                self._native_background_candidate = None
                self._native_background_context = None
                self._native_background_profile_digest = None
                self._native_background_parameter_set = None
                self._local_background_context_digest = None
                self._local_background_parameters = {}
                self._sparse_overlay_session_id = None
                self._sparse_overlay_generation = 0
                self._sparse_overlay_snapshot_digest = None
                self._local_background_status = {
                    "state": "host_full_scene", "operation": "set_all_takeover"
                }
                if was_native:
                    self._native_background_status = {
                        "state": "host_full_scene",
                        "operation": "set_all_takeover",
                        "rollout_enabled": self._receiver_native_modules_enabled,
                        "progress": 1.0,
                        "error": None,
                        "agreement": {
                            "exact_roster": True,
                            "verified_receiver_ids": list(range(self.num_devices)),
                        },
                    }
            elif was_native:
                stop_errors = []
                for receiver_id, device in enumerate(self.devices):
                    try:
                        self._require_native_ack(
                            device.native_stop(),
                            "native partial-takeover compensation",
                            receiver_id,
                        )
                    except Exception as exc:
                        stop_errors.append({
                            "logical_device": receiver_id,
                            "error": str(exc),
                        })
                self._native_background_active = bool(stop_errors)
                if not stop_errors:
                    self._local_background_active = False
                    self._native_background_binding = None
                    self._native_background_candidate = None
                    self._native_background_context = None
                    self._native_background_parameter_set = None
                    self._native_background_parameters = {}
                self._native_background_status = {
                    "state": "degraded" if stop_errors else "fallback",
                    "operation": "set_all_takeover_failed",
                    "rollout_enabled": self._receiver_native_modules_enabled,
                    "progress": 0.0,
                    "error": "complete RGB takeover did not reach the exact roster",
                    "compensation_errors": stop_errors,
                }
            elif was_local or self._any_receiver_may_be_local():
                # A lost complete frame can otherwise leave a mixed wall. The
                # fallback command is ownership-safe even on receivers that
                # already accepted SET_ALL.
                self._stop_local_background_best_effort("set_all_partial_failure")

    def _any_receiver_may_be_local(self):
        """Conservatively detect retained local playback after a Pi restart."""
        try:
            return any(
                int(status.get("receiver_base_mode", -1)) == 1
                for status in self._receiver_statuses()
            )
        except Exception:
            # Unknown state after partial SET_ALL is unsafe; issue STOP to every
            # receiver and let its post-status verification decide the result.
            return True

    def set_frame(self, colors, dirty_ranges=None):
        """Present a frame, using partial board updates when ranges are known."""
        self._controller_lock()
        if (not dirty_ranges or self._local_background_active
                or not self._display_ownership_known):
            self.set_all_pixels(colors)
            return

        with self._controller_lock():
            device_frames = self._split_frame(colors)
            wall_frame_sequence = self._claim_logical_wall_frame_sequence()
            device_ranges = {}
            for device_id in range(self.num_devices):
                ranges = self._receiver_local_dirty_ranges(
                    device_id, dirty_ranges
                )
                if ranges:
                    device_ranges[device_id] = ranges

            if self._executor is not None:
                futures = [
                    self._executor.submit(
                        self._send_bus_partial,
                        device_ids,
                        device_frames,
                        device_ranges,
                        wall_frame_sequence,
                    )
                    for device_ids in self._devices_by_bus.values()
                ]
                for future in futures:
                    future.result()
            else:
                for device_ids in self._devices_by_bus.values():
                    self._send_bus_partial(
                        device_ids,
                        device_frames,
                        device_ranges,
                        wall_frame_sequence,
                    )
            self._logical_frames_sent += 1
    
    def set_pixel(self, pixel: int, r: int, g: int, b: int):
        """Set a single pixel color"""
        with self._controller_lock():
            if pixel >= self.total_leds:
                return

            strip = pixel // self.leds_per_strip
            led_in_strip = pixel % self.leds_per_strip
            for device_id, (offset, width) in enumerate(zip(
                self.receiver_global_strip_offsets, self.receiver_strip_counts
            )):
                if offset <= strip < offset + width:
                    local_strip = strip - offset
                    if self.reverse_host_strips_by_logical_receiver[device_id]:
                        local_strip = width - 1 - local_strip
                    local_pixel = local_strip * self.leds_per_strip + led_in_strip
                    self.devices[device_id].set_pixel(local_pixel, r, g, b)
                    break
    
    def set_brightness(self, brightness: int):
        """Set global brightness on all devices"""
        with self._controller_lock():
            self.current_brightness = brightness
            for device in self.devices:
                device.set_brightness(brightness)
    
    def set_lane_mask(self, lane_mask: int):
        """Apply the same diagnostic lane mask to every device."""
        for device in self.devices:
            device.set_lane_mask(lane_mask)

    def set_stagger_phases(self, phases: int):
        """Apply the same WS2812 edge-stagger phase count to every device."""
        for device in self.devices:
            device.set_stagger_phases(phases)

    def show(self):
        """Update LED display on all devices"""
        with self._controller_lock():
            if not self.inline_show:
                for device in self.devices:
                    device.show()
    
    def clear(self):
        """Clear all LEDs on all devices"""
        with self._controller_lock():
            for device in self.devices:
                device.clear()
    
    def configure(self):
        """Configure all devices"""
        with self._controller_lock():
            for device_id, device in enumerate(self.devices):
                try:
                    device.configure()
                    if self.debug:
                        print(f"✓ Device {device_id} configured")
                except Exception as e:
                    if self.debug:
                        print(f"✗ Device {device_id} configuration failed: {e}")

    @staticmethod
    def _status_result_ok(status, *, allow_none=False):
        if not isinstance(status, dict):
            return False
        result = int(status.get("receiver_last_result", 0) or 0)
        return result in ((0, 1) if allow_none else (1,))

    def _receiver_statuses(
        self,
        *,
        require_capability=False,
        require_identity=True,
        required_capabilities=None,
    ):
        statuses = []
        for index, device in enumerate(self.devices):
            status = device.query_receiver_status()
            if not isinstance(status, dict):
                raise RuntimeError(f"receiver {index} returned no status")
            if (require_capability
                    and int(status.get("receiver_status_version", 0) or 0) < 3):
                raise RuntimeError(f"receiver {index} does not expose status v3")
            capabilities = int(status.get("receiver_capabilities", 0) or 0)
            required = (
                LOCAL_BACKGROUND_REQUIRED_CAPABILITIES
                if required_capabilities is None
                else int(required_capabilities)
            )
            missing = required & ~capabilities
            if require_capability and missing:
                raise RuntimeError(
                    f"receiver {index} lacks required receiver capabilities "
                    f"0x{missing:08x}"
                )
            if (require_capability and require_identity
                    and status.get("receiver_logical_device") != index):
                raise RuntimeError(
                    f"receiver {index} reports logical identity "
                    f"{status.get('receiver_logical_device')!r}"
                )
            statuses.append(status)
        return statuses

    @staticmethod
    def _overlay_snapshot_digest(pixels: np.ndarray) -> bytes:
        return hashlib.sha256(memoryview(pixels).cast("B")).digest()

    def _normalize_overlay_pixels(self, pixels) -> np.ndarray:
        array = np.asarray(pixels)
        if array.shape != (self.total_leds, 4):
            raise ValueError(
                "aggregate foreground must have shape "
                f"({self.total_leds}, 4), got {array.shape}"
            )
        if array.dtype != np.uint8:
            raise TypeError("aggregate foreground must use uint8 premultiplied RGBA")
        array = np.ascontiguousarray(array)
        if np.any(array[:, :3] > array[:, 3, None]):
            raise ValueError("aggregate foreground RGB must not exceed alpha")
        return array

    def _normalize_overlay_dirty_ranges(self, dirty_ranges):
        if dirty_ranges is None:
            raise ValueError("delta foreground publication requires dirty_ranges")
        normalized = []
        prior_end = 0
        for index, item in enumerate(dirty_ranges):
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise TypeError(
                    f"dirty range {index} must be a (start, end) pair"
                )
            start, end = item
            if (
                isinstance(start, bool)
                or not isinstance(start, (int, np.integer))
                or isinstance(end, bool)
                or not isinstance(end, (int, np.integer))
            ):
                raise TypeError(f"dirty range {index} bounds must be integers")
            start = int(start)
            end = int(end)
            if start < 0 or end > self.total_leds or start >= end:
                raise ValueError(
                    f"dirty range {index} [{start}, {end}) is outside "
                    f"[0, {self.total_leds}) or empty"
                )
            if normalized and start < prior_end:
                raise ValueError(
                    "dirty ranges must be sorted and non-overlapping"
                )
            normalized.append((start, end))
            prior_end = end
        return tuple(normalized)

    def _local_overlay_patches(
        self,
        pixels: np.ndarray,
        *,
        receiver_id: int,
        update_kind: int,
        dirty_ranges,
    ):
        local_start = self.receiver_pixel_offsets[receiver_id]
        local_end = local_start + self.receiver_pixel_counts[receiver_id]
        local_pixels = self._host_local_pixels(
            receiver_id, pixels[local_start:local_end]
        )
        if update_kind == OVERLAY_UPDATE_FULL_SNAPSHOT:
            # Sparse-overlay protocol v1 defines an eight-lane replacement
            # plane even for the finalized fifth receiver, whose semantic
            # width is one lane.  Fill the unused transport lanes with
            # transparent pixels; never replicate column 32 into them.
            transport_pixels = local_pixels
            if len(local_pixels) < OVERLAY_LOCAL_PIXELS:
                transport_pixels = np.zeros(
                    (OVERLAY_LOCAL_PIXELS, local_pixels.shape[1]),
                    dtype=np.uint8,
                )
                transport_pixels[:len(local_pixels)] = local_pixels
            return [
                (
                    start,
                    transport_pixels[
                        start:start + MAX_RGBA_PIXELS_PER_BATCH_SPAN
                    ],
                )
                for start in range(
                    0, len(transport_pixels), MAX_RGBA_PIXELS_PER_BATCH_SPAN
                )
            ]

        ranges = self._receiver_local_dirty_ranges(receiver_id, dirty_ranges)
        patches = []
        for start, end in ranges:
            while start < end:
                patch_end = min(end, start + MAX_RGBA_PIXELS_PER_BATCH_SPAN)
                patches.append((start, local_pixels[start:patch_end]))
                start = patch_end
        return patches

    def publish_sparse_overlay(
        self,
        pixels,
        *,
        controller_session_id,
        generation,
        prior_generation,
        scene_revision,
        scene_epoch,
        base_revision,
        lease_ms,
        present_at_scene_time_us,
        dirty_ranges=None,
        full_snapshot=False,
    ):
        """Stage and schedule one authoritative aggregate foreground generation."""
        self._controller_lock()
        overlay = self._normalize_overlay_pixels(pixels)
        session = LEDController._controller_session(controller_session_id)
        overlay_generation = LEDController._bounded_uint(
            "generation", generation, 0xFFFFFFFFFFFFFFFF
        )
        previous_generation = LEDController._bounded_uint(
            "prior_generation", prior_generation, 0xFFFFFFFFFFFFFFFF
        )
        if overlay_generation == 0 or overlay_generation <= previous_generation:
            raise ValueError("generation must be newer than prior_generation")
        if overlay_generation == 0xFFFFFFFFFFFFFFFF:
            raise ValueError(
                "generation must leave one counter value for partial-wall compensation"
            )
        update_kind = (
            OVERLAY_UPDATE_FULL_SNAPSHOT if full_snapshot
            else OVERLAY_UPDATE_DELTA
        )
        normalized_ranges = (
            None if full_snapshot
            else self._normalize_overlay_dirty_ranges(dirty_ranges)
        )
        digest = self._overlay_snapshot_digest(overlay)
        prior_session = getattr(self, "_sparse_overlay_session_id", None)
        new_session = prior_session != session
        if new_session and not full_snapshot:
            raise ValueError(
                "a new controller session must begin with a full foreground snapshot"
            )
        if new_session and previous_generation != 0:
            raise ValueError("a new controller session must start at prior_generation 0")
        if (
            not new_session
            and previous_generation != self._sparse_overlay_generation
        ):
            raise ValueError(
                "prior_generation does not match the controller's committed generation"
            )

        per_device_patches = []
        for index in range(self.num_devices):
            per_device_patches.append(self._local_overlay_patches(
                overlay,
                receiver_id=index,
                update_kind=update_kind,
                dirty_ranges=normalized_ranges,
            ))

        # Serialize the complete transaction before the first receiver command.
        # Device methods repeat these checks immediately before I/O; this
        # aggregate preflight prevents malformed later commands from leaving a
        # controller session or staged generation behind.
        if new_session:
            LEDController.serialize_controller_session_begin(
                controller_session_id=session,
                desired_revision=scene_revision,
                authoritative_snapshot_digest=digest,
            )
        for patches in per_device_patches:
            LEDController.serialize_overlay_begin(
                controller_session_id=session,
                generation=overlay_generation,
                prior_generation=previous_generation,
                scene_revision=scene_revision,
                scene_epoch=scene_epoch,
                base_revision=base_revision,
                format=OVERLAY_FORMAT_PREMULTIPLIED_RGBA8,
                update_kind=update_kind,
                expected_patches=len(patches),
                lease_ms=lease_ms,
            )
            LEDController.serialize_overlay_patch_batches(
                controller_session_id=session,
                generation=overlay_generation,
                patches=patches,
                update_kind=update_kind,
            )
        LEDController.serialize_overlay_commit(
            controller_session_id=session,
            generation=overlay_generation,
            scene_epoch=scene_epoch,
            base_revision=base_revision,
            present_at_scene_time_us=present_at_scene_time_us,
        )
        compensation_generation = overlay_generation + 1
        LEDController.serialize_overlay_clear(
            controller_session_id=session,
            generation=compensation_generation,
            scene_revision=scene_revision,
        )

        with self._controller_lock():
            touched = False
            try:
                statuses = self._receiver_statuses(
                    require_capability=True,
                    required_capabilities=SPARSE_OVERLAY_REQUIRED_CAPABILITIES,
                )
                if any(int(status.get("receiver_base_mode", -1)) != 1 for status in statuses):
                    raise RuntimeError("sparse foreground requires local background ownership")
                if not new_session:
                    expected_session = session.hex()
                    for index, status in enumerate(statuses):
                        if status.get("receiver_overlay_session_id") != expected_session:
                            raise RuntimeError(
                                f"receiver {index} lost foreground session authority"
                            )
                        if int(status.get(
                            "receiver_overlay_committed_generation", -1
                        )) != previous_generation:
                            raise RuntimeError(
                                f"receiver {index} disagrees on committed foreground "
                                f"generation {previous_generation}"
                            )
                        if int(status.get(
                            "receiver_overlay_staged_generation", 0
                        )) != 0:
                            raise RuntimeError(
                                f"receiver {index} still has a staged foreground"
                            )
                    if (
                        update_kind == OVERLAY_UPDATE_DELTA
                        and any(int(status.get(
                            "receiver_foreground_state", -1
                        )) != 2 for status in statuses)
                    ):
                        self._local_background_status = {
                            "state": "foreground_repair_required",
                            "operation": "foreground_delta_preflight",
                            "error": (
                                "a receiver has no active foreground; publish a "
                                "complete authoritative snapshot before deltas"
                            ),
                            "foreground_generation": previous_generation,
                        }
                        return False

                if new_session:
                    for index, device in enumerate(self.devices):
                        touched = True
                        status = device.begin_controller_session(
                            controller_session_id=session,
                            desired_revision=scene_revision,
                            authoritative_snapshot_digest=digest,
                        )
                        self._require_overlay_ack(
                            status, "foreground session", index
                        )

                for index, (device, patches) in enumerate(zip(
                    self.devices, per_device_patches
                )):
                    touched = True
                    status = device.begin_overlay(
                        controller_session_id=session,
                        generation=overlay_generation,
                        prior_generation=previous_generation,
                        scene_revision=scene_revision,
                        scene_epoch=scene_epoch,
                        base_revision=base_revision,
                        format=OVERLAY_FORMAT_PREMULTIPLIED_RGBA8,
                        update_kind=update_kind,
                        expected_patches=len(patches),
                        lease_ms=lease_ms,
                    )
                    self._require_overlay_ack(status, "foreground begin", index)

                for index, (device, patches) in enumerate(zip(
                    self.devices, per_device_patches
                )):
                    for status in device.send_overlay_patches(
                        controller_session_id=session,
                        generation=overlay_generation,
                        patches=patches,
                        update_kind=update_kind,
                    ):
                        self._require_overlay_ack(status, "foreground patch", index)

                for index, device in enumerate(self.devices):
                    status = device.commit_overlay(
                        controller_session_id=session,
                        generation=overlay_generation,
                        scene_epoch=scene_epoch,
                        base_revision=base_revision,
                        present_at_scene_time_us=present_at_scene_time_us,
                    )
                    self._require_overlay_ack(status, "foreground commit", index)

                committed = self._receiver_statuses(
                    require_capability=True,
                    required_capabilities=SPARSE_OVERLAY_REQUIRED_CAPABILITIES,
                )
                receiver_states = []
                for index, status in enumerate(committed):
                    committed_generation = int(
                        status.get("receiver_overlay_committed_generation", -1)
                    )
                    staged_generation = int(
                        status.get("receiver_overlay_staged_generation", -1)
                    )
                    if committed_generation == overlay_generation:
                        state = "active"
                    elif staged_generation == overlay_generation:
                        state = "scheduled"
                    else:
                        raise RuntimeError(
                            f"receiver {index} retained neither committed nor staged "
                            f"foreground generation {overlay_generation}"
                        )
                    receiver_states.append(state)
                    if status.get("receiver_overlay_session_id") != session.hex():
                        raise RuntimeError(
                            f"receiver {index} did not retain the foreground session"
                        )
                    expected_binding = {
                        "receiver_foreground_scene_revision": scene_revision,
                        "receiver_foreground_scene_epoch": scene_epoch,
                        "receiver_foreground_base_revision": base_revision,
                        "receiver_foreground_present_at_scene_time_us": (
                            present_at_scene_time_us
                        ),
                    }
                    for key, expected in expected_binding.items():
                        if int(status.get(key, -1)) != expected:
                            raise RuntimeError(
                                f"receiver {index} disagrees on {key}: "
                                f"expected {expected}, got {status.get(key)!r}"
                            )
                if len(set(receiver_states)) != 1:
                    raise RuntimeError(
                        "receivers disagree on scheduled foreground activation"
                    )
            except Exception as exc:
                cleanup_errors = []
                if touched:
                    for index, device in enumerate(self.devices):
                        try:
                            status = device.clear_overlay(
                                controller_session_id=session,
                                generation=compensation_generation,
                                scene_revision=scene_revision,
                            )
                            self._require_overlay_ack(
                                status, "foreground compensation clear", index
                            )
                        except Exception as cleanup_exc:
                            cleanup_errors.append({
                                "logical_device": index,
                                "error": str(cleanup_exc),
                            })
                self._local_background_status = {
                    "state": (
                        "degraded" if cleanup_errors or not touched
                        else "foreground_cleared"
                    ),
                    "operation": "foreground_publish_failed",
                    "error": str(exc),
                    "cleanup_errors": cleanup_errors,
                }
                self._sparse_overlay_session_id = (
                    None if cleanup_errors else session
                )
                self._sparse_overlay_generation = (
                    0 if cleanup_errors else compensation_generation
                )
                self._sparse_overlay_snapshot_digest = None
                return False

            self._sparse_overlay_session_id = session
            self._sparse_overlay_generation = overlay_generation
            self._sparse_overlay_snapshot_digest = digest
            self._local_background_status = {
                "state": receiver_states[0],
                "operation": (
                    "foreground_publish" if receiver_states[0] == "active"
                    else "foreground_scheduled"
                ),
                "foreground_generation": overlay_generation,
                "foreground_snapshot_digest": digest.hex(),
                "foreground_patch_counts": [len(items) for items in per_device_patches],
            }
            return True

    def renew_sparse_overlay(
        self, *, controller_session_id, generation, lease_ms
    ):
        """Renew one committed foreground lease only when every board agrees."""
        session = LEDController._controller_session(controller_session_id)
        renewal_generation = LEDController._bounded_uint(
            "generation", generation, 0xFFFFFFFFFFFFFFFF
        )
        if renewal_generation == 0xFFFFFFFFFFFFFFFF:
            raise ValueError(
                "generation must leave one counter value for renewal compensation"
            )
        LEDController.serialize_overlay_renew(
            controller_session_id=session,
            generation=renewal_generation,
            lease_ms=lease_ms,
        )
        with self._controller_lock():
            touched = False
            scene_revision = None
            try:
                if (
                    session != getattr(self, "_sparse_overlay_session_id", None)
                    or renewal_generation
                    != getattr(self, "_sparse_overlay_generation", 0)
                ):
                    raise ValueError(
                        "foreground renew must match the committed session/generation"
                    )
                statuses = self._receiver_statuses(
                    require_capability=True,
                    required_capabilities=SPARSE_OVERLAY_REQUIRED_CAPABILITIES,
                )
                revisions = {
                    int(status.get("receiver_foreground_scene_revision", -1))
                    for status in statuses
                }
                if len(revisions) != 1:
                    raise RuntimeError(
                        "receivers disagree on foreground scene revision"
                    )
                scene_revision = revisions.pop()
                for index, status in enumerate(statuses):
                    if (
                        int(status.get("receiver_foreground_state", -1)) != 2
                        or status.get("receiver_overlay_session_id") != session.hex()
                        or int(status.get(
                            "receiver_overlay_committed_generation", -1
                        )) != renewal_generation
                        or int(status.get(
                            "receiver_overlay_staged_generation", 0
                        )) != 0
                    ):
                        raise RuntimeError(
                            f"receiver {index} is not on the active renewable foreground"
                        )
                for index, device in enumerate(self.devices):
                    touched = True
                    status = device.renew_overlay(
                        controller_session_id=session,
                        generation=renewal_generation,
                        lease_ms=lease_ms,
                    )
                    self._require_overlay_ack(status, "foreground renew", index)
            except Exception as exc:
                cleanup_errors = []
                compensation_generation = renewal_generation + 1
                if touched and scene_revision is not None:
                    for index, device in enumerate(self.devices):
                        try:
                            status = device.clear_overlay(
                                controller_session_id=session,
                                generation=compensation_generation,
                                scene_revision=scene_revision,
                            )
                            self._require_overlay_ack(
                                status, "foreground renew compensation clear", index
                            )
                        except Exception as cleanup_exc:
                            cleanup_errors.append({
                                "logical_device": index,
                                "error": str(cleanup_exc),
                            })
                if touched and not cleanup_errors:
                    self._sparse_overlay_generation = compensation_generation
                    self._sparse_overlay_snapshot_digest = None
                self._local_background_status = {
                    "state": (
                        "degraded" if cleanup_errors or not touched
                        else "foreground_cleared"
                    ),
                    "operation": "foreground_renew_failed",
                    "error": str(exc),
                    "cleanup_errors": cleanup_errors,
                }
                return False
            self._local_background_status.update({
                "state": "active",
                "operation": "foreground_renew",
                "foreground_generation": renewal_generation,
            })
            return True

    def clear_sparse_overlay(
        self, *, controller_session_id, generation, scene_revision
    ):
        """Clear the aggregate foreground everywhere and verify agreement."""
        session = LEDController._controller_session(controller_session_id)
        clear_generation = LEDController._bounded_uint(
            "generation", generation, 0xFFFFFFFFFFFFFFFF
        )
        LEDController.serialize_overlay_clear(
            controller_session_id=session,
            generation=clear_generation,
            scene_revision=scene_revision,
        )
        with self._controller_lock():
            errors = []
            if session != getattr(self, "_sparse_overlay_session_id", None):
                raise ValueError("foreground clear must match the committed session")
            for index, device in enumerate(self.devices):
                try:
                    status = device.clear_overlay(
                        controller_session_id=session,
                        generation=clear_generation,
                        scene_revision=scene_revision,
                    )
                    self._require_overlay_ack(status, "foreground clear", index)
                except Exception as exc:
                    errors.append({"logical_device": index, "error": str(exc)})
            if not errors:
                try:
                    statuses = self._receiver_statuses(
                        require_capability=True,
                        required_capabilities=SPARSE_OVERLAY_REQUIRED_CAPABILITIES,
                    )
                    for index, status in enumerate(statuses):
                        if int(status.get("receiver_foreground_state", -1)) != 0:
                            raise RuntimeError(
                                f"receiver {index} retained foreground state"
                            )
                        if int(status.get(
                            "receiver_overlay_committed_generation", -1
                        )) != clear_generation:
                            raise RuntimeError(
                                f"receiver {index} did not commit foreground clear "
                                f"generation {clear_generation}"
                            )
                except Exception as exc:
                    errors.append({"logical_device": -1, "error": str(exc)})
            self._local_background_status = {
                "state": "degraded" if errors else "active",
                "operation": "foreground_clear",
                **({"errors": errors} if errors else {}),
            }
            if errors:
                self._sparse_overlay_session_id = None
                self._sparse_overlay_generation = 0
                self._sparse_overlay_snapshot_digest = None
            else:
                self._sparse_overlay_generation = clear_generation
                self._sparse_overlay_snapshot_digest = None
            return not errors

    def _provision_local_identities(self):
        """Provision physical host mapping before any context is staged."""
        for device in self.devices:
            for _ in range(2):
                device.query_receiver_status()
        self._receiver_statuses(
            require_capability=True, require_identity=False
        )
        for index, device in enumerate(self.devices):
            device.logical_device_id = index
            device.configure()
            for _ in range(2):
                device.query_receiver_status()
        return self._receiver_statuses(require_capability=True)

    def _commit_presentation_contexts(self, context):
        """Commit each board against one compensated host monotonic anchor."""
        monotonic_ns = getattr(self, "_monotonic_ns", time.monotonic_ns)
        host_anchor_ns = monotonic_ns()
        statuses = []
        for index, device in enumerate(self.devices):
            status = device.commit_presentation_context(
                context, host_monotonic_anchor_ns=host_anchor_ns
            )
            self._require_ack(status, "presentation commit", index)
            statuses.append(status)
        return statuses

    def update_presentation_context(self, context):
        """Atomically replace receiver presentation context without restarting base.

        A successful context commit intentionally invalidates the committed
        foreground on the receivers.  The caller must follow this operation
        with a complete authoritative foreground snapshot before reporting a
        healthy hybrid scene.
        """
        with self._controller_lock():
            if not self._local_background_active:
                raise RuntimeError("presentation context requires an active local background")
            touched = False
            try:
                statuses = self._receiver_statuses(require_capability=True)
                if any(int(status.get("receiver_base_mode", -1)) != 1 for status in statuses):
                    raise RuntimeError(
                        "presentation context requires local background ownership everywhere"
                    )
                operations = (
                    ("presentation begin", "begin_presentation_context"),
                    ("presentation set", "set_presentation_context"),
                )
                for operation, method_name in operations:
                    for index, device in enumerate(self.devices):
                        touched = True
                        status = getattr(device, method_name)(context)
                        self._require_ack(status, operation, index)
                self._commit_presentation_contexts(context)
                committed = self._receiver_statuses(require_capability=True)
                self._validate_presentation_agreement(committed, context)
                expected_digest = context.context_digest.hex()
                for index, status in enumerate(committed):
                    expected = {
                        "receiver_base_mode": 1,
                        "receiver_active_scene_revision": context.scene_revision,
                        "receiver_active_context_digest": expected_digest,
                        "receiver_active_session_id": context.controller_session_id.hex(),
                        "receiver_logical_device": index,
                    }
                    for key, expected_value in expected.items():
                        if status.get(key) != expected_value:
                            raise RuntimeError(
                                f"receiver {index} reported unexpected {key}: "
                                f"{status.get(key)!r}"
                            )
            except Exception as exc:
                self._local_background_status = {
                    "state": "degraded",
                    "operation": "presentation_context_update_failed",
                    "error": str(exc),
                    "receivers_touched": touched,
                }
                self._sparse_overlay_session_id = None
                self._sparse_overlay_generation = 0
                self._sparse_overlay_snapshot_digest = None
                return False

            self._local_background_context_digest = expected_digest
            if getattr(self, "_native_background_active", False):
                self._native_background_context = context
                self._native_background_status.update({
                    "context_digest": expected_digest,
                })
            self._sparse_overlay_session_id = None
            self._sparse_overlay_generation = 0
            self._sparse_overlay_snapshot_digest = None
            self._local_background_status = {
                "state": "foreground_repair_required",
                "operation": "presentation_context_update",
                "context_digest": expected_digest,
                "scene_revision": context.scene_revision,
            }
            return True

    def sparse_overlay_authority(self):
        """Return a detached snapshot of controller-owned foreground authority."""
        with self._controller_lock():
            session = getattr(self, "_sparse_overlay_session_id", None)
            return {
                "controller_session_id": session.hex() if session is not None else None,
                "generation": int(getattr(self, "_sparse_overlay_generation", 0)),
                "snapshot_digest": (
                    self._sparse_overlay_snapshot_digest.hex()
                    if getattr(self, "_sparse_overlay_snapshot_digest", None) is not None
                    else None
                ),
                "local_background_active": bool(self._local_background_active),
                "status": dict(self._local_background_status),
            }

    @staticmethod
    def _agreement_value(statuses, key):
        values = {status.get(key) for status in statuses}
        if len(values) > 1:
            raise RuntimeError(f"receivers disagree on {key}")
        return next(iter(values), None)

    @classmethod
    def _validate_presentation_agreement(cls, statuses, context=None):
        keys = (
            "receiver_vibe_revision",
            "receiver_vibe_digest",
            "receiver_plant_modifier_revision",
            "receiver_plant_modifier_digest",
        )
        agreement = {key: cls._agreement_value(statuses, key) for key in keys}
        if context is not None:
            expected = {
                "receiver_vibe_revision": context.vibe.state.revision,
                "receiver_vibe_digest": context.vibe.state.resolved_profile_digest,
                "receiver_plant_modifier_revision": context.plant_revision,
                "receiver_plant_modifier_digest": context.plant_digest.hex(),
            }
            for key, expected_value in expected.items():
                actual = agreement[key]
                if actual != expected_value:
                    raise RuntimeError(
                        f"receivers reported unexpected {key}: {actual!r}"
                    )
        return agreement

    @staticmethod
    def _require_ack(status, operation, logical_device):
        if not MultiDeviceLEDController._status_result_ok(status):
            result = status.get("receiver_last_result") if isinstance(status, dict) else None
            raise RuntimeError(
                f"receiver {logical_device} did not acknowledge {operation} "
                f"(result={result!r})"
            )

    @classmethod
    def _require_overlay_ack(cls, status, operation, logical_device):
        cls._require_ack(status, operation, logical_device)
        overlay_result = (
            status.get("receiver_overlay_operation_result")
            if isinstance(status, dict) else None
        )
        if overlay_result not in (1, 2):
            raise RuntimeError(
                f"receiver {logical_device} rejected {operation} "
                f"(overlay_result={overlay_result!r})"
            )

    def _stop_local_background_best_effort(self, operation):
        errors = []
        for index, device in enumerate(self.devices):
            try:
                status = device.stop_local_background()
                self._require_ack(status, "local-background stop", index)
            except Exception as exc:
                errors.append({"logical_device": index, "error": str(exc)})
        for index, device in enumerate(self.devices):
            try:
                status = device.query_receiver_status()
                mode = int(status.get("receiver_base_mode", -1))
                if mode != 0:
                    raise RuntimeError(f"receiver remained in base mode {mode}")
            except Exception as exc:
                if not any(error["logical_device"] == index for error in errors):
                    errors.append({"logical_device": index, "error": str(exc)})
        self._local_background_active = bool(errors)
        if not errors:
            self._display_ownership_known = True
            self._local_background_context_digest = None
            self._local_background_parameters = {}
            self._sparse_overlay_session_id = None
            self._sparse_overlay_generation = 0
            self._sparse_overlay_snapshot_digest = None
        self._local_background_status = {
            "state": "degraded" if errors else "fallback",
            "operation": operation,
            **({"errors": errors} if errors else {}),
        }
        return not errors

    def start_local_background(
        self,
        context,
        *,
        component_id=1,
        preferred_cadence_hz=30,
        common_seed=0,
    ):
        """Atomically stage one context and start the static canary everywhere."""
        self._controller_lock()
        # Validate every field through the single-device serializer before any
        # receiver is touched. Each board differs only by its global offset.
        for index in range(self.num_devices):
            LEDController.serialize_local_background_start(
                component_id=component_id,
                preferred_cadence_hz=preferred_cadence_hz,
                global_strip_offset=self.receiver_global_strip_offsets[index],
                common_seed=common_seed,
                scene_epoch=context.scene_epoch,
            )

        with self._controller_lock():
            needs_compensation = False
            try:
                self._provision_local_identities()

                operations = (
                    ("presentation begin", "begin_presentation_context"),
                    ("presentation set", "set_presentation_context"),
                )
                for operation, method_name in operations:
                    for index, device in enumerate(self.devices):
                        needs_compensation = True
                        status = getattr(device, method_name)(context)
                        self._require_ack(status, operation, index)

                needs_compensation = True
                self._commit_presentation_contexts(context)

                committed = self._receiver_statuses(require_capability=True)
                self._validate_presentation_agreement(committed, context)
                expected_context_digest = context.context_digest.hex()
                for index, status in enumerate(committed):
                    active_digest = status.get("receiver_active_context_digest")
                    if active_digest != expected_context_digest:
                        raise RuntimeError(
                            f"receiver {index} committed unexpected presentation context"
                        )
                    if status.get("receiver_active_session_id") != context.controller_session_id.hex():
                        raise RuntimeError(
                            f"receiver {index} committed unexpected controller session"
                        )

                for index, device in enumerate(self.devices):
                    status = device.start_local_background(
                        component_id=component_id,
                        preferred_cadence_hz=preferred_cadence_hz,
                        global_strip_offset=self.receiver_global_strip_offsets[index],
                        common_seed=common_seed,
                        scene_epoch=context.scene_epoch,
                    )
                    self._require_ack(status, "local-background start", index)

                active = self._receiver_statuses(require_capability=True)
                self._validate_presentation_agreement(active, context)
                for index, status in enumerate(active):
                    expected = {
                        "receiver_base_mode": 1,
                        "receiver_component_id": component_id,
                        "receiver_declared_cadence_hz": preferred_cadence_hz,
                        "receiver_global_strip_offset": (
                            self.receiver_global_strip_offsets[index]
                        ),
                        "receiver_common_seed": common_seed,
                        "receiver_scene_epoch": context.scene_epoch,
                        "receiver_active_scene_revision": context.scene_revision,
                        "receiver_active_context_digest": expected_context_digest,
                        "receiver_active_session_id": context.controller_session_id.hex(),
                        "receiver_logical_device": index,
                    }
                    for key, expected_value in expected.items():
                        if status.get(key) != expected_value:
                            raise RuntimeError(
                                f"receiver {index} reported unexpected {key}: "
                                f"{status.get(key)!r}"
                            )
            except Exception as exc:
                if needs_compensation:
                    self._stop_local_background_best_effort("start_compensation")
                    self._local_background_status["start_error"] = str(exc)
                else:
                    self._local_background_active = False
                    self._local_background_status = {
                        "state": "rejected", "operation": "start_preflight",
                        "start_error": str(exc),
                    }
                return False

            self._local_background_active = True
            self._display_ownership_known = True
            self._local_background_context_digest = context.context_digest.hex()
            self._sparse_overlay_session_id = None
            self._sparse_overlay_generation = 0
            self._sparse_overlay_snapshot_digest = None
            self._local_background_parameters = {
                "component_id": component_id,
                "preferred_cadence_hz": preferred_cadence_hz,
                "common_seed": common_seed,
                "scene_epoch": context.scene_epoch,
            }
            self._local_background_status = {
                "state": "active", "operation": "start",
                "context_digest": self._local_background_context_digest,
            }
            return True

    def stop_local_background(self):
        """Return every receiver to its compiled startup/failure fallback."""
        with self._controller_lock():
            return self._stop_local_background_best_effort("stop")

    def update_local_background_params(
        self, *, preferred_cadence_hz, common_seed
    ):
        """Update all boards or restore the prior parameters on partial failure."""
        self._controller_lock()
        for index in range(self.num_devices):
            LEDController.serialize_local_background_params(
                preferred_cadence_hz=preferred_cadence_hz,
                global_strip_offset=self.receiver_global_strip_offsets[index],
                common_seed=common_seed,
            )
        with self._controller_lock():
            if not self._local_background_active:
                raise RuntimeError("local background is not active")
            prior = dict(self._local_background_parameters)
            updated = []
            try:
                for index, device in enumerate(self.devices):
                    status = device.update_local_background_params(
                        preferred_cadence_hz=preferred_cadence_hz,
                        global_strip_offset=self.receiver_global_strip_offsets[index],
                        common_seed=common_seed,
                    )
                    self._require_ack(status, "local-background parameters", index)
                    updated.append(index)
                active = self._receiver_statuses(require_capability=True)
                for index, status in enumerate(active):
                    expected = {
                        "receiver_base_mode": 1,
                        "receiver_declared_cadence_hz": preferred_cadence_hz,
                        "receiver_global_strip_offset": (
                            self.receiver_global_strip_offsets[index]
                        ),
                        "receiver_common_seed": common_seed,
                    }
                    for key, expected_value in expected.items():
                        if status.get(key) != expected_value:
                            raise RuntimeError(
                                f"receiver {index} reported unexpected {key}: "
                                f"{status.get(key)!r}"
                            )
            except Exception as exc:
                rollback_errors = []
                for index in reversed(updated):
                    try:
                        status = self.devices[index].update_local_background_params(
                            preferred_cadence_hz=prior["preferred_cadence_hz"],
                            global_strip_offset=(
                                self.receiver_global_strip_offsets[index]
                            ),
                            common_seed=prior["common_seed"],
                        )
                        self._require_ack(status, "parameter rollback", index)
                    except Exception as rollback_exc:
                        rollback_errors.append({
                            "logical_device": index, "error": str(rollback_exc)
                        })
                if rollback_errors:
                    self._stop_local_background_best_effort("parameter_fallback")
                else:
                    try:
                        restored = self._receiver_statuses(require_capability=True)
                        for index, status in enumerate(restored):
                            if (
                                status.get("receiver_declared_cadence_hz")
                                != prior["preferred_cadence_hz"]
                                or status.get("receiver_common_seed") != prior["common_seed"]
                                or status.get("receiver_global_strip_offset")
                                != self.receiver_global_strip_offsets[index]
                            ):
                                raise RuntimeError(
                                    f"receiver {index} did not restore prior parameters"
                                )
                    except Exception as rollback_exc:
                        rollback_errors.append({
                            "logical_device": -1, "error": str(rollback_exc)
                        })
                        self._stop_local_background_best_effort("parameter_fallback")
                self._local_background_status.update({
                    "parameter_error": str(exc),
                    **({"rollback_errors": rollback_errors} if rollback_errors else {}),
                })
                return False
            self._local_background_parameters.update({
                "preferred_cadence_hz": preferred_cadence_hz,
                "common_seed": common_seed,
            })
            self._local_background_status = {
                "state": "active", "operation": "parameter_update",
                "context_digest": self._local_background_context_digest,
            }
            return True
    
    def close(self):
        """Close all SPI connections"""
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
        for device_id, device in enumerate(self.devices):
            try:
                device.close()
                if self.debug:
                    print(f"✓ Device {device_id} closed")
            except Exception as e:
                if self.debug:
                    print(f"⚠ Device {device_id} close warning: {e}")

    def get_stats(self):
        """Return aggregated stats across all devices."""
        self._controller_lock()
        device_stats = []
        max_frames_sent = 0
        spi_transfers = 0
        bytes_sent = 0
        semantic_bytes_sent = 0
        transport_envelope_bytes_sent = 0
        transport_padding_bytes_sent = 0
        full_frame_transfers = 0
        full_frame_status_transfers = 0
        full_frame_status_samples = 0
        full_frame_status_sample_misses = 0
        full_frame_write_only_transfers = 0
        full_frame_frames_since_status_sample = 0
        full_frame_max_status_sample_gap = 0
        spidev_buffer_sizes = []
        full_frame_write_only_support = []
        full_frame_semantic_bytes_sent = 0
        full_frame_wire_bytes_sent = 0
        transport_envelope_devices = 0
        fec_transport_requested_devices = 0
        fec_transport_enabled_devices = 0
        fec_frames_sent = 0
        fec_codewords_sent = 0
        fec_parity_bytes_sent = 0
        fec_data_padding_bytes_sent = 0
        crc_bytes_sent = 0
        errors = 0
        receiver_status_devices = 0
        receiver_legacy_status_devices = 0
        receiver_crc_errors = 0
        receiver_packets = 0
        receiver_crc_ok_packets = 0
        receiver_fec_packets_received = 0
        receiver_fec_packets_accepted = 0
        receiver_fec_corrected_packets = 0
        receiver_fec_corrected_codewords = 0
        receiver_fec_uncorrectable_packets = 0
        receiver_fec_semantic_crc_errors = 0
        receiver_fec_framing_errors = 0
        receiver_fec_uncorrectable_packets_process_delta = 0
        receiver_fec_semantic_crc_errors_process_delta = 0
        receiver_fec_framing_errors_process_delta = 0
        receiver_fec_uncorrectable_packets_process_baseline = 0
        receiver_fec_semantic_crc_errors_process_baseline = 0
        receiver_fec_framing_errors_process_baseline = 0
        receiver_fec_terminal_baselines_established = 0
        receiver_fec_terminal_invalid_baselines = 0
        receiver_fec_terminal_counter_resets = 0
        receiver_fec_last_decode_us = 0
        receiver_fec_max_decode_us = 0
        receiver_frames_rendered = 0
        receiver_frames_accepted = 0
        receiver_frames_displayed = 0
        receiver_frames_superseded = 0
        receiver_publish_drops = 0
        receiver_spi_queue_errors = 0
        receiver_display_errors = 0
        receiver_status_misses = 0
        receiver_status_versions = []
        receiver_status_max_versions_seen = []
        receiver_last_encode_us = 0
        receiver_last_show_us = 0
        receiver_capabilities_all = None
        receiver_base_modes = []
        receiver_context_digests = []
        receiver_declared_cadence_hz = []
        receiver_local_missed_deadlines = 0
        receiver_max_local_render_us = 0
        receiver_transition_reasons = []
        receiver_lane_masks = []
        receiver_stagger_phases = []
        last_frame_ms = 0.0
        weighted_avg_total = 0.0
        weighted_avg_frames = 0

        for device in self.devices:
            stats = {}
            if hasattr(device, "get_stats"):
                stats = device.get_stats()
            device_stats.append(stats)

            frames = int(stats.get('frames_sent', 0) or 0)
            # Use max (not sum) — all devices receive the same logical frame
            max_frames_sent = max(max_frames_sent, frames)
            spi_transfers += int(stats.get('spi_transfers', 0) or 0)
            bytes_sent += int(stats.get('bytes_sent', 0) or 0)
            semantic_bytes_sent += int(stats.get('semantic_bytes_sent', 0) or 0)
            transport_envelope_bytes_sent += int(
                stats.get('transport_envelope_bytes_sent', 0) or 0
            )
            transport_padding_bytes_sent += int(
                stats.get('transport_padding_bytes_sent', 0) or 0
            )
            full_frame_transfers += int(
                stats.get('full_frame_transfers', 0) or 0
            )
            full_frame_status_transfers += int(
                stats.get('full_frame_status_transfers', 0) or 0
            )
            full_frame_status_samples += int(
                stats.get('full_frame_status_samples', 0) or 0
            )
            full_frame_status_sample_misses += int(
                stats.get('full_frame_status_sample_misses', 0) or 0
            )
            full_frame_write_only_transfers += int(
                stats.get('full_frame_write_only_transfers', 0) or 0
            )
            full_frame_frames_since_status_sample = max(
                full_frame_frames_since_status_sample,
                int(stats.get('full_frame_frames_since_status_sample', 0) or 0),
            )
            full_frame_max_status_sample_gap = max(
                full_frame_max_status_sample_gap,
                int(stats.get('full_frame_max_status_sample_gap', 0) or 0),
            )
            spidev_buffer_size = stats.get('spidev_buffer_size')
            if type(spidev_buffer_size) is int and spidev_buffer_size > 0:
                spidev_buffer_sizes.append(spidev_buffer_size)
            full_frame_write_only_support.append(
                stats.get('full_frame_write_only_supported') is True
            )
            full_frame_semantic_bytes_sent += int(
                stats.get('full_frame_semantic_bytes_sent', 0) or 0
            )
            full_frame_wire_bytes_sent += int(
                stats.get('full_frame_wire_bytes_sent', 0) or 0
            )
            if stats.get('transport_envelope_enabled'):
                transport_envelope_devices += 1
            if stats.get('fec_transport_requested'):
                fec_transport_requested_devices += 1
            if stats.get('fec_transport_enabled'):
                fec_transport_enabled_devices += 1
            fec_frames_sent += int(stats.get('fec_frames_sent', 0) or 0)
            fec_codewords_sent += int(stats.get('fec_codewords_sent', 0) or 0)
            fec_parity_bytes_sent += int(
                stats.get('fec_parity_bytes_sent', 0) or 0
            )
            fec_data_padding_bytes_sent += int(
                stats.get('fec_data_padding_bytes_sent', 0) or 0
            )
            crc_bytes_sent += int(stats.get('crc_bytes_sent', 0) or 0)
            errors += int(stats.get('errors', 0) or 0)
            if stats.get('receiver_status_seen'):
                receiver_status_devices += 1
                receiver_lane_masks.append(int(stats.get('receiver_lane_mask', 0xFF)))
                receiver_stagger_phases.append(int(stats.get('receiver_stagger_phases', 0)))
                if stats.get('receiver_status_legacy'):
                    receiver_legacy_status_devices += 1
            receiver_crc_errors += int(stats.get('receiver_crc_errors', 0) or 0)
            receiver_packets += int(stats.get('receiver_packets', 0) or 0)
            receiver_crc_ok_packets += int(stats.get('receiver_crc_ok_packets', 0) or 0)
            receiver_fec_packets_received += int(
                stats.get('receiver_fec_packets_received', 0) or 0
            )
            receiver_fec_packets_accepted += int(
                stats.get('receiver_fec_packets_accepted', 0) or 0
            )
            receiver_fec_corrected_packets += int(
                stats.get('receiver_fec_corrected_packets', 0) or 0
            )
            receiver_fec_corrected_codewords += int(
                stats.get('receiver_fec_corrected_codewords', 0) or 0
            )
            receiver_fec_uncorrectable_packets += int(
                stats.get('receiver_fec_uncorrectable_packets', 0) or 0
            )
            receiver_fec_semantic_crc_errors += int(
                stats.get('receiver_fec_semantic_crc_errors', 0) or 0
            )
            receiver_fec_framing_errors += int(
                stats.get('receiver_fec_framing_errors', 0) or 0
            )
            receiver_fec_uncorrectable_packets_process_delta += int(
                stats.get(
                    'receiver_fec_uncorrectable_packets_process_delta', 0
                ) or 0
            )
            receiver_fec_semantic_crc_errors_process_delta += int(
                stats.get(
                    'receiver_fec_semantic_crc_errors_process_delta', 0
                ) or 0
            )
            receiver_fec_framing_errors_process_delta += int(
                stats.get('receiver_fec_framing_errors_process_delta', 0) or 0
            )
            receiver_fec_uncorrectable_packets_process_baseline += int(
                stats.get(
                    'receiver_fec_uncorrectable_packets_process_baseline', 0
                ) or 0
            )
            receiver_fec_semantic_crc_errors_process_baseline += int(
                stats.get(
                    'receiver_fec_semantic_crc_errors_process_baseline', 0
                ) or 0
            )
            receiver_fec_framing_errors_process_baseline += int(
                stats.get('receiver_fec_framing_errors_process_baseline', 0)
                or 0
            )
            if stats.get('receiver_fec_terminal_baseline_established') is True:
                receiver_fec_terminal_baselines_established += 1
            if stats.get('receiver_fec_terminal_baseline_invalid') is True:
                receiver_fec_terminal_invalid_baselines += 1
            receiver_fec_terminal_counter_resets += int(
                stats.get('receiver_fec_terminal_counter_resets', 0) or 0
            )
            receiver_fec_last_decode_us = max(
                receiver_fec_last_decode_us,
                int(stats.get('receiver_fec_last_decode_us', 0) or 0),
            )
            receiver_fec_max_decode_us = max(
                receiver_fec_max_decode_us,
                int(stats.get('receiver_fec_max_decode_us', 0) or 0),
            )
            receiver_frames_rendered += int(stats.get('receiver_frames_rendered', 0) or 0)
            receiver_frames_accepted += int(stats.get('receiver_frames_accepted', 0) or 0)
            receiver_frames_displayed += int(stats.get('receiver_frames_displayed', 0) or 0)
            receiver_frames_superseded += int(stats.get('receiver_frames_superseded', 0) or 0)
            receiver_publish_drops += int(stats.get('receiver_publish_drops', 0) or 0)
            receiver_spi_queue_errors += int(stats.get('receiver_spi_queue_errors', 0) or 0)
            receiver_display_errors += int(stats.get('receiver_display_errors', 0) or 0)
            receiver_status_misses += int(stats.get('receiver_status_misses', 0) or 0)
            receiver_status_versions.append(
                int(stats.get('receiver_status_version', 0) or 0)
            )
            receiver_status_max_versions_seen.append(
                int(stats.get('receiver_status_max_version_seen', 0) or 0)
            )
            receiver_last_encode_us = max(
                receiver_last_encode_us,
                int(stats.get('receiver_last_encode_us', 0) or 0),
            )
            receiver_last_show_us = max(
                receiver_last_show_us,
                int(stats.get('receiver_last_show_us', 0) or 0),
            )
            capabilities = int(stats.get('receiver_capabilities', 0) or 0)
            receiver_capabilities_all = (
                capabilities if receiver_capabilities_all is None
                else receiver_capabilities_all & capabilities
            )
            receiver_base_modes.append(stats.get('receiver_base_mode'))
            receiver_context_digests.append(
                stats.get('receiver_active_context_digest')
            )
            receiver_declared_cadence_hz.append(
                int(stats.get('receiver_declared_cadence_hz', 0) or 0)
            )
            receiver_local_missed_deadlines += int(
                stats.get('receiver_local_missed_deadlines', 0) or 0
            )
            receiver_max_local_render_us = max(
                receiver_max_local_render_us,
                int(stats.get('receiver_max_local_render_us', 0) or 0),
            )
            receiver_transition_reasons.append(
                stats.get('receiver_transition_reason')
            )

            last_frame_ms = max(last_frame_ms, float(stats.get('last_frame_duration_ms', 0.0) or 0.0))
            avg_ms = float(stats.get('avg_frame_duration_ms', 0.0) or 0.0)
            if frames > 0:
                weighted_avg_total += avg_ms * frames
                weighted_avg_frames += frames

        avg_frame_ms = weighted_avg_total / weighted_avg_frames if weighted_avg_frames else 0.0

        return {
            'devices': device_stats,
            'aggregate': {
                'num_devices': self.num_devices,
                'strip_count': self.strip_count,
                'total_leds': self.total_leds,
                'frames_sent': max_frames_sent,
                'logical_frames_sent': self._logical_frames_sent,
                'spi_bus_count': len(self._devices_by_bus),
                'device_dispatch_order': [
                    logical_device
                    for device_ids in self._devices_by_bus.values()
                    for logical_device in device_ids
                ],
                'device_map': [
                    {
                        'logical_device': logical_device,
                        'bus': bus,
                        'chip_select': chip_select,
                        'local_strip_count': self.receiver_strip_counts[
                            logical_device
                        ],
                        'global_strip_offset': self.receiver_global_strip_offsets[
                            logical_device
                        ],
                        'reverse_host_strip_order': (
                            self.reverse_host_strips_by_logical_receiver[
                                logical_device
                            ]
                        ),
                        'reverse_native_strip_order': (
                            self.reverse_native_strips_by_logical_receiver[
                                logical_device
                            ]
                        ),
                        'physical_output_lane_mask': self.receiver_lane_masks[
                            logical_device
                        ],
                        'spi_speed_hz': device_stats[logical_device].get(
                            'spi_speed_hz'
                        ),
                        'spi_mode': device_stats[logical_device].get('spi_mode'),
                    }
                    for logical_device, (bus, chip_select) in enumerate(self.device_map)
                ],
                'spi_transfers': spi_transfers,
                'bytes_sent': bytes_sent,
                'semantic_bytes_sent': semantic_bytes_sent,
                'transport_envelope_bytes_sent': transport_envelope_bytes_sent,
                'transport_padding_bytes_sent': transport_padding_bytes_sent,
                'full_frame_transfers': full_frame_transfers,
                'full_frame_status_transfers': full_frame_status_transfers,
                'full_frame_status_samples': full_frame_status_samples,
                'full_frame_status_sample_misses': (
                    full_frame_status_sample_misses
                ),
                'full_frame_write_only_transfers': (
                    full_frame_write_only_transfers
                ),
                'full_frame_frames_since_status_sample': (
                    full_frame_frames_since_status_sample
                ),
                'full_frame_max_status_sample_gap': (
                    full_frame_max_status_sample_gap
                ),
                'spidev_buffer_size': (
                    min(spidev_buffer_sizes) if spidev_buffer_sizes else None
                ),
                'full_frame_write_only_supported': (
                    bool(full_frame_write_only_support)
                    and all(full_frame_write_only_support)
                ),
                'full_frame_semantic_bytes_sent': full_frame_semantic_bytes_sent,
                'full_frame_wire_bytes_sent': full_frame_wire_bytes_sent,
                'transport_envelope_devices': transport_envelope_devices,
                'fec_transport_requested_devices': (
                    fec_transport_requested_devices
                ),
                'fec_transport_enabled_devices': fec_transport_enabled_devices,
                'fec_frames_sent': fec_frames_sent,
                'fec_codewords_sent': fec_codewords_sent,
                'fec_parity_bytes_sent': fec_parity_bytes_sent,
                'fec_data_padding_bytes_sent': fec_data_padding_bytes_sent,
                'crc_bytes_sent': crc_bytes_sent,
                'errors': errors,
                'receiver_status_devices': receiver_status_devices,
                'receiver_legacy_status_devices': receiver_legacy_status_devices,
                'receiver_lane_masks': receiver_lane_masks,
                'receiver_stagger_phases': receiver_stagger_phases,
                'receiver_crc_errors': receiver_crc_errors,
                'receiver_packets': receiver_packets,
                'receiver_crc_ok_packets': receiver_crc_ok_packets,
                'receiver_fec_packets_received': receiver_fec_packets_received,
                'receiver_fec_packets_accepted': receiver_fec_packets_accepted,
                'receiver_fec_corrected_packets': receiver_fec_corrected_packets,
                'receiver_fec_corrected_codewords': (
                    receiver_fec_corrected_codewords
                ),
                'receiver_fec_uncorrectable_packets': (
                    receiver_fec_uncorrectable_packets
                ),
                'receiver_fec_semantic_crc_errors': (
                    receiver_fec_semantic_crc_errors
                ),
                'receiver_fec_framing_errors': receiver_fec_framing_errors,
                'receiver_fec_uncorrectable_packets_process_delta': (
                    receiver_fec_uncorrectable_packets_process_delta
                ),
                'receiver_fec_semantic_crc_errors_process_delta': (
                    receiver_fec_semantic_crc_errors_process_delta
                ),
                'receiver_fec_framing_errors_process_delta': (
                    receiver_fec_framing_errors_process_delta
                ),
                'receiver_fec_uncorrectable_packets_process_baseline': (
                    receiver_fec_uncorrectable_packets_process_baseline
                ),
                'receiver_fec_semantic_crc_errors_process_baseline': (
                    receiver_fec_semantic_crc_errors_process_baseline
                ),
                'receiver_fec_framing_errors_process_baseline': (
                    receiver_fec_framing_errors_process_baseline
                ),
                'receiver_fec_terminal_baselines_established': (
                    receiver_fec_terminal_baselines_established
                ),
                'receiver_fec_terminal_invalid_baselines': (
                    receiver_fec_terminal_invalid_baselines
                ),
                'receiver_fec_terminal_counter_resets': (
                    receiver_fec_terminal_counter_resets
                ),
                'receiver_fec_last_decode_us': receiver_fec_last_decode_us,
                'receiver_fec_max_decode_us': receiver_fec_max_decode_us,
                'receiver_frames_rendered': receiver_frames_rendered,
                'receiver_frames_accepted': receiver_frames_accepted,
                'receiver_frames_displayed': receiver_frames_displayed,
                'receiver_frames_superseded': receiver_frames_superseded,
                'receiver_publish_drops': receiver_publish_drops,
                'receiver_spi_queue_errors': receiver_spi_queue_errors,
                'receiver_display_errors': receiver_display_errors,
                'receiver_status_misses': receiver_status_misses,
                'receiver_status_version': (
                    min(receiver_status_versions)
                    if receiver_status_versions else 0
                ),
                'receiver_status_max_version_seen': (
                    min(receiver_status_max_versions_seen)
                    if receiver_status_max_versions_seen else 0
                ),
                'receiver_last_encode_us': receiver_last_encode_us,
                'receiver_last_show_us': receiver_last_show_us,
                'receiver_capabilities_all': receiver_capabilities_all or 0,
                'receiver_base_modes': receiver_base_modes,
                'receiver_active_context_digests': receiver_context_digests,
                'receiver_declared_cadence_hz': receiver_declared_cadence_hz,
                'receiver_local_missed_deadlines': receiver_local_missed_deadlines,
                'receiver_max_local_render_us': receiver_max_local_render_us,
                'receiver_transition_reasons': receiver_transition_reasons,
                'receiver_status_refresh': dict(getattr(
                    self, '_receiver_status_refresh',
                    {
                        'request_id': None,
                        'completed_at': None,
                        'passed': False,
                        'errors': ['no explicit receiver-status refresh has completed'],
                    },
                )),
                'local_background': dict(getattr(
                    self, '_local_background_status',
                    {'state': 'host_full_scene', 'operation': 'legacy_status'},
                )),
                'installation_profile': dict(getattr(
                    self, '_installation_profile_status',
                    {'state': 'idle', 'operation': 'legacy_status'},
                )),
                'native_background': dict(getattr(
                    self, '_native_background_status',
                    {'state': 'idle', 'operation': 'legacy_status'},
                )),
                'last_frame_duration_ms': last_frame_ms,
                'avg_frame_duration_ms': avg_frame_ms,
                'spi_speed_hz': device_stats[0].get('spi_speed_hz') if device_stats else None,
                'spi_speeds_hz': [
                    stats.get('spi_speed_hz') for stats in device_stats
                ],
                'spi_mode': device_stats[0].get('spi_mode') if device_stats else None,
            }
        }
    
    @staticmethod
    def _device_exists(bus: int, device: int) -> bool:
        """Check if a /dev/spidev device exists"""
        return os.path.exists(f"/dev/spidev{bus}.{device}")
    
    @staticmethod
    def _parse_device_map_env() -> Optional[List[DeviceMapEntry]]:
        """
        Optional override via LEDGRID_DEVICE_MAP, e.g. "0:0;0:1".
        Each entry is bus:device.
        """
        raw = os.environ.get("LEDGRID_DEVICE_MAP", "").strip()
        if not raw:
            return None

        entries: List[DeviceMapEntry] = []
        for chunk in raw.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split(":")
            if len(parts) != 2:
                raise ValueError(f"Invalid LEDGRID_DEVICE_MAP entry: {chunk!r}")
            bus = int(parts[0])
            device = int(parts[1])
            entries.append((bus, device))
        return entries

    def _build_device_map(self, num_devices: int, primary_bus: int) -> List[DeviceMapEntry]:
        """
        Map devices to available SPI buses.
        
        Prefers sequential devices on the primary bus, but if additional chip
        selects are unavailable (e.g. only 0.0/0.1 exist), falls back to SPI1.
        
        Args:
            num_devices: Number of devices to map
            primary_bus: Primary SPI bus (usually 0)
            
        Returns:
            List of (bus, device_id) tuples
        """
        env_map = self._parse_device_map_env()
        if env_map is not None:
            if len(env_map) < num_devices:
                raise ValueError(
                    f"LEDGRID_DEVICE_MAP defines {len(env_map)} devices, but {num_devices} were requested"
                )
            return env_map[:num_devices]

        # For 1-2 devices, just use the primary bus
        if num_devices <= 2:
            return [(primary_bus, device_id) for device_id in range(num_devices)]

        # Wall layout: SPI0 CE0/CE1 plus SPI1, unless the primary bus already
        # exposes CE2+ (unusual custom overlay).
        if not self._device_exists(primary_bus, 2) and self._device_exists(1, 0):
            if self.debug:
                print(
                    "[INFO] Using SPI1 fallback for logical receivers 2+ "
                    "(CE1, CE0, CE2)"
                )
            return wall_device_map(num_devices)

        return [(primary_bus, device_id) for device_id in range(num_devices)]
    
    @staticmethod
    def _resolve_mode(bus: int, default_mode: int) -> int:
        """
        Allow per-bus SPI mode overrides via env (LEDGRID_SPI0_MODE, LEDGRID_SPI1_MODE).
        
        Args:
            bus: SPI bus number
            default_mode: Default SPI mode
            
        Returns:
            Resolved SPI mode
        """
        env_key = f"LEDGRID_SPI{bus}_MODE"
        raw = os.environ.get(env_key)
        if raw is None:
            return default_mode
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default_mode

    @staticmethod
    def _resolve_speed(bus: int, default_speed: int) -> int:
        """Resolve an optional positive per-bus SPI clock override."""
        env_key = f"LEDGRID_SPI{bus}_SPEED"
        raw = os.environ.get(env_key)
        if raw is None:
            return default_speed
        try:
            resolved = int(raw)
        except (TypeError, ValueError):
            return default_speed
        return resolved if resolved > 0 else default_speed
