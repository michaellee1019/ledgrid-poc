#!/usr/bin/env python3
"""
Multi-Device LED Grid Controller - SPI version
Controls multiple ESP32 devices via SPI with different CS pins
"""

import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Optional

import numpy as np

from drivers.spi_controller import LEDController, SPI_BUS, SPI_SPEED, SPI_MODE

DeviceMapEntry = Tuple[int, int]


class MultiDeviceLEDController:
    """Multi-device LED controller that manages multiple ESP32 devices"""
    
    def __init__(self, 
                 num_devices: int = 1,
                 bus: int = SPI_BUS,
                 speed: int = SPI_SPEED,
                 mode: int = SPI_MODE,
                 strips_per_device: int = 8,
                 leds_per_strip: int = 140,
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
            leds_per_strip: LEDs per strip (default: 140)
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
        
        # For compatibility with animation system
        self.inline_show = True
        self.current_brightness = None
        
        if self.debug:
            print(f"Multi-Device LED Controller")
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
            # Send to devices sequentially
            for device_id, device_colors in enumerate(device_frames):
                self._send_to_device(device_id, device_colors)
        self._logical_frames_sent += 1

    def set_frame(self, colors, dirty_ranges=None):
        """Present a frame, using partial board updates when ranges are known."""
        if not dirty_ranges:
            self.set_all_pixels(colors)
            return

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
        # DISABLED: Physical reordering causes issues when not all devices are connected
        # Users with 4 boards should use explicit device_map parameter if they need custom ordering
        # Old logic: Physical left-to-right board order for 4-board wall installs:
        # SPI1-CS0, SPI1-CS1, SPI0-CS1, SPI0-CS0
        # if (num_devices == 4 and all devices exist):
        #     reordered = [(1, 0), (1, 1), (primary_bus, 1), (primary_bus, 0)]

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
                print(f"[INFO] Using SPI1 fallback for devices 2 and 3 (CE1, CE0)")
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
