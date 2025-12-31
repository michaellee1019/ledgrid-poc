#!/usr/bin/env python3
"""
Fast rainbow animation across all 8 strips (160 total LEDs)
Each strip has 20 LEDs
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
NUM_STRIPS = 8
NUM_LED_PER_STRIP = 20
TOTAL_LEDS = NUM_STRIPS * NUM_LED_PER_STRIP  # 160 LEDs

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


def test_all_strips(duration_sec=10, inter_cmd_delay_ms=0.2, pattern="rainbow"):
    """Test all 8 strips with various patterns"""
    spi = FastSPI(speed_hz=50000, inter_cmd_delay_ms=inter_cmd_delay_ms)
    
    try:
        print(f"\n=== All 8 Strips Test ===")
        print(f"Total LEDs: {TOTAL_LEDS} ({NUM_STRIPS} strips × {NUM_LED_PER_STRIP} LEDs)")
        print(f"Pattern: {pattern}")
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
            if pattern == "rainbow":
                # Rainbow across all LEDs
                for i in range(TOTAL_LEDS):
                    hue = ((i / TOTAL_LEDS) + offset) % 1.0
                    r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
                    spi.set_pixel(i, r, g, b)
            
            elif pattern == "rainbow_per_strip":
                # Independent rainbow on each strip
                for strip in range(NUM_STRIPS):
                    for led in range(NUM_LED_PER_STRIP):
                        pixel = strip * NUM_LED_PER_STRIP + led
                        hue = ((led / NUM_LED_PER_STRIP) + offset) % 1.0
                        r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
                        spi.set_pixel(pixel, r, g, b)
            
            elif pattern == "strips_different_colors":
                # Each strip a different color from rainbow
                for strip in range(NUM_STRIPS):
                    hue = ((strip / NUM_STRIPS) + offset) % 1.0
                    r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
                    for led in range(NUM_LED_PER_STRIP):
                        pixel = strip * NUM_LED_PER_STRIP + led
                        spi.set_pixel(pixel, r, g, b)
            
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
        
        print(f"\n{'='*60}")
        print(f"✓ Animation complete!")
        print(f"  Total LEDs: {TOTAL_LEDS}")
        print(f"  Total frames: {frame_count}")
        print(f"  Total time: {total_time:.2f}s")
        print(f"  Average FPS: {avg_fps:.1f}")
        print(f"  Frame time: {1000.0/avg_fps:.1f}ms")
        print(f"  Commands per frame: {TOTAL_LEDS + 1} (160 pixels + 1 show)")
        print(f"  Commands per second: {(TOTAL_LEDS + 1) * avg_fps:.0f}")
        print(f"{'='*60}\n")
        
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
    print("All 8 Strips Rainbow Test - FPS Benchmark")
    print("="*60)
    
    # Parse command line arguments
    delay_ms = 0.2
    pattern = "rainbow"
    duration = 10
    
    if len(sys.argv) > 1:
        delay_ms = float(sys.argv[1])
    if len(sys.argv) > 2:
        pattern = sys.argv[2]
    if len(sys.argv) > 3:
        duration = int(sys.argv[3])
    
    print(f"\nConfiguration:")
    print(f"  Inter-command delay: {delay_ms}ms")
    print(f"  Pattern: {pattern}")
    print(f"  Duration: {duration}s")
    print(f"\nAvailable patterns:")
    print(f"  - rainbow: Rainbow across all 160 LEDs")
    print(f"  - rainbow_per_strip: Independent rainbow on each strip")
    print(f"  - strips_different_colors: Each strip a different color")
    print(f"\nUsage: python3 test_all_strips.py [delay_ms] [pattern] [duration_sec]")
    
    test_all_strips(duration_sec=duration, inter_cmd_delay_ms=delay_ms, pattern=pattern)

