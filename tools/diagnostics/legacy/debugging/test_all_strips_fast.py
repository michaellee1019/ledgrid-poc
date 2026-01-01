#!/usr/bin/env python3
"""
ULTRA-FAST rainbow animation using CMD_SET_ALL_PIXELS batch command
Sends all 160 pixels in ONE SPI transaction instead of 160 separate ones
Target: 60 FPS
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
CMD_SET_ALL_PIXELS = 0x06  # NEW: Batch command
CMD_PING = 0xFF

class UltraFastSPI:
    """Ultra-optimized software SPI using batch commands"""
    
    def __init__(self, sclk=SCLK_PIN, mosi=MOSI_PIN, cs=CS_PIN):
        self.sclk = sclk
        self.mosi = mosi
        self.cs = cs
        
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        GPIO.setup(self.sclk, GPIO.OUT)
        GPIO.setup(self.mosi, GPIO.OUT)
        GPIO.setup(self.cs, GPIO.OUT)
        
        GPIO.output(self.sclk, GPIO.LOW)
        GPIO.output(self.mosi, GPIO.LOW)
        GPIO.output(self.cs, GPIO.HIGH)
        
        # Pre-allocate pixel buffer to avoid reallocations
        self.pixel_buffer = [0] * TOTAL_LEDS * 3
        
        print(f"✓ Ultra-Fast Software SPI initialized")
        print(f"  SCLK: GPIO {self.sclk}")
        print(f"  MOSI: GPIO {self.mosi}")
        print(f"  CS:   GPIO {self.cs}")
        print(f"  Mode: BATCH (sends all {TOTAL_LEDS} pixels at once)")
    
    def _send_byte(self, byte_data):
        """Send a single byte - optimized for speed (no delays)"""
        for i in range(8):
            # Set data bit
            GPIO.output(self.mosi, (byte_data >> (7 - i)) & 0x01)
            # Clock pulse
            GPIO.output(self.sclk, GPIO.HIGH)
            GPIO.output(self.sclk, GPIO.LOW)
    
    def _send_command(self, data):
        """Send command with CS control"""
        GPIO.output(self.cs, GPIO.LOW)
        # CRITICAL: Small delay after CS assertion for SCORPIO to detect it
        time.sleep(0.0001)  # 100us delay for slave to wake up
        for byte in data:
            self._send_byte(byte)
        GPIO.output(self.cs, GPIO.HIGH)
        # Longer delay between commands to ensure slave is ready
        time.sleep(0.002)  # 2ms between commands (was 500us)
    
    def set_pixel_buffered(self, pixel, r, g, b):
        """Set pixel in local buffer (doesn't send yet)"""
        if pixel < TOTAL_LEDS:
            idx = pixel * 3
            self.pixel_buffer[idx] = r
            self.pixel_buffer[idx + 1] = g
            self.pixel_buffer[idx + 2] = b
    
    def send_all_pixels(self):
        """Send ALL pixels in one batch command"""
        # Format: [CMD_SET_ALL_PIXELS, r0, g0, b0, r1, g1, b1, ...]
        # Total: 1 + (160 * 3) = 481 bytes in ONE transaction
        data = [CMD_SET_ALL_PIXELS] + self.pixel_buffer
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


def ultra_fast_test(duration_sec=10, pattern="rainbow"):
    """Ultra-fast animation test using batch commands"""
    spi = UltraFastSPI()
    
    try:
        print(f"\n=== ULTRA-FAST Test (Batch Mode) ===")
        print(f"Total LEDs: {TOTAL_LEDS}")
        print(f"Commands per frame: 2 (1 batch + 1 show) vs 161 old way")
        print(f"Pattern: {pattern}")
        print(f"Duration: {duration_sec}s")
        print(f"Target: 60 FPS 🎯")
        print(f"Press Ctrl+C to stop early\n")
        
        # Set brightness
        spi.set_brightness(50)
        time.sleep(0.01)
        
        frame_count = 0
        start_time = time.time()
        last_fps_time = start_time
        last_fps_frame = 0
        
        offset = 0.0
        
        print("Starting animation...")
        
        while time.time() - start_time < duration_sec:
            if pattern == "rainbow":
                # Rainbow across all LEDs
                for i in range(TOTAL_LEDS):
                    hue = ((i / TOTAL_LEDS) + offset) % 1.0
                    r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
                    spi.set_pixel_buffered(i, r, g, b)
            
            elif pattern == "rainbow_per_strip":
                # Independent rainbow on each strip
                for strip in range(NUM_STRIPS):
                    for led in range(NUM_LED_PER_STRIP):
                        pixel = strip * NUM_LED_PER_STRIP + led
                        hue = ((led / NUM_LED_PER_STRIP) + offset) % 1.0
                        r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
                        spi.set_pixel_buffered(pixel, r, g, b)
            
            elif pattern == "strips_different_colors":
                # Each strip a different color from rainbow
                for strip in range(NUM_STRIPS):
                    hue = ((strip / NUM_STRIPS) + offset) % 1.0
                    r, g, b = hsv_to_rgb(hue, 1.0, 1.0)
                    for led in range(NUM_LED_PER_STRIP):
                        pixel = strip * NUM_LED_PER_STRIP + led
                        spi.set_pixel_buffered(pixel, r, g, b)
            
            # Send all pixels in ONE command + show
            spi.send_all_pixels()
            spi.show()
            frame_count += 1
            
            # Debug: Print first few frames to verify show is being sent
            if frame_count <= 3:
                print(f"  [Frame {frame_count}] Sent set_all_pixels + show")
            
            # Update offset for animation
            offset += 0.05
            if offset >= 1.0:
                offset -= 1.0
            
            # Display FPS every second
            current_time = time.time()
            if current_time - last_fps_time >= 1.0:
                fps = (frame_count - last_fps_frame) / (current_time - last_fps_time)
                elapsed = current_time - start_time
                print(f"⚡ FPS: {fps:.1f} | Frames: {frame_count} | Elapsed: {elapsed:.1f}s")
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
        print(f"  Commands per frame: 2 (batch + show)")
        print(f"  Commands per second: {2 * avg_fps:.0f}")
        print(f"\n  🚀 Speedup vs old method: {avg_fps / 2.9:.1f}x faster!")
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
    print("🚀 ULTRA-FAST Rainbow Test - Batch Mode")
    print("="*60)
    
    # Parse command line arguments
    pattern = "rainbow"
    duration = 10
    
    if len(sys.argv) > 1:
        pattern = sys.argv[1]
    if len(sys.argv) > 2:
        duration = int(sys.argv[2])
    
    print(f"\nConfiguration:")
    print(f"  Mode: BATCH (all pixels in ONE SPI transaction)")
    print(f"  Pattern: {pattern}")
    print(f"  Duration: {duration}s")
    print(f"\nAvailable patterns:")
    print(f"  - rainbow: Rainbow across all 160 LEDs")
    print(f"  - rainbow_per_strip: Independent rainbow on each strip")
    print(f"  - strips_different_colors: Each strip a different color")
    print(f"\nUsage: python3 test_all_strips_fast.py [pattern] [duration_sec]")
    
    ultra_fast_test(duration_sec=duration, pattern=pattern)

