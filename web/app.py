#!/usr/bin/env python3
"""
Web Interface for LED Animation Management

Flask-based web server for controlling animations, uploading plugins,
and adjusting parameters in real-time.
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

from animation.core.manager import AnimationManager, PreviewLEDController
from ipc.control_channel import FileControlChannel
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP
from drivers.frame_codec import (
    decode_frame_data,
    encode_frame_data,
    FRAME_ENCODING_NAME,
)


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
        self.project_root = Path(__file__).resolve().parents[1]
        self.painter_presets_dir = self.project_root / "presets" / "frame_painter"

        # Create Flask app
        self.app = Flask(__name__)
        self.app.secret_key = 'led-grid-secret-key-change-in-production'

        # Configure upload settings
        self.app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max file size
        self.app.config['UPLOAD_FOLDER'] = str(self.project_root / "animation" / "plugins")

        # Ensure upload directory exists
        Path(self.app.config['UPLOAD_FOLDER']).mkdir(exist_ok=True)
        self.painter_presets_dir.mkdir(parents=True, exist_ok=True)

        # Register routes
        self._register_routes()

    def _register_routes(self):
        """Register Flask routes"""
        
        @self.app.route('/')
        def index():
            """Main dashboard"""
            animations = self.preview_manager.list_animations()
            status = self._status_payload()
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
            return jsonify(self._status_payload())
        
        @self.app.route('/api/stats')
        def api_get_stats():
            """API: Runtime stats payload that mirrors /api/status"""
            status = self._status_payload()
            return jsonify(status)

        @self.app.route('/api/metrics')
        def api_get_metrics():
            """API: Summarized performance metrics."""
            status = self._status_payload()
            return jsonify({
                'animation': {
                    'target_fps': status.get('target_fps', 0),
                    'actual_fps': status.get('actual_fps', 0),
                    'uptime': status.get('uptime', 0),
                },
                'performance': status.get('performance', {}),
                'driver': status.get('driver_stats', {}),
                'system': {},
            })

        @self.app.route('/api/hardware/stats')
        def api_get_hardware_stats():
            """API: Hardware stats for SPI devices."""
            status = self._status_payload()
            return jsonify(status.get('driver_stats', {}))

        @self.app.route('/api/hole', methods=['POST'])
        def api_trigger_hole():
            """API: Ask the running animation to punch a random hole"""
            self.control_channel.send_command('puncture_hole')
            return jsonify({'success': True})

        @self.app.route('/api/frame')
        def api_get_frame():
            """API: Get current animation frame data"""
            return jsonify(self._status_payload(decode_frame=True))

        @self.app.route('/api/painter/updates', methods=['POST'])
        def api_painter_apply_updates():
            """API: Apply sparse frame painter pixel updates."""
            payload = request.get_json(silent=True) or {}
            updates = payload.get('updates')
            if not isinstance(updates, list) or not updates:
                return jsonify({'error': 'updates must be a non-empty list'}), 400

            self.control_channel.send_command('painter_apply_updates', updates=updates)
            return jsonify({'success': True, 'queued_updates': len(updates)})

        @self.app.route('/api/painter/frame', methods=['POST'])
        def api_painter_set_frame():
            """API: Replace the entire frame painter frame."""
            payload = request.get_json(silent=True) or {}
            led_info = self._normalize_led_info(payload.get('led_info'))
            normalized_frame = self._extract_normalized_frame(payload, led_info=led_info)
            if normalized_frame is None:
                return jsonify({'error': 'Provide frame_data or frame_data_encoded'}), 400

            self.control_channel.send_command(
                'painter_set_frame',
                frame_data_encoded=encode_frame_data(normalized_frame),
                frame_data_length=len(normalized_frame),
            )
            return jsonify({'success': True, 'frame_data_length': len(normalized_frame)})

        @self.app.route('/api/painter/clear', methods=['POST'])
        def api_painter_clear():
            """API: Clear the frame painter output to black."""
            self.control_channel.send_command('painter_clear')
            return jsonify({'success': True})

        @self.app.route('/api/painter/presets')
        def api_painter_list_presets():
            """API: List available frame painter presets."""
            return jsonify({'presets': self._list_painter_presets()})

        @self.app.route('/api/painter/presets/<preset_id>')
        def api_painter_get_preset(preset_id: str):
            """API: Load a frame painter preset by id."""
            preset = self._load_painter_preset(preset_id)
            if not preset:
                return jsonify({'error': 'Preset not found'}), 404
            return jsonify(preset)

        @self.app.route('/api/painter/presets', methods=['POST'])
        def api_painter_save_preset():
            """API: Save or overwrite a frame painter preset."""
            payload = request.get_json(silent=True) or {}
            raw_name = (payload.get('name') or '').strip()
            if not raw_name:
                return jsonify({'error': 'Preset name is required'}), 400

            preset_id = self._sanitize_preset_id(raw_name)
            if not preset_id:
                return jsonify({'error': 'Preset name is invalid'}), 400

            status = self._status_payload()
            led_info = self._normalize_led_info(payload.get('led_info') or status.get('led_info'))
            frame_data = self._extract_normalized_frame(payload, led_info=led_info)
            if frame_data is None:
                frame_data = self._extract_normalized_frame(status, led_info=led_info)
            if frame_data is None:
                frame_data = [[0, 0, 0] for _ in range(led_info['total_leds'])]

            existing = self._load_painter_preset(preset_id)
            now = time.time()
            preset_payload = {
                'preset_id': preset_id,
                'name': raw_name,
                'created_at': existing.get('created_at', now) if isinstance(existing, dict) else now,
                'updated_at': now,
                'led_info': led_info,
                'frame_encoding': FRAME_ENCODING_NAME,
                'frame_data_length': len(frame_data),
                'frame_data_encoded': encode_frame_data(frame_data),
            }
            self._write_painter_preset(preset_id, preset_payload)

            return jsonify({
                'success': True,
                'preset': self._preset_summary(preset_payload),
            })

        @self.app.route('/api/preview/<animation_name>')
        def api_get_preview(animation_name):
            """API: Get preview frame data for a specific animation"""
            try:
                self._sync_preview_layout_from_status()
                # Get a sample frame from the animation without starting it
                preview_data = self.preview_manager.get_animation_preview(animation_name)
                return jsonify(preview_data)
            except Exception as e:
                return jsonify({
                    'error': f'Failed to get preview for {animation_name}: {str(e)}',
                    'frame_data': [],
                    'led_info': self._fallback_led_info(),
                    'is_running': False,
                    'frame_count': 0,
                    'timestamp': time.time()
                }), 500

        @self.app.route('/api/preview/<animation_name>/with_params', methods=['POST'])
        def api_get_preview_with_params(animation_name):
            """API: Get preview frame data for a specific animation with custom parameters"""
            try:
                self._sync_preview_layout_from_status()
                params = request.get_json() or {}
                preview_data = self.preview_manager.get_animation_preview_with_params(animation_name, params)
                return jsonify(preview_data)
            except Exception as e:
                return jsonify({
                    'error': f'Failed to get preview for {animation_name}: {str(e)}',
                    'frame_data': [],
                    'led_info': self._fallback_led_info(),
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

        def _handle_dpad(direction: str):
            """API: Send a D-pad input to the running animation."""
            direction = (direction or '').lower().replace('_', '-')
            valid = {'up', 'down', 'left', 'right', 'rotate-left', 'rotate-right', 'drop'}
            if direction not in valid:
                return jsonify({'error': 'Invalid dpad direction'}), 400
            self.control_channel.send_command('dpad', direction=direction)
            return jsonify({'success': True, 'direction': direction})

        @self.app.route('/dpad/<direction>', methods=['POST'])
        def api_dpad(direction):
            return _handle_dpad(direction)

        @self.app.route('/api/dpad/<direction>', methods=['POST'])
        def api_dpad_via_api(direction):
            return _handle_dpad(direction)

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
            status = self._status_payload()
            return render_template('control.html', animations=animations, status=status)

        @self.app.route('/emoji')
        def emoji_arranger_page():
            """Emoji arranger page"""
            status = self._status_payload()
            return render_template('emoji_arranger.html', status=status)

        @self.app.route('/painter')
        def frame_painter_page():
            """Frame painter page."""
            status = self._status_payload()
            return render_template('painter.html', status=status)
    
    def run(self, debug=False):
        """Start the web server"""
        print(f"🌐 Starting web interface at http://{self.host}:{self.port}")
        print(f"   Dashboard: http://{self.host}:{self.port}/")
        print(f"   Control:   http://{self.host}:{self.port}/control")
        print(f"   Painter:   http://{self.host}:{self.port}/painter")
        print(f"   Emoji:     http://{self.host}:{self.port}/emoji")
        print(f"   Upload:    http://{self.host}:{self.port}/upload")
        
        self.app.run(host=self.host, port=self.port, debug=debug, threaded=True)

    def _fallback_led_info(self) -> Dict[str, int]:
        """Current preview-manager dimensions used as a fallback layout."""
        return {
            'total_leds': self.preview_manager.controller.total_leds,
            'strip_count': self.preview_manager.controller.strip_count,
            'leds_per_strip': self.preview_manager.controller.leds_per_strip,
        }

    @staticmethod
    def _coerce_positive_int(value: Any, fallback: int) -> int:
        """Parse positive integers from untrusted payloads."""
        try:
            parsed = int(value)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
        return fallback

    def _normalize_led_info(self, led_info: Any) -> Dict[str, int]:
        """Normalize LED layout payloads into a validated shape."""
        fallback = self._fallback_led_info()
        if not isinstance(led_info, dict):
            return fallback

        strip_count = self._coerce_positive_int(led_info.get('strip_count'), fallback['strip_count'])
        leds_per_strip = self._coerce_positive_int(led_info.get('leds_per_strip'), fallback['leds_per_strip'])
        return {
            'strip_count': strip_count,
            'leds_per_strip': leds_per_strip,
            'total_leds': strip_count * leds_per_strip,
        }

    @staticmethod
    def _coerce_byte(value: Any) -> int:
        """Clamp any input to an 8-bit channel value."""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 0
        return max(0, min(255, parsed))

    def _normalize_frame_data(self, frame_data: Any, led_info: Optional[Dict[str, int]] = None) -> Optional[List[List[int]]]:
        """Normalize incoming frame payloads to a fixed-length RGB list."""
        if not isinstance(frame_data, list):
            return None

        layout = self._normalize_led_info(led_info)
        total_leds = layout['total_leds']
        normalized: List[List[int]] = []

        for pixel in frame_data[:total_leds]:
            if isinstance(pixel, (list, tuple)) and len(pixel) >= 3:
                normalized.append([
                    self._coerce_byte(pixel[0]),
                    self._coerce_byte(pixel[1]),
                    self._coerce_byte(pixel[2]),
                ])
            else:
                normalized.append([0, 0, 0])

        if len(normalized) < total_leds:
            normalized.extend([[0, 0, 0] for _ in range(total_leds - len(normalized))])

        return normalized

    def _extract_normalized_frame(self, payload: Dict[str, Any], led_info: Optional[Dict[str, int]] = None) -> Optional[List[List[int]]]:
        """Read either raw or encoded frame payloads and normalize the result."""
        if not isinstance(payload, dict):
            return None

        frame_data = payload.get('frame_data')
        normalized = self._normalize_frame_data(frame_data, led_info=led_info)
        if normalized is not None:
            return normalized

        encoded = payload.get('frame_data_encoded')
        if isinstance(encoded, str) and encoded:
            decoded = decode_frame_data(encoded)
            return self._normalize_frame_data(decoded, led_info=led_info)

        return None

    @staticmethod
    def _sanitize_preset_id(raw_name: str) -> str:
        """Convert user-provided preset names to a filesystem-safe id."""
        cleaned = re.sub(r'[^a-zA-Z0-9_-]+', '_', (raw_name or '').strip().lower())
        cleaned = re.sub(r'_+', '_', cleaned).strip('_')
        return cleaned[:64]

    def _preset_path(self, preset_id: str) -> Optional[Path]:
        """Resolve a preset id to a file path in the painter preset directory."""
        safe_id = self._sanitize_preset_id(preset_id)
        if not safe_id:
            return None
        return self.painter_presets_dir / f"{safe_id}.json"

    def _read_json_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read a JSON object from disk."""
        try:
            raw = path.read_text(encoding='utf-8')
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _preset_summary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return a concise summary shape for preset list responses."""
        return {
            'preset_id': payload.get('preset_id'),
            'name': payload.get('name'),
            'updated_at': payload.get('updated_at'),
            'created_at': payload.get('created_at'),
            'led_info': self._normalize_led_info(payload.get('led_info')),
            'frame_data_length': self._coerce_positive_int(payload.get('frame_data_length'), 0),
        }

    def _list_painter_presets(self) -> List[Dict[str, Any]]:
        """Read and summarize all painter presets from disk."""
        summaries: List[Dict[str, Any]] = []
        for path in sorted(self.painter_presets_dir.glob('*.json')):
            payload = self._read_json_file(path)
            if not payload:
                continue
            payload.setdefault('preset_id', path.stem)
            payload.setdefault('name', path.stem)
            summaries.append(self._preset_summary(payload))
        summaries.sort(key=lambda preset: preset.get('updated_at') or 0, reverse=True)
        return summaries

    def _load_painter_preset(self, preset_id: str) -> Optional[Dict[str, Any]]:
        """Load and decode a painter preset for editing."""
        path = self._preset_path(preset_id)
        if path is None or not path.exists():
            return None

        payload = self._read_json_file(path)
        if not payload:
            return None

        payload.setdefault('preset_id', path.stem)
        payload.setdefault('name', path.stem)
        led_info = self._normalize_led_info(payload.get('led_info'))
        frame_data = self._extract_normalized_frame(payload, led_info=led_info)
        if frame_data is None:
            frame_data = [[0, 0, 0] for _ in range(led_info['total_leds'])]

        return {
            **payload,
            'led_info': led_info,
            'frame_data': frame_data,
            'frame_data_length': len(frame_data),
            'frame_encoding': FRAME_ENCODING_NAME,
        }

    def _write_painter_preset(self, preset_id: str, payload: Dict[str, Any]):
        """Persist a painter preset atomically."""
        path = self._preset_path(preset_id)
        if path is None:
            raise ValueError("Invalid preset id")

        tmp_path = path.with_suffix('.json.tmp')
        tmp_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        tmp_path.replace(path)

    def _apply_preview_layout(self, led_info: Dict[str, int]):
        """Keep preview manager/controller dimensions in lock-step."""
        self.preview_manager.controller.strip_count = led_info['strip_count']
        self.preview_manager.controller.leds_per_strip = led_info['leds_per_strip']
        self.preview_manager.controller.total_leds = led_info['total_leds']

        preview_controller = getattr(self.preview_manager, 'preview_controller', None)
        if preview_controller is not None:
            preview_controller.strip_count = led_info['strip_count']
            preview_controller.leds_per_strip = led_info['leds_per_strip']
            preview_controller.total_leds = led_info['total_leds']

    def _sync_preview_layout_from_status(self, raw_status: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        """
        Sync preview dimensions from controller status so preview and live frames
        use the same geometry.
        """
        status = raw_status if isinstance(raw_status, dict) else (self.control_channel.read_status() or {})
        led_info = self._normalize_led_info(status.get('led_info'))
        self._apply_preview_layout(led_info)
        return led_info

    def _status_payload(self, decode_frame: bool = False) -> Dict[str, Any]:
        """Normalize the controller status so every consumer sees the same structure."""
        raw_status = self.control_channel.read_status()
        if not raw_status:
            return self._empty_status()

        status = dict(raw_status)
        status['led_info'] = self._sync_preview_layout_from_status(status)
        stats = status.get('animation_stats') or status.get('stats') or {}
        status['animation_stats'] = stats
        status['stats'] = stats
        status.setdefault('animation_hash', None)
        status.setdefault('animation_info', None)
        status.setdefault('performance', {})
        status.setdefault('driver_stats', {})
        status.setdefault('current_animation', None)
        status.setdefault('is_running', False)
        status.setdefault('mode', 'animation' if status.get('is_running') else 'idle')
        status.setdefault('painter_active', status.get('mode') == 'painter')
        status.setdefault('painter_updated_at', None)
        status.setdefault('frame_count', 0)
        status.setdefault('target_fps', 0)
        status.setdefault('actual_fps', 0)
        status.setdefault('uptime', 0)
        timestamp = status.get('updated_at') or status.get('timestamp')
        if not timestamp:
            timestamp = time.time()
        status['timestamp'] = timestamp

        encoded_frame = raw_status.get('frame_data_encoded')
        raw_frame_list = raw_status.get('frame_data')
        frame_length = raw_status.get('frame_data_length')

        if isinstance(raw_frame_list, list):
            frame_length = len(raw_frame_list)
            if not encoded_frame:
                encoded_frame = encode_frame_data(raw_frame_list)
        elif isinstance(raw_frame_list, str) and not encoded_frame:
            # Backwards compatibility: some snapshots may have stored the encoded
            # string under frame_data.
            encoded_frame = raw_frame_list

        status['frame_data_encoded'] = encoded_frame or ''
        status['frame_data_length'] = frame_length or 0
        status['frame_encoding'] = raw_status.get('frame_encoding') or (
            FRAME_ENCODING_NAME if encoded_frame else None
        )

        if decode_frame:
            if isinstance(raw_frame_list, list):
                status['frame_data'] = raw_frame_list
            else:
                status['frame_data'] = decode_frame_data(encoded_frame or '')
        else:
            status['frame_data'] = []

        return status

    def _empty_status(self):
        """Fallback status when controller process has not written a status file yet."""
        return {
            'is_running': False,
            'mode': 'idle',
            'painter_active': False,
            'painter_updated_at': None,
            'current_animation': None,
            'frame_count': 0,
            'uptime': 0,
            'target_fps': 0,
            'actual_fps': 0,
            'animation_stats': {},
            'stats': {},
            'animation_hash': None,
            'animation_info': None,
            'led_info': self._fallback_led_info(),
            'driver_stats': {},
            'frame_data': [],
            'frame_data_encoded': '',
            'frame_data_length': 0,
            'frame_encoding': None,
            'timestamp': time.time()
        }


def create_app(control_channel: FileControlChannel = None,
               host: str = '0.0.0.0',
               port: int = 5000,
               strips: int = DEFAULT_STRIP_COUNT,
               leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
               animations_dir: str = None,
               animation_speed_scale: float = 1.0):
    """Factory function to create the web application"""
    if control_channel is None:
        control_channel = FileControlChannel()

    # Preview-only controller keeps renderer and plugin listing in this process
    preview_controller = PreviewLEDController(strips, leds_per_strip)

    # Create animation manager (preview only, no hardware access)
    animation_manager = AnimationManager(
        preview_controller,
        plugins_dir=animations_dir,
        animation_speed_scale=animation_speed_scale,
    )

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
    parser.add_argument('--animation-speed-scale', type=float, default=1.0,
                        help='Speed multiplier applied to preview animations')
    
    args = parser.parse_args()
    
    # Create and run web interface
    web_interface = create_app(
        host=args.host,
        port=args.port,
        strips=args.strips,
        leds_per_strip=args.leds_per_strip,
        animation_speed_scale=args.animation_speed_scale
    )
    web_interface.run(debug=args.debug)
