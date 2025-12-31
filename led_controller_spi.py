#!/usr/bin/env python3
"""
LED Grid Controller - SPI version
Controls multiple SCORPIO boards via SPI
"""

import time
import colorsys
import argparse
import spidev
import sys

from led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP

# LED Configuration defaults
DEFAULT_LED_PER_STRIP = DEFAULT_LEDS_PER_STRIP
DEFAULT_NUM_STRIPS = DEFAULT_STRIP_COUNT

# SPI Configuration
SPI_BUS = 0  # SPI bus number (0 = /dev/spidev0.X)
SPI_DEVICE = 0  # CE0 matches wiring to XIAO GPIO2 (D1)
SPI_SPEED = 8000000  # 8 MHz - now stable with 0.1% error rate
SPI_MODE = 3  # CPOL=1, CPHA=1 required by ESP32 slave driver
SPI_INTER_FRAME_DELAY = 0.0  # No delay needed - SPI is stable now

MAX_SPI_TRANSFER = 4096
MAX_PIXELS_SET_ALL = (MAX_SPI_TRANSFER - 1) // 3
MAX_PIXELS_PER_RANGE = min(255, (MAX_SPI_TRANSFER - 4) // 3)

GLOBAL_OPTS_WITH_VALUE = {"--bus", "--device", "--spi-speed", "--mode", "--brightness", "--strips", "--leds-per-strip"}
GLOBAL_BOOL_OPTS = {"--debug"}


def _normalize_global_args(argv):
    """Move global options ahead of subcommand to appease argparse."""
    if not argv:
        return []

    front = []
    rest = []
    i = 0
    prefixes = tuple(f"{opt}=" for opt in GLOBAL_OPTS_WITH_VALUE)

    while i < len(argv):
        token = argv[i]
        if token in GLOBAL_OPTS_WITH_VALUE:
            front.append(token)
            if i + 1 < len(argv):
                front.append(argv[i + 1])
                i += 2
            else:
                i += 1
            continue

        if token in GLOBAL_BOOL_OPTS:
            front.append(token)
            i += 1
            continue

        matched_prefix = False
        for prefix in prefixes:
            if token.startswith(prefix):
                front.append(token)
                matched_prefix = True
                break

        if matched_prefix:
            i += 1
            continue

        rest.append(token)
        i += 1

    return front + rest


def _pad_payload(payload):
    pad_len = (-len(payload)) % 4
    if pad_len:
        payload.extend([0] * pad_len)
    return payload

# Command definitions
CMD_SET_PIXEL = 0x01
CMD_SET_BRIGHTNESS = 0x02
CMD_SHOW = 0x03
CMD_CLEAR = 0x04
CMD_SET_RANGE = 0x05
CMD_SET_ALL = 0x06
CMD_CONFIG = 0x07
CMD_PING = 0xFF


class LEDController:
    """Control LED strips via SPI"""
    
    def __init__(self, bus=SPI_BUS, device=SPI_DEVICE, speed=SPI_SPEED, mode=SPI_MODE,
                 strips=DEFAULT_NUM_STRIPS, leds_per_strip=DEFAULT_LED_PER_STRIP,
                 debug=False):
        self.debug = debug
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = speed
        self.spi.mode = mode
        self.spi.bits_per_word = 8

        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = self.strip_count * self.leds_per_strip
        # When True, set_all_pixels already issues CMD_SHOW so callers must not call show()
        self.inline_show = True
        self.current_brightness = None
        self._last_config_refresh = 0.0
        self._last_brightness_refresh = 0.0
        self._config_refresh_interval = 30.0  # seconds - reduced frequency to avoid LED blanking
        self._last_sent_config = None  # Track last config to avoid unnecessary refreshes
        
        if self.debug:
            print("SPI Controller initialized")
            print(f"  Bus: {bus}, Device: {device}")
            print(f"  Speed: {speed/1000000:.1f} MHz")
            print(f"  Mode: {mode}")
            print(f"  Device: /dev/spidev{bus}.{device}")
            print(f"  Number of strips: {self.strip_count}")
            print(f"  LEDs per strip: {self.leds_per_strip}")
            print(f"  Total LEDs: {self.total_leds}")
        
        # Test ping
        try:
            self._xfer([CMD_PING])
            time.sleep(0.01)
            if self.debug:
                print("✓ SPI connection OK\n")
        except Exception as e:
            print(f"Warning: SPI test failed: {e}\n", file=sys.stderr)
    
    def _xfer(self, payload):
        buf = list(payload)
        _pad_payload(buf)
        return self.spi.xfer2(buf)

    def _refresh_configuration(self, force=False):
        now = time.time()
        
        # Only send config if it's actually different or forced
        current_config = (self.strip_count, self.leds_per_strip)
        config_changed = (self._last_sent_config != current_config)
        
        if force or config_changed or (now - self._last_config_refresh) > self._config_refresh_interval:
            cfg = [
                CMD_CONFIG,
                self.strip_count & 0xFF,
                (self.leds_per_strip >> 8) & 0xFF,
                self.leds_per_strip & 0xFF,
                1 if self.debug else 0,
            ]
            self._xfer(cfg)
            self._last_config_refresh = now
            self._last_sent_config = current_config
            if self.debug:
                print(f"✓ Configuration refresh (strips={self.strip_count}, leds/strip={self.leds_per_strip})")

        if self.current_brightness is not None and (force or (now - self._last_brightness_refresh) > self._config_refresh_interval):
            self._xfer([CMD_SET_BRIGHTNESS, self.current_brightness & 0xFF])
            self._last_brightness_refresh = now
            if self.debug:
                print(f"✓ Brightness refresh ({self.current_brightness})")
    
    def set_pixel(self, pixel, r, g, b):
        """Set a single pixel color"""
        if pixel >= self.total_leds:
            return
        
        self._refresh_configuration()

        data = [
            CMD_SET_PIXEL,
            (pixel >> 8) & 0xFF,
            pixel & 0xFF,
            int(r) & 0xFF,
            int(g) & 0xFF,
            int(b) & 0xFF
        ]
        self._xfer(data)
    
    def set_brightness(self, brightness):
        """Set global brightness (0-255)"""
        level = int(brightness) & 0xFF
        self.current_brightness = level
        self._refresh_configuration(force=True)
    
    def show(self):
        """Update the LED display"""
        self._refresh_configuration()
        self._xfer([CMD_SHOW])
    
    def clear(self):
        """Clear all LEDs"""
        self._refresh_configuration()
        self._xfer([CMD_CLEAR])
    
    def set_range(self, start_pixel, colors):
        """
        Set a range of pixels efficiently
        colors: list of (r, g, b) tuples
        """
        count = min(len(colors), MAX_PIXELS_PER_RANGE)
        
        if start_pixel >= self.total_leds:
            return

        count = min(count, self.total_leds - start_pixel)

        self._refresh_configuration()

        data = [
            CMD_SET_RANGE,
            (start_pixel >> 8) & 0xFF,
            start_pixel & 0xFF,
            count
        ]
        
        # Add RGB data
        for i in range(count):
            r, g, b = colors[i]
            data.extend([int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF])
        
        self._xfer(data)

    def configure(self):
        self.total_leds = self.strip_count * self.leds_per_strip
        self._refresh_configuration(force=True)
        if self.debug:
            print(f"✓ Configuration sent (strips={self.strip_count}, leds/strip={self.leds_per_strip})")

    def set_all_pixels(self, colors):
        """Send all pixels in one SPI transaction"""
        self._refresh_configuration()

        total_pixels = self.total_leds
        base_colors = list(colors)

        if len(base_colors) < total_pixels:
            frame_colors = base_colors + [(0, 0, 0)] * (total_pixels - len(base_colors))
        elif len(base_colors) > total_pixels:
            frame_colors = base_colors[:total_pixels]
        else:
            frame_colors = base_colors

        if total_pixels <= MAX_PIXELS_SET_ALL:
            data = [CMD_SET_ALL]
            for r, g, b in frame_colors:
                data.extend([int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF])
            self._xfer(data)
            # Inter-frame delay if configured (0 = no delay)
            if SPI_INTER_FRAME_DELAY > 0:
                time.sleep(SPI_INTER_FRAME_DELAY)
            # CMD_SET_ALL already calls FastLED.show(), no explicit SHOW needed
        else:
            start = 0
            while start < total_pixels:
                count = min(MAX_PIXELS_PER_RANGE, total_pixels - start)
                payload = [
                    CMD_SET_RANGE,
                    (start >> 8) & 0xFF,
                    start & 0xFF,
                    count
                ]
                for r, g, b in frame_colors[start:start + count]:
                    payload.extend([int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF])
                self._xfer(payload)
                start += count

            self._xfer([CMD_SHOW])
    
    def close(self):
        """Close SPI connection"""
        self.spi.close()


def hsv_to_rgb(h, s, v):
    """Convert HSV to RGB (0-255)"""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def rainbow_animation(controller, duration=None, speed=0.3, span=None):
    """Rainbow cycle animation"""
    if controller.debug:
        print("Starting rainbow animation...")
        print("Press Ctrl+C to stop\n")

    start_time = time.time()
    frame_count = 0
    span_pixels = span if span else max(controller.leds_per_strip, 30)
    hue_offset = 0.0
    hue_step = 0.01 * speed

    try:
        while True:
            if duration and (time.time() - start_time) > duration:
                break

            # Calculate colors for all pixels
            pixel_colors = [(0, 0, 0)] * controller.total_leds

            for led in range(controller.leds_per_strip):
                hue = (hue_offset + (led / span_pixels)) % 1.0
                color = hsv_to_rgb(hue, 1.0, 1.0)
                for strip in range(controller.strip_count):
                    idx = strip * controller.leds_per_strip + led
                    pixel_colors[idx] = color

            controller.set_all_pixels(pixel_colors)

            hue_offset += hue_step
            if hue_offset >= 1.0:
                hue_offset -= 1.0

            frame_count += 1

            if controller.debug and frame_count % 100 == 0:
                elapsed = time.time() - start_time
                fps = frame_count / elapsed
                print(f"FPS: {fps:.1f} | Frames: {frame_count}")
                # Reset counters to report instantaneous rate
                frame_count = 0
                start_time = time.time()

            time.sleep(0.02)

    except KeyboardInterrupt:
        if controller.debug:
            print("\nAnimation stopped")


def solid_color(controller, r, g, b):
    """Set all LEDs to a solid color"""
    if controller.debug:
        print(f"Setting all LEDs to RGB({r}, {g}, {b})")
    controller.set_all_pixels([(r, g, b)] * controller.total_leds)


def test_strips(controller):
    """Test each strip individually"""
    if controller.debug:
        print("Testing each strip individually...")
    
    colors = [
        (255, 0, 0),
        (255, 127, 0),
        (255, 255, 0),
        (0, 255, 0),
        (0, 255, 255),
        (0, 0, 255),
        (255, 0, 255),
    ]
    
    pixel_buffer = [(0, 0, 0)] * controller.total_leds

    for strip in range(controller.strip_count):
        if controller.debug:
            print(f"Testing strip {strip}...")
        r, g, b = colors[strip % len(colors)]

        for pixel in range(controller.leds_per_strip):
            pixel_index = strip * controller.leds_per_strip + pixel
            pixel_buffer[pixel_index] = (r, g, b)

        controller.set_all_pixels(pixel_buffer)
        time.sleep(0.5)

        # Clear this strip in the local buffer for the next iteration
        for pixel in range(controller.leds_per_strip):
            pixel_index = strip * controller.leds_per_strip + pixel
            pixel_buffer[pixel_index] = (0, 0, 0)
    
    if controller.debug:
        print("Test complete!")


def main():
    parser = argparse.ArgumentParser(description='LED Grid Controller (SPI)')
    parser.add_argument('--bus', type=int, default=SPI_BUS,
                        help=f'SPI bus number (default: {SPI_BUS})')
    parser.add_argument('--device', type=int, default=SPI_DEVICE,
                        help=f'SPI device/CS number (default: {SPI_DEVICE})')
    parser.add_argument('--spi-speed', type=int, default=SPI_SPEED,
                        help=f'SPI bus speed in Hz (default: {SPI_SPEED})')
    parser.add_argument('--mode', type=int, default=SPI_MODE,
                        choices=[0, 1, 2, 3],
                        help=f'SPI mode (default: {SPI_MODE})')
    parser.add_argument('--brightness', type=int, default=50,
                        help='LED brightness 0-255 (default: 50)')
    parser.add_argument('--strips', type=int, default=DEFAULT_NUM_STRIPS,
                        help=f'Number of strips (default: {DEFAULT_NUM_STRIPS})')
    parser.add_argument('--leds-per-strip', type=int, default=DEFAULT_LED_PER_STRIP,
                        help=f'LEDs per strip (default: {DEFAULT_LED_PER_STRIP})')
    parser.add_argument('--debug', action='store_true', help='Enable verbose controller output')
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    rainbow_parser = subparsers.add_parser('rainbow', help='Rainbow animation')
    rainbow_parser.add_argument('--speed', type=float, default=0.3, dest='anim_speed')
    rainbow_parser.add_argument('--duration', type=float, default=None)
    
    solid_parser = subparsers.add_parser('solid', help='Solid color')
    solid_parser.add_argument('r', type=int, help='Red (0-255)')
    solid_parser.add_argument('g', type=int, help='Green (0-255)')
    solid_parser.add_argument('b', type=int, help='Blue (0-255)')
    
    subparsers.add_parser('test', help='Test each strip')
    subparsers.add_parser('clear', help='Clear all LEDs')
    
    parse_fn = getattr(parser, 'parse_known_intermixed_args', None)
    norm_argv = _normalize_global_args(sys.argv[1:])

    if parse_fn is None:
        args = parser.parse_args(norm_argv)
    else:
        try:
            args, extras = parse_fn(norm_argv)
            if extras:
                parser.error(f"unrecognized arguments: {' '.join(extras)}")
        except TypeError:
            args = parser.parse_args(norm_argv)
    
    controller = None
    try:
        controller = LEDController(bus=args.bus, device=args.device,
                                  speed=args.spi_speed, mode=args.mode,
                                  strips=args.strips, leds_per_strip=args.leds_per_strip,
                                  debug=args.debug)

        controller.set_brightness(args.brightness)
        if controller.debug:
            print(f"Brightness set to {args.brightness}\n")
        controller.configure()

        if args.command == 'rainbow':
            rainbow_animation(controller,
                               duration=args.duration,
                               speed=args.anim_speed)
        elif args.command == 'solid':
            solid_color(controller, args.r, args.g, args.b)
        elif args.command == 'test':
            test_strips(controller)
        elif args.command == 'clear':
            controller.clear()
            if controller.debug:
                print("All LEDs cleared")
        else:
            rainbow_animation(controller)

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if controller:
            controller.close()
            if controller.debug:
                print("\nSPI connection closed")


if __name__ == '__main__':
    main()
