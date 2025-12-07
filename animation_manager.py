#!/usr/bin/env python3
"""
Animation Manager Service

Coordinates between LED controller, animation plugins, and web interface.
Handles animation switching, parameter updates, and frame generation.
"""

import time
import threading
import traceback
from typing import Optional, Dict, Any, List
from pathlib import Path

from animation_system import AnimationBase, StatefulAnimationBase, AnimationPluginLoader

# Try to import the real LED controller, fall back to mock for testing
try:
    from led_controller_spi import LEDController
except ImportError:
    # Mock LED controller for testing without SPI hardware
    class LEDController:
        def __init__(self, strips=7, leds_per_strip=20, **kwargs):
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


class AnimationManager:
    """Manages animation playback and plugin system"""
    
    def __init__(self, controller: LEDController, plugins_dir: str = "animations"):
        """
        Initialize animation manager
        
        Args:
            controller: LED controller instance
            plugins_dir: Directory containing animation plugins
        """
        self.controller = controller
        self.plugin_loader = AnimationPluginLoader(plugins_dir)
        
        # Animation state
        self.current_animation: Optional[AnimationBase] = None
        self.current_animation_name: Optional[str] = None
        self.is_running = False
        self.target_fps = 50
        self.frame_count = 0
        self.start_time = 0.0
        
        # Threading
        self.animation_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        
        # Performance tracking
        self.fps_history = []
        self.last_fps_update = 0.0

        # Current frame data for web interface
        self.current_frame_data = []
        self.frame_data_lock = threading.Lock()

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

            print(f"🔍 Animation instance created: {type(self.current_animation)}")
            print(f"🔍 Is StatefulAnimationBase? {isinstance(self.current_animation, StatefulAnimationBase)}")

            # Start animation
            self.current_animation.start()
            self.is_running = True
            self.stop_event.clear()
            self.frame_count = 0
            self.start_time = time.time()

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

            # Stop the animation (stateful animations handle their own threads)
            if self.current_animation:
                self.current_animation.stop()
                self.current_animation.cleanup()
                self.current_animation = None

            self.current_animation_name = None

            # Clear LEDs
            self.controller.clear()

            print("✓ Animation stopped")
    
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
            'uptime': time.time() - self.start_time if self.is_running else 0,
            'target_fps': self.target_fps,
            'actual_fps': self._calculate_fps(),
            'led_info': {
                'total_leds': self.controller.total_leds,
                'strip_count': self.controller.strip_count,
                'leds_per_strip': self.controller.leds_per_strip
            }
        }
        
        if self.current_animation:
            status['animation_info'] = self.current_animation.get_info()
        
        return status

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

        try:
            # Create a temporary instance of the animation
            temp_animation = animation_class(self.controller, {})

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
        frame_time = 1.0 / self.target_fps
        
        while self.is_running and not self.stop_event.is_set():
            loop_start = time.time()
            
            try:
                if self.current_animation:
                    # Generate frame
                    time_elapsed = time.time() - self.start_time
                    colors = self.current_animation.generate_frame(time_elapsed, self.frame_count)

                    # Store frame data for web interface
                    with self.frame_data_lock:
                        self.current_frame_data = list(colors)

                    # Send to LEDs
                    self.controller.set_all_pixels(colors)

                    self.frame_count += 1

                    # Update FPS tracking
                    self._update_fps_tracking()
                
            except Exception as e:
                print(f"✗ Animation loop error: {e}")
                traceback.print_exc()
                break
            
            # Sleep to maintain target FPS
            loop_time = time.time() - loop_start
            sleep_time = max(0, frame_time - loop_time)
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    def _update_fps_tracking(self):
        """Update FPS calculation"""
        now = time.time()
        if now - self.last_fps_update >= 1.0:  # Update every second
            if self.start_time > 0:
                elapsed = now - self.start_time
                current_fps = self.frame_count / elapsed if elapsed > 0 else 0
                self.fps_history.append(current_fps)
                
                # Keep only last 10 seconds of history
                if len(self.fps_history) > 10:
                    self.fps_history.pop(0)
            
            self.last_fps_update = now
    
    def _calculate_fps(self) -> float:
        """Calculate current FPS"""
        if not self.fps_history:
            return 0.0
        return sum(self.fps_history) / len(self.fps_history)
    
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
