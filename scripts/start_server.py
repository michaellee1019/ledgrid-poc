#!/usr/bin/env python3
"""
LED Animation Server Startup Script

Supports running either the controller process (hardware + animation loop) or
the web/preview UI as separate Python processes that communicate via files.
"""

import argparse
import sys
import time
from pathlib import Path

# Add repo root to Python path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animation.core.manager import AnimationManager
from ipc.control_channel import FileControlChannel
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP
from web.app import create_app

# Try to import the real LED controller, fall back to mock for testing
try:
    from drivers.uart_controller import LEDController
except ImportError:
    try:
        from drivers.multi_device import MultiDeviceLEDController as LEDController
    except ImportError:
        class LEDController:
            def __init__(self, strips=DEFAULT_STRIP_COUNT, leds_per_strip=DEFAULT_LEDS_PER_STRIP, **kwargs):
                self.strip_count = strips
                self.leds_per_strip = leds_per_strip
                self.total_leds = strips * leds_per_strip
                self.debug = kwargs.get('debug', False)
                self.inline_show = True
                print(f"🔧 Mock LED Controller: {strips} strips × {leds_per_strip} LEDs = {self.total_leds} total")

            def set_all_pixels(self, *_args, **_kwargs):
                pass

            def show(self):
                pass

            def clear(self):
                pass

            def configure(self):
                pass


