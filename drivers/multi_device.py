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
    CAPABILITY_EXPLICIT_BASE_OWNERSHIP,
    CAPABILITY_PRESENTATION_CONTEXT_V1,
    CAPABILITY_STATIC_LOCAL_BACKGROUND,
    CAPABILITY_STATUS_V3,
    CAPABILITY_SPARSE_OVERLAY_V1,
    CAPABILITY_SPARSE_OVERLAY_BATCH_V1,
    LEDController,
    MAX_RGBA_PIXELS_PER_BATCH_SPAN,
    OVERLAY_FORMAT_PREMULTIPLIED_RGBA8,
    OVERLAY_UPDATE_DELTA,
    OVERLAY_UPDATE_FULL_SNAPSHOT,
    SPI_RESPONSE_QUEUE_DEPTH,
    SPI_BUS,
    SPI_MODE,
    SPI_SPEED,
)
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP

DeviceMapEntry = Tuple[int, int]
LOCAL_BACKGROUND_REQUIRED_CAPABILITIES = (
    CAPABILITY_STATIC_LOCAL_BACKGROUND
    | CAPABILITY_PRESENTATION_CONTEXT_V1
    | CAPABILITY_STATUS_V3
    | CAPABILITY_EXPLICIT_BASE_OWNERSHIP
)
SPARSE_OVERLAY_REQUIRED_CAPABILITIES = (
    LOCAL_BACKGROUND_REQUIRED_CAPABILITIES
    | CAPABILITY_SPARSE_OVERLAY_V1
    | CAPABILITY_SPARSE_OVERLAY_BATCH_V1
)


