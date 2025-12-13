#!/usr/bin/env python3
"""
Animation Manager Service

Coordinates between LED controller, animation plugins, and web interface.
Handles animation switching, parameter updates, and frame generation.
"""

import hashlib
import time
import threading
import traceback
from collections import deque
from typing import Optional, Dict, Any, List
from pathlib import Path

from animation_system import AnimationBase, StatefulAnimationBase, AnimationPluginLoader
from led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP

# Try to import the real LED controller, fall back to mock for testing
try:
    from led_controller_spi_multi import MultiDeviceLEDController as LEDController
except ImportError:
    try:
        from led_controller_spi import LEDController
    except ImportError:
        # Mock LED controller for testing without SPI hardware
        class LEDController:
            def __init__(self, strips=DEFAULT_STRIP_COUNT, leds_per_strip=DEFAULT_LEDS_PER_STRIP, **kwargs):
                self.strip_count = strips
                self.leds_per_strip = leds_per_strip
                self.total_leds = strips * leds_per_strip
                self.debug = kwargs.get('debug', False)
                print(f"🔧 Mock LED Controller: {strips} strips × {leds_per_strip} LEDs = {self.total_leds} total")

            def set_all_pixels(self, pixel_data):
                """Mock set all pixels"""
                if self.debug and len(pixel_data) > 0:
                    r, g, b = pixel_data[0]
                    print(f"📊 Frame: First pixel = RGB({r}, {g}, {b})")

            def show(self):
                """Mock show"""
                pass

            def clear(self):
                """Mock clear"""
                if self.debug:
                    print("🧹 Cleared LEDs")

            def configure(self):
                """Mock configure"""
                pass


class PreviewLEDController:
    """
    Lightweight controller used for preview generation.
    Mirrors the dimensions of the real controller but performs no I/O so preview
    requests can never block or interfere with the SPI device.
    """
    def __init__(self, strips: int, leds_per_strip: int, debug: bool = False):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip
        self.debug = debug

    def set_all_pixels(self, *_args, **_kwargs):
        pass

    def set_pixel(self, *_args, **_kwargs):
        pass

    def set_range(self, *_args, **_kwargs):
        pass

    def set_brightness(self, *_args, **_kwargs):
        pass

    def show(self, *_args, **_kwargs):
        pass

    def clear(self, *_args, **_kwargs):
        pass

    def configure(self, *_args, **_kwargs):
        pass


