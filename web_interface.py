#!/usr/bin/env python3
"""
Web Interface for LED Animation Management

Flask-based web server for controlling animations, uploading plugins,
and adjusting parameters in real-time.
"""

import os
import json
import time
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from werkzeug.utils import secure_filename
from pathlib import Path

from animation_manager import AnimationManager

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


class AnimationWebInterface:
    """Web interface for animation management"""
    
    def __init__(self, animation_manager: AnimationManager, host='0.0.0.0', port=5000):
        """
        Initialize web interface
        
        Args:
            animation_manager: AnimationManager instance
            host: Host to bind to
            port: Port to listen on
        """
        self.animation_manager = animation_manager
        self.host = host
        self.port = port
        
        # Create Flask app
        self.app = Flask(__name__)
        self.app.secret_key = 'led-grid-secret-key-change-in-production'
        
        # Configure upload settings
        self.app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max file size
        self.app.config['UPLOAD_FOLDER'] = 'animations'
        
        # Ensure upload directory exists
        Path(self.app.config['UPLOAD_FOLDER']).mkdir(exist_ok=True)
        
        # Register routes
        self._register_routes()
    
    def _register_routes(self):
        """Register Flask routes"""
        
        @self.app.route('/')
        def index():
            """Main dashboard"""
            animations = self.animation_manager.list_animations()
            status = self.animation_manager.get_current_status()
            return render_template('index.html', animations=animations, status=status)
        
        @self.app.route('/api/animations')
        def api_list_animations():
            """API: Get list of available animations"""
            animations = self.animation_manager.list_animations()
            return jsonify(animations)
        
        @self.app.route('/api/animations/<animation_name>')
        def api_get_animation(animation_name):
            """API: Get detailed info about specific animation"""
            info = self.animation_manager.get_animation_info(animation_name)
            if info:
                return jsonify(info)
            return jsonify({'error': 'Animation not found'}), 404
        
        @self.app.route('/api/start/<animation_name>', methods=['POST'])
        def api_start_animation(animation_name):
            """API: Start an animation"""
            config = request.get_json() or {}
            success = self.animation_manager.start_animation(animation_name, config)
            return jsonify({'success': success})
        
        @self.app.route('/api/stop', methods=['POST'])
        def api_stop_animation():
            """API: Stop current animation"""
            self.animation_manager.stop_animation()
            return jsonify({'success': True})
        
        @self.app.route('/api/status')
        def api_get_status():
            """API: Get current status"""
            status = self.animation_manager.get_current_status()
            return jsonify(status)

        @self.app.route('/api/frame')
        def api_get_frame():
            """API: Get current animation frame data"""
            frame_data = self.animation_manager.get_current_frame()
            return jsonify(frame_data)

        @self.app.route('/api/preview/<animation_name>')
        def api_get_preview(animation_name):
            """API: Get preview frame data for a specific animation"""
            try:
                # Get a sample frame from the animation without starting it
                preview_data = self.animation_manager.get_animation_preview(animation_name)
                return jsonify(preview_data)
            except Exception as e:
                return jsonify({
                    'error': f'Failed to get preview for {animation_name}: {str(e)}',
                    'frame_data': [],
                    'led_info': {
                        'total_leds': self.animation_manager.controller.total_leds,
                        'strip_count': self.animation_manager.controller.strip_count,
                        'leds_per_strip': self.animation_manager.controller.leds_per_strip
                    },
                    'is_running': False,
                    'frame_count': 0,
                    'timestamp': time.time()
                }), 500
        
        @self.app.route('/api/parameters', methods=['POST'])
        def api_update_parameters():
            """API: Update animation parameters"""
            params = request.get_json() or {}
            success = self.animation_manager.update_animation_parameters(params)
            return jsonify({'success': success})
        
        @self.app.route('/api/upload', methods=['POST'])
        def api_upload_animation():
            """API: Upload new animation plugin"""
            # Handle JSON code submission
            if request.is_json:
                data = request.get_json()
                if 'name' in data and 'code' in data:
                    plugin_name = data['name']
                    content = data['code']

                    success = self.animation_manager.save_animation(plugin_name, content)

                    if success:
                        # Reload plugins
                        self.animation_manager.refresh_plugins()
                        return jsonify({'success': True, 'plugin_name': plugin_name})
                    else:
                        return jsonify({'error': 'Failed to save animation'}), 500

                return jsonify({'error': 'Missing name or code in request'}), 400

            # Handle file upload
            if 'file' not in request.files:
                return jsonify({'error': 'No file provided'}), 400

            file = request.files['file']
            if file.filename == '':
                return jsonify({'error': 'No file selected'}), 400

            if file and file.filename.endswith('.py'):
                filename = secure_filename(file.filename)
                plugin_name = filename[:-3]  # Remove .py extension

                # Save file content
                content = file.read().decode('utf-8')
                success = self.animation_manager.save_animation(plugin_name, content)

                if success:
                    # Reload plugins
                    self.animation_manager.refresh_plugins()
                    return jsonify({'success': True, 'plugin_name': plugin_name})
                else:
                    return jsonify({'error': 'Failed to save animation'}), 500

            return jsonify({'error': 'Invalid file type. Only .py files allowed'}), 400
        
        @self.app.route('/api/reload/<animation_name>', methods=['POST'])
        def api_reload_animation(animation_name):
            """API: Reload specific animation plugin"""
            success = self.animation_manager.reload_animation(animation_name)
            return jsonify({'success': success})
        
        @self.app.route('/api/refresh', methods=['POST'])
        def api_refresh_plugins():
            """API: Refresh all plugins"""
            plugins = self.animation_manager.refresh_plugins()
            return jsonify({'success': True, 'plugins': plugins})
        
        @self.app.route('/upload')
        def upload_page():
            """Upload page"""
            return render_template('upload.html')
        
        @self.app.route('/control')
        def control_page():
            """Animation control page"""
            animations = self.animation_manager.list_animations()
            status = self.animation_manager.get_current_status()
            return render_template('control.html', animations=animations, status=status)
    
    def run(self, debug=False):
        """Start the web server"""
        print(f"🌐 Starting web interface at http://{self.host}:{self.port}")
        print(f"   Dashboard: http://{self.host}:{self.port}/")
        print(f"   Control:   http://{self.host}:{self.port}/control")
        print(f"   Upload:    http://{self.host}:{self.port}/upload")
        
        self.app.run(host=self.host, port=self.port, debug=debug, threaded=True)


