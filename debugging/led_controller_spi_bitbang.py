#!/usr/bin/env python3
"""
LED Controller using bit-banged (software) SPI instead of hardware SPI
This bypasses the Raspberry Pi's SPI hardware and manually toggles pins
"""

import RPi.GPIO as GPIO
import time
import colorsys
import argparse

# GPIO Pin Configuration (BCM numbering)
# Using non-SPI pins to avoid GPIO 11 issue
SCLK_PIN = 17   # Physical pin 11 (was GPIO 11/pin 23)
MOSI_PIN = 27   # Physical pin 13 (was GPIO 10/pin 19)
CS_PIN = 22     # Physical pin 15 (was GPIO 8/pin 24)
# MISO_PIN = 23  # Physical pin 16 (optional, not needed for LED control)

# LED Configuration
NUM_LED_PER_STRIP = 30
TOTAL_LEDS = NUM_LED_PER_STRIP * 8

# SPI Commands
CMD_SET_PIXEL = 0x01
CMD_SET_BRIGHTNESS = 0x02
CMD_SHOW = 0x03
CMD_CLEAR = 0x04
CMD_SET_RANGE = 0x05
CMD_PING = 0xFF

class LEDControllerBitBang:
    """Software SPI LED controller - bypasses hardware SPI issues"""
    
    def __init__(self, sclk=SCLK_PIN, mosi=MOSI_PIN, cs=CS_PIN, speed_hz=1000000):
        """Initialize software SPI"""
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        self.sclk = sclk
        self.mosi = mosi
        self.cs = cs
        
        # Calculate bit delay from speed
        self.bit_delay = 1.0 / (speed_hz * 2)  # Half period
        
        # Set up GPIO pins
        GPIO.setup(self.sclk, GPIO.OUT)
        GPIO.setup(self.mosi, GPIO.OUT)
        GPIO.setup(self.cs, GPIO.OUT)
        
        # Initialize to idle state
        GPIO.output(self.sclk, GPIO.LOW)
        GPIO.output(self.mosi, GPIO.LOW)
        GPIO.output(self.cs, GPIO.HIGH)  # CS is active LOW
        
        print(f"Software SPI initialized")
        print(f"  SCLK: GPIO {sclk} (Pin 23)")
        print(f"  MOSI: GPIO {mosi} (Pin 19)")
        print(f"  CS:   GPIO {cs} (Pin 24)")
        print(f"  Speed: {speed_hz / 1000:.1f} kHz")
        print(f"✓ Bit-banged SPI ready (bypassing hardware SPI)")
    
    def _send_byte(self, byte):
        """Send a single byte using bit-banging"""
        for i in range(8):
            # Set MOSI to the current bit (MSB first)
            bit = (byte >> (7 - i)) & 0x01
            GPIO.output(self.mosi, bit)
            
            # Small delay
            if self.bit_delay > 0:
                time.sleep(self.bit_delay)
            
            # Clock HIGH
            GPIO.output(self.sclk, GPIO.HIGH)
            if self.bit_delay > 0:
                time.sleep(self.bit_delay)
            
            # Clock LOW
            GPIO.output(self.sclk, GPIO.LOW)
    
    def _send_command(self, data):
        """Send a command sequence"""
        # Assert CS (LOW)
        GPIO.output(self.cs, GPIO.LOW)
        time.sleep(0.000001)  # 1us setup time
        
        # Send each byte
        for byte in data:
            self._send_byte(byte)
        
        # Deassert CS (HIGH)
        time.sleep(0.000001)  # 1us hold time
        GPIO.output(self.cs, GPIO.HIGH)
        
        # Small delay between commands
        time.sleep(0.001)
    
    def set_pixel(self, pixel, r, g, b):
        """Set a single pixel color"""
        cmd = [
            CMD_SET_PIXEL,
            (pixel >> 8) & 0xFF,  # Pixel high byte
            pixel & 0xFF,          # Pixel low byte
            r, g, b
        ]
        self._send_command(cmd)
    
    def set_brightness(self, brightness):
        """Set global brightness (0-255)"""
        cmd = [CMD_SET_BRIGHTNESS, brightness & 0xFF]
        self._send_command(cmd)
    
    def show(self):
        """Update the LED display"""
        self._send_command([CMD_SHOW])
    
    def clear(self):
        """Clear all LEDs"""
        self._send_command([CMD_CLEAR])
    
    def set_range(self, start_pixel, colors):
        """Set a range of pixels"""
        count = len(colors)
        if count > 84:  # Limit to prevent buffer overflow (84 * 3 + 4 = 256)
            count = 84
            colors = colors[:count]
        
        cmd = [
            CMD_SET_RANGE,
            (start_pixel >> 8) & 0xFF,
            start_pixel & 0xFF,
            count & 0xFF
        ]
        
        for r, g, b in colors:
            cmd.extend([r, g, b])
        
        self._send_command(cmd)
    
    def ping(self):
        """Send ping command"""
        self._send_command([CMD_PING])
        print("Ping sent")
    
    def close(self):
        """Clean up GPIO"""
        GPIO.cleanup()

