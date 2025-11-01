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

# LED Configuration  
NUM_LED_PER_STRIP = 30
NUM_STRIPS = 1
TOTAL_LEDS = NUM_LED_PER_STRIP * NUM_STRIPS

# SPI Configuration
SPI_BUS = 0  # SPI bus number (0 = /dev/spidev0.X)
SPI_DEVICE = 0  # CE0 matches wiring to XIAO GPIO2 (D1)
SPI_SPEED = 5000000  # 5 MHz default
SPI_MODE = 3  # CPOL=1, CPHA=1 required by ESP32 slave driver

GLOBAL_OPTS_WITH_VALUE = {"--bus", "--device", "--spi-speed", "--mode", "--brightness"}


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

# Command definitions
CMD_SET_PIXEL = 0x01
CMD_SET_BRIGHTNESS = 0x02
CMD_SHOW = 0x03
CMD_CLEAR = 0x04
CMD_SET_RANGE = 0x05
CMD_SET_ALL = 0x06
CMD_PING = 0xFF


class LEDController:
    """Control LED strips via SPI"""
    
    def __init__(self, bus=SPI_BUS, device=SPI_DEVICE, speed=SPI_SPEED, mode=SPI_MODE):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = speed
        self.spi.mode = mode
        self.spi.bits_per_word = 8
        
        print(f"SPI Controller initialized")
        print(f"  Bus: {bus}, Device: {device}")
        print(f"  Speed: {speed/1000000:.1f} MHz")
        print(f"  Mode: {mode}")
        print(f"  Device: /dev/spidev{bus}.{device}")
        
        # Test ping
        try:
            self.spi.xfer2([CMD_PING])
            time.sleep(0.01)
            print("✓ SPI connection OK\n")
        except Exception as e:
            print(f"Warning: SPI test failed: {e}\n")
    
    def set_pixel(self, pixel, r, g, b):
        """Set a single pixel color"""
        if pixel >= TOTAL_LEDS:
            return
        
        data = [
            CMD_SET_PIXEL,
            (pixel >> 8) & 0xFF,
            pixel & 0xFF,
            int(r) & 0xFF,
            int(g) & 0xFF,
            int(b) & 0xFF
        ]
        self.spi.xfer2(data)
    
    def set_brightness(self, brightness):
        """Set global brightness (0-255)"""
        data = [CMD_SET_BRIGHTNESS, int(brightness) & 0xFF]
        self.spi.xfer2(data)
    
    def show(self):
        """Update the LED display"""
        self.spi.xfer2([CMD_SHOW])
    
    def clear(self):
        """Clear all LEDs"""
        self.spi.xfer2([CMD_CLEAR])
    
    def set_range(self, start_pixel, colors):
        """
        Set a range of pixels efficiently
        colors: list of (r, g, b) tuples
        """
        count = min(len(colors), 160)  # Limit to avoid buffer overflow
        
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
        
        self.spi.xfer2(data)

    def set_all_pixels(self, colors):
        """Send all pixels in one SPI transaction"""
        if len(colors) < TOTAL_LEDS:
            # Pad with zeros if caller supplied fewer pixels
            colors = list(colors) + [(0, 0, 0)] * (TOTAL_LEDS - len(colors))
        elif len(colors) > TOTAL_LEDS:
            colors = colors[:TOTAL_LEDS]

        data = [CMD_SET_ALL]
        for r, g, b in colors:
            data.extend([int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF])

        self.spi.xfer2(data)
    
    def close(self):
        """Close SPI connection"""
        self.spi.close()


def hsv_to_rgb(h, s, v):
    """Convert HSV to RGB (0-255)"""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def rainbow_animation(controller, duration=None, speed=0.3, span=None):
    """Rainbow cycle animation"""
    print("Starting rainbow animation...")
    print("Press Ctrl+C to stop\n")

    hue_offset = 0
    start_time = time.time()
    frame_count = 0
    span_pixels = span if span else max(TOTAL_LEDS, 60)

    try:
        while True:
            if duration and (time.time() - start_time) > duration:
                break

            # Calculate colors for all pixels
            pixel_colors = []
            for pixel in range(TOTAL_LEDS):
                hue = (hue_offset + (pixel / span_pixels)) % 1.0
                pixel_colors.append(hsv_to_rgb(hue, 1.0, 1.0))

            controller.set_all_pixels(pixel_colors)

            hue_offset += 0.01 * speed
            if hue_offset >= 1.0:
                hue_offset -= 1.0

            frame_count += 1

            if frame_count % 100 == 0:
                elapsed = time.time() - start_time
                fps = frame_count / elapsed
                print(f"FPS: {fps:.1f} | Frames: {frame_count}")
                # Reset counters to report instantaneous rate
                frame_count = 0
                start_time = time.time()

            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\nAnimation stopped")


def solid_color(controller, r, g, b):
    """Set all LEDs to a solid color"""
    print(f"Setting all LEDs to RGB({r}, {g}, {b})")
    controller.set_all_pixels([(r, g, b)] * TOTAL_LEDS)


def test_strips(controller):
    """Test each strip individually"""
    print("Testing each strip individually...")
    
    colors = [
        (255, 0, 0), (255, 127, 0), (255, 255, 0), (0, 255, 0),
        (0, 255, 255), (0, 0, 255), (127, 0, 255), (255, 0, 255),
    ]
    
    pixel_buffer = [(0, 0, 0)] * TOTAL_LEDS

    for strip in range(NUM_STRIPS):
        print(f"Testing strip {strip}...")
        r, g, b = colors[strip]

        # Update buffer for current strip
        for pixel in range(NUM_LED_PER_STRIP):
            pixel_index = strip * NUM_LED_PER_STRIP + pixel
            pixel_buffer[pixel_index] = (r, g, b)

        controller.set_all_pixels(pixel_buffer)
        time.sleep(0.5)

        # Clear this strip in the local buffer for the next iteration
        for pixel in range(NUM_LED_PER_STRIP):
            pixel_index = strip * NUM_LED_PER_STRIP + pixel
            pixel_buffer[pixel_index] = (0, 0, 0)
    
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
                                  speed=args.spi_speed, mode=args.mode)

        controller.set_brightness(args.brightness)
        print(f"Brightness set to {args.brightness}\n")

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
            print("\nSPI connection closed")


if __name__ == '__main__':
    main()