def create_app(controller_config=None, host='0.0.0.0', port=5000):
    """Factory function to create the web application"""
    # Default controller configuration
    if controller_config is None:
        controller_config = {
            'bus': 0,
            'device': 0,
            'speed': 5000000,
            'mode': 3,
            'strips': 7,
            'leds_per_strip': 20,
            'debug': False
        }
    
    # Create LED controller
    controller = LEDController(**controller_config)
    
    # Create animation manager
    animation_manager = AnimationManager(controller)
    
    # Create web interface
    web_interface = AnimationWebInterface(animation_manager, host=host, port=port)

    return web_interface


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='LED Animation Web Interface')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=5000, help='Port to listen on')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    # LED controller arguments
    parser.add_argument('--bus', type=int, default=0, help='SPI bus number')
    parser.add_argument('--device', type=int, default=0, help='SPI device number')
    parser.add_argument('--spi-speed', type=int, default=5000000, help='SPI speed')
    parser.add_argument('--strips', type=int, default=7, help='Number of strips')
    parser.add_argument('--leds-per-strip', type=int, default=20, help='LEDs per strip')
    parser.add_argument('--controller-debug', action='store_true', help='Enable controller debug')
    
    args = parser.parse_args()
    
    # Controller configuration
    controller_config = {
        'bus': args.bus,
        'device': args.device,
        'speed': args.spi_speed,
        'mode': 3,
        'strips': args.strips,
        'leds_per_strip': args.leds_per_strip,
        'debug': args.controller_debug
    }
    
    # Create and run web interface
    web_interface = create_app(controller_config)
    web_interface.run(debug=args.debug)
