#!/usr/bin/env python3
"""
Simple test for just the plugin system without dependencies
"""

import sys
from pathlib import Path

# Add repo root to Python path
repo_root = Path(__file__).resolve()
while repo_root != repo_root.parent and not (repo_root / "animation").exists():
    repo_root = repo_root.parent
sys.path.insert(0, str(repo_root))

# Local imports (after sys.path update)
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


def test_plugin_loading():
    """Test loading animation plugins"""
    print("🧪 Testing Animation Plugin Loading")
    print("=" * 40)
    
    try:
        from animation.core.plugin_loader import AnimationPluginLoader
        
        # Create plugin loader
        loader = AnimationPluginLoader()
        
        # Scan for plugin files
        plugin_names = loader.scan_plugins()
        print(f"✓ Found {len(plugin_names)} plugin files: {plugin_names}")
        
        # Load all plugins
        plugins = loader.load_all_plugins()
        print(f"✓ Loaded {len(plugins)} plugins successfully")
        
        # Test each plugin
        for name, plugin_class in plugins.items():
            print(f"\n📦 Plugin: {name}")
            print(f"  Class: {plugin_class.__name__}")
            print(f"  Name: {plugin_class.ANIMATION_NAME}")
            print(f"  Author: {plugin_class.ANIMATION_AUTHOR}")
            print(f"  Description: {plugin_class.ANIMATION_DESCRIPTION}")
            print(f"  Version: {plugin_class.ANIMATION_VERSION}")
            
            # Test parameter schema
            try:
                # Create a mock controller for testing
                class MockController:
                    def __init__(self):
                        self.strips = DEFAULT_STRIP_COUNT
                        self.leds_per_strip = DEFAULT_LEDS_PER_STRIP
                
                mock_controller = MockController()
                instance = plugin_class(mock_controller)
                schema = instance.get_parameter_schema()
                print(f"  Parameters: {list(schema.keys())}")
                
                # Test frame generation
                frame = instance.generate_frame(0.0, 0)
                expected_pixels = DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP
                if len(frame) == expected_pixels:
                    print(f"  ✓ Frame generation: {len(frame)} pixels")
                else:
                    print(f"  ⚠️  Frame generation: {len(frame)} pixels (expected {expected_pixels})")
                    
            except Exception as e:
                print(f"  ✗ Error testing plugin: {e}")
        
        return len(plugins)
        
    except Exception as e:
        print(f"✗ Plugin loading failed: {e}")
        import traceback
        traceback.print_exc()
        return 0


def test_plugin_creation():
    """Test creating a new plugin"""
    print("\n🔧 Testing Plugin Creation")
    print("=" * 40)
    
    try:
        from animation.core.plugin_loader import AnimationPluginLoader
        
        # Create plugin loader
        loader = AnimationPluginLoader()
        
        # Test plugin code
        test_code = '''#!/usr/bin/env python3
"""
Test Animation Plugin
"""

import math
from typing import List, Tuple, Dict, Any
from animation import AnimationBase


class TestAnimation(AnimationBase):
    """Test animation for validation"""
    
    ANIMATION_NAME = "Test Animation"
    ANIMATION_DESCRIPTION = "Simple test animation"
    ANIMATION_AUTHOR = "Test System"
    ANIMATION_VERSION = "1.0"
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> List[Tuple[int, int, int]]:
        """Generate test frame"""
        strip_count, leds_per_strip = self.get_strip_info()
        
        # Simple red color
        pixel_colors = []
        for strip in range(strip_count):
            for led in range(leds_per_strip):
                pixel_colors.append((255, 0, 0))  # Red
        
        return pixel_colors
'''
        
        # Save test plugin
        success = loader.save_plugin('test_animation', test_code)
        if success:
            print("✓ Test plugin saved successfully")
            
            # Try to load it
            plugin_class = loader.load_plugin('test_animation')
            if plugin_class:
                print(f"✓ Test plugin loaded: {plugin_class.ANIMATION_NAME}")
                return True
            else:
                print("✗ Failed to load test plugin")
                return False
        else:
            print("✗ Failed to save test plugin")
            return False
            
    except Exception as e:
        print(f"✗ Plugin creation test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run plugin tests"""
    print("🧪 Animation Plugin Test Suite")
    print("=" * 50)
    
    # Test plugin loading
    plugin_count = test_plugin_loading()
    
    # Test plugin creation
    creation_success = test_plugin_creation()
    
    # Summary
    print("\n📋 Test Summary")
    print("=" * 40)
    print(f"Plugins loaded: {plugin_count}")
    print(f"Plugin creation: {'✓' if creation_success else '✗'}")
    
    if plugin_count > 0 and creation_success:
        print("\n🎉 Plugin system is working correctly!")
        print("\nNext steps:")
        print("  1. Install Flask: pip install flask")
        print("  2. Install spidev: pip install spidev (on Raspberry Pi)")
        print("  3. Run full system: python scripts/start_server.py")
    else:
        print("\n⚠️  Some plugin tests failed.")


if __name__ == '__main__':
    main()
