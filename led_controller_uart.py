#!/usr/bin/env python3
"""
Simple UART LED Controller with Hardcoded Sparkle Animation
============================================================

Usage:
    python3 led_controller_uart.py [--port PORT] [--baudrate BAUD]

Examples:
    python3 led_controller_uart.py
    python3 led_controller_uart.py --port /dev/ttyACM0 --baudrate 921600

This script communicates with ESP32 via UART to control LED strips.
Sparkle animation is hardcoded - just run it and watch!
"""

import serial
import time
import random
import sys
import argparse

# ============================================================================
# Configuration
# ============================================================================
NUM_STRIPS = 8
LEDS_PER_STRIP = 140
TOTAL_LEDS = NUM_STRIPS * LEDS_PER_STRIP

# UART Protocol Commands
CMD_PING = 0xFF
CMD_CONFIG = 0x07
CMD_SET_ALL = 0x06
CMD_CLEAR = 0x04
CMD_SET_BRIGHTNESS = 0x02

# Packet framing
PACKET_START = 0xAA
PACKET_END = 0x55

# Sparkle Animation Parameters
SPARKLE_DECAY = 0.92  # How fast sparkles fade (0.0 = instant, 1.0 = never)
SPARKLE_RATE = 0.02   # Probability of new sparkle per LED per frame
SPARKLE_COLOR = (255, 200, 150)  # Warm white sparkle
TARGET_FPS = 60


# ============================================================================
# UART Communication
# ============================================================================

def send_packet(ser, payload):
    """Send a packet with framing: [START][LEN_LOW][LEN_HIGH][PAYLOAD][END]"""
    packet = bytearray([PACKET_START])
    packet.extend(len(payload).to_bytes(2, 'little'))
    packet.extend(payload)
    packet.append(PACKET_END)
    ser.write(packet)


def send_ping(ser):
    """Send ping command"""
    print("Sending PING...")
    send_packet(ser, [CMD_PING])
    time.sleep(0.1)


def send_config(ser, strips, leds_per_strip):
    """Send configuration: strips, LEDs per strip"""
    print(f"Configuring: {strips} strips x {leds_per_strip} LEDs = {strips * leds_per_strip} total")
    payload = [CMD_CONFIG, strips, (leds_per_strip >> 8) & 0xFF, leds_per_strip & 0xFF]
    send_packet(ser, payload)
    time.sleep(0.1)


def send_brightness(ser, brightness):
    """Set brightness (0-255)"""
    print(f"Setting brightness: {brightness}")
    send_packet(ser, [CMD_SET_BRIGHTNESS, brightness])
    time.sleep(0.1)


def send_clear(ser):
    """Clear all LEDs"""
    print("Clearing LEDs...")
    send_packet(ser, [CMD_CLEAR])
    time.sleep(0.1)


def send_frame(ser, led_data):
    """
    Send full frame of LED data
    led_data: list of (r, g, b) tuples, length = TOTAL_LEDS
    """
    payload = bytearray([CMD_SET_ALL])
    for r, g, b in led_data:
        payload.extend([r, g, b])
    send_packet(ser, payload)


# ============================================================================
# Sparkle Animation
# ============================================================================

class SparkleAnimation:
    """Simple sparkle effect - random pixels light up and fade"""
    
    def __init__(self, num_leds, decay=0.92, rate=0.02, color=(255, 200, 150)):
        self.num_leds = num_leds
        self.decay = decay
        self.rate = rate
        self.sparkle_color = color
        
        # Current brightness of each LED (0.0 to 1.0)
        self.brightness = [0.0] * num_leds
    
    def update(self):
        """Update animation state and return LED colors"""
        # Decay existing sparkles
        for i in range(self.num_leds):
            self.brightness[i] *= self.decay
            if self.brightness[i] < 0.01:
                self.brightness[i] = 0.0
        
        # Add new sparkles randomly
        for i in range(self.num_leds):
            if random.random() < self.rate:
                self.brightness[i] = 1.0
        
        # Convert brightness to RGB
        led_data = []
        for b in self.brightness:
            r = int(self.sparkle_color[0] * b)
            g = int(self.sparkle_color[1] * b)
            b_val = int(self.sparkle_color[2] * b)
            led_data.append((r, g, b_val))
        
        return led_data


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='Simple UART LED Controller with Sparkle')
    parser.add_argument('--port', default='/dev/ttyACM0', help='Serial port (default: /dev/ttyACM0)')
    parser.add_argument('--baudrate', type=int, default=115200, help='Baudrate (default: 115200)')
    args = parser.parse_args()
    
    print("=" * 70)
    print("UART LED Controller - Sparkle Animation")
    print("=" * 70)
    print(f"Port: {args.port}")
    print(f"Baudrate: {args.baudrate}")
    print(f"Strips: {NUM_STRIPS}")
    print(f"LEDs per strip: {LEDS_PER_STRIP}")
    print(f"Total LEDs: {TOTAL_LEDS}")
    print("=" * 70)
    print()
    
    # Connect to ESP32
    try:
        print(f"Connecting to {args.port}...")
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baudrate,
            timeout=1.0,
            write_timeout=1.0
        )
        print("✓ Connected!")
        time.sleep(2)  # Give ESP32 time to boot
    except Exception as e:
        print(f"✗ Failed to connect: {e}")
        print("\nTroubleshooting:")
        print("  1. Check USB cable is connected")
        print("  2. Check port name (ls /dev/ttyACM* or ls /dev/ttyUSB*)")
        print("  3. Check permissions (sudo usermod -a -G dialout $USER)")
        print("  4. Try pressing RESET on ESP32")
        return 1
    
    try:
        # Initialize ESP32
        print("\nInitializing ESP32...")
        send_ping(ser)
        send_config(ser, NUM_STRIPS, LEDS_PER_STRIP)
        send_brightness(ser, 50)  # 50/255 brightness
        send_clear(ser)
        
        print("\n✓ Initialization complete!")
        print("\nStarting sparkle animation...")
        print("Press Ctrl+C to stop\n")
        
        # Create sparkle animation
        sparkle = SparkleAnimation(
            num_leds=TOTAL_LEDS,
            decay=SPARKLE_DECAY,
            rate=SPARKLE_RATE,
            color=SPARKLE_COLOR
        )
        
        # Animation loop
        frame_count = 0
        start_time = time.time()
        last_stats_time = start_time
        
        while True:
            frame_start = time.time()
            
            # Get next frame
            led_data = sparkle.update()
            
            # Send to ESP32
            send_frame(ser, led_data)
            
            frame_count += 1
            
            # Stats every 5 seconds
            now = time.time()
            if now - last_stats_time >= 5.0:
                elapsed = now - last_stats_time
                fps = frame_count / (now - start_time)
                recent_fps = (frame_count % 1000) / elapsed if elapsed > 0 else 0
                
                print(f"📊 Frame {frame_count:6d} | FPS: {fps:6.1f} avg, {recent_fps:6.1f} recent")
                last_stats_time = now
            
            # Frame rate limiting
            frame_time = time.time() - frame_start
            target_frame_time = 1.0 / TARGET_FPS
            if frame_time < target_frame_time:
                time.sleep(target_frame_time - frame_time)
    
    except KeyboardInterrupt:
        print("\n\n✓ Stopped by user")
        print(f"Total frames: {frame_count}")
        elapsed = time.time() - start_time
        if elapsed > 0:
            print(f"Average FPS: {frame_count / elapsed:.1f}")
        
        # Clear LEDs on exit
        print("\nClearing LEDs...")
        send_clear(ser)
    
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("✓ Serial port closed")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

