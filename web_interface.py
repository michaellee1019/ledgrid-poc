#!/usr/bin/env python3
"""
Web Interface for LED Animation Management

Flask-based web server for controlling animations, uploading plugins,
and adjusting parameters in real-time.
"""

import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

from animation_manager import AnimationManager, PreviewLEDController
from control_channel import FileControlChannel
from led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


class AnimationWebInterface:
    """Web interface for animation management"""

    def __init__(self, control_channel: FileControlChannel,
                 preview_manager: AnimationManager,
                 host: str = '0.0.0.0',
                 port: int = 5000):
        """
        Initialize web interface

        Args:
            control_channel: FileControlChannel used to send commands to controller
            preview_manager: AnimationManager instance used only for previews/listing
            host: Host to bind to
            port: Port to listen on
        """
        self.control_channel = control_channel
        self.preview_manager = preview_manager
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
            animations = self.preview_manager.list_animations()
            status = self._read_status_for_view()
            return render_template('index.html', animations=animations, status=status)
        
        @self.app.route('/api/animations')
        def api_list_animations():
            """API: Get list of available animations"""
            animations = self.preview_manager.list_animations()
            return jsonify(animations)
        
        @self.app.route('/api/animations/<animation_name>')
        def api_get_animation(animation_name):
            """API: Get detailed info about specific animation"""
            info = self.preview_manager.get_animation_info(animation_name)
            if info:
                return jsonify(info)
            return jsonify({'error': 'Animation not found'}), 404
        
        @self.app.route('/api/start/<animation_name>', methods=['POST'])
        def api_start_animation(animation_name):
            """API: Start an animation"""
            config = request.get_json() or {}
            self.control_channel.send_command('start', animation=animation_name, config=config)
            # Controller polls periodically, so assume success if write succeeded
            success = True
            return jsonify({'success': success})
        
        @self.app.route('/api/stop', methods=['POST'])
        def api_stop_animation():
            """API: Stop current animation"""
            self.control_channel.send_command('stop')
            return jsonify({'success': True})
        
        @self.app.route('/api/status')
        def api_get_status():
            """API: Get current status"""
            status = self.control_channel.read_status() or self._empty_status()
            return jsonify(status)

        @self.app.route('/api/frame')
        def api_get_frame():
            """API: Get current animation frame data"""
            status = self.control_channel.read_status() or self._empty_status()
            return jsonify(status)

        @self.app.route('/api/preview/<animation_name>')
        def api_get_preview(animation_name):
            """API: Get preview frame data for a specific animation"""
            try:
                # Get a sample frame from the animation without starting it
                preview_data = self.preview_manager.get_animation_preview(animation_name)
                return jsonify(preview_data)
            except Exception as e:
                return jsonify({
                    'error': f'Failed to get preview for {animation_name}: {str(e)}',
                    'frame_data': [],
                    'led_info': {
                        'total_leds': self.preview_manager.controller.total_leds,
                        'strip_count': self.preview_manager.controller.strip_count,
                        'leds_per_strip': self.preview_manager.controller.leds_per_strip
                    },
                    'is_running': False,
                    'frame_count': 0,
                    'timestamp': time.time()
                }), 500
        
        @self.app.route('/api/parameters', methods=['POST'])
        def api_update_parameters():
            """API: Update animation parameters"""
            params = request.get_json() or {}
            self.control_channel.send_command('update_params', params=params)
            return jsonify({'success': True})
        
        @self.app.route('/api/upload', methods=['POST'])
        def api_upload_animation():
            """API: Upload new animation plugin"""
            # Handle JSON code submission
            if request.is_json:
                data = request.get_json()
                if 'name' in data and 'code' in data:
                    plugin_name = data['name']
                    content = data['code']

                    success = self.preview_manager.save_animation(plugin_name, content)

                    if success:
                        # Reload plugins
                        self.preview_manager.refresh_plugins()
                        # Ask controller to reload plugins
                        self.control_channel.send_command('refresh_plugins')
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
                success = self.preview_manager.save_animation(plugin_name, content)

                if success:
                    # Reload plugins
                    self.preview_manager.refresh_plugins()
                    self.control_channel.send_command('refresh_plugins')
                    return jsonify({'success': True, 'plugin_name': plugin_name})
                else:
                    return jsonify({'error': 'Failed to save animation'}), 500

            return jsonify({'error': 'Invalid file type. Only .py files allowed'}), 400
        
        @self.app.route('/api/reload/<animation_name>', methods=['POST'])
        def api_reload_animation(animation_name):
            """API: Reload specific animation plugin"""
            success = self.preview_manager.reload_animation(animation_name)
            if success:
                self.control_channel.send_command('refresh_plugins', animation=animation_name)
            return jsonify({'success': success})
        
        @self.app.route('/api/refresh', methods=['POST'])
        def api_refresh_plugins():
            """API: Refresh all plugins"""
            plugins = self.preview_manager.refresh_plugins()
            self.control_channel.send_command('refresh_plugins')
            return jsonify({'success': True, 'plugins': plugins})
        
        @self.app.route('/upload')
        def upload_page():
            """Upload page"""
            return render_template('upload.html')
        
        @self.app.route('/control')
        def control_page():
            """Animation control page"""
            animations = self.preview_manager.list_animations()
            status = self._read_status_for_view()
            return render_template('control.html', animations=animations, status=status)
    
    def run(self, debug=False):
        """Start the web server"""
        print(f"🌐 Starting web interface at http://{self.host}:{self.port}")
        print(f"   Dashboard: http://{self.host}:{self.port}/")
        print(f"   Control:   http://{self.host}:{self.port}/control")
        print(f"   Upload:    http://{self.host}:{self.port}/upload")
        
        self.app.run(host=self.host, port=self.port, debug=debug, threaded=True)

    def _read_status_for_view(self):
        status = self.control_channel.read_status()
        if status:
            return status
        return self._empty_status()

    def _empty_status(self):
        """Fallback status when controller process has not written a status file yet."""
        return {
            'is_running': False,
            'current_animation': None,
            'frame_count': 0,
            'uptime': 0,
            'target_fps': 0,
            'actual_fps': 0,
            'led_info': {
                'total_leds': self.preview_manager.controller.total_leds,
                'strip_count': self.preview_manager.controller.strip_count,
                'leds_per_strip': self.preview_manager.controller.leds_per_strip
            },
            'frame_data': []
        }


def create_app(control_channel: FileControlChannel = None,
               host: str = '0.0.0.0',
               port: int = 5000,
               strips: int = DEFAULT_STRIP_COUNT,
               leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
               animations_dir: str = 'animations'):
    """Factory function to create the web application"""
    if control_channel is None:
        control_channel = FileControlChannel()

    # Preview-only controller keeps renderer and plugin listing in this process
    preview_controller = PreviewLEDController(strips, leds_per_strip)

    # Create animation manager (preview only, no hardware access)
    animation_manager = AnimationManager(preview_controller, plugins_dir=animations_dir)

    # Create web interface
    web_interface = AnimationWebInterface(control_channel, animation_manager, host=host, port=port)

    return web_interface


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='LED Animation Web Interface')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=5000, help='Port to listen on')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    # LED layout for previews (does not touch hardware)
    parser.add_argument('--strips', type=int, default=DEFAULT_STRIP_COUNT, help='Number of strips')
    parser.add_argument('--leds-per-strip', type=int, default=DEFAULT_LEDS_PER_STRIP, help='LEDs per strip')
    
    args = parser.parse_args()
    
    # Create and run web interface
    web_interface = create_app(strips=args.strips, leds_per_strip=args.leds_per_strip)
    web_interface.run(debug=args.debug)