def run_controller_mode(args):
    """Controller process: drives LEDs and writes status/frames to disk."""
    # Determine if we're using multi-device or single-device controller
    # Multi-device controller expects total strips, single-device expects strips per device
    if hasattr(LEDController, '__name__') and 'Multi' in LEDController.__name__:
        # Multi-device controller - calculate number of devices from strip count
        strips_per_device = 8  # ESP32-S3 DevKitC has 8 strips
        num_devices = max(1, args.strips // strips_per_device)
        controller = LEDController(
            num_devices=num_devices,
            ports=[f'/dev/ttyACM{i}' for i in range(num_devices)],
            baudrate=args.baudrate,
            strips_per_device=strips_per_device,
            leds_per_strip=args.leds_per_strip,
            debug=args.controller_debug,
            parallel=True,
        )
    else:
        # Single-device UART controller or mock
        # Enable debug for first few frames to verify communication
        controller = LEDController(
            port=args.serial_port,
            baudrate=args.baudrate,
            strips=args.strips,
            leds_per_strip=args.leds_per_strip,
            debug=True,  # Always enable for initial feedback
        )
        print(f"✓ Controller initialized: {args.strips} strips × {args.leds_per_strip} LEDs")
        print()
    manager = AnimationManager(
        controller,
        plugins_dir=args.animations_dir,
        animation_speed_scale=args.animation_speed_scale,
    )
    manager.target_fps = args.target_fps

    channel = FileControlChannel(control_path=args.control_file, status_path=args.status_file)

    print("🎛️ Controller mode")
    print(f"  Control file: {args.control_file}")
    print(f"  Status file : {args.status_file}")
    print(f"  Poll every  : {args.poll_interval}s")
    print(f"  Status every: {args.status_interval}s")
    print()

    last_command_id = None
    last_status_time = 0.0

    try:
        while True:
            cmd = channel.read_control()
            if cmd and cmd.get('command_id') != last_command_id:
                last_command_id = cmd.get('command_id')
                action = cmd.get('action')
                data = cmd.get('data') or {}
                handle_command(manager, action, data)

            now = time.time()
            if now - last_status_time >= args.status_interval:
                status_payload = manager.get_current_frame()
                status_payload.update(manager.get_current_status())
                status_payload['last_command_id'] = last_command_id
                status_payload['updated_at'] = now
                channel.write_status(status_payload)
                last_status_time = now

            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\n👋 Controller stopped by user")
    finally:
        manager.stop_animation()
        if hasattr(controller, "close"):
            try:
                controller.close()
            except Exception:
                pass


def handle_command(manager: AnimationManager, action: str, data: dict):
    """Dispatch a command to the animation manager."""
    if action == 'start':
        animation = data.get('animation')
        config = data.get('config') or {}
        print(f"▶️  Start requested: {animation}")
        manager.start_animation(animation, config)
    elif action == 'stop':
        print("⏹️  Stop requested")
        manager.stop_animation()
    elif action == 'update_params':
        params = data.get('params') or {}
        if params:
            print(f"⚙️  Update params: {params}")
            manager.update_animation_parameters(params)
    elif action == 'refresh_plugins':
        animation = data.get('animation')
        if animation:
            print(f"🔄 Reload plugin: {animation}")
            manager.reload_animation(animation)
        else:
            print("🔄 Refresh all plugins")
            manager.refresh_plugins()
    elif action == 'puncture_hole':
        print("💥 Random hole requested")
        manager.trigger_random_hole()
    elif action == 'dpad':
        direction = (data.get('direction') or '').lower().replace('_', '-')
        if manager.current_animation and hasattr(manager.current_animation, 'handle_input'):
            manager.current_animation.handle_input(direction)
        else:
            print(f"⚠️ D-pad input ignored (no handler): {direction}")
    else:
        print(f"⚠️ Unknown action: {action}")


def run_web_mode(args):
    """Web/preview process."""
    channel = FileControlChannel(control_path=args.control_file, status_path=args.status_file)
    web_interface = create_app(
        control_channel=channel,
        host=args.host,
        port=args.port,
        strips=args.strips,
        leds_per_strip=args.leds_per_strip,
        animations_dir=args.animations_dir,
        animation_speed_scale=args.animation_speed_scale,
    )

    print("🌐 Web/Preview mode")
    print(f"  Control file: {args.control_file}")
    print(f"  Status file : {args.status_file}")
    print(f"  URL: http://{args.host}:{args.port}")
    print(f"  Dashboard: http://{args.host}:{args.port}/")
    print(f"  Control:   http://{args.host}:{args.port}/control")
    print(f"  Upload:    http://{args.host}:{args.port}/upload")
    print()

    web_interface.run(debug=args.debug)


def main():
    parser = argparse.ArgumentParser(description='LED Animation Server')

    parser.add_argument('--mode', choices=['controller', 'web'], default='web',
                        help='Run as controller (hardware) or web/preview process')

    # Shared options
    default_plugins_dir = str((Path(__file__).resolve().parents[1] / "animation" / "plugins").resolve())
    parser.add_argument('--animations-dir', default=default_plugins_dir,
                        help=f'Directory containing animation plugins (default: {default_plugins_dir})')
    parser.add_argument('--control-file', default='run_state/control.json',
                        help='Path to control file (default: run_state/control.json)')
    parser.add_argument('--status-file', default='run_state/status.json',
                        help='Path to status file (default: run_state/status.json)')
    parser.add_argument('--strips', type=int, default=DEFAULT_STRIP_COUNT,
                        help=f'Number of LED strips (default: {DEFAULT_STRIP_COUNT})')
    parser.add_argument('--leds-per-strip', type=int, default=DEFAULT_LEDS_PER_STRIP,
                        help=f'LEDs per strip (default: {DEFAULT_LEDS_PER_STRIP})')

    # Web options
    parser.add_argument('--host', default='0.0.0.0',
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=5000,
                        help='Port to listen on (default: 5000)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode for Flask')

    # Controller options
    parser.add_argument('--serial-port', default='/dev/ttyACM0',
                        help='Serial port for ESP32 (default: /dev/ttyACM0)')
    parser.add_argument('--baudrate', type=int, default=921600,
                        help='Serial baudrate (default: 921600 for high FPS)')
    parser.add_argument('--controller-debug', action='store_true',
                        help='Enable LED controller debug output')
    parser.add_argument('--target-fps', type=int, default=150,
                        help='Target animation FPS (default: 150)')
    parser.add_argument('--animation-speed-scale', type=float, default=0.2,
                        help='Multiplier applied to each animation\'s speed parameter (default: 0.2)')
    parser.add_argument('--poll-interval', type=float, default=0.5,
                        help='Seconds between control-file polls (controller mode)')
    parser.add_argument('--status-interval', type=float, default=0.5,
                        help='Seconds between status writes (controller mode)')

    args = parser.parse_args()

    print("🎨 LED Grid Animation Server")
    print("=" * 40)
    print(f"Mode: {args.mode}")
    print(f"Animations: {args.animations_dir}/")
    print(f"Layout: {args.strips} strips × {args.leds_per_strip} LEDs = {args.strips * args.leds_per_strip} total")
    print()

    try:
        if args.mode == 'controller':
            print(f"UART: {args.serial_port} @ {args.baudrate} bps")
            print(f"Target FPS: {args.target_fps}")
            print()
            run_controller_mode(args)
        else:
            run_web_mode(args)
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
