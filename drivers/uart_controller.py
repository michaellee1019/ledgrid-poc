#!/usr/bin/env python3
"""
LED Grid Controller - UART/USB-CDC version
Controls ESP32 LED controller via USB serial
"""

import time
import serial
import sys

from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP

# LED Configuration defaults
DEFAULT_LED_PER_STRIP = DEFAULT_LEDS_PER_STRIP
DEFAULT_NUM_STRIPS = DEFAULT_STRIP_COUNT

# UART Configuration
DEFAULT_PORT = '/dev/ttyACM0'  # Linux default (Raspberry Pi)
DEFAULT_BAUDRATE = 921600  # High speed for 120+ FPS (match ESP32)
UART_TIMEOUT = 0.5  # 500ms timeout for reads/writes

# Packet framing
PACKET_START = 0xAA
PACKET_END = 0x55

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
    """Control LED strips via UART/USB-CDC"""
    
    def __init__(self, port=DEFAULT_PORT, baudrate=DEFAULT_BAUDRATE,
                 strips=DEFAULT_NUM_STRIPS, leds_per_strip=DEFAULT_LED_PER_STRIP,
                 debug=False):
        self.debug = debug
        self.port = port
        self.baudrate = baudrate
        
        # Open serial port
        try:
            self.serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=UART_TIMEOUT,
                write_timeout=1.0  # 1 second write timeout to prevent blocking
            )
            # Flush any pending data
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
        except serial.SerialException as e:
            print(f"Error opening serial port {port}: {e}", file=sys.stderr)
            raise

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
        self._config_changed = True  # Send config on first frame
        self._frames_sent = 0
        self._bytes_sent = 0
        self._errors = 0
        self._last_frame_duration = 0.0
        self._total_frame_duration = 0.0
        
        if self.debug:
            print("UART Controller initialized")
            print(f"  Port: {port}")
            print(f"  Baudrate: {baudrate}")
            print(f"  Number of strips: {self.strip_count}")
            print(f"  LEDs per strip: {self.leds_per_strip}")
            print(f"  Total LEDs: {self.total_leds}")
        
        # Give ESP32 time to boot if just connected
        time.sleep(0.5)
        
        # Flush any startup messages
        self._read_responses(timeout=0.5)
        
        # Test ping
        try:
            print("Sending PING to ESP32...")
            self._send_command([CMD_PING])
            print("Waiting for response...")
            responses = self._read_responses(timeout=1.0)
            if self.debug:
                if responses:
                    print(f"✓ UART connection OK - ESP32 responded: {responses}")
                else:
                    print("⚠ UART connection established but no response from ESP32")
                    print("  This is normal - ESP32 may still be booting or not sending responses yet")
                    print("  Continuing anyway...\n")
        except Exception as e:
            print(f"⚠ Warning: UART test error: {e}", file=sys.stderr)
            print(f"  Port: {port}, Baudrate: {baudrate}", file=sys.stderr)
            print(f"  Continuing anyway - will retry with actual commands...\n", file=sys.stderr)
    
    def _send_packet(self, payload):
        """Send a packet with framing: [START][LEN_LOW][LEN_HIGH][PAYLOAD][END]"""
        payload_len = len(payload)
        if payload_len > 65535:
            raise ValueError(f"Payload too large: {payload_len} bytes (max 65535)")
        
        packet = bytearray()
        packet.append(PACKET_START)
        packet.append(payload_len & 0xFF)  # Length low byte
        packet.append((payload_len >> 8) & 0xFF)  # Length high byte
        packet.extend(payload)
        packet.append(PACKET_END)
        
        self._bytes_sent += len(packet)
        try:
            self.serial.write(packet)
            self.serial.flush()  # Ensure data is sent immediately
        except serial.SerialTimeoutException:
            self._errors += 1
            raise
        except Exception:
            self._errors += 1
            raise
    
    def _send_command(self, payload):
        """Send a command (wrapper for _send_packet)"""
        self._send_packet(payload)
    
    def _read_responses(self, timeout=0.1):
        """Read any response packets from ESP32"""
        responses = []
        old_timeout = self.serial.timeout
        self.serial.timeout = timeout
        
        try:
            while self.serial.in_waiting > 0 or len(responses) == 0:
                # Try to read start marker
                start = self.serial.read(1)
                if not start or start[0] != PACKET_START:
                    continue
                
                # Read length
                len_bytes = self.serial.read(2)
                if len(len_bytes) < 2:
                    continue
                
                payload_len = len_bytes[0] | (len_bytes[1] << 8)
                if payload_len > 1024:  # Sanity check
                    continue
                
                # Read payload
                payload = self.serial.read(payload_len)
                if len(payload) < payload_len:
                    continue
                
                # Read end marker
                end = self.serial.read(1)
                if not end or end[0] != PACKET_END:
                    continue
                
                # Parse response
                if payload_len > 0:
                    response_code = payload[0]
                    message = payload[1:].decode('utf-8', errors='ignore') if payload_len > 1 else ""
                    responses.append({'code': response_code, 'message': message})
                    
                    if self.debug:
                        print(f"  ESP32 response: code={response_code} msg='{message}'")
                
                # Check if more data available
                if self.serial.in_waiting == 0:
                    break
                    
        except Exception as e:
            if self.debug:
                print(f"  Warning: Error reading response: {e}")
        finally:
            self.serial.timeout = old_timeout
        
        return responses
    
    def _refresh_configuration(self):
        """Send configuration to ESP32 if needed"""
        now = time.time()
        
        # Check if config needs refresh
        if not self._config_changed and (now - self._last_config_refresh < self._config_refresh_interval):
            return
        
        # Only send if config actually changed
        config_tuple = (self.strip_count, self.leds_per_strip)
        if not self._config_changed and self._last_sent_config == config_tuple:
            return
        
        # Send config
        payload = bytearray([CMD_CONFIG, self.strip_count])
        payload.append((self.leds_per_strip >> 8) & 0xFF)
        payload.append(self.leds_per_strip & 0xFF)
        payload.append(1 if self.debug else 0)  # Debug flag
        
        self._send_command(payload)
        
        # Read response
        responses = self._read_responses(timeout=0.2)
        
        self._last_config_refresh = now
        self._last_sent_config = config_tuple
        self._config_changed = False
        
        if self.debug:
            if responses:
                print(f"Config sent: {self.strip_count} strips x {self.leds_per_strip} LEDs - ESP32: {responses[0]['message']}")
            else:
                print(f"Config sent: {self.strip_count} strips x {self.leds_per_strip} LEDs (no response)")
    
    def set_all_pixels(self, colors):
        """
        Set all pixels at once and show.
        colors: list of (r, g, b) tuples, length must be total_leds
        """
        if len(colors) != self.total_leds:
            raise ValueError(f"Expected {self.total_leds} colors, got {len(colors)}")
        
        start_time = time.time()
        
        # Refresh configuration if needed
        self._refresh_configuration()
        
        # Build SET_ALL command
        payload = bytearray([CMD_SET_ALL])
        for r, g, b in colors:
            payload.extend([r, g, b])
        
        # Log first few frames for debugging
        if self._frames_sent < 3 and self.debug:
            print(f"Sending frame #{self._frames_sent + 1}: {len(payload)} bytes, first RGB: ({colors[0][0]},{colors[0][1]},{colors[0][2]})")
        
        # Send packet
        self._send_command(payload)
        
        # Read response for first few frames
        if self._frames_sent < 3:
            self._read_responses(timeout=0.1)
        
        self._frames_sent += 1
        self._last_frame_duration = time.time() - start_time
        self._total_frame_duration += self._last_frame_duration
    
    def set_pixel(self, pixel, r, g, b):
        """Set a single pixel color (does not show)"""
        if pixel >= self.total_leds:
            return
        
        payload = bytearray([CMD_SET_PIXEL])
        payload.append((pixel >> 8) & 0xFF)
        payload.append(pixel & 0xFF)
        payload.extend([r, g, b])
        
        self._send_command(payload)
    
    def set_brightness(self, brightness):
        """Set global brightness (0-255)"""
        if brightness == self.current_brightness:
            return
        
        payload = bytearray([CMD_SET_BRIGHTNESS, brightness])
        self._send_command(payload)
        self.current_brightness = brightness
        
        if self.debug:
            print(f"Brightness set to {brightness}")
    
    def show(self):
        """Update the LED display (only needed if not using set_all_pixels)"""
        self._send_command([CMD_SHOW])
    
    def clear(self):
        """Clear all LEDs and show"""
        self._send_command([CMD_CLEAR])
        if self.debug:
            print("LEDs cleared")
    
    def ping(self):
        """Send a ping command to test connectivity"""
        self._send_command([CMD_PING])
    
    def get_stats(self):
        """Get controller statistics"""
        avg_frame_time = (self._total_frame_duration / self._frames_sent) if self._frames_sent > 0 else 0.0
        return {
            'frames_sent': self._frames_sent,
            'bytes_sent': self._bytes_sent,
            'errors': self._errors,
            'last_frame_duration': self._last_frame_duration,
            'avg_frame_duration': avg_frame_time,
            'theoretical_fps': 1.0 / avg_frame_time if avg_frame_time > 0 else 0.0
        }
    
    def print_stats(self):
        """Print controller statistics"""
        stats = self.get_stats()
        print(f"\n📊 UART Controller Stats:")
        print(f"  Frames sent: {stats['frames_sent']}")
        print(f"  Bytes sent: {stats['bytes_sent']:,}")
        print(f"  Errors: {stats['errors']}")
        print(f"  Last frame: {stats['last_frame_duration']*1000:.2f}ms")
        print(f"  Avg frame: {stats['avg_frame_duration']*1000:.2f}ms")
        print(f"  Theoretical FPS: {stats['theoretical_fps']:.1f}")
    
    def close(self):
        """Close the serial connection"""
        if hasattr(self, 'serial') and self.serial.is_open:
            self.serial.close()
            if self.debug:
                print("Serial port closed")
    
    def __del__(self):
        """Cleanup on deletion"""
        self.close()