class AnimationManager:
    """Manages animation playback and plugin system"""

    # Only ship with a small, known-good set of animations
    ALLOWED_PLUGINS = {
        "rainbow",
        "emoji",
        "sparkle",
        "fluid_tank",
        "flame_burst",
        "simple_test",
    }
    
    def __init__(self, controller: LEDController, plugins_dir: str = "animations",
                 animation_speed_scale: float = 1.0):
        """
        Initialize animation manager
        
        Args:
            controller: LED controller instance
            plugins_dir: Directory containing animation plugins
            animation_speed_scale: Multiplier applied to each animation's speed parameter at start
        """
        self.controller = controller
        self.plugin_loader = AnimationPluginLoader(
            plugins_dir, allowed_plugins=self.ALLOWED_PLUGINS
        )
        
        # Animation state
        self.current_animation: Optional[AnimationBase] = None
        self.current_animation_name: Optional[str] = None
        self.current_animation_hash: Optional[str] = None
        self.is_running = False
        self.target_fps = 40
        self.frame_count = 0
        self.start_time = 0.0
        self.animation_speed_scale = animation_speed_scale
        
        # Threading
        self.animation_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        
        # Performance tracking
        self.frame_timestamps = deque(maxlen=240)  # ~4 seconds at 60 FPS
        self.perf_samples = deque(maxlen=300)
        self.perf_lock = threading.Lock()
        self._last_perf_sample: Dict[str, float] = {}

        # Current frame data for web interface
        self.current_frame_data = []
        self.frame_data_lock = threading.Lock()

        # Preview controller avoids hitting the real SPI device during previews
        self.preview_controller = PreviewLEDController(
            self.controller.strip_count,
            self.controller.leds_per_strip,
            getattr(self.controller, 'debug', False)
        )

        # Load all plugins on startup
        self.refresh_plugins()
    
    def refresh_plugins(self) -> Dict[str, Any]:
        """Reload all animation plugins"""
        try:
            plugins = self.plugin_loader.load_all_plugins()
            print(f"✓ Loaded {len(plugins)} animation plugins")
            return {name: self.plugin_loader.get_plugin_info(name) for name in plugins.keys()}
        except Exception as e:
            print(f"✗ Error loading plugins: {e}")
            traceback.print_exc()
            return {}

    def _apply_speed_scale(self):
        """Apply global speed scaling to the current animation if supported"""
        if not self.current_animation:
            return
        if not hasattr(self.current_animation, "params"):
            return
        if 'speed' not in self.current_animation.params:
            return
        base_speed = self.current_animation.params['speed']
        scaled_speed = base_speed * self.animation_speed_scale
        # Prevent negative or zero speeds
        if scaled_speed <= 0:
            scaled_speed = base_speed
        self.current_animation.update_parameters({'speed': scaled_speed})
    
    def list_animations(self) -> List[Dict[str, Any]]:
        """Get list of available animations with metadata"""
        animations = []
        for plugin_name in self.plugin_loader.list_plugins():
            info = self.plugin_loader.get_plugin_info(plugin_name)
            if info:
                animations.append(info)
        return animations
    
    def get_animation_info(self, animation_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed info about a specific animation"""
        return self.plugin_loader.get_plugin_info(animation_name)
    
    def start_animation(self, animation_name: str, config: Dict[str, Any] = None) -> bool:
        """
        Start playing an animation
        
        Args:
            animation_name: Name of animation plugin to start
            config: Animation configuration parameters
            
        Returns:
            True if started successfully
        """
        try:
            # Stop current animation if running
            self.stop_animation()
            
            # Get animation class
            animation_class = self.plugin_loader.get_plugin(animation_name)
            if animation_class is None:
                print(f"✗ Animation not found: {animation_name}")
                return False
            
            # Create animation instance
            self.current_animation = animation_class(self.controller, config or {})
            self.current_animation_name = animation_name
            self.current_animation_hash = self._compute_animation_hash(animation_name)

            print(f"🔍 Animation instance created: {type(self.current_animation)}")
            print(f"🔍 Is StatefulAnimationBase? {isinstance(self.current_animation, StatefulAnimationBase)}")

            self._apply_speed_scale()

            # Ensure controller is configured before frames start flowing
            if hasattr(self.controller, "configure"):
                try:
                    self.controller.configure()
                except Exception as controller_error:
                    print(f"⚠️ Controller configure failed: {controller_error}")

            # Start animation
            self.current_animation.start()
            self.is_running = True
            self.stop_event.clear()
            self.frame_count = 0
            self.frame_timestamps.clear()
            self.start_time = time.perf_counter()

            # Check if this is a stateful animation
            if isinstance(self.current_animation, StatefulAnimationBase):
                # Stateful animations manage their own threads and timing
                print(f"✓ Started stateful animation: {animation_name}")
            else:
                # Frame-based animations need the animation loop
                self.animation_thread = threading.Thread(target=self._animation_loop, daemon=True)
                self.animation_thread.start()
                print(f"✓ Started frame-based animation: {animation_name}")

            return True
            
        except Exception as e:
            print(f"✗ Failed to start animation {animation_name}: {e}")
            traceback.print_exc()
            return False
    
    def stop_animation(self):
        """Stop current animation"""
        if self.is_running:
            self.is_running = False
            self.stop_event.set()

            # Stop frame-based animation thread if it exists
            if self.animation_thread and self.animation_thread.is_alive():
                self.animation_thread.join(timeout=1.0)
            self.animation_thread = None

            # Stop the animation (stateful animations handle their own threads)
            if self.current_animation:
                self.current_animation.stop()
                self.current_animation.cleanup()
                self.current_animation = None

            self.current_animation_name = None
            self.frame_timestamps.clear()
            with self.frame_data_lock:
                self.current_frame_data = []

            # Clear LEDs
            self.controller.clear()

            print("✓ Animation stopped")
        
        self.current_animation_hash = None
    
    def update_animation_parameters(self, params: Dict[str, Any]) -> bool:
        """Update current animation parameters in real-time"""
        if self.current_animation:
            try:
                self.current_animation.update_parameters(params)
                print(f"✓ Updated animation parameters: {params}")
                return True
            except Exception as e:
                print(f"✗ Failed to update parameters: {e}")
                return False
        return False
    
    def get_current_status(self) -> Dict[str, Any]:
        """Get current animation status and performance info"""
        status = {
            'is_running': self.is_running,
            'current_animation': self.current_animation_name,
                'frame_count': self.frame_count,
                'uptime': (time.perf_counter() - self.start_time) if self.is_running else 0,
                'target_fps': self.target_fps,
                'animation_speed_scale': self.animation_speed_scale,
                'actual_fps': self._calculate_fps(),
                'animation_hash': self.current_animation_hash,
                'led_info': {
                    'total_leds': self.controller.total_leds,
                    'strip_count': self.controller.strip_count,
                    'leds_per_strip': self.controller.leds_per_strip
                }
        }
        
        status['animation_info'] = None
        status['animation_stats'] = {}
        if self.current_animation:
            status['animation_info'] = self.current_animation.get_info()
            try:
                stats = self.current_animation.get_runtime_stats()
                if isinstance(stats, dict):
                    status['animation_stats'] = stats
            except Exception as exc:
                status['animation_stats'] = {'error': str(exc)}

        performance = self._get_perf_summary()
        if performance:
            status['performance'] = performance
        
        return status

    def trigger_random_hole(self):
        """Request the current animation to spawn a random puncture if supported."""
        if not self.current_animation:
            return False
        if hasattr(self.current_animation, 'trigger_random_hole'):
            try:
                self.current_animation.trigger_random_hole()
                return True
            except Exception as exc:
                print(f"⚠️ Failed to trigger hole: {exc}")
        return False

    def _compute_animation_hash(self, animation_name: str) -> Optional[str]:
        path = self.plugin_loader.get_plugin_file(animation_name)
        if not path:
            return None
        try:
            hasher = hashlib.sha256()
            with open(path, 'rb') as fh:
                for chunk in iter(lambda: fh.read(8192), b''):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except OSError as exc:
            print(f"⚠️ Failed to hash animation file {path}: {exc}")
            return None

    def get_current_frame(self) -> Dict[str, Any]:
        """Get current animation frame data for web rendering"""
        with self.frame_data_lock:
            frame_data = list(self.current_frame_data)

        return {
            'frame_data': frame_data,
            'led_info': {
                'total_leds': self.controller.total_leds,
                'strip_count': self.controller.strip_count,
                'leds_per_strip': self.controller.leds_per_strip
            },
            'is_running': self.is_running,
            'frame_count': self.frame_count,
            'current_animation': self.current_animation_name if self.is_running else None,
            'timestamp': time.time()
        }

    def get_animation_preview(self, animation_name: str) -> Dict[str, Any]:
        """Get a preview frame from a specific animation without starting it"""
        if animation_name not in self.plugin_loader.loaded_plugins:
            raise ValueError(f"Animation '{animation_name}' not found")

        animation_class = self.plugin_loader.loaded_plugins[animation_name]

        # Keep preview controller dimensions in sync with the real controller
        self.preview_controller.strip_count = self.controller.strip_count
        self.preview_controller.leds_per_strip = self.controller.leds_per_strip
        self.preview_controller.total_leds = self.controller.total_leds

        try:
            # Create a temporary instance of the animation
            temp_animation = animation_class(self.preview_controller, {})

            # Generate a sample frame
            if hasattr(temp_animation, 'generate_frame'):
                # For frame-based animations
                frame_data = temp_animation.generate_frame(time_elapsed=0.0, frame_count=0)
                if frame_data is None:
                    frame_data = [(0, 0, 0)] * self.controller.total_leds
            else:
                # For step-based animations, run a few steps
                temp_animation.reset()
                for _ in range(5):  # Run a few steps to get interesting output
                    temp_animation.step()

                # Get the current state
                frame_data = [(0, 0, 0)] * self.controller.total_leds
                if hasattr(temp_animation, 'get_current_colors'):
                    frame_data = temp_animation.get_current_colors()

            frame_data = self._normalize_frame(frame_data)

            return {
                'frame_data': frame_data,
                'led_info': {
                    'total_leds': self.controller.total_leds,
                    'strip_count': self.controller.strip_count,
                    'leds_per_strip': self.controller.leds_per_strip
                },
                'is_running': False,
                'frame_count': 0,
                'current_animation': animation_name,
                'timestamp': time.time(),
                'preview': True
            }

        except Exception as e:
            print(f"Error generating preview for {animation_name}: {e}")
            # Return a default pattern
            return {
                'frame_data': [(50, 50, 50)] * self.controller.total_leds,  # Dim gray
                'led_info': {
                    'total_leds': self.controller.total_leds,
                    'strip_count': self.controller.strip_count,
                    'leds_per_strip': self.controller.leds_per_strip
                },
                'is_running': False,
                'frame_count': 0,
                'current_animation': animation_name,
                'timestamp': time.time(),
                'preview': True,
                'error': str(e)
            }

    def _animation_loop(self):
        """Main animation loop running in separate thread"""
        target_frame_time = 1.0 / max(1, int(self.target_fps) or 1)

        while self.is_running and not self.stop_event.is_set():
            loop_start = time.perf_counter()
            generate_duration = 0.0
            send_duration = 0.0
            show_duration = 0.0
            inline_show = getattr(self.controller, "inline_show", False)

            try:
                if not self.current_animation:
                    break

                # Generate frame
                time_elapsed = loop_start - self.start_time
                gen_start = time.perf_counter()
                colors = self.current_animation.generate_frame(time_elapsed, self.frame_count)
                frame = self._normalize_frame(colors)
                generate_duration = time.perf_counter() - gen_start

                # Store frame data for web interface
                with self.frame_data_lock:
                    self.current_frame_data = frame

                # Send to LEDs
                send_start = time.perf_counter()
                self.controller.set_all_pixels(frame)
                send_duration = time.perf_counter() - send_start

                # Some controllers need an explicit show; skip if controller handles it internally
                if not inline_show and hasattr(self.controller, "show"):
                    try:
                        show_start = time.perf_counter()
                        self.controller.show()
                        show_duration = time.perf_counter() - show_start
                    except Exception:
                        # Controllers that embed show inside set_all_pixels will ignore this
                        pass

                self.frame_count += 1

                # Update FPS tracking
                self._update_fps_tracking(loop_start)

            except Exception as e:
                print(f"✗ Animation loop error: {e}")
                traceback.print_exc()
                time.sleep(0.05)

            # Sleep to maintain target FPS
            loop_duration = time.perf_counter() - loop_start
            sleep_time = max(0.0, target_frame_time - loop_duration)
            if sleep_time > 0:
                time.sleep(sleep_time)

            self._record_perf_sample({
                'generate': generate_duration,
                'send': send_duration,
                'show': show_duration,
                'process': loop_duration,
                'sleep': sleep_time,
                'frame': loop_duration + sleep_time,
            })

    def _normalize_frame(self, colors: Optional[List[Any]]) -> List[Any]:
        """Ensure frame length matches the LED count and is always a list"""
        total_pixels = self.controller.total_leds

        if colors is None:
            return [(0, 0, 0)] * total_pixels

        frame = list(colors)

        if len(frame) < total_pixels:
            frame.extend([(0, 0, 0)] * (total_pixels - len(frame)))
        elif len(frame) > total_pixels:
            frame = frame[:total_pixels]

        return frame
    
    def _update_fps_tracking(self, timestamp: Optional[float] = None):
        """Record frame timestamps for FPS calculation"""
        now = timestamp if timestamp is not None else time.perf_counter()
        self.frame_timestamps.append(now)

        # Keep only a small window of timestamps to reflect current performance
        while self.frame_timestamps and (now - self.frame_timestamps[0]) > 5.0:
            self.frame_timestamps.popleft()
    
    def _calculate_fps(self) -> float:
        """Calculate current FPS"""
        if len(self.frame_timestamps) < 2:
            return 0.0
        duration = self.frame_timestamps[-1] - self.frame_timestamps[0]
        if duration <= 0:
            return 0.0
        return (len(self.frame_timestamps) - 1) / duration

    def _record_perf_sample(self, sample: Dict[str, float]):
        """Store per-frame timing samples for debugging"""
        with self.perf_lock:
            self.perf_samples.append(sample)
            self._last_perf_sample = sample

    def _get_perf_summary(self) -> Dict[str, Any]:
        """Summarize recent performance metrics"""
        with self.perf_lock:
            if not self.perf_samples:
                return {}

            count = len(self.perf_samples)
            totals = {key: 0.0 for key in ('generate', 'send', 'show', 'process', 'sleep', 'frame')}
            for sample in self.perf_samples:
                for key in totals.keys():
                    totals[key] += sample.get(key, 0.0)

            target_frame_ms = 1000.0 / max(1, float(self.target_fps or 1))
            summary = {
                'samples': count,
                'target_frame_ms': target_frame_ms,
                'controller_inline_show': bool(getattr(self.controller, "inline_show", False)),
            }

            for key, total in totals.items():
                summary[f'avg_{key}_ms'] = (total / count) * 1000.0

            if self._last_perf_sample:
                for key in totals.keys():
                    summary[f'last_{key}_ms'] = self._last_perf_sample.get(key, 0.0) * 1000.0

            return summary
    
    def save_animation(self, name: str, code: str) -> bool:
        """Save new animation plugin"""
        return self.plugin_loader.save_plugin(name, code)
    
    def reload_animation(self, name: str) -> bool:
        """Reload specific animation plugin"""
        try:
            self.plugin_loader.reload_plugin(name)
            return True
        except Exception as e:
            print(f"✗ Failed to reload animation {name}: {e}")
            return False
