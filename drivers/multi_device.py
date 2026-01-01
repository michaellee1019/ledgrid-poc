#!/usr/bin/env python3
"""
Multi-Device LED Grid Controller - SPI version
Controls multiple ESP32 devices via SPI with different CS pins
"""

import threading
from typing import List, Tuple
from drivers.spi_controller import LEDController, SPI_BUS, SPI_SPEED, SPI_MODE


class MultiDeviceLEDController:
    """Multi-device LED controller that manages multiple ESP32 devices"""
    
    def __init__(self, 
                 num_devices: int = 1,
                 bus: int = SPI_BUS,
                 speed: int = SPI_SPEED,
                 mode: int = SPI_MODE,
                 strips_per_device: int = 7,
                 leds_per_strip: int = 140,
                 debug: bool = False,
                 parallel: bool = True):
        """
        Initialize multi-device LED controller
        
        Args:
            num_devices: Number of ESP32 devices (default: 1 for XIAO S3)
            bus: SPI bus number (default: 0)
            speed: SPI speed in Hz (default: 8MHz)
            mode: SPI mode (default: 3)
            strips_per_device: LED strips per device (default: 7 for XIAO S3 D0-D6)
            leds_per_strip: LEDs per strip (default: 140)
            debug: Enable debug output
            parallel: Send data to devices in parallel using threads
        """
        self.num_devices = num_devices
        self.strips_per_device = strips_per_device
        self.leds_per_strip = leds_per_strip
        self.debug = debug
        self.parallel = parallel
        
        # Calculate total dimensions
        self.strip_count = num_devices * strips_per_device
        self.total_leds = self.strip_count * leds_per_strip
        self.leds_per_device = strips_per_device * leds_per_strip
        
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
        
        # Initialize individual device controllers
        self.devices: List[LEDController] = []
        for device_id in range(num_devices):
            if self.debug:
                print(f"\nInitializing Device {device_id} on /dev/spidev{bus}.{device_id}")
            
            device = LEDController(
                bus=bus,
                device=device_id,  # CE0, CE1, etc.
                speed=speed,
                mode=mode,
                strips=strips_per_device,
                leds_per_strip=leds_per_strip,
                debug=debug
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
        device_frames = []
        
        for device_id in range(self.num_devices):
            device_colors = []
            
            # Each device gets consecutive strips
            for local_strip in range(self.strips_per_device):
                global_strip = device_id * self.strips_per_device + local_strip
                start_idx = global_strip * self.leds_per_strip
                end_idx = start_idx + self.leds_per_strip
                
                # Extract this strip's pixels
                if start_idx < len(colors):
                    strip_pixels = colors[start_idx:end_idx]
                else:
                    strip_pixels = []
                
                # Pad if needed
                if len(strip_pixels) < self.leds_per_strip:
                    strip_pixels.extend([(0, 0, 0)] * (self.leds_per_strip - len(strip_pixels)))
                
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
    
    def set_all_pixels(self, colors: List[Tuple[int, int, int]]):
        """
        Set all pixels across all devices
        
        Args:
            colors: List of (r,g,b) tuples for entire grid
        """
        # Split frame into per-device chunks
        device_frames = self._split_frame(colors)
        
        if self.parallel and self.num_devices > 1:
            # Send to all devices in parallel using threads
            threads = []
            for device_id, device_colors in enumerate(device_frames):
                thread = threading.Thread(
                    target=self._send_to_device,
                    args=(device_id, device_colors),
                    daemon=True
                )
                thread.start()
                threads.append(thread)
            
            # Wait for all devices to complete
            for thread in threads:
                thread.join(timeout=1.0)
        else:
            # Send to devices sequentially
            for device_id, device_colors in enumerate(device_frames):
                self._send_to_device(device_id, device_colors)
    
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
        frames_sent = 0
        bytes_sent = 0
        errors = 0
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
            frames_sent += frames
            bytes_sent += int(stats.get('bytes_sent', 0) or 0)
            errors += int(stats.get('errors', 0) or 0)

            last_frame_ms = max(last_frame_ms, float(stats.get('last_frame_duration_ms', 0.0) or 0.0))
            avg_ms = float(stats.get('avg_frame_duration_ms', 0.0) or 0.0)
            if frames > 0:
                weighted_avg_total += avg_ms * frames
                weighted_avg_frames += frames

        avg_frame_ms = weighted_avg_total / weighted_avg_frames if weighted_avg_frames else 0.0

        return {
            'devices': device_stats,
            'aggregate': {
                'total_leds': total_leds,
                'frames_sent': frames_sent,
                'bytes_sent': bytes_sent,
                'errors': errors,
                'last_frame_duration_ms': last_frame_ms,
                'avg_frame_duration_ms': avg_frame_ms,
            }
        }
