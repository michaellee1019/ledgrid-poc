"""Host RGB routing for the installed five-receiver wall."""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple, Optional
import numpy as np
from drivers.spi_controller import LEDController, SPI_BUS, SPI_MODE, SPI_SPEED
from drivers.led_layout import (DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP,
    STRIPS_PER_DEVICE, DeviceMapEntry, logical_strip_count, wall_device_map,
    WALL_RECEIVER_STRIP_COUNTS, WALL_RECEIVER_GLOBAL_STRIP_OFFSETS,
    WALL_RECEIVER_SPI_SPEEDS_HZ, WALL_PHYSICAL_OUTPUT_LANE_MASKS,
    WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER)

class MultiDeviceLEDController:
    """Serialize commands and overlap independent SPI buses during full frames."""
    def __init__(self, num_devices=5, bus=SPI_BUS, speed=SPI_SPEED, mode=SPI_MODE,
                 strips_per_device=STRIPS_PER_DEVICE, strip_count=None,
                 leds_per_strip=DEFAULT_LEDS_PER_STRIP, debug=False, parallel=True,
                 device_map=None, reverse_host_strips_by_logical_receiver=None,
                 receiver_lane_masks=None, receiver_strip_counts=None,
                 receiver_global_strip_offsets=None, receiver_spi_speeds_hz=None,
                 fec_receiver_ids=(3,), **_removed_options):
        if type(num_devices) is not int or not 1 <= num_devices <= 5:
            raise ValueError('wall supports one through five receivers')
        self.num_devices = num_devices
        self.debug = debug
        self.parallel = parallel
        self.strips_per_device = strips_per_device
        self.leds_per_strip = leds_per_strip
        installed = num_devices == 5 and strip_count in (None, DEFAULT_STRIP_COUNT)
        if receiver_strip_counts is None:
            if installed:
                receiver_strip_counts = WALL_RECEIVER_STRIP_COUNTS
            else:
                width = logical_strip_count(num_devices, strips_per_device, strip_count)
                receiver_strip_counts = tuple(min(strips_per_device, width-i*strips_per_device)
                                              for i in range(num_devices))
        self.receiver_strip_counts = tuple(receiver_strip_counts)
        if len(self.receiver_strip_counts) != num_devices or any(type(x) is not int or not 1 <= x <= 8 for x in self.receiver_strip_counts):
            raise ValueError('one receiver width from one through eight is required')
        if receiver_global_strip_offsets is None:
            receiver_global_strip_offsets = tuple(sum(self.receiver_strip_counts[:i]) for i in range(num_devices))
        self.receiver_global_strip_offsets = tuple(receiver_global_strip_offsets)
        if len(self.receiver_global_strip_offsets) != num_devices or any(type(x) is not int or x < 0 for x in self.receiver_global_strip_offsets):
            raise ValueError('one nonnegative strip origin per receiver is required')
        occupied = [x for start, count in zip(self.receiver_global_strip_offsets, self.receiver_strip_counts) for x in range(start,start+count)]
        if sorted(occupied) != list(range(len(occupied))):
            raise ValueError('receiver strips must partition one contiguous wall')
        self.strip_count = len(occupied)
        if strip_count is not None and strip_count != self.strip_count:
            raise ValueError('strip_count disagrees with physical routing')
        if type(leds_per_strip) is not int or leds_per_strip < 1:
            raise ValueError('leds_per_strip must be positive')
        self.total_leds = self.strip_count * leds_per_strip
        self.receiver_pixel_counts = tuple(x*leds_per_strip for x in self.receiver_strip_counts)
        self.receiver_pixel_offsets = tuple(x*leds_per_strip for x in self.receiver_global_strip_offsets)
        self.reverse_host_strips_by_logical_receiver = tuple(reverse_host_strips_by_logical_receiver or (False,)*num_devices)
        if len(self.reverse_host_strips_by_logical_receiver) != num_devices or any(type(x) is not bool for x in self.reverse_host_strips_by_logical_receiver):
            raise ValueError('one host strip direction per receiver is required')
        self.receiver_lane_masks = tuple(receiver_lane_masks or (WALL_PHYSICAL_OUTPUT_LANE_MASKS if installed else tuple((1<<x)-1 for x in self.receiver_strip_counts)))
        if len(self.receiver_lane_masks) != num_devices or any(type(x) is not int or not 1<=x<=255 for x in self.receiver_lane_masks):
            raise ValueError('one output lane mask per receiver is required')
        if any(mask.bit_count() < count for mask,count in zip(self.receiver_lane_masks,self.receiver_strip_counts)):
            raise ValueError('output mask hides assigned receiver lanes')
        self.device_map = list(device_map) if device_map is not None else self._build_device_map(num_devices,bus)
        if len(self.device_map) != num_devices or len(set(self.device_map)) != num_devices:
            raise ValueError('exact unique SPI routes are required')
        self._devices_by_bus = {}
        for i, (device_bus, chip_select) in enumerate(self.device_map):
            self._devices_by_bus.setdefault(device_bus, []).append(i)
        self._executor = ThreadPoolExecutor(max_workers=len(self._devices_by_bus),thread_name_prefix='led-spi-bus') if parallel and len(self._devices_by_bus)>1 else None
        self._transport_lock = threading.RLock()
        self._logical_frames_sent = 0
        self._logical_wall_frame_sequence = 0
        self._last_full_frame_failure = None
        self._host_full_receiver3_frames_skipped = 0
        self.inline_show = True
        self.current_brightness = None
        self.fec_receiver_ids = tuple(fec_receiver_ids)
        if len(set(self.fec_receiver_ids)) != len(self.fec_receiver_ids) or any(type(x) is not int or not 0 <= x < num_devices for x in self.fec_receiver_ids):
            raise ValueError('FEC receivers must be unique configured logical IDs')
        speeds = tuple(receiver_spi_speeds_hz or (WALL_RECEIVER_SPI_SPEEDS_HZ if installed else (speed,)*num_devices))
        if len(speeds) != num_devices or any(type(x) is not int or x <= 0 for x in speeds):
            raise ValueError('one positive SPI clock per receiver is required')
        self.devices = [LEDController(bus=device_bus, device=chip_select,
            speed=self._resolve_speed(device_bus,speeds[i]), mode=self._resolve_mode(device_bus,mode),
            strips=self.receiver_strip_counts[i], leds_per_strip=leds_per_strip, debug=debug,
            logical_device_id=i, global_strip_offset=self.receiver_global_strip_offsets[i],
            fec_transport=i in self.fec_receiver_ids)
            for i,(device_bus,chip_select) in enumerate(self.device_map)]

    def _controller_lock(self):
        return self._transport_lock

    def set_all_pixels(self, colors):
        return self._set_all_pixels(colors, stream=False)

    def stream_host_full_pixels(self, colors):
        return self._set_all_pixels(colors, stream=True)

    def set_frame(self, colors, dirty_ranges=None):
        return self.stream_host_full_pixels(colors)

    def _set_all_pixels(self, colors, *, stream):
        pixels = np.asarray(colors)
        if pixels.shape != (self.total_leds,3) or pixels.dtype != np.uint8:
            raise ValueError('wall frames must contain exactly the configured uint8 RGB pixels')
        with self._controller_lock():
            frames = self._split_frame(pixels)
            sequence = self._claim_logical_wall_frame_sequence()
            now = time.perf_counter()
            skip_receiver3 = stream and self.num_devices == 5 and now-getattr(self,'_receiver3_last_send_at',float('-inf')) < 1/15
            dispatch = {bus: tuple(i for i in ids if not(skip_receiver3 and i==3)) for bus,ids in self._devices_by_bus.items()}
            if skip_receiver3:
                self._host_full_receiver3_frames_skipped += 1
            if self._executor is not None:
                futures = [self._executor.submit(self._send_bus_frames,ids,frames,sequence) for ids in dispatch.values()]
                results = [future.result() for future in futures]
            else:
                results = [self._send_bus_frames(ids,frames,sequence) for ids in dispatch.values()]
            if self.num_devices == 5 and not skip_receiver3:
                self._receiver3_last_send_at = now
            self._logical_frames_sent += 1
            successful = all(results)
            self._last_full_frame_failure = None if successful else {'error':'one or more RGB receiver transfers failed','wall_frame_sequence':sequence}
            return successful

    def set_pixel(self, pixel, r, g, b):
        if type(pixel) is not int or not 0 <= pixel < self.total_leds:
            raise ValueError('pixel index is outside wall')
        for i,(offset,count) in enumerate(zip(self.receiver_pixel_offsets,self.receiver_pixel_counts)):
            if offset <= pixel < offset+count:
                local = pixel-offset
                if self.reverse_host_strips_by_logical_receiver[i]:
                    local = (self.receiver_strip_counts[i]-1-local//self.leds_per_strip)*self.leds_per_strip+local%self.leds_per_strip
                return self.devices[i].set_pixel(local,r,g,b)

    def configure(self):
        with self._controller_lock():
            for device,mask in zip(self.devices,self.receiver_lane_masks):
                device.configure()
                device.set_lane_mask(mask)

    def set_lane_mask(self, lane_mask):
        with self._controller_lock():
            for device in self.devices:
                device.set_lane_mask(lane_mask)

    def set_stagger_phases(self, phases):
        with self._controller_lock():
            for device in self.devices:
                device.set_stagger_phases(phases)

    def refresh_receiver_status(self, request_id=None):
        with self._controller_lock():
            for device in self.devices:
                device.query_fresh_receiver_status()
        return self.get_stats()

    def close(self):
        if self._executor is not None:
            self._executor.shutdown(wait=True)
        for device in self.devices:
            device.close()

    def get_stats(self):
        devices = [dict(device.get_stats(),device_index=i,logical_device=i,spi_route=list(self.device_map[i])) for i,device in enumerate(self.devices)]
        return {'devices':devices,'aggregate':{
            'frames_sent':self._logical_frames_sent,
            'bytes_sent':sum(x.get('bytes_sent',0) for x in devices),
            'errors':sum(x.get('errors',0) for x in devices),
            'connected_receivers':[x['device_index'] for x in devices if x.get('receiver_connected')],
            'last_full_frame_failure':self._last_full_frame_failure,
            'host_full_receiver3_frames_skipped':self._host_full_receiver3_frames_skipped,
            'render_mode':'host_full_rgb'}}

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
                accepted = self.devices[device_id].set_all_pixels(colors)
            else:
                accepted = self.devices[device_id].set_all_pixels(
                    colors, wall_frame_sequence=wall_frame_sequence
                )
            if accepted is False:
                return False
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

    def set_brightness(self, brightness: int):
        """Set global brightness on all devices"""
        with self._controller_lock():
            # A failed serial update may leave mixed physical levels even when
            # the manager still reports the previous value. Never reuse it.
            self._applied_brightness = None
            self.current_brightness = brightness
            for device in self.devices:
                device.set_brightness(brightness)
            self._applied_brightness = brightness

    def brightness_is_applied(self, brightness: int) -> bool:
        """Whether the last whole-wall brightness write completed at this level."""
        with self._controller_lock():
            return (type(brightness) is int
                    and getattr(self, "_applied_brightness", None) == brightness
                    and self.current_brightness == brightness)

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
