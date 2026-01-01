#!/usr/bin/env python3
"""
Demo of the animation system working with mock hardware
"""

import sys
import time
import threading
from pathlib import Path

# Add repo root to Python path
repo_root = Path(__file__).resolve()
while repo_root != repo_root.parent and not (repo_root / "animation").exists():
    repo_root = repo_root.parent
sys.path.insert(0, str(repo_root))

from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


class MockLEDController:
    """Mock LED controller that simulates the real hardware interface"""
    
    def __init__(self, strips=DEFAULT_STRIP_COUNT, leds_per_strip=DEFAULT_LEDS_PER_STRIP):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip
        self.current_frame = [(0, 0, 0)] * self.total_leds
        print(f"🔧 Mock LED Controller initialized: {strips} strips × {leds_per_strip} LEDs")
    
    def set_all_pixels(self, pixel_data):
        """Set all pixels at once"""
        if len(pixel_data) == self.total_leds:
            self.current_frame = pixel_data
            # Show a sample of the frame
            sample_pixels = pixel_data[:5]  # First 5 pixels
            colors = [f"RGB({r},{g},{b})" for r, g, b in sample_pixels]
            print(f"📊 Frame update: {', '.join(colors)}...")
        else:
            print(f"⚠️  Warning: Expected {self.total_leds} pixels, got {len(pixel_data)}")
    
    def show(self):
        """Display the current frame"""
        pass
    
    def clear(self):
        """Clear all LEDs"""
        self.current_frame = [(0, 0, 0)] * self.total_leds
        print("🧹 LEDs cleared")


def demo_animation(animation_class, controller, duration=5.0):
    """Demo an animation for a specified duration"""
    print(f"\n🎬 Running {animation_class.ANIMATION_NAME} for {duration}s...")
    print(f"   {animation_class.ANIMATION_DESCRIPTION}")
    
    # Create animation instance
    animation = animation_class(controller)
    
    # Get parameter info
    schema = animation.get_parameter_schema()
    if schema:
        print(f"   Parameters: {list(schema.keys())}")
    
    # Run animation loop
    start_time = time.time()
    frame_count = 0
    
    try:
        while time.time() - start_time < duration:
            time_elapsed = time.time() - start_time
            
            # Generate frame
            frame = animation.generate_frame(time_elapsed, frame_count)
            
            # Send to controller
            controller.set_all_pixels(frame)
            controller.show()
            
            frame_count += 1
            time.sleep(0.05)  # ~20 FPS for demo
            
    except KeyboardInterrupt:
        print("   Stopped by user")
    
    fps = frame_count / duration
    print(f"   Completed: {frame_count} frames @ {fps:.1f} FPS")


def main():
    """Run animation demos"""
    print("🎨 LED Animation System Demo")
    print("=" * 50)
    
    # Create mock controller
    controller = MockLEDController(strips=DEFAULT_STRIP_COUNT, leds_per_strip=DEFAULT_LEDS_PER_STRIP)
    
    # Load plugins
    loader = AnimationPluginLoader()
    plugins = loader.load_all_plugins()
    
    print(f"\n📦 Loaded {len(plugins)} animation plugins:")
    for name, plugin_class in plugins.items():
        print(f"  - {plugin_class.ANIMATION_NAME} ({name})")
    
    # Demo each animation
    for name, plugin_class in plugins.items():
        demo_animation(plugin_class, controller, duration=3.0)
        time.sleep(1)  # Pause between animations
    
    print("\n🎉 Demo completed!")
    print("\nTo run the full web interface:")
    print("  1. Install Flask: pip install flask")
    print("  2. Start server: python scripts/start_server.py")
    print("  3. Open browser: http://localhost:5000/")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Demo stopped by user")
