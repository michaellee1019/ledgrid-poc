#!/usr/bin/env python3
"""
LED Animation Server Startup Script

Simple script to start the animation web interface with default settings.
"""

import argparse
import sys
from pathlib import Path

# Add current directory to Python path for imports
sys.path.insert(0, str(Path(__file__).parent))

from web_interface import create_app


def main():
    parser = argparse.ArgumentParser(description='LED Animation Server')
    
    # Web server options
    parser.add_argument('--host', default='0.0.0.0', 
                       help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=5000, 
                       help='Port to listen on (default: 5000)')
    parser.add_argument('--debug', action='store_true', 
                       help='Enable debug mode')
    
    # LED controller options
    parser.add_argument('--bus', type=int, default=0, 
                       help='SPI bus number (default: 0)')
    parser.add_argument('--device', type=int, default=0, 
                       help='SPI device number (default: 0)')
    parser.add_argument('--spi-speed', type=int, default=5000000, 
                       help='SPI speed in Hz (default: 5000000)')
    parser.add_argument('--strips', type=int, default=7, 
                       help='Number of LED strips (default: 7)')
    parser.add_argument('--leds-per-strip', type=int, default=20, 
                       help='LEDs per strip (default: 20)')
    parser.add_argument('--controller-debug', action='store_true', 
                       help='Enable LED controller debug output')
    
    # Animation options
    parser.add_argument('--animations-dir', default='animations', 
                       help='Directory containing animation plugins (default: animations)')
    parser.add_argument('--target-fps', type=int, default=50, 
                       help='Target animation FPS (default: 50)')
    
    args = parser.parse_args()
    
    print("🎨 LED Grid Animation Server")
    print("=" * 40)
    
    # Controller configuration
    controller_config = {
        'bus': args.bus,
        'device': args.device,
        'speed': args.spi_speed,
        'mode': 3,  # SPI mode 3 required for ESP32
        'strips': args.strips,
        'leds_per_strip': args.leds_per_strip,
        'debug': args.controller_debug
    }
    
    print(f"LED Configuration:")
    print(f"  SPI: /dev/spidev{args.bus}.{args.device} @ {args.spi_speed/1000000:.1f} MHz")
    print(f"  LEDs: {args.strips} strips × {args.leds_per_strip} = {args.strips * args.leds_per_strip} total")
    print(f"  Target FPS: {args.target_fps}")
    print(f"  Animations: {args.animations_dir}/")
    print()
    
    try:
        # Create web application
        web_interface = create_app(controller_config, host=args.host, port=args.port)

        # Set target FPS
        web_interface.animation_manager.target_fps = args.target_fps
        
        print(f"🌐 Web Interface:")
        print(f"  URL: http://{args.host}:{args.port}")
        print(f"  Dashboard: http://{args.host}:{args.port}/")
        print(f"  Control Panel: http://{args.host}:{args.port}/control")
        print(f"  Upload: http://{args.host}:{args.port}/upload")
        print()
        
        # Start server
        print("🚀 Starting server... (Press Ctrl+C to stop)")
        web_interface.run(debug=args.debug)
        
    except KeyboardInterrupt:
        print("\n👋 Server stopped by user")
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
