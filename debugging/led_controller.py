#!/usr/bin/env python3
"""
LED Grid Controller - Sends I2C commands to SCORPIO board
Controls 8 parallel LED strips via I2C protocol
"""

import time
import colorsys
import argparse
from smbus2 import SMBus

# I2C Configuration
I2C_ADDRESS = 0x42
I2C_BUS = 1  # Default for Raspberry Pi

# LED Configuration
NUM_LED_PER_STRIP = 30
NUM_STRIPS = 8
TOTAL_LEDS = NUM_LED_PER_STRIP * NUM_STRIPS

# Command definitions (must match Arduino code)
CMD_SET_PIXEL = 0x01
CMD_SET_BRIGHTNESS = 0x02
CMD_SHOW = 0x03
CMD_CLEAR = 0x04
CMD_SET_RANGE = 0x05


class LEDController:
    """Control LED strips via I2C"""
    
    def __init__(self, bus_number=I2C_BUS, address=I2C_ADDRESS):
        try:
            self.bus = SMBus(bus_number)
            self.address = address
            print(f"LED Controller initialized on I2C bus {bus_number}, address 0x{address:02X}")
            
            # Test connection
            try:
                self.bus.read_byte(self.address)
                print(f"✓ Successfully connected to device at 0x{address:02X}")
            except OSError:
                print(f"✗ Warning: Cannot communicate with device at 0x{address:02X}")
                print(f"  Run 'python3 i2c_scan.py' to check available devices")
                raise
        except FileNotFoundError:
            print(f"✗ Error: I2C bus {bus_number} not found")
            print(f"  Check if I2C is enabled: ls /dev/i2c-*")
            raise
        except PermissionError:
            print(f"✗ Error: Permission denied accessing I2C bus")
            print(f"  Try: sudo python3 led_controller.py [command]")
            print(f"  Or add user to i2c group: sudo usermod -a -G i2c $USER")
            raise
    
    def set_pixel(self, pixel, r, g, b):
        """Set a single pixel color"""
        if pixel >= TOTAL_LEDS:
            return
        
        data = [
            CMD_SET_PIXEL,
            (pixel >> 8) & 0xFF,  # Pixel high byte
            pixel & 0xFF,          # Pixel low byte
            int(r) & 0xFF,
            int(g) & 0xFF,
            int(b) & 0xFF
        ]
        self.bus.write_i2c_block_data(self.address, data[0], data[1:])
    
    def set_range(self, start_pixel, colors):
        """
        Set a range of pixels efficiently
        colors: list of (r, g, b) tuples
        """
        count = min(len(colors), 80)  # Limit to avoid I2C buffer overflow
        
        data = [
            CMD_SET_RANGE,
            (start_pixel >> 8) & 0xFF,  # Start pixel high byte
            start_pixel & 0xFF,          # Start pixel low byte
            count                        # Number of pixels
        ]
        
        # Add RGB data for each pixel
        for i in range(count):
            r, g, b = colors[i]
            data.extend([int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF])
        
        # Send in chunks if needed
        chunk_size = 32
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i+chunk_size]
            if i == 0:
                self.bus.write_i2c_block_data(self.address, chunk[0], chunk[1:])
            else:
                self.bus.write_i2c_block_data(self.address, 0, chunk)
            time.sleep(0.001)  # Small delay between chunks
    
    def set_brightness(self, brightness):
        """Set global brightness (0-255)"""
        data = [CMD_SET_BRIGHTNESS, int(brightness) & 0xFF]
        self.bus.write_i2c_block_data(self.address, data[0], data[1:])
    
    def show(self):
        """Update the LED display"""
        self.bus.write_byte(self.address, CMD_SHOW)
    
    def clear(self):
        """Clear all LEDs"""
        self.bus.write_byte(self.address, CMD_CLEAR)
    
    def close(self):
        """Close I2C connection"""
        self.bus.close()


