#!/usr/bin/env python3
"""
ULTRA-FAST rainbow animation using HARDWARE SPI + DMA
Both Pi and SCORPIO use hardware SPI for maximum speed
Target: 60 FPS
"""

import spidev
import time
import colorsys
import sys

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
CMD_SET_ALL_PIXELS = 0x06
CMD_PING = 0xFF

class HardwareSPI:
    """Hardware SPI LED controller using spidev"""
    
    def __init__(self, bus=0, device=0, speed_hz=10_000_000):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        
        # SPI Mode 3 (CPOL=1, CPHA=1) - more reliable for RP2040 slave!
        self.spi.mode = 3
        
        # Hardware SPI can go MUCH faster
        self.spi.max_speed_hz = speed_hz
        
        # 8 bits per word
        self.spi.bits_per_word = 8
        
        # Pre-allocate pixel buffer to avoid reallocations
        self.pixel_buffer = [1] * TOTAL_LEDS * 3
        
        print(f"✓ Hardware SPI initialized")
        print(f"  Bus: {bus}, Device: {device}")
        print(f"  Speed: {speed_hz / 1_000_000:.1f} MHz")
        print(f"  Mode: {self.spi.mode}")
        print(f"  Device: /dev/spidev{bus}.{device}")
        print(f"  Using: GPIO 10 (MOSI), GPIO 11 (SCLK), GPIO 8 (CE0)")
    
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
        self.spi.xfer2(data)
    
    def set_brightness(self, brightness):
        """Set brightness (0-255)"""
        self.spi.xfer2([CMD_SET_BRIGHTNESS, brightness])
    
    def show(self):
        """Update LEDs"""
        self.spi.xfer2([CMD_SHOW])
    
    def clear(self):
        """Clear all LEDs"""
        self.spi.xfer2([CMD_CLEAR])
    
    def close(self):
        """Clean up SPI"""
        self.spi.close()


def hsv_to_rgb(h, s, v):
    """Convert HSV to RGB (0-255)"""
    return tuple(round(i * 255) for i in colorsys.hsv_to_rgb(h, s, v))


def hardware_spi_test(duration_sec=10, pattern="rainbow", speed_hz=10_000_000):
    """Hardware SPI animation test"""
    spi = HardwareSPI(bus=0, device=0, speed_hz=speed_hz)
    
    try:
        print(f"\n=== HARDWARE SPI + DMA Test ===")
        print(f"Total LEDs: {TOTAL_LEDS}")
        print(f"Commands per frame: 2 (1 batch + 1 show)")
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
        print(f"\n  🚀 Speedup vs software SPI: {avg_fps / 6.6:.1f}x faster!")
        print(f"{'='*60}\n")
        
        # Clear
        spi.clear()
        
        
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        spi.clear()
        spi.close()
        print("Cleaned up SPI")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("⚡ HARDWARE SPI + DMA Test")
    print("="*60)
    
    # Parse command line arguments
    pattern = "rainbow"
    duration = 10
    speed_mhz = 10
    
    if len(sys.argv) > 1:
        pattern = sys.argv[1]
    if len(sys.argv) > 2:
        duration = int(sys.argv[2])
    if len(sys.argv) > 3:
        speed_mhz = int(sys.argv[3])
    
    print(f"\nConfiguration:")
    print(f"  Mode: HARDWARE SPI + DMA")
    print(f"  SPI Speed: {speed_mhz} MHz")
    print(f"  Pattern: {pattern}")
    print(f"  Duration: {duration}s")
    print(f"\nAvailable patterns:")
    print(f"  - rainbow: Rainbow across all 160 LEDs")
    print(f"  - rainbow_per_strip: Independent rainbow on each strip")
    print(f"  - strips_different_colors: Each strip a different color")
    print(f"\nUsage: python3 test_hardware_spi.py [pattern] [duration_sec] [speed_mhz]")
    
    hardware_spi_test(duration_sec=duration, pattern=pattern, speed_hz=speed_mhz * 1_000_000)