class MultiDeviceLEDController:
    """Multi-device LED controller that manages multiple ESP32 devices"""

    def with_receiver_hybrid_transport_policy(
        self, transport_policy, *, physical_lane_order=(0, 1, 2, 3)
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
            self, physical_lane_order=physical_lane_order
        )

    def _controller_lock(self):
        """Lazily initialize orchestration state for legacy/test construction."""
        if not hasattr(self, "_transport_lock"):
            self._transport_lock = threading.RLock()
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
        return self._transport_lock
    
    def __init__(self, 
                 num_devices: int = 1,
                 bus: int = SPI_BUS,
                 speed: int = SPI_SPEED,
                 mode: int = SPI_MODE,
                 strips_per_device: int = 8,
                 leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
                 debug: bool = False,
                 parallel: bool = True,
                 device_map: Optional[List[DeviceMapEntry]] = None):
        """
        Initialize multi-device LED controller
        
        Args:
            num_devices: Number of ESP32-S3 receivers (default: 1)
            bus: SPI bus number (default: 0)
            speed: SPI speed in Hz (default: SPI_SPEED, currently 20 MHz)
            mode: SPI mode (default: SPI_MODE, currently mode 0)
            strips_per_device: LED strips per receiver (default: 8)
            leds_per_strip: LEDs per strip (installed default: 138)
            debug: Enable debug output
            parallel: Send data to devices in parallel using threads
            device_map: Optional list of (bus, device) tuples for each device
        """
        self.num_devices = num_devices
        self.strips_per_device = strips_per_device
        self.leds_per_strip = leds_per_strip
        self.debug = debug
        self.parallel = parallel
        self._executor = None
        
        # Calculate total dimensions
        self.strip_count = num_devices * strips_per_device
        self.total_leds = self.strip_count * leds_per_strip
        self.leds_per_device = strips_per_device * leds_per_strip
        self._logical_frames_sent = 0
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
        self.device_map = device_map or self._build_device_map(num_devices, bus)
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
        
        # Initialize individual device controllers
        self.devices: List[LEDController] = []
        for device_index, (device_bus, device_id) in enumerate(self.device_map):
            if self.debug:
                print(f"\nInitializing Device {device_index} on /dev/spidev{device_bus}.{device_id}")
            
            device = LEDController(
                bus=device_bus,
                device=device_id,  # CE0, CE1, etc.
                speed=speed,
                mode=self._resolve_mode(device_bus, mode),
                strips=strips_per_device,
                leds_per_strip=leds_per_strip,
                debug=debug,
                logical_device_id=device_index,
            )
            self.devices.append(device)

        # All bus/controller objects must exist before observability I/O begins.
        # Confirmed v3 receivers get their physical logical identity immediately;
        # older receivers retain the exact legacy five-byte CONFIG packet.
        self._initialize_receiver_identity_observability()
        
        if self.debug:
            print(f"\n✓ All {num_devices} devices initialized\n")

    def _initialize_receiver_identity_observability(self):
        """Best-effort identity provisioning without blocking legacy streaming."""
        for index, device in enumerate(self.devices):
            try:
                for _ in range(2):
                    device.query_receiver_status()
                device.logical_device_id = index
                device.configure()
                if int(device.get_stats().get("receiver_status_version", 0) or 0) >= 3:
                    status = None
                    for _ in range(2):
                        status = device.query_receiver_status()
                    if status.get("receiver_logical_device") != index:
                        raise RuntimeError(
                            f"reported logical identity "
                            f"{status.get('receiver_logical_device')!r}, expected {index}"
                        )
            except Exception as exc:
                print(
                    f"[LEDGRID] Receiver {index} identity observability unavailable: {exc}; "
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
    
    def _split_frame(self, colors: List[Tuple[int, int, int]]) -> List[List[Tuple[int, int, int]]]:
        """
        Split full frame into per-device chunks
        
        Args:
            colors: Full frame of (r,g,b) tuples for all pixels
            
        Returns:
            List of color lists, one per device
        """
        pixels_per_device = self.strips_per_device * self.leds_per_strip

        if isinstance(colors, np.ndarray):
            total_needed = self.num_devices * pixels_per_device
            if colors.shape[0] < total_needed:
                colors = np.concatenate([colors, np.zeros((total_needed - colors.shape[0], 3), dtype=np.uint8)])
            device_frames = []
            for device_id in range(self.num_devices):
                start = device_id * pixels_per_device
                device_frames.append(colors[start:start + pixels_per_device])
            return device_frames

        device_frames = []
        for device_id in range(self.num_devices):
            device_colors = []
            for local_strip in range(self.strips_per_device):
                global_strip = device_id * self.strips_per_device + local_strip
                start_idx = global_strip * self.leds_per_strip
                end_idx = start_idx + self.leds_per_strip

                if start_idx < len(colors):
                    strip_pixels = colors[start_idx:end_idx]
                else:
                    strip_pixels = []

                if len(strip_pixels) < self.leds_per_strip:
                    strip_pixels = list(strip_pixels) + [(0, 0, 0)] * (self.leds_per_strip - len(strip_pixels))

                device_colors.extend(strip_pixels[:self.leds_per_strip])

            device_frames.append(device_colors)

        return device_frames
    
    def _send_to_device(self, device_id: int, colors: List[Tuple[int, int, int]]):
        """Send frame data to a specific device"""
        try:
            self.devices[device_id].set_all_pixels(colors)
            return True
        except Exception as e:
            if self.debug:
                print(f"✗ Error sending to device {device_id}: {e}")
            return False

    def _send_bus_frames(self, device_ids, device_frames):
        """Serialize chip selects on one bus while independent buses overlap."""
        successful = True
        for device_id in device_ids:
            successful = self._send_to_device(
                device_id, device_frames[device_id]
            ) and successful
        return successful

    def _send_bus_partial(self, device_ids, device_frames, device_ranges):
        for device_id in device_ids:
            ranges = device_ranges.get(device_id)
            if not ranges:
                continue
            try:
                dirty_pixels = sum(end - start for start, end in ranges)
                if dirty_pixels > self.leds_per_device * 0.35:
                    self.devices[device_id].set_all_pixels(device_frames[device_id])
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
            had_sparse_authority = (
                getattr(self, "_sparse_overlay_session_id", None) is not None
            )
            # Split frame into per-device chunks. A complete SET_ALL is the
            # protocol's universal takeover path; no local STOP is required.
            device_frames = self._split_frame(colors)
            successful = True

            if self._executor is not None:
                futures = [
                    self._executor.submit(self._send_bus_frames, device_ids, device_frames)
                    for device_ids in self._devices_by_bus.values()
                ]
                for future in futures:
                    successful = bool(future.result()) and successful
            else:
                # Send to devices sequentially
                for device_id, device_colors in enumerate(device_frames):
                    successful = self._send_to_device(device_id, device_colors) and successful
            self._logical_frames_sent += 1
            if successful and (
                was_local or had_sparse_authority or not self._display_ownership_known
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
                self._local_background_context_digest = None
                self._local_background_parameters = {}
                self._sparse_overlay_session_id = None
                self._sparse_overlay_generation = 0
                self._sparse_overlay_snapshot_digest = None
                self._local_background_status = {
                    "state": "host_full_scene", "operation": "set_all_takeover"
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
            pixels_per_device = self.leds_per_device
            device_ranges = {}
            for start, end in sorted(dirty_ranges):
                start = max(0, int(start))
                end = min(self.total_leds, int(end))
                while start < end:
                    device_id = start // pixels_per_device
                    device_end = min(end, (device_id + 1) * pixels_per_device)
                    local_start = start - device_id * pixels_per_device
                    local_end = device_end - device_id * pixels_per_device
                    ranges = device_ranges.setdefault(device_id, [])
                    if ranges and ranges[-1][1] >= local_start:
                        ranges[-1] = (ranges[-1][0], max(ranges[-1][1], local_end))
                    else:
                        ranges.append((local_start, local_end))
                    start = device_end

            if self._executor is not None:
                futures = [
                    self._executor.submit(
                        self._send_bus_partial,
                        device_ids,
                        device_frames,
                        device_ranges,
                    )
                    for device_ids in self._devices_by_bus.values()
                ]
                for future in futures:
                    future.result()
            else:
                for device_ids in self._devices_by_bus.values():
                    self._send_bus_partial(device_ids, device_frames, device_ranges)
            self._logical_frames_sent += 1
    
    def set_pixel(self, pixel: int, r: int, g: int, b: int):
        """Set a single pixel color"""
        with self._controller_lock():
            if pixel >= self.total_leds:
                return

            strip = pixel // self.leds_per_strip
            led_in_strip = pixel % self.leds_per_strip
            device_id = strip // self.strips_per_device
            local_strip = strip % self.strips_per_device
            local_pixel = local_strip * self.leds_per_strip + led_in_strip
            if device_id < self.num_devices:
                self.devices[device_id].set_pixel(local_pixel, r, g, b)
    
    def set_brightness(self, brightness: int):
        """Set global brightness on all devices"""
        with self._controller_lock():
            self.current_brightness = brightness
            for device in self.devices:
                device.set_brightness(brightness)
    
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

    @staticmethod
    def _local_overlay_patches(
        pixels: np.ndarray,
        *,
        local_start: int,
        local_end: int,
        update_kind: int,
        dirty_ranges,
    ):
        local_pixels = pixels[local_start:local_end]
        if update_kind == OVERLAY_UPDATE_FULL_SNAPSHOT:
            return [
                (start, local_pixels[start:start + MAX_RGBA_PIXELS_PER_BATCH_SPAN])
                for start in range(
                    0, len(local_pixels), MAX_RGBA_PIXELS_PER_BATCH_SPAN
                )
            ]

        ranges = []
        for start, end in dirty_ranges or ():
            clipped_start = max(local_start, start)
            clipped_end = min(local_end, end)
            if clipped_start >= clipped_end:
                continue
            local_first = clipped_start - local_start
            local_last = clipped_end - local_start
            if ranges and local_first <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], local_last))
            else:
                ranges.append((local_first, local_last))
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
            local_start = index * self.leds_per_device
            per_device_patches.append(self._local_overlay_patches(
                overlay,
                local_start=local_start,
                local_end=local_start + self.leds_per_device,
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
        # Validate every field through the single-device serializer before any
        # receiver is touched. Each board differs only by its global offset.
        for index in range(self.num_devices):
            LEDController.serialize_local_background_start(
                component_id=component_id,
                preferred_cadence_hz=preferred_cadence_hz,
                global_strip_offset=index * self.strips_per_device,
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
                        global_strip_offset=index * self.strips_per_device,
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
                        "receiver_global_strip_offset": index * self.strips_per_device,
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
        for index in range(self.num_devices):
            LEDController.serialize_local_background_params(
                preferred_cadence_hz=preferred_cadence_hz,
                global_strip_offset=index * self.strips_per_device,
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
                        global_strip_offset=index * self.strips_per_device,
                        common_seed=common_seed,
                    )
                    self._require_ack(status, "local-background parameters", index)
                    updated.append(index)
                active = self._receiver_statuses(require_capability=True)
                for index, status in enumerate(active):
                    expected = {
                        "receiver_base_mode": 1,
                        "receiver_declared_cadence_hz": preferred_cadence_hz,
                        "receiver_global_strip_offset": index * self.strips_per_device,
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
                            global_strip_offset=index * self.strips_per_device,
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
                                != index * self.strips_per_device
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
        device_stats = []
        total_leds = 0
        max_frames_sent = 0
        spi_transfers = 0
        bytes_sent = 0
        crc_bytes_sent = 0
        errors = 0
        receiver_status_devices = 0
        receiver_crc_errors = 0
        receiver_packets = 0
        receiver_crc_ok_packets = 0
        receiver_frames_rendered = 0
        receiver_frames_accepted = 0
        receiver_frames_displayed = 0
        receiver_frames_superseded = 0
        receiver_publish_drops = 0
        receiver_spi_queue_errors = 0
        receiver_display_errors = 0
        receiver_status_misses = 0
        receiver_last_encode_us = 0
        receiver_last_show_us = 0
        receiver_capabilities_all = None
        receiver_base_modes = []
        receiver_context_digests = []
        receiver_declared_cadence_hz = []
        receiver_local_missed_deadlines = 0
        receiver_max_local_render_us = 0
        receiver_transition_reasons = []
        last_frame_ms = 0.0
        weighted_avg_total = 0.0
        weighted_avg_frames = 0

        for device in self.devices:
            stats = {}
            if hasattr(device, "get_stats"):
                stats = device.get_stats()
            device_stats.append(stats)

            total_leds += int(stats.get('total_leds', 0) or 0)
            frames = int(stats.get('frames_sent', 0) or 0)
            # Use max (not sum) — all devices receive the same logical frame
            max_frames_sent = max(max_frames_sent, frames)
            spi_transfers += int(stats.get('spi_transfers', 0) or 0)
            bytes_sent += int(stats.get('bytes_sent', 0) or 0)
            crc_bytes_sent += int(stats.get('crc_bytes_sent', 0) or 0)
            errors += int(stats.get('errors', 0) or 0)
            if stats.get('receiver_status_seen'):
                receiver_status_devices += 1
            receiver_crc_errors += int(stats.get('receiver_crc_errors', 0) or 0)
            receiver_packets += int(stats.get('receiver_packets', 0) or 0)
            receiver_crc_ok_packets += int(stats.get('receiver_crc_ok_packets', 0) or 0)
            receiver_frames_rendered += int(stats.get('receiver_frames_rendered', 0) or 0)
            receiver_frames_accepted += int(stats.get('receiver_frames_accepted', 0) or 0)
            receiver_frames_displayed += int(stats.get('receiver_frames_displayed', 0) or 0)
            receiver_frames_superseded += int(stats.get('receiver_frames_superseded', 0) or 0)
            receiver_publish_drops += int(stats.get('receiver_publish_drops', 0) or 0)
            receiver_spi_queue_errors += int(stats.get('receiver_spi_queue_errors', 0) or 0)
            receiver_display_errors += int(stats.get('receiver_display_errors', 0) or 0)
            receiver_status_misses += int(stats.get('receiver_status_misses', 0) or 0)
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
                'total_leds': total_leds,
                'frames_sent': max_frames_sent,
                'logical_frames_sent': self._logical_frames_sent,
                'spi_bus_count': len(self._devices_by_bus),
                'device_map': [
                    {
                        'logical_device': logical_device,
                        'bus': bus,
                        'chip_select': chip_select,
                    }
                    for logical_device, (bus, chip_select) in enumerate(self.device_map)
                ],
                'spi_transfers': spi_transfers,
                'bytes_sent': bytes_sent,
                'crc_bytes_sent': crc_bytes_sent,
                'errors': errors,
                'receiver_status_devices': receiver_status_devices,
                'receiver_crc_errors': receiver_crc_errors,
                'receiver_packets': receiver_packets,
                'receiver_crc_ok_packets': receiver_crc_ok_packets,
                'receiver_frames_rendered': receiver_frames_rendered,
                'receiver_frames_accepted': receiver_frames_accepted,
                'receiver_frames_displayed': receiver_frames_displayed,
                'receiver_frames_superseded': receiver_frames_superseded,
                'receiver_publish_drops': receiver_publish_drops,
                'receiver_spi_queue_errors': receiver_spi_queue_errors,
                'receiver_display_errors': receiver_display_errors,
                'receiver_status_misses': receiver_status_misses,
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
                'last_frame_duration_ms': last_frame_ms,
                'avg_frame_duration_ms': avg_frame_ms,
                'spi_speed_hz': device_stats[0].get('spi_speed_hz') if device_stats else None,
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

        map_entries: List[DeviceMapEntry] = []
        
        # For 1-2 devices, just use the primary bus
        if num_devices <= 2:
            for device_id in range(num_devices):
                map_entries.append((primary_bus, device_id))
            return map_entries
        
        # For 3+ devices, check if CE2+ exist on primary bus
        # If not, fall back to SPI1 for devices 3-4
        if not self._device_exists(primary_bus, 2) and self._device_exists(1, 0):
            # Wall left-to-right: SPI0 CE0, SPI0 CE1, SPI1 CE1, SPI1 CE0
            # (SPI1 chip-selects are swapped so logical groups 3 and 4 match
            # physical board order on the wall.)
            spi1_ces = [1, 0]  # CE1 then CE0
            for idx in range(num_devices):
                if idx < 2:
                    map_entries.append((primary_bus, idx))
                else:
                    map_entries.append((1, spi1_ces[idx - 2]))

            if self.debug:
                print("[INFO] Using SPI1 fallback for devices 2 and 3 (CE1, CE0)")
        else:
            # All devices on primary bus
            for device_id in range(num_devices):
                map_entries.append((primary_bus, device_id))
        
        return map_entries
    
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