def hsv_to_rgb(h, s, v):
    """Convert HSV to RGB (0-255)"""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def rainbow_animation(controller, duration=None, speed=1.0):
    """
    Rainbow cycle animation across all 8 strips
    
    Args:
        controller: LEDController instance
        duration: How long to run (None = infinite)
        speed: Animation speed multiplier
    """
    print("Starting rainbow animation...")
    print("Press Ctrl+C to stop")
    
    hue_offset = 0
    start_time = time.time()
    frame_count = 0
    
    try:
        while True:
            # Check duration
            if duration and (time.time() - start_time) > duration:
                break
            
            # Calculate colors for all pixels
            for pixel in range(TOTAL_LEDS):
                # Calculate hue based on pixel position and time
                hue = (hue_offset + (pixel / TOTAL_LEDS)) % 1.0
                r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
                controller.set_pixel(pixel, r, g, b)
            
            # Update display
            controller.show()
            
            # Advance animation
            hue_offset += 0.01 * speed
            if hue_offset >= 1.0:
                hue_offset -= 1.0
            
            frame_count += 1
            
            # Print FPS every 100 frames
            if frame_count % 100 == 0:
                elapsed = time.time() - start_time
                fps = frame_count / elapsed
                print(f"FPS: {fps:.1f} | Frames: {frame_count}")
            
            time.sleep(0.02)  # ~50 FPS
    
    except KeyboardInterrupt:
        print("\nAnimation stopped by user")


def solid_color(controller, r, g, b):
    """Set all LEDs to a solid color"""
    print(f"Setting all LEDs to RGB({r}, {g}, {b})")
    for pixel in range(TOTAL_LEDS):
        controller.set_pixel(pixel, r, g, b)
    controller.show()


def test_strips(controller):
    """Test each strip individually"""
    print("Testing each strip individually...")
    
    colors = [
        (255, 0, 0),    # Red
        (255, 127, 0),  # Orange
        (255, 255, 0),  # Yellow
        (0, 255, 0),    # Green
        (0, 255, 255),  # Cyan
        (0, 0, 255),    # Blue
        (127, 0, 255),  # Purple
        (255, 0, 255),  # Magenta
    ]
    
    for strip in range(NUM_STRIPS):
        print(f"Testing strip {strip}...")
        r, g, b = colors[strip]
        
        # Light up all LEDs in this strip
        for pixel in range(NUM_LED_PER_STRIP):
            pixel_index = strip * NUM_LED_PER_STRIP + pixel
            controller.set_pixel(pixel_index, r, g, b)
        
        controller.show()
        time.sleep(0.5)
    
    print("Test complete!")


def main():
    parser = argparse.ArgumentParser(description='LED Grid Controller')
    parser.add_argument('--bus', type=int, default=I2C_BUS,
                        help=f'I2C bus number (default: {I2C_BUS})')
    parser.add_argument('--address', type=str, default=hex(I2C_ADDRESS),
                        help=f'I2C address (default: {hex(I2C_ADDRESS)})')
    parser.add_argument('--brightness', type=int, default=50,
                        help='LED brightness 0-255 (default: 50)')
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Rainbow animation
    rainbow_parser = subparsers.add_parser('rainbow', help='Rainbow animation')
    rainbow_parser.add_argument('--speed', type=float, default=1.0,
                                help='Animation speed (default: 1.0)')
    rainbow_parser.add_argument('--duration', type=float, default=None,
                                help='Duration in seconds (default: infinite)')
    
    # Solid color
    solid_parser = subparsers.add_parser('solid', help='Solid color')
    solid_parser.add_argument('r', type=int, help='Red (0-255)')
    solid_parser.add_argument('g', type=int, help='Green (0-255)')
    solid_parser.add_argument('b', type=int, help='Blue (0-255)')
    
    # Test strips
    subparsers.add_parser('test', help='Test each strip individually')
    
    # Clear
    subparsers.add_parser('clear', help='Clear all LEDs')
    
    args = parser.parse_args()
    
    # Parse address if hex
    address = int(args.address, 16) if args.address.startswith('0x') else int(args.address)
    
    # Initialize controller
    controller = None
    try:
        controller = LEDController(bus_number=args.bus, address=address)
        
        # Set brightness
        controller.set_brightness(args.brightness)
        print(f"Brightness set to {args.brightness}")
        
        # Execute command
        if args.command == 'rainbow':
            rainbow_animation(controller, duration=args.duration, speed=args.speed)
        elif args.command == 'solid':
            solid_color(controller, args.r, args.g, args.b)
        elif args.command == 'test':
            test_strips(controller)
        elif args.command == 'clear':
            controller.clear()
            print("All LEDs cleared")
        else:
            # Default to rainbow if no command specified
            rainbow_animation(controller)
        
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if controller:
            controller.close()
            print("Controller closed")


if __name__ == '__main__':
    main()

