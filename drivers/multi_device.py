#!/usr/bin/env python3
"""
Multi-Device LED Grid Controller - SPI version
Controls multiple ESP32 devices via SPI with different CS pins
"""

import os
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple, Optional

import numpy as np

from drivers.spi_controller import (
    LEDController, SPI_BUS, SPI_SPEED, SPI_MODE,
    CAPABILITY_NATIVE, CAPABILITY_FRAME_TRACK, CAPABILITY_SIGNED_PACKAGES,
    CAPABILITY_ASSET_UPLOAD, CAPABILITY_TYPED_PARAMETERS,
    CAPABILITY_LOGICAL_DEVICE_IDENTITY,
)
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP

DeviceMapEntry = Tuple[int, int]


class MultiDeviceLEDController:
    """Multi-device LED controller that manages multiple ESP32 devices"""
    
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
            num_devices: Number of ESP32 devices (default: 1 for ESP32-S3 DevKitC)
            bus: SPI bus number (default: 0)
            speed: SPI speed in Hz (default: 8MHz)
            mode: SPI mode (default: 3)
            strips_per_device: LED strips per device (default: 8 for ESP32-S3 DevKitC)
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
        self._firmware_active = False
        self._active_payload_digests: List[str] = []
        self._firmware_parameters: Dict[str, Any] = {}
        self._firmware_runtime_state = 'stopped'
        self._firmware_runtime_status: Dict[str, Any] = {
            'state': 'stopped', 'operation': 'initialize', 'error': None,
        }
        self._firmware_install_status: Dict[str, Any] = {
            'state': 'idle', 'progress': 0.0, 'error': None,
        }
        
        # Animation manager output contract.
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
            )
            self.devices.append(device)
        
        if self.debug:
            print(f"\n✓ All {num_devices} devices initialized\n")

    def _operation_lock(self):
        return self._transport_lock
    
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
        except Exception as e:
            if self.debug:
                print(f"✗ Error sending to device {device_id}: {e}")

    def _send_bus_frames(self, device_ids, device_frames):
        """Serialize chip selects on one bus while independent buses overlap."""
        for device_id in device_ids:
            self._send_to_device(device_id, device_frames[device_id])

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
        with self._operation_lock():
            self._stop_local_for_host_frame()
            # Split frame into per-device chunks
            device_frames = self._split_frame(colors)
        
            if self._executor is not None:
                futures = [
                    self._executor.submit(self._send_bus_frames, device_ids, device_frames)
                    for device_ids in self._devices_by_bus.values()
                ]
                for future in futures:
                    future.result()
            else:
                for device_id, device_colors in enumerate(device_frames):
                    self._send_to_device(device_id, device_colors)
            self._logical_frames_sent += 1

    def set_frame(self, colors, dirty_ranges=None):
        """Present a frame, using partial board updates when ranges are known."""
        if not dirty_ranges:
            self.set_all_pixels(colors)
            return

        with self._operation_lock():
            self._stop_local_for_host_frame()
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

    def _stop_local_for_host_frame(self):
        """A complete host presentation explicitly takes ownership back."""
        if not self._firmware_active:
            return
        if not self.stop_firmware_animation():
            raise RuntimeError("could not verify that every receiver stopped local playback")

    @staticmethod
    def _payload_for_device(asset: Dict[str, Any], logical_device: int) -> Dict[str, Any]:
        payloads = asset.get('payloads')
        if not isinstance(payloads, list) or logical_device >= len(payloads):
            raise ValueError(f"missing payload for logical device {logical_device}")
        payload = payloads[logical_device]
        if (not isinstance(payload, dict)
                or payload.get('logical_device') != logical_device):
            raise ValueError(f"payload {logical_device} is not in canonical order")
        data = payload.get('data')
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ValueError(f"payload {logical_device} data must be bytes")
        digest = payload.get('digest')
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"payload {logical_device} digest is invalid")
        data = bytes(data)
        if hashlib.sha256(data).hexdigest() != digest.lower():
            raise ValueError(f"payload {logical_device} digest does not match data")
        envelope = payload.get('envelope')
        required = ('package_digest', 'key_id', 'kind', 'device_index', 'payload_size',
                    'payload_digest', 'signed_index', 'signature')
        if envelope is None or any(not hasattr(envelope, name) for name in required):
            raise ValueError(f"payload {logical_device} has no signed verification envelope")
        if (
            int(envelope.device_index) != logical_device
            or int(envelope.payload_size) != len(data)
            or bytes(envelope.payload_digest).hex() != digest.lower()
            or len(bytes(envelope.signed_index)) != 176
            or len(bytes(envelope.signature)) != 64
            or len(envelope.key_id.encode('ascii')) != 20
        ):
            raise ValueError(f"payload {logical_device} verification envelope is invalid")
        return {**payload, 'data': data, 'digest': digest.lower(), 'envelope': envelope}

    def _validated_asset_payloads(self, asset: Dict[str, Any]) -> List[Dict[str, Any]]:
        if (not isinstance(asset.get('payloads'), list)
                or len(asset['payloads']) != self.num_devices):
            raise ValueError(f"asset must contain exactly {self.num_devices} payloads")
        payloads = [self._payload_for_device(asset, index) for index in range(self.num_devices)]
        first = payloads[0]['envelope']
        package_digest = asset.get('package_digest')
        kind = asset.get('kind')
        if kind not in ('native', 'frames'):
            raise ValueError("asset kind must be native or frames")
        if (
            not isinstance(package_digest, str)
            or any(payload['envelope'].package_digest != package_digest for payload in payloads)
            or any(payload['envelope'].key_id != first.key_id for payload in payloads)
            or any(bytes(payload['envelope'].signed_index) != bytes(first.signed_index) for payload in payloads)
            or any(bytes(payload['envelope'].signature) != bytes(first.signature) for payload in payloads)
            or any(payload['envelope'].kind != kind for payload in payloads)
        ):
            raise ValueError("receiver verification envelopes do not describe one signed package")
        return payloads

    @staticmethod
    def _probe_present(result: Any) -> bool:
        if not isinstance(result, dict):
            raise RuntimeError("receiver probe returned no status")
        result_code = int(result.get('receiver_last_result') or 0)
        if result_code == 1:
            return True
        if result_code == 15:
            return False
        raise RuntimeError(f"receiver probe failed with result {result_code}")

    @staticmethod
    def _retry(operation, retries: int = 3):
        last_error = None
        for _attempt in range(max(1, retries)):
            try:
                return operation()
            except Exception as exc:
                last_error = exc
        raise last_error

    @staticmethod
    def _require_ok(result: Any, operation: str):
        if not isinstance(result, dict) or 'receiver_last_result' not in result:
            raise RuntimeError(f"receiver {operation} returned no status")
        code = int(result.get('receiver_last_result') or 0)
        if code != 1:
            raise RuntimeError(f"receiver {operation} failed with result {code}")
        return result

    @classmethod
    def _require_abort_complete(cls, result: Any):
        status = cls._require_ok(result, 'asset abort')
        if ('receiver_upload_state' not in status
                or 'receiver_display_mode' not in status):
            raise RuntimeError("receiver asset abort returned incomplete status")
        upload_state = int(status['receiver_upload_state'])
        display_mode = int(status['receiver_display_mode'])
        if upload_state != 0 or display_mode == 3:
            raise RuntimeError(
                "receiver asset abort did not leave upload idle and maintenance mode"
            )
        return status

    def _capability_report(
        self, asset: Dict[str, Any], *, require_parameters: bool = False
    ) -> Dict[str, Any]:
        kind = asset.get('kind')
        if kind not in ('native', 'frames'):
            raise ValueError("asset kind must be native or frames")
        kind_capability = CAPABILITY_FRAME_TRACK if kind == 'frames' else CAPABILITY_NATIVE
        required = CAPABILITY_SIGNED_PACKAGES | CAPABILITY_ASSET_UPLOAD | kind_capability
        if require_parameters:
            required |= CAPABILITY_TYPED_PARAMETERS
        devices = []
        supported = True
        # Status reads use the same SPI transport as frames and commands. Keep
        # the report atomic even when a future caller does not already hold the
        # operation lock.
        with self._operation_lock():
            for index, device in enumerate(self.devices):
                try:
                    status = device.query_receiver_status()
                    if not isinstance(status, dict):
                        raise TypeError("receiver status is not an object")
                    version = int(status.get('receiver_status_version', 0) or 0)
                    capabilities = int(status.get('receiver_capabilities', 0) or 0)
                    receiver_logical_device = status.get('receiver_logical_device')
                except Exception as exc:
                    status = {'error': str(exc)}
                    version = 0
                    capabilities = 0
                    receiver_logical_device = None
                missing = required & ~capabilities
                identity_valid = bool(
                    capabilities & CAPABILITY_LOGICAL_DEVICE_IDENTITY
                    and receiver_logical_device == index
                )
                device_supported = version >= 3 and missing == 0 and identity_valid
                supported = supported and device_supported
                devices.append({
                    'logical_device': index,
                    'status_version': version,
                    'capabilities': capabilities,
                    'required_capabilities': required,
                    'missing_capabilities': missing,
                    'receiver_logical_device': receiver_logical_device,
                    'identity_valid': identity_valid,
                    'supported': device_supported,
                    **({'error': status['error']} if 'error' in status else {}),
                })
        return {'supported': supported, 'required_capabilities': required, 'devices': devices}

    def _record_runtime_status(
        self, state: str, operation: str, *, error: Optional[str] = None,
        devices: Optional[List[Dict[str, Any]]] = None,
        command_errors: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        status: Dict[str, Any] = {
            'state': state, 'operation': operation, 'error': error,
        }
        if devices is not None:
            status['devices'] = devices
        if command_errors:
            status['command_errors'] = command_errors
        self._firmware_runtime_state = state
        self._firmware_runtime_status = status
        self._firmware_install_status['runtime'] = dict(status)

    def _reconcile_stopped_receivers(
        self, operation: str, expected_digests: List[str],
        command_errors: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Clear local playback state only after all receivers prove stopped."""
        receiver_states = []
        unanimous = True
        for index, device in enumerate(self.devices):
            try:
                status = device.query_receiver_status()
                if not isinstance(status, dict):
                    raise TypeError("receiver status is not an object")
                mode = int(status.get('receiver_display_mode', -1))
                digest = status.get('receiver_active_digest')
                stopped = mode != 2 and digest is None
                receiver_states.append({
                    'logical_device': index,
                    'display_mode': mode,
                    'active_digest': digest,
                    'expected_digest': (
                        expected_digests[index]
                        if index < len(expected_digests) else None
                    ),
                    'stopped': stopped,
                })
                unanimous = unanimous and stopped
            except Exception as exc:
                receiver_states.append({
                    'logical_device': index, 'stopped': False,
                    'error': str(exc),
                })
                unanimous = False

        if unanimous:
            self._firmware_active = False
            self._active_payload_digests = []
            self._firmware_parameters = {}
            self._record_runtime_status(
                'stopped', operation, devices=receiver_states,
                command_errors=command_errors,
            )
            return True

        # Conservatively retain the requested identity. A subsequent host frame
        # will retry the stop instead of assuming ownership from an incomplete
        # or contradictory status snapshot.
        self._firmware_active = True
        if expected_digests:
            self._active_payload_digests = list(expected_digests)
        self._record_runtime_status(
            'degraded', operation,
            error='could not prove that every receiver stopped local playback',
            devices=receiver_states, command_errors=command_errors,
        )
        return False

    def _require_capabilities(
        self, asset: Dict[str, Any], *, require_parameters: bool = False
    ) -> Dict[str, Any]:
        report = self._capability_report(asset, require_parameters=require_parameters)
        if not report['supported']:
            self._firmware_install_status = {
                'state': 'unsupported', 'progress': 0.0,
                'error': 'one or more receivers lack required firmware-animation capabilities',
                'capability_report': report,
            }
            raise RuntimeError(self._firmware_install_status['error'])
        return report

    def install_firmware_asset(self, asset: Dict[str, Any], retries: int = 3) -> Dict[str, Any]:
        """Install every missing device payload while freezing the last frame."""
        if not isinstance(asset, dict):
            raise ValueError("asset descriptor must be an object")
        payloads = self._validated_asset_payloads(asset)
        with self._operation_lock():
            capability_report = self._require_capabilities(asset)
            prior_firmware = self._firmware_active
            self._firmware_install_status = {
                'state': 'probing', 'progress': 0.0, 'error': None,
                'capability_report': capability_report,
            }
            installed = []
            skipped = []
            rollback_candidates = []
            begun_candidates = []
            try:
                missing = []
                for index, (device, payload) in enumerate(zip(self.devices, payloads)):
                    probe = self._retry(lambda d=device, p=payload: d.asset_probe(p['digest']), retries)
                    if self._probe_present(probe):
                        skipped.append(index)
                    else:
                        missing.append(index)

                total_bytes = sum(len(payloads[index]['data']) for index in missing)
                sent_bytes = 0
                self._firmware_install_status['state'] = 'uploading'
                for index in missing:
                    device = self.devices[index]
                    payload = payloads[index]
                    data = payload['data']
                    # The begin acknowledgement can be lost after the receiver
                    # enters maintenance, so treat it as possibly begun before
                    # issuing the command.
                    begun_candidates.append(index)
                    self._require_ok(
                        device.asset_begin(payload['envelope']), 'asset begin'
                    )
                    offset = 0
                    max_chunk = int(device.MAX_ASSET_CHUNK_BYTES)
                    max_chunk = min(4089, max(1, max_chunk))
                    while offset < len(data):
                        chunk = data[offset:offset + max_chunk]
                        self._retry(
                            lambda d=device, o=offset, c=chunk: self._require_ok(
                                d.asset_chunk(o, c), 'asset chunk'
                            ), retries
                        )
                        offset += len(chunk)
                        sent_bytes += len(chunk)
                        self._firmware_install_status['progress'] = (
                            sent_bytes / total_bytes if total_bytes else 1.0
                        )
                    rollback_candidates.append(index)
                    self._require_ok(device.asset_commit(payload['digest']), 'asset commit')
                    installed.append(index)

                self._firmware_install_status = {
                    'state': 'ready', 'progress': 1.0, 'error': None,
                    'installed_devices': installed, 'skipped_devices': skipped,
                    'capability_report': capability_report,
                }
                return dict(self._firmware_install_status)
            except Exception as exc:
                aborted = []
                abort_failed = []
                for index in reversed(begun_candidates):
                    try:
                        self._require_abort_complete(
                            self.devices[index].asset_abort()
                        )
                        aborted.append(index)
                    except Exception as abort_exc:
                        abort_failed.append({
                            'logical_device': index, 'error': str(abort_exc),
                        })
                removed = []
                rollback_failed = []
                for index in reversed(rollback_candidates):
                    try:
                        self._require_ok(
                            self.devices[index].asset_remove(payloads[index]['digest']),
                            'rollback remove',
                        )
                        removed.append(index)
                    except Exception as rollback_exc:
                        rollback_failed.append({
                            'logical_device': index, 'error': str(rollback_exc),
                        })
                remaining = []
                verification_failed = []
                # A successful remove acknowledgement is not enough to claim
                # transactional rollback. Probe every cache entry that might
                # have been published, including the receiver whose commit
                # acknowledgement may have been lost.
                for index in reversed(rollback_candidates):
                    try:
                        probe = self._retry(
                            lambda i=index: self.devices[i].asset_probe(
                                payloads[i]['digest']
                            ),
                            retries,
                        )
                        if self._probe_present(probe):
                            remaining.append(index)
                    except Exception as verify_exc:
                        verification_failed.append({
                            'logical_device': index, 'error': str(verify_exc),
                        })
                self._firmware_install_status.update(
                    state='retry', error=str(exc),
                    rollback={
                        'abort_attempted_devices': list(reversed(begun_candidates)),
                        'aborted_devices': aborted,
                        'abort_failed_devices': abort_failed,
                        'upload_abort_complete': not abort_failed,
                        'attempted_devices': list(reversed(rollback_candidates)),
                        'committed_devices': list(installed),
                        'removed_devices': removed,
                        'failed_devices': rollback_failed,
                        'remaining_devices': remaining,
                        'verification_failed_devices': verification_failed,
                        'verified_absent': not remaining and not verification_failed,
                        'partial_cache_publication': bool(
                            remaining or verification_failed
                        ),
                    },
                )
                raise
            finally:
                # Maintenance commands can displace receiver-local playback.
                # Restore it after install-only success or failure; streamed host
                # playback resumes naturally when this lock is released.
                if prior_firmware:
                    for device in self.devices:
                        try:
                            self._require_ok(
                                device.restart_firmware_animation(),
                                'animation restart',
                            )
                        except Exception:
                            pass

    def start_firmware_animation(self, asset: Dict[str, Any], parameters=None) -> bool:
        """Verify all caches first, then start devices in deterministic order."""
        payloads = self._validated_asset_payloads(asset)
        with self._operation_lock():
            try:
                self._require_capabilities(
                    asset, require_parameters=bool(parameters)
                )
            except RuntimeError:
                return False
            for device, payload in zip(self.devices, payloads):
                if not self._probe_present(device.asset_probe(payload['digest'])):
                    return False
            try:
                for index, (device, payload) in enumerate(zip(self.devices, payloads)):
                    self._require_ok(device.start_firmware_animation(
                        payload['digest'], index * self.strips_per_device, parameters or {}
                    ), 'animation start')
            except Exception as exc:
                command_errors = []
                for index, device in enumerate(self.devices):
                    try:
                        self._require_ok(
                            device.stop_firmware_animation(), 'animation stop'
                        )
                    except Exception as stop_exc:
                        command_errors.append({
                            'logical_device': index, 'error': str(stop_exc),
                        })
                self._active_payload_digests = [
                    payload['digest'] for payload in payloads
                ]
                self._firmware_parameters = dict(parameters or {})
                self._reconcile_stopped_receivers(
                    'start_rollback', self._active_payload_digests,
                    command_errors=command_errors,
                )
                self._firmware_runtime_status['start_error'] = str(exc)
                self._firmware_install_status['runtime'] = dict(
                    self._firmware_runtime_status
                )
                return False
            self._active_payload_digests = [payload['digest'] for payload in payloads]
            self._firmware_parameters = dict(parameters or {})
            self._firmware_active = True
            self._record_runtime_status('active', 'start')
            return True

    def adopt_firmware_animation(
        self, asset: Dict[str, Any], parameters: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Adopt retained playback only when every receiver reports the exact asset."""
        payloads = self._validated_asset_payloads(asset)
        with self._operation_lock():
            try:
                self._require_capabilities(asset, require_parameters=bool(parameters))
            except RuntimeError:
                return False
            for device, payload in zip(self.devices, payloads):
                try:
                    status = device.query_receiver_status()
                except Exception:
                    return False
                if (
                    not isinstance(status, dict)
                    or int(status.get('receiver_display_mode', -1)) != 2
                    or status.get('receiver_active_digest') != payload['digest']
                    or int(status.get('receiver_quarantine_state', 0) or 0) != 0
                ):
                    return False
            self._active_payload_digests = [payload['digest'] for payload in payloads]
            self._firmware_parameters = dict(parameters or {})
            self._firmware_active = True
            self._record_runtime_status('active', 'adopt')
            return True

    def stop_firmware_animation(self) -> bool:
        with self._operation_lock():
            expected_digests = list(self._active_payload_digests)
            command_errors = []
            for index, device in enumerate(self.devices):
                try:
                    self._require_ok(
                        device.stop_firmware_animation(), 'animation stop'
                    )
                except Exception as exc:
                    command_errors.append({
                        'logical_device': index, 'error': str(exc),
                    })
            return self._reconcile_stopped_receivers(
                'stop', expected_digests, command_errors=command_errors,
            )

    def restart_firmware_animation(self) -> bool:
        with self._operation_lock():
            for device in self.devices:
                self._require_ok(
                    device.restart_firmware_animation(), 'animation restart'
                )
            self._firmware_active = True
            self._record_runtime_status('active', 'restart')
            return True

    def update_firmware_parameters(self, parameters: Dict[str, Any]) -> bool:
        with self._operation_lock():
            if not self._firmware_active:
                return False
            previous = dict(self._firmware_parameters)
            updated = []
            try:
                for device in self.devices:
                    self._require_ok(
                        device.update_firmware_parameters(parameters),
                        'parameter update',
                    )
                    updated.append(device)
            except Exception:
                rollback_errors = []
                for index in reversed(range(len(updated))):
                    device = updated[index]
                    try:
                        self._require_ok(
                            device.update_firmware_parameters(previous),
                            'parameter rollback',
                        )
                    except Exception as rollback_exc:
                        rollback_errors.append({
                            'logical_device': index,
                            'error': str(rollback_exc),
                        })
                self._firmware_parameters = previous
                if rollback_errors:
                    self._record_runtime_status(
                        'degraded', 'parameter_rollback',
                        error='one or more receivers rejected parameter rollback',
                        command_errors=rollback_errors,
                    )
                else:
                    self._record_runtime_status(
                        'active', 'parameter_rollback',
                        error='parameter update failed and was rolled back',
                    )
                return False
            self._firmware_parameters = dict(parameters)
            self._record_runtime_status('active', 'parameter_update')
            return True

    def remove_firmware_asset(self, asset: Dict[str, Any]) -> bool:
        payloads = self._validated_asset_payloads(asset)
        digests = [payload['digest'] for payload in payloads]
        if (self._firmware_active
                and any(d in self._active_payload_digests for d in digests)):
            raise ValueError("cannot delete the active firmware animation")
        with self._operation_lock():
            for device, digest in zip(self.devices, digests):
                self._require_ok(device.asset_remove(digest), 'asset remove')
        return True
    
    def set_pixel(self, pixel: int, r: int, g: int, b: int):
        """Set a single pixel color"""
        if pixel >= self.total_leds:
            return
        
        # Determine which device and local pixel index
        strip = pixel // self.leds_per_strip
        led_in_strip = pixel % self.leds_per_strip
        
        device_id = strip // self.strips_per_device
        local_strip = strip % self.strips_per_device
        local_pixel = local_strip * self.leds_per_strip + led_in_strip
        
        if device_id < self.num_devices:
            self.devices[device_id].set_pixel(local_pixel, r, g, b)
    
    def set_brightness(self, brightness: int):
        """Set global brightness on all devices"""
        self.current_brightness = brightness
        for device in self.devices:
            device.set_brightness(brightness)
    
    def show(self):
        """Update LED display on all devices"""
        if not self.inline_show:
            for device in self.devices:
                device.show()
    
    def clear(self):
        """Clear all LEDs on all devices"""
        for device in self.devices:
            device.clear()
    
    def configure(self):
        """Configure all devices"""
        for device_id, device in enumerate(self.devices):
            try:
                device.configure()
                if self.debug:
                    print(f"✓ Device {device_id} configured")
            except Exception as e:
                if self.debug:
                    print(f"✗ Device {device_id} configuration failed: {e}")
    
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
                'last_frame_duration_ms': last_frame_ms,
                'avg_frame_duration_ms': avg_frame_ms,
                'spi_speed_hz': device_stats[0].get('spi_speed_hz') if device_stats else None,
                'spi_mode': device_stats[0].get('spi_mode') if device_stats else None,
                'firmware_install': dict(self._firmware_install_status),
                'firmware_active': self._firmware_active,
                'firmware_runtime_state': self._firmware_runtime_state,
                'firmware_runtime': dict(self._firmware_runtime_status),
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
