#!/usr/bin/env python3
"""
Fast rainbow animation test to measure maximum FPS
Uses optimized batching and reduced delays
"""

import RPi.GPIO as GPIO
import time
import colorsys
import sys

# GPIO Pin Configuration (BCM numbering)
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
CMD_SET_RANGE = 0x05
CMD_PING = 0xFF

class FastSPI:
    """Optimized software SPI for high-speed LED control"""
    
    def __init__(self, sclk=SCLK_PIN, mosi=MOSI_PIN, cs=CS_PIN, speed_hz=50000, inter_cmd_delay_ms=0.2):
        self.sclk = sclk
        self.mosi = mosi
        self.cs = cs
        self.delay_us = 1_000_000 / (2 * speed_hz)  # Half-period delay
        self.inter_cmd_delay = inter_cmd_delay_ms / 1000.0  # Convert to seconds
        
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        GPIO.setup(self.sclk, GPIO.OUT)
        GPIO.setup(self.mosi, GPIO.OUT)
        GPIO.setup(self.cs, GPIO.OUT)
        
        GPIO.output(self.sclk, GPIO.LOW)
        GPIO.output(self.mosi, GPIO.LOW)
        GPIO.output(self.cs, GPIO.HIGH)
        
        print(f"✓ Fast Software SPI initialized")
        print(f"  SCLK: GPIO {self.sclk}")
        print(f"  MOSI: GPIO {self.mosi}")
        print(f"  CS:   GPIO {self.cs}")
        print(f"  Speed: {speed_hz / 1000:.1f} kHz")
        print(f"  Inter-command delay: {inter_cmd_delay_ms:.2f}ms")
    
    def _send_byte(self, byte_data):
        """Send a single byte - optimized for speed"""
        for i in range(8):
            # Set data bit
            GPIO.output(self.mosi, (byte_data >> (7 - i)) & 0x01)
            # Clock pulse (no delay for maximum speed)
            GPIO.output(self.sclk, GPIO.HIGH)
            GPIO.output(self.sclk, GPIO.LOW)
    
    def _send_command(self, data):
        """Send command with CS control"""
        GPIO.output(self.cs, GPIO.LOW)
        for byte in data:
            self._send_byte(byte)
        GPIO.output(self.cs, GPIO.HIGH)
        # Small delay to let SCORPIO catch up
        if self.inter_cmd_delay > 0:
            time.sleep(self.inter_cmd_delay)
    
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


def fast_rainbow_test(duration_sec=10, inter_cmd_delay_ms=0.2):
    """Test fast rainbow animation"""
    spi = FastSPI(speed_hz=50000, inter_cmd_delay_ms=inter_cmd_delay_ms)
    
    try:
        print(f"\n=== Fast Rainbow Animation Test ===")
        print(f"Duration: {duration_sec}s")
        print(f"Press Ctrl+C to stop early\n")
        
        # Set brightness
        spi.set_brightness(50)
        time.sleep(0.01)
        
        frame_count = 0
        start_time = time.time()
        last_fps_time = start_time
        last_fps_frame = 0
        
        offset = 0.0
        
        while time.time() - start_time < duration_sec:
            # Update rainbow pattern
            for i in range(STRIP_0_START, STRIP_0_END + 1):
                hue = ((i / NUM_LED_PER_STRIP) + offset) % 1.0
                r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
                spi.set_pixel(i, r, g, b)
            
            spi.show()
            frame_count += 1
            
            # Update offset for animation
            offset += 0.05
            if offset >= 1.0:
                offset -= 1.0
            
            # Display FPS every second
            current_time = time.time()
            if current_time - last_fps_time >= 1.0:
                fps = (frame_count - last_fps_frame) / (current_time - last_fps_time)
                elapsed = current_time - start_time
                print(f"FPS: {fps:.1f} | Frames: {frame_count} | Elapsed: {elapsed:.1f}s")
                last_fps_time = current_time
                last_fps_frame = frame_count
        
        # Final stats
        total_time = time.time() - start_time
        avg_fps = frame_count / total_time
        
        print(f"\n{'='*50}")
        print(f"✓ Animation complete!")
        print(f"  Total frames: {frame_count}")
        print(f"  Total time: {total_time:.2f}s")
        print(f"  Average FPS: {avg_fps:.1f}")
        print(f"  Frame time: {1000.0/avg_fps:.1f}ms")
        print(f"{'='*50}\n")
        
        # Clear
        spi.clear()
        spi.show()
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        spi.clear()
        spi.show()
        spi.close()
        print("Cleaned up GPIO")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("Fast Rainbow Animation Test - FPS Benchmark")
    print("="*60)
    
    # Test with different inter-command delays
    if len(sys.argv) > 1:
        delay_ms = float(sys.argv[1])
        print(f"\nUsing custom inter-command delay: {delay_ms}ms")
        fast_rainbow_test(duration_sec=10, inter_cmd_delay_ms=delay_ms)
    else:
        print("\nStarting with 0.2ms inter-command delay")
        print("(You can specify delay: python3 test_fast_rainbow.py 0.5)")
        fast_rainbow_test(duration_sec=10, inter_cmd_delay_ms=0.2)

