#!/usr/bin/env python3
"""
Simple test to control just Strip 0 (first 30 LEDs)
Uses software SPI to ensure reliability
"""

import RPi.GPIO as GPIO
import time
import colorsys

# GPIO Pin Configuration (BCM numbering)
# Using non-SPI pins to avoid GPIO 11 issues
SCLK_PIN = 17   # Physical pin 11
MOSI_PIN = 27   # Physical pin 13
CS_PIN = 22     # Physical pin 15

# LED Configuration
NUM_LED_PER_STRIP = 30
STRIP_0_START = 0
STRIP_0_END = 29

# SPI Command Protocol
CMD_SET_PIXEL = 0x01
CMD_SET_BRIGHTNESS = 0x02
CMD_SHOW = 0x03
CMD_CLEAR = 0x04
CMD_PING = 0xFF

class SimpleSPI:
    """Simple software SPI for LED control"""
    
    def __init__(self, sclk=SCLK_PIN, mosi=MOSI_PIN, cs=CS_PIN, speed_hz=10000):
        self.sclk = sclk
        self.mosi = mosi
        self.cs = cs
        self.delay_us = 1_000_000 / (2 * speed_hz)  # Half-period delay
        
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        GPIO.setup(self.sclk, GPIO.OUT)
        GPIO.setup(self.mosi, GPIO.OUT)
        GPIO.setup(self.cs, GPIO.OUT)
        
        GPIO.output(self.sclk, GPIO.LOW)
        GPIO.output(self.mosi, GPIO.LOW)
        GPIO.output(self.cs, GPIO.HIGH)
        
        print(f"✓ Software SPI initialized")
        print(f"  SCLK: GPIO {self.sclk} (Pin 11)")
        print(f"  MOSI: GPIO {self.mosi} (Pin 13)")
        print(f"  CS:   GPIO {self.cs} (Pin 15)")
        print(f"  Speed: {speed_hz / 1000:.1f} kHz (slow for software SPI slave)")
        print(f"  GND:  Connected to SCORPIO GND")
        print(f"\n⚠️  Using NON-HARDWARE-SPI pins to avoid GPIO 11 issues")
    
    def _send_byte(self, byte_data):
        """Send a single byte"""
        for i in range(8):
            # Set data bit
            GPIO.output(self.mosi, (byte_data >> (7 - i)) & 0x01)
            time.sleep(self.delay_us / 1_000_000)  # Setup time
            # Clock pulse
            GPIO.output(self.sclk, GPIO.HIGH)
            time.sleep(self.delay_us / 1_000_000)  # High time
            GPIO.output(self.sclk, GPIO.LOW)
            time.sleep(self.delay_us / 1_000_000)  # Low time
    
    def _send_command(self, data):
        """Send command with CS control"""
        GPIO.output(self.cs, GPIO.LOW)
        time.sleep(0.000001)
        for byte in data:
            self._send_byte(byte)
        GPIO.output(self.cs, GPIO.HIGH)
        # CRITICAL: Give SCORPIO time to process before next command
        time.sleep(0.001)  # 1ms delay between commands
    
    def set_pixel(self, pixel, r, g, b):
        """Set a single pixel"""
        data = [CMD_SET_PIXEL, (pixel >> 8) & 0xFF, pixel & 0xFF, r, g, b]
        self._send_command(data)
    
    def set_brightness(self, brightness):
        """Set brightness (0-255)"""
        data = [CMD_SET_BRIGHTNESS, brightness]
        self._send_command(data)
    
    def show(self):
        """Update LEDs"""
        data = [CMD_SHOW]
        self._send_command(data)
    
    def clear(self):
        """Clear all LEDs"""
        data = [CMD_CLEAR]
        self._send_command(data)
    
    def close(self):
        """Clean up GPIO"""
        GPIO.cleanup([self.sclk, self.mosi, self.cs])


def hsv_to_rgb(h, s, v):
    """Convert HSV to RGB (0-255)"""
    return tuple(round(i * 255) for i in colorsys.hsv_to_rgb(h, s, v))


def test_strip_0():
    """Test strip 0 with various patterns"""
    spi = SimpleSPI(speed_hz=10000)  # 10 kHz for reliable software SPI
    
    try:
        print("\n=== Testing Strip 0 (First 30 LEDs) ===\n")
        
        # Set brightness
        spi.set_brightness(50)
        print("✓ Brightness set to 50")
        
        # Test 1: Red
        print("\nTest 1: Solid RED")
        for i in range(STRIP_0_START, STRIP_0_END + 1):
            spi.set_pixel(i, 255, 0, 0)
        spi.show()
        time.sleep(1.5)
        
        # Test 2: Green
        print("Test 2: Solid GREEN")
        for i in range(STRIP_0_START, STRIP_0_END + 1):
            spi.set_pixel(i, 0, 255, 0)
        spi.show()
        time.sleep(1.5)
        
        # Test 3: Blue
        print("Test 3: Solid BLUE")
        for i in range(STRIP_0_START, STRIP_0_END + 1):
            spi.set_pixel(i, 0, 0, 255)
        spi.show()
        time.sleep(1.5)
        
        # Test 4: Rainbow
        print("Test 4: Rainbow pattern")
        for i in range(STRIP_0_START, STRIP_0_END + 1):
            hue = i / NUM_LED_PER_STRIP
            r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
            spi.set_pixel(i, r, g, b)
        spi.show()
        time.sleep(2)
        
        # Test 5: Chase
        print("Test 5: White chase (5 cycles)")
        for cycle in range(5):
            for i in range(STRIP_0_START, STRIP_0_END + 1):
                # Clear all
                for j in range(STRIP_0_START, STRIP_0_END + 1):
                    spi.set_pixel(j, 0, 0, 0)
                # Set current pixel
                spi.set_pixel(i, 255, 255, 255)
                spi.show()
                time.sleep(0.05)
        
        # Clear
        print("\nClearing LEDs...")
        spi.clear()
        spi.show()
        
        print("\n✓ All tests complete!")
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n✗ Error: {e}")
    finally:
        spi.clear()
        spi.show()
        spi.close()
        print("Cleaned up GPIO")


if __name__ == "__main__":
    print("\n" + "="*50)
    print("Strip 0 Test - First 30 LEDs Only")
    print("="*50)
    test_strip_0()