def hsv_to_rgb(h, s, v):
    """Convert HSV to RGB (0-255 range)"""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)

def rainbow_animation(controller, duration=10):
    """Rainbow animation"""
    print(f"\nStarting rainbow animation (software SPI)...")
    print("Press Ctrl+C to stop")
    
    start_time = time.time()
    frame = 0
    
    try:
        while (time.time() - start_time) < duration:
            # Generate rainbow colors
            colors = []
            for i in range(TOTAL_LEDS):
                hue = (i / TOTAL_LEDS + frame / 100.0) % 1.0
                r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
                colors.append((r, g, b))
            
            # Send in chunks to avoid buffer overflow
            chunk_size = 80
            for start in range(0, TOTAL_LEDS, chunk_size):
                end = min(start + chunk_size, TOTAL_LEDS)
                controller.set_range(start, colors[start:end])
            
            controller.show()
            
            frame += 1
            if frame % 10 == 0:
                print(f"  Frame {frame}, elapsed: {time.time() - start_time:.1f}s")
            
            time.sleep(0.05)
    
    except KeyboardInterrupt:
        print("\nAnimation stopped")
    
    controller.clear()
    print("✓ Animation complete")

def main():
    parser = argparse.ArgumentParser(description='LED Controller (Software SPI)')
    parser.add_argument('command', choices=['ping', 'clear', 'rainbow', 'test'],
                       help='Command to execute')
    parser.add_argument('--brightness', type=int, default=50,
                       help='Brightness (0-255)')
    parser.add_argument('--duration', type=int, default=10,
                       help='Animation duration in seconds')
    parser.add_argument('--speed', type=int, default=100000,
                       help='SPI speed in Hz (default: 100kHz)')
    
    args = parser.parse_args()
    
    try:
        controller = LEDControllerBitBang(speed_hz=args.speed)
        
        controller.set_brightness(args.brightness)
        print(f"\nBrightness set to {args.brightness}")
        
        if args.command == 'ping':
            controller.ping()
        
        elif args.command == 'clear':
            controller.clear()
            print("✓ All LEDs cleared")
        
        elif args.command == 'rainbow':
            rainbow_animation(controller, duration=args.duration)
        
        elif args.command == 'test':
            print("\nTesting first 10 LEDs...")
            for i in range(10):
                controller.set_pixel(i, 255, 0, 0)  # Red
            controller.show()
            time.sleep(1)
            
            for i in range(10):
                controller.set_pixel(i, 0, 255, 0)  # Green
            controller.show()
            time.sleep(1)
            
            for i in range(10):
                controller.set_pixel(i, 0, 0, 255)  # Blue
            controller.show()
            time.sleep(1)
            
            controller.clear()
            print("✓ Test complete")
        
        controller.close()
    
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

