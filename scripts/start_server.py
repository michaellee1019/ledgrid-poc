#!/usr/bin/env python3
"""
LED Animation Server Startup Script

Supports running either the controller process (hardware + animation loop) or
the web/preview UI as separate Python processes that communicate via files.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

# Add repo root to Python path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animation.core.manager import AnimationManager
from ipc.control_channel import FileControlChannel
from ipc.runtime_control import (
    restore_display_state as _restore_display_state,
    start_scene as _start_scene,
    update_scene_component as _update_scene_component,
)
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP, default_strip_count
from drivers.frame_codec import decode_frame_data
from web.app import create_app
from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from tools.deployment.preserve_deploy_settings import load_saved_state, save_status


RELEASE_METADATA = ".release.json"
RELEASE_ID_PATTERN = re.compile(r"[0-9a-f]{64}")


def resolve_active_release_id(project_root: Path) -> str | None:
    """Return the verified immutable release selected by ``current``.

    A checkout using the legacy root layout has no release metadata and returns
    ``None``. Once a release marker exists, startup fails closed unless the
    marker, content-addressed directory, and deployment root's ``current``
    symlink all identify the same release.
    """
    root = project_root.resolve()
    metadata_path = root / RELEASE_METADATA
    deploy_root = root.parent.parent if root.parent.name == "releases" else None
    current_path = deploy_root / "current" if deploy_root is not None else None

    if not metadata_path.exists():
        if current_path is not None and current_path.is_symlink():
            try:
                selected = current_path.resolve(strict=True)
            except OSError as exc:
                raise RuntimeError(f"active release symlink is invalid: {exc}") from exc
            if selected == root:
                raise RuntimeError(f"active release is missing {RELEASE_METADATA}")
        return None
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise RuntimeError(f"{RELEASE_METADATA} must be a regular file")

    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read active release metadata: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("active release metadata must be a JSON object")

    release_id = payload.get("id")
    digest = payload.get("digest")
    if (
        not isinstance(release_id, str)
        or RELEASE_ID_PATTERN.fullmatch(release_id) is None
        or digest != release_id
    ):
        raise RuntimeError("active release metadata has an invalid identity")
    if root.name != release_id or root.parent.name != "releases":
        raise RuntimeError("active release identity does not match its directory")

    assert current_path is not None
    if not current_path.is_symlink():
        raise RuntimeError("active release has no current symlink")
    try:
        selected = current_path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"active release symlink is invalid: {exc}") from exc
    if selected != root:
        raise RuntimeError("release is not selected by current")
    return release_id

# Try to import the real LED controller, fall back to mock for testing
try:
    from drivers.multi_device import MultiDeviceLEDController as LEDController
except ImportError:
    try:
        from drivers.spi_controller import LEDController
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


def device_count_for_strips(strip_count: int, strips_per_device: int = 8) -> int:
    """Return enough devices to cover every configured strip."""
    return max(1, (max(1, strip_count) + strips_per_device - 1) // strips_per_device)


def controller_status_payload(
    manager: AnimationManager,
    *,
    release_id: str | None,
    last_command_id: str | None,
    updated_at: float,
) -> dict:
    """Build one controller snapshot with its immutable release identity."""
    payload = manager.get_current_frame()
    payload.update(manager.get_current_status())
    payload['release_id'] = release_id
    payload['last_command_id'] = last_command_id
    payload['updated_at'] = updated_at
    return payload


def run_controller_mode(args):
    """Controller process: drives LEDs and writes status/frames to disk."""
    saved_state = None
    try:
        saved_state = load_saved_state(Path(args.saved_state_file))
        print(f"💾 Restart default: {saved_state['animation']}/before-deploy")
    except RuntimeError as exc:
        print(f"ℹ️ No usable saved animation state: {exc}")

    # Determine if we're using multi-device or single-device controller
    # Multi-device controller expects total strips, single-device expects strips per device
    if hasattr(LEDController, '__name__') and 'Multi' in LEDController.__name__:
        # Multi-device controller - calculate number of devices from strip count
        strips_per_device = 8  # ESP32-S3 DevKitC has 8 strips
        num_devices = device_count_for_strips(args.strips, strips_per_device)
        controller = LEDController(
            num_devices=num_devices,
            bus=args.bus,
            speed=args.spi_speed,
            mode=0,
            strips_per_device=strips_per_device,
            leds_per_strip=args.leds_per_strip,
            debug=args.controller_debug,
            parallel=True,
        )
    else:
        # Single-device or mock controller
        controller = LEDController(
            bus=args.bus,
            device=args.device,
            speed=args.spi_speed,
            mode=0,
            strips=args.strips,
            leds_per_strip=args.leds_per_strip,
            debug=args.controller_debug,
        )
    startup_speed_scale = (
        saved_state.get('animation_speed_scale', args.animation_speed_scale)
        if saved_state else args.animation_speed_scale
    )
    startup_modifiers = saved_state.get('plant_modifiers') if saved_state else None
    startup_brightness = (
        saved_state.get('brightness', args.brightness) if saved_state else args.brightness
    )
    manager = AnimationManager(
        controller,
        plugins_dir=args.animations_dir,
        animation_speed_scale=startup_speed_scale,
        plant_aware=DEFAULT_PLANT_AWARE,
        plant_modifiers=startup_modifiers,
        vibe=saved_state.get('vibe') if saved_state else None,
        default_animation=saved_state.get('animation') if saved_state else None,
        default_animation_config=saved_state.get('params') if saved_state else None,
        default_animation_preset=(
            saved_state.get('current_preset') if saved_state else None
        ),
        auto_start=not bool(saved_state and saved_state.get('scene')),
    )
    manager.target_fps = int(saved_state.get('target_fps', args.target_fps)) if saved_state else args.target_fps

    try:
        manager.set_output_brightness(startup_brightness)
        print(f"  Brightness : {startup_brightness}")
    except (RuntimeError, ValueError) as exc:
        print(f"⚠️ Failed to set controller brightness to {startup_brightness}: {exc}")

    if saved_state and saved_state.get('scene'):
        try:
            if not _restore_display_state(manager, saved_state):
                raise RuntimeError("manager rejected the saved scene")
        except (RuntimeError, TypeError, ValueError) as exc:
            fallback = saved_state.get("fallback_scene")
            if fallback is None or not _start_scene(manager, fallback):
                raise RuntimeError(
                    f"saved scene and its recorded Python fallback were rejected: {exc}"
                ) from exc
            print(f"⚠️ Restored recorded Python fallback: {exc}")

    channel = FileControlChannel(control_path=args.control_file, status_path=args.status_file)

    print("🎛️ Controller mode")
    print(f"  Control file: {args.control_file}")
    print(f"  Status file : {args.status_file}")
    print(f"  Poll every  : {args.poll_interval}s")
    print(f"  Status every: {args.status_interval}s")
    print()

    # Seed from any existing control file so stale commands aren't re-executed on restart
    stale_cmd = channel.read_control()
    last_command_id = stale_cmd.get('command_id') if stale_cmd else None
    last_status_time = 0.0

    try:
        while True:
            cmd = channel.read_control()
            if cmd and cmd.get('command_id') != last_command_id:
                last_command_id = cmd.get('command_id')
                action = cmd.get('action')
                data = cmd.get('data') or {}
                if handle_command(manager, action, data):
                    try:
                        save_status(
                            manager.get_current_status(),
                            Path(args.presets_dir),
                            Path(args.saved_state_file),
                        )
                        print(f"💾 Saved restart state: {manager.current_animation_name}/before-deploy")
                    except Exception as exc:
                        print(f"⚠️ Failed to save restart state: {exc}")

            now = time.time()
            if now - last_status_time >= args.status_interval:
                status_payload = controller_status_payload(
                    manager,
                    release_id=args.release_id,
                    last_command_id=last_command_id,
                    updated_at=now,
                )
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
    """Dispatch a command and report whether restart state changed."""
    if action == 'start':
        animation = data.get('animation')
        config = data.get('config') or {}
        print(f"▶️  Start requested: {animation}")
        return manager.start_animation(animation, config, preset=data.get('preset'))
    elif action == 'start_scene':
        print("▶️  Scene start requested")
        try:
            return _start_scene(manager, data.get("scene"))
        except (TypeError, ValueError) as exc:
            print(f"⚠️ Invalid scene: {exc}")
            return False
    elif action == 'update_scene_component':
        try:
            return _update_scene_component(
                manager, data.get("target"), data.get("update") or {}
            )
        except (TypeError, ValueError) as exc:
            print(f"⚠️ Invalid scene update: {exc}")
            return False
    elif action == 'stop_scene':
        stopper = getattr(manager, "stop_scene", manager.stop_animation)
        stopper()
    elif action == 'restore_display_state':
        try:
            return _restore_display_state(manager, data.get("state"))
        except (TypeError, ValueError) as exc:
            print(f"⚠️ Invalid desired display state: {exc}")
            return False
    elif action == 'stop':
        print("⏹️  Stop requested")
        manager.stop_animation()
    elif action == 'update_params':
        params = data.get('params') or {}
        if params:
            print(f"⚙️  Update params: {params}")
            return manager.update_animation_parameters(params)
    elif action == 'set_current_preset':
        preset = data.get('preset') or {}
        return manager.set_current_preset(preset)
    elif action == 'set_target_fps':
        requested = data.get('target_fps')
        try:
            applied = manager.set_target_fps(int(requested))
            print(f"🎚️ Target FPS: {applied}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid target FPS: {requested!r}")
    elif action == 'set_animation_speed_scale':
        requested = data.get('animation_speed_scale')
        try:
            applied = manager.set_animation_speed_scale(float(requested))
            print(f"🎚️ Animation speed scale: {applied:.3f}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid animation speed scale: {requested!r}")
    elif action == 'set_output_brightness':
        requested = data.get('brightness')
        try:
            applied = manager.set_output_brightness(requested)
            print(f"💡 Output brightness: {applied}")
            return bool(manager.is_running)
        except (RuntimeError, TypeError, ValueError):
            print(f"⚠️ Invalid output brightness: {requested!r}")
    elif action == 'set_device_state':
        try:
            applied = manager.apply_device_state(data)
            print(f"🏛️ Device state: {data}")
            return bool(applied and manager.is_running)
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"⚠️ Invalid device state: {exc}")
    elif action == 'set_plant_aware':
        requested = data.get('plant_aware')
        try:
            applied = manager.set_plant_aware(requested)
            print(f"🌿 Plant-aware mode: {'on' if applied else 'off'}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid plant-aware state: {requested!r}")
    elif action == 'set_plant_modifiers':
        requested = data.get('plant_modifiers')
        try:
            applied = manager.set_plant_modifiers(requested)
            print(f"🌿 Plant modifiers: {', '.join(applied['active']) or 'off'}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid plant modifier state: {requested!r}")
    elif action == 'set_vibe':
        requested = data.get('vibe', data.get('vibe_id'))
        if requested is None:
            print("⚠️ Invalid vibe: None")
            return False
        try:
            applied = manager.set_vibe(requested)
            state = applied.get('state', applied) if isinstance(applied, dict) else {}
            vibe_id = state.get('id', state.get('vibe_id', requested))
            print(f"🎨 Vibe: {vibe_id}")
            return True
        except (TypeError, ValueError):
            print(f"⚠️ Invalid vibe: {requested!r}")
    elif action == 'refresh_plugins':
        animation = data.get('animation')
        if animation:
            print(f"🔄 Reload plugin: {animation}")
            manager.reload_animation(animation)
        else:
            print("🔄 Refresh all plugins")
            manager.refresh_plugins()
    elif action == 'puncture_hole':
        x = data.get('x')
        y = data.get('y')
        radius = data.get('radius')
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            print(f"💥 Hole requested at ({x:.1f}, {y:.1f})")
            manager.trigger_hole(float(x), float(y), radius)
        else:
            print("💥 Random hole requested")
            manager.trigger_random_hole()
    elif action == 'animation_interaction':
        try:
            return manager.dispatch_interaction(
                data.get('kind', 'primary'), data.get('x'), data.get('y'),
                data.get('strength', 1.0),
            )
        except (TypeError, ValueError) as exc:
            print(f"⚠️ Invalid animation interaction: {exc}")
            return False
    elif action == 'dpad':
        direction = (data.get('direction') or '').lower().replace('_', '-')
        if manager.current_animation and hasattr(manager.current_animation, 'handle_input'):
            manager.current_animation.handle_input(direction)
        else:
            print(f"⚠️ D-pad input ignored (no handler): {direction}")
    elif action == 'painter_set_frame':
        frame_data = data.get('frame_data')
        encoded = data.get('frame_data_encoded')
        if isinstance(encoded, str) and encoded:
            frame_data = decode_frame_data(encoded)
        if isinstance(frame_data, list):
            print(f"🖌️  Painter set frame ({len(frame_data)} pixels)")
            manager.set_painter_frame(frame_data)
        else:
            print("⚠️ painter_set_frame ignored: missing frame payload")
    elif action == 'painter_apply_updates':
        updates = data.get('updates') or []
        if isinstance(updates, list):
            applied = manager.apply_painter_updates(updates)
            print(f"🖌️  Painter updates: {len(updates)} ({'applied' if applied else 'no changes'})")
        else:
            print("⚠️ painter_apply_updates ignored: updates must be a list")
    elif action == 'painter_clear':
        print("🧽 Painter clear requested")
        manager.clear_painter_frame()
    else:
        print(f"⚠️ Unknown action: {action}")
    return False


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
        release_id=args.release_id,
    )

    print("🌐 Web/Preview mode")
    print(f"  Control file: {args.control_file}")
    print(f"  Status file : {args.status_file}")
    print(f"  URL: http://{args.host}:{args.port}")
    print(f"  Dashboard: http://{args.host}:{args.port}/")
    print(f"  Control:   http://{args.host}:{args.port}/control")
    print(f"  Painter:   http://{args.host}:{args.port}/painter")
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
    parser.add_argument('--presets-dir', default='presets/animations',
                        help='Directory for restart-state animation presets')
    parser.add_argument('--saved-state-file', default='run_state/before_deploy.json',
                        help='Path to the persisted restart animation state')
    parser.add_argument('--release-id', default=None,
                        help='Verified immutable release identity supplied by production startup')
    parser.add_argument('--strips', type=int, default=default_strip_count(),
                        help=f'Number of LED strips (default: {default_strip_count()})')
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
    parser.add_argument('--bus', type=int, default=0,
                        help='SPI bus number (default: 0)')
    parser.add_argument('--device', type=int, default=0,
                        help='SPI device number (default: 0)')
    parser.add_argument('--spi-speed', type=int, default=20000000,
                        help='SPI speed in Hz (default: 20000000)')
    parser.add_argument('--controller-debug', action='store_true',
                        help='Enable LED controller debug output')
    parser.add_argument('--target-fps', type=int, default=200,
                        help=f'Target animation FPS (default: 200; tuned for {DEFAULT_LEDS_PER_STRIP}-pixel WS2812 strips)')
    parser.add_argument('--brightness', type=int, default=50,
                        help='Global hardware brightness 0-255 (default: 50)')
    parser.add_argument('--animation-speed-scale', type=float, default=DEFAULT_ANIMATION_SPEED_SCALE,
                        help=f'Multiplier applied to animation speed parameters (default: {DEFAULT_ANIMATION_SPEED_SCALE})')
    parser.add_argument('--poll-interval', type=float, default=0.05,
                        help='Seconds between control-file polls (controller mode)')
    parser.add_argument('--status-interval', type=float, default=0.5,
                        help='Seconds between status writes (controller mode)')

    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    try:
        active_release_id = resolve_active_release_id(project_root)
    except RuntimeError as exc:
        parser.error(str(exc))
    if args.release_id is not None:
        if RELEASE_ID_PATTERN.fullmatch(args.release_id) is None:
            parser.error('--release-id must be a lowercase SHA-256 digest')
        if args.release_id != active_release_id:
            parser.error('--release-id does not match the active current release')
    args.release_id = active_release_id

    print("🎨 LED Grid Animation Server")
    print("=" * 40)
    print(f"Mode: {args.mode}")
    print(f"Animations: {args.animations_dir}/")
    print(f"Layout: {args.strips} strips × {args.leds_per_strip} LEDs = {args.strips * args.leds_per_strip} total")
    print()

    try:
        if args.mode == 'controller':
            print(f"SPI: /dev/spidev{args.bus}.{args.device} @ {args.spi_speed/1000000:.1f} MHz")
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
