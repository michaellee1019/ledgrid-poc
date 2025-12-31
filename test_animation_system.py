#!/usr/bin/env python3
"""
Test script for the animation system without hardware dependencies
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP

# Add current directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

# Mock missing modules for testing
sys.modules['spidev'] = MagicMock()
sys.modules['flask'] = MagicMock()
sys.modules['werkzeug'] = MagicMock()
sys.modules['werkzeug.utils'] = MagicMock()


class MockLEDController:
    """Mock LED controller for testing without hardware"""
    
    def __init__(self, strips=DEFAULT_STRIP_COUNT, leds_per_strip=DEFAULT_LEDS_PER_STRIP, **kwargs):
        self.strips = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip
        print(f"🔧 Mock LED Controller: {strips} strips × {leds_per_strip} LEDs = {self.total_leds} total")
    
    def set_all_pixels(self, pixel_data):
        """Mock set all pixels"""
        if len(pixel_data) != self.total_leds:
            print(f"⚠️  Warning: Expected {self.total_leds} pixels, got {len(pixel_data)}")
        # Just print a sample of the data
        if pixel_data:
            r, g, b = pixel_data[0]
            print(f"📊 Frame: First pixel = RGB({r}, {g}, {b})")
    
    def show(self):
        """Mock show"""
        pass
    
    def clear(self):
        """Mock clear"""
        print("🧹 Cleared LEDs")


def test_plugin_system():
    """Test the plugin loading system"""
    print("\n🧪 Testing Plugin System")
    print("=" * 40)
    
    try:
        from animation.core.plugin_loader import AnimationPluginLoader
        
        # Create plugin loader
        loader = AnimationPluginLoader()
        
        # Discover plugins
        plugins = loader.discover_plugins()
        print(f"✓ Found {len(plugins)} plugins:")
        
        for name, plugin_class in plugins.items():
            print(f"  - {name}: {plugin_class.ANIMATION_NAME}")
            print(f"    Author: {plugin_class.ANIMATION_AUTHOR}")
            print(f"    Description: {plugin_class.ANIMATION_DESCRIPTION}")
        
        return plugins
        
    except Exception as e:
        print(f"✗ Plugin system test failed: {e}")
        import traceback
        traceback.print_exc()
        return {}


def test_animation_manager():
    """Test the animation manager"""
    print("\n🎮 Testing Animation Manager")
    print("=" * 40)
    
    try:
        from animation.core.manager import AnimationManager
        
        # Create mock controller
        controller = MockLEDController()
        
        # Create animation manager
        manager = AnimationManager(controller)
        
        # List animations
        animations = manager.list_animations()
        print(f"✓ Animation manager created with {len(animations)} animations")
        
        if animations:
            # Test starting an animation
            first_animation = animations[0]['plugin_name']
            print(f"🎬 Testing animation: {first_animation}")
            
            success = manager.start_animation(first_animation)
            if success:
                print("✓ Animation started successfully")
                
                # Let it run for a few frames
                time.sleep(0.5)
                
                # Get status
                status = manager.get_current_status()
                print(f"📊 Status: {status['current_animation']} @ {status['actual_fps']:.1f} FPS")
                
                # Stop animation
                manager.stop_animation()
                print("✓ Animation stopped")
            else:
                print("✗ Failed to start animation")
        
        return manager
        
    except Exception as e:
        print(f"✗ Animation manager test failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_web_interface():
    """Test web interface creation"""
    print("\n🌐 Testing Web Interface")
    print("=" * 40)
    
    try:
        # Import the web interface (mocks already set up)
        from web.app import AnimationWebInterface
        from animation.core.manager import AnimationManager
        from ipc.control_channel import FileControlChannel
        
        # Create mock controller, manager, and control channel
        controller = MockLEDController()
        manager = AnimationManager(controller)
        channel = FileControlChannel(control_path="run_state/test_control.json",
                                      status_path="run_state/test_status.json")
        
        # Create web interface
        web_interface = AnimationWebInterface(channel, manager, host='127.0.0.1', port=5001)
        
        print("✓ Web interface created successfully")
        print(f"  Host: {web_interface.host}")
        print(f"  Port: {web_interface.port}")
        print(f"  Upload folder: {web_interface.app.config['UPLOAD_FOLDER']}")
        
        return web_interface
        
    except Exception as e:
        print(f"✗ Web interface test failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Run all tests"""
    print("🧪 LED Animation System Test Suite")
    print("=" * 50)
    
    # Test plugin system
    plugins = test_plugin_system()
    
    # Test animation manager
    manager = test_animation_manager()
    
    # Test web interface
    web_interface = test_web_interface()
    
    # Summary
    print("\n📋 Test Summary")
    print("=" * 40)
    print(f"Plugins discovered: {len(plugins)}")
    print(f"Animation manager: {'✓' if manager else '✗'}")
    print(f"Web interface: {'✓' if web_interface else '✗'}")
    
    if plugins and manager and web_interface:
        print("\n🎉 All tests passed! System is ready.")
        print("\nTo start the full system:")
        print("  1. Install dependencies: pip install flask spidev")
        print("  2. Run: python scripts/start_server.py")
        print("  3. Open: http://localhost:5000/")
    else:
        print("\n⚠️  Some tests failed. Check the errors above.")


if __name__ == '__main__':
    main()