def main():
    """Simple test program"""
    import argparse
    
    parser = argparse.ArgumentParser(description='UART LED Controller Test')
    parser.add_argument('--port', default=DEFAULT_PORT, help=f'Serial port (default: {DEFAULT_PORT})')
    parser.add_argument('--baudrate', type=int, default=DEFAULT_BAUDRATE, help=f'Baud rate (default: {DEFAULT_BAUDRATE})')
    parser.add_argument('--strips', type=int, default=DEFAULT_NUM_STRIPS, help=f'Number of strips (default: {DEFAULT_NUM_STRIPS})')
    parser.add_argument('--leds-per-strip', type=int, default=DEFAULT_LED_PER_STRIP, help=f'LEDs per strip (default: {DEFAULT_LED_PER_STRIP})')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    
    args = parser.parse_args()
    
    try:
        controller = LEDController(
            port=args.port,
            baudrate=args.baudrate,
            strips=args.strips,
            leds_per_strip=args.leds_per_strip,
            debug=args.debug
        )
        
        print("Testing UART LED Controller...")
        print("1. Ping test...")
        controller.ping()
        time.sleep(0.1)
        
        print("2. Clear test...")
        controller.clear()
        time.sleep(0.5)
        
        print("3. Red color test...")
        colors = [(255, 0, 0)] * controller.total_leds
        controller.set_all_pixels(colors)
        time.sleep(1)
        
        print("4. Green color test...")
        colors = [(0, 255, 0)] * controller.total_leds
        controller.set_all_pixels(colors)
        time.sleep(1)
        
        print("5. Blue color test...")
        colors = [(0, 0, 255)] * controller.total_leds
        controller.set_all_pixels(colors)
        time.sleep(1)
        
        print("6. Rainbow pattern test...")
        for i in range(50):
            colors = []
            for led in range(controller.total_leds):
                hue = (led + i * 10) % 256
                r = int(255 * abs((hue / 256 * 6) % 2 - 1))
                g = int(255 * abs(((hue / 256 * 6) + 2) % 2 - 1))
                b = int(255 * abs(((hue / 256 * 6) + 4) % 2 - 1))
                colors.append((r, g, b))
            controller.set_all_pixels(colors)
            time.sleep(0.02)
        
        print("\n7. Clear and stats...")
        controller.clear()
        controller.print_stats()
        
        print("\n✓ Test complete!")
        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
    finally:
        if 'controller' in locals():
            controller.close()


if __name__ == '__main__':
    main()

