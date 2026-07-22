#!/usr/bin/env python3
"""
Web Interface for LED Animation Management

Flask-based web server for controlling animations and adjusting parameters in
real time.
"""

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.defaults import DEFAULT_ANIMATION_SPEED_SCALE, DEFAULT_PLANT_AWARE
from animation.core.plant_awareness import PlantModifierState
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
        self.animation_presets_dir = self.project_root / "presets" / "animations"
        self.deployment_status_path = self.project_root / "run_state" / "deployment.json"

        # Create Flask app
        self.app = Flask(__name__)
        self.app.secret_key = 'led-grid-secret-key-change-in-production'

        self.painter_presets_dir.mkdir(parents=True, exist_ok=True)
        self.animation_presets_dir.mkdir(parents=True, exist_ok=True)

        # Register routes
        self._register_routes()

    def _register_routes(self):
        """Register Flask routes"""
        
        @self.app.route('/')
        def index():
            """Main dashboard"""
            animations = self._dashboard_animations()
            status = self._status_payload()
            return render_template(
                'index.html',
                animations=[item for item in animations if not item['is_test']],
                test_animations=[item for item in animations if item['is_test']],
                status=status,
                speed_baseline=DEFAULT_ANIMATION_SPEED_SCALE,
            )
        
        @self.app.route('/api/animations')
        def api_list_animations():
            """API: Get list of available animations"""
            animations = self._sorted_animations()
            return jsonify(animations)
        
        @self.app.route('/api/animations/<animation_name>')
        def api_get_animation(animation_name):
            """API: Get detailed info about specific animation"""
            info = self.preview_manager.get_animation_info(animation_name)
            if info:
                return jsonify(info)
            return jsonify({'error': 'Animation not found'}), 404

        @self.app.route('/api/animations/<animation_name>/presets')
        def api_list_animation_presets(animation_name: str):
            """API: List presets for one animation, reading disk on every call."""
            return jsonify({
                'animation': animation_name,
                'presets': self._list_animation_presets(animation_name),
            })

        @self.app.route('/api/animations/<animation_name>/presets/<preset_id>')
        def api_get_animation_preset(animation_name: str, preset_id: str):
            """API: Load an animation preset from disk."""
            preset = self._load_animation_preset(animation_name, preset_id)
            if not preset:
                return jsonify({'error': 'Preset not found'}), 404
            return jsonify(preset)

        @self.app.route('/api/animations/<animation_name>/presets', methods=['POST'])
        def api_save_animation_preset(animation_name: str):
            """API: Save or overwrite a named set of animation parameters."""
            if not self._animation_preset_dir(animation_name):
                return jsonify({'error': 'Animation name is invalid'}), 400

            payload = request.get_json(silent=True) or {}
            raw_name = (payload.get('name') or '').strip()
            params = payload.get('params')
            if not raw_name:
                return jsonify({'error': 'Preset name is required'}), 400
            if not isinstance(params, dict):
                return jsonify({'error': 'params must be a JSON object'}), 400
            params = dict(params)
            validation_error = self._validate_animation_params(animation_name, params)
            if validation_error:
                return jsonify({'error': validation_error}), 400

            preset_id = self._sanitize_preset_id(raw_name)
            if not preset_id:
                return jsonify({'error': 'Preset name is invalid'}), 400

            existing = self._load_animation_preset(animation_name, preset_id)
            now = time.time()
            preset_payload = {
                'version': 2,
                'preset_id': preset_id,
                'name': raw_name,
                'animation': animation_name,
                'params': params,
                'created_at': existing.get('created_at', now) if existing else now,
                'updated_at': now,
            }
            for field in ('category', 'description', 'tags', 'palette'):
                if field in payload:
                    preset_payload[field] = payload[field]
                elif existing and field in existing:
                    preset_payload[field] = existing[field]
            self._write_animation_preset(animation_name, preset_id, preset_payload)
            return jsonify({'success': True, 'preset': self._animation_preset_summary(preset_payload)})

        @self.app.route('/api/animations/<animation_name>/presets/<preset_id>/apply', methods=['POST'])
        def api_apply_animation_preset(animation_name: str, preset_id: str):
            """API: Re-read a preset from disk and start its animation with those settings."""
            preset = self._load_animation_preset(animation_name, preset_id)
            if not preset:
                return jsonify({'error': 'Preset not found'}), 404
            self.control_channel.send_command(
                'start', animation=animation_name, config=preset['params']
            )
            return jsonify({'success': True, 'preset': preset})

        @self.app.route('/api/animations/<animation_name>/presets/<preset_id>', methods=['DELETE'])
        def api_delete_animation_preset(animation_name: str, preset_id: str):
            """API: Delete one animation preset."""
            path = self._animation_preset_path(animation_name, preset_id)
            if path is None or not path.is_file():
                return jsonify({'error': 'Preset not found'}), 404
            try:
                path.unlink()
            except OSError:
                return jsonify({'error': 'Failed to delete preset'}), 500
            return jsonify({'success': True})
        
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

        @self.app.route('/api/config/target-fps', methods=['POST'])
        def api_set_target_fps():
            payload = request.get_json(silent=True) or {}
            try:
                target_fps = int(payload.get('target_fps'))
            except (TypeError, ValueError):
                return jsonify({'error': 'target_fps must be an integer'}), 400
            if target_fps < 1 or target_fps > 200:
                return jsonify({'error': 'target_fps must be between 1 and 200'}), 400
            self.control_channel.send_command(
                'set_target_fps', target_fps=target_fps
            )
            return jsonify({'success': True, 'target_fps': target_fps})

        @self.app.route('/api/config/animation-speed', methods=['POST'])
        def api_set_animation_speed():
            payload = request.get_json(silent=True) or {}
            try:
                multiplier = float(payload.get('multiplier'))
            except (TypeError, ValueError):
                return jsonify({'error': 'multiplier must be numeric'}), 400
            if not math.isfinite(multiplier) or multiplier <= 0:
                return jsonify({'error': 'multiplier must be a positive finite number'}), 400
            speed_scale = DEFAULT_ANIMATION_SPEED_SCALE * multiplier
            self.control_channel.send_command(
                'set_animation_speed_scale', animation_speed_scale=speed_scale
            )
            return jsonify({
                'success': True,
                'multiplier': multiplier,
                'animation_speed_scale': speed_scale,
            })

        @self.app.route('/api/config/plant-aware', methods=['POST'])
        def api_set_plant_aware():
            payload = request.get_json(silent=True) or {}
            enabled = payload.get('plant_aware')
            if not isinstance(enabled, bool):
                return jsonify({'error': 'plant_aware must be boolean'}), 400
            if hasattr(self.preview_manager, 'set_plant_aware'):
                self.preview_manager.set_plant_aware(enabled)
            self.control_channel.send_command('set_plant_aware', plant_aware=enabled)
            return jsonify({'success': True, 'plant_aware': enabled})

        @self.app.route('/api/config/plant-modifiers', methods=['POST'])
        def api_set_plant_modifiers():
            payload = request.get_json(silent=True) or {}
            try:
                state = PlantModifierState.from_payload(payload.get('plant_modifiers'))
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            serialized = state.to_dict()
            if hasattr(self.preview_manager, 'set_plant_modifiers'):
                self.preview_manager.set_plant_modifiers(serialized)
            self.control_channel.send_command(
                'set_plant_modifiers', plant_modifiers=serialized
            )
            return jsonify({'success': True, 'plant_modifiers': serialized})

        @self.app.route('/api/hardware/stats')
        def api_get_hardware_stats():
            """API: Hardware stats for SPI devices."""
            status = self._status_payload()
            return jsonify(status.get('driver_stats', {}))

        @self.app.route('/api/hole', methods=['POST'])
        def api_trigger_hole():
            """Punch a random hole or one at the supplied grid coordinate."""
            payload = request.get_json(silent=True) or {}
            data: Dict[str, float] = {}
            for key in ('x', 'y', 'radius'):
                value = payload.get(key)
                if value is not None:
                    if not isinstance(value, (int, float)):
                        return jsonify({'error': f'{key} must be numeric'}), 400
                    data[key] = float(value)
            if ('x' in data) != ('y' in data):
                return jsonify({'error': 'x and y must be provided together'}), 400
            self.control_channel.send_command('puncture_hole', **data)
            return jsonify({'success': True, 'positioned': 'x' in data})

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
        
        @self.app.route('/control')
        def control_page():
            """Animation control page"""
            animations = self._sorted_animations()
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

    def _dashboard_animations(self) -> List[Dict[str, Any]]:
        """Decorate plugin metadata for the dashboard's show/test galleries."""
        catalog = []
        for animation in self._sorted_animations():
            item = dict(animation)
            plugin_name = item.get('plugin_name', '')
            item.setdefault('emoji', '✨')
            item.setdefault('is_test', False)
            presets = self._list_animation_presets(plugin_name)
            for preset in presets:
                preset['emoji'] = self._preset_emoji(preset, item['emoji'])
            item['presets'] = presets
            catalog.append(item)
        return catalog

    def _sorted_animations(self) -> List[Dict[str, Any]]:
        """Return animation metadata alphabetized by its display name."""
        return sorted(
            self.preview_manager.list_animations(),
            key=lambda animation: str(
                animation.get('name') or animation.get('plugin_name') or ''
            ).casefold(),
        )

    @staticmethod
    def _preset_emoji(preset: Dict[str, Any], fallback: str) -> str:
        """Choose a discoverable icon from curated preset language."""
        text = ' '.join([
            str(preset.get('name', '')),
            str(preset.get('category', '')),
            ' '.join(map(str, preset.get('tags') or [])),
        ]).lower()
        choices = (
            (('ice', 'crystal', 'frost'), '❄️'),
            (('fire', 'ember', 'solar', 'gold'), '🔥'),
            (('ocean', 'tide', 'water'), '🌊'),
            (('space', 'star', 'galaxy'), '🌌'),
            (('earth', 'garden', 'orchard'), '🌍'),
            (('neon', 'synthwave', 'arcade'), '🎆'),
            (('quiet', 'calm'), '🌙'),
            (('chaos', 'storm', 'finale'), '⚡'),
        )
        return next((emoji for terms, emoji in choices if any(term in text for term in terms)), fallback)

    @staticmethod
    def _preset_swatches(preset: Dict[str, Any]) -> List[str]:
        """Extract up to three representative colors from preset parameters."""
        palette = preset.get('palette')
        if isinstance(palette, dict) and isinstance(palette.get('colors'), list):
            colors = [
                color.upper() for color in palette['colors']
                if isinstance(color, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', color)
            ]
            if colors:
                return colors[:3]

        params = preset.get('params') or {}
        colors = []
        for red_name, red_value in params.items():
            if not red_name.endswith('red'):
                continue
            prefix = red_name[:-3]
            green_name, blue_name = f'{prefix}green', f'{prefix}blue'
            if green_name not in params or blue_name not in params:
                continue
            try:
                channels = [int(red_value), int(params[green_name]), int(params[blue_name])]
            except (TypeError, ValueError):
                continue
            if all(0 <= channel <= 255 for channel in channels):
                colors.append('#' + ''.join(f'{channel:02X}' for channel in channels))
        return colors[:3]

    def _validate_animation_params(
        self, animation_name: str, params: Dict[str, Any]
    ) -> Optional[str]:
        """Validate runtime preset parameters against the plugin schema."""
        info = self.preview_manager.get_animation_info(animation_name)
        if not info:
            return f"Unknown animation: {animation_name}"
        schema = info.get('parameters')
        if not isinstance(schema, dict):
            return f"Animation schema is unavailable: {animation_name}"

        expected_types = {
            'bool': lambda value: isinstance(value, bool),
            'int': lambda value: isinstance(value, int) and not isinstance(value, bool),
            'float': lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
            'str': lambda value: isinstance(value, str),
        }
        for name, value in params.items():
            definition = schema.get(name)
            if not isinstance(definition, dict):
                return f"Unsupported parameter for {animation_name}: {name}"
            type_name = definition.get('type')
            validator = expected_types.get(type_name)
            if validator and not validator(value):
                return f"Parameter {name} must be {type_name}"
            if 'options' in definition and value not in definition['options']:
                return f"Parameter {name} must be one of {definition['options']}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    finite = math.isfinite(float(value))
                except OverflowError:
                    finite = False
                if not finite:
                    return f"Parameter {name} must be finite"
                if 'min' in definition and value < definition['min']:
                    return f"Parameter {name} must be at least {definition['min']}"
                if 'max' in definition and value > definition['max']:
                    return f"Parameter {name} must be at most {definition['max']}"
        return None
    
    def run(self, debug=False):
        """Start the web server"""
        print(f"🌐 Starting web interface at http://{self.host}:{self.port}")
        print(f"   Dashboard: http://{self.host}:{self.port}/")
        print(f"   Control:   http://{self.host}:{self.port}/control")
        print(f"   Painter:   http://{self.host}:{self.port}/painter")
        print(f"   Emoji:     http://{self.host}:{self.port}/emoji")

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
        summaries.sort(
            key=lambda preset: str(preset.get('name') or preset.get('preset_id') or '').casefold()
        )
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

    def _animation_preset_dir(self, animation_name: str) -> Optional[Path]:
        """Resolve the writable runtime-preset directory for an animation."""
        safe_name = self._sanitize_preset_id(animation_name)
        if not safe_name or safe_name != animation_name:
            return None
        return self.animation_presets_dir / safe_name

    def _curated_animation_preset_dir(self, animation_name: str) -> Optional[Path]:
        """Resolve the read-only preset directory owned by a plugin package."""
        safe_name = self._sanitize_preset_id(animation_name)
        if not safe_name or safe_name != animation_name:
            return None
        loader = getattr(self.preview_manager, 'plugin_loader', None)
        plugin_dir = loader.get_plugin_dir(animation_name) if loader is not None else None
        if plugin_dir is None:
            return None
        return plugin_dir / 'presets'

    def _animation_preset_path(self, animation_name: str, preset_id: str) -> Optional[Path]:
        """Resolve an animation/preset pair without allowing path traversal."""
        preset_dir = self._animation_preset_dir(animation_name)
        safe_id = self._sanitize_preset_id(preset_id)
        if preset_dir is None or not safe_id:
            return None
        return preset_dir / f"{safe_id}.json"

    def _animation_preset_summary(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'version': payload.get('version', 1),
            'preset_id': payload.get('preset_id'),
            'name': payload.get('name'),
            'animation': payload.get('animation'),
            'created_at': payload.get('created_at'),
            'updated_at': payload.get('updated_at'),
            'category': payload.get('category'),
            'description': payload.get('description'),
            'tags': payload.get('tags', []),
            'palette': payload.get('palette'),
            'swatches': self._preset_swatches(payload),
        }

    def _list_animation_presets(self, animation_name: str) -> List[Dict[str, Any]]:
        """List curated and runtime presets, with runtime files overriding IDs."""
        paths: Dict[str, Path] = {}
        curated_dir = self._curated_animation_preset_dir(animation_name)
        runtime_dir = self._animation_preset_dir(animation_name)
        for preset_dir in (curated_dir, runtime_dir):
            if preset_dir is not None and preset_dir.is_dir():
                paths.update({path.stem: path for path in sorted(preset_dir.glob('*.json'))})

        summaries: List[Dict[str, Any]] = []
        for path in paths.values():
            payload = self._read_json_file(path)
            if payload and payload.get('animation', animation_name) == animation_name:
                payload.setdefault('preset_id', path.stem)
                payload.setdefault('name', path.stem)
                summaries.append(self._animation_preset_summary(payload))
        summaries.sort(
            key=lambda preset: str(preset.get('name') or preset.get('preset_id') or '').casefold()
        )
        return summaries

    def _load_animation_preset(self, animation_name: str, preset_id: str) -> Optional[Dict[str, Any]]:
        """Read a runtime preset or fall back to its curated package preset."""
        path = self._animation_preset_path(animation_name, preset_id)
        if path is None:
            return None
        if not path.is_file():
            curated_dir = self._curated_animation_preset_dir(animation_name)
            path = curated_dir / path.name if curated_dir is not None else path
        if not path.is_file():
            return None
        payload = self._read_json_file(path)
        if not payload or not isinstance(payload.get('params'), dict):
            return None
        if payload.get('animation', animation_name) != animation_name:
            return None
        payload.setdefault('preset_id', path.stem)
        payload.setdefault('name', path.stem)
        payload.setdefault('animation', animation_name)
        return payload

    def _write_animation_preset(
        self, animation_name: str, preset_id: str, payload: Dict[str, Any]
    ):
        """Persist an animation preset atomically."""
        path = self._animation_preset_path(animation_name, preset_id)
        if path is None:
            raise ValueError("Invalid animation preset path")
        path.parent.mkdir(parents=True, exist_ok=True)
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
        if hasattr(self.preview_manager, 'set_plant_modifiers'):
            try:
                if 'plant_modifiers' in status:
                    self.preview_manager.set_plant_modifiers(status['plant_modifiers'])
                elif isinstance(status.get('plant_aware'), bool):
                    self.preview_manager.set_plant_aware(status['plant_aware'])
            except ValueError:
                pass
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
        status.setdefault('animation_speed_scale', DEFAULT_ANIMATION_SPEED_SCALE)
        status.setdefault('plant_aware', DEFAULT_PLANT_AWARE)
        status.setdefault(
            'plant_modifiers',
            PlantModifierState.from_legacy(DEFAULT_PLANT_AWARE).to_dict(),
        )
        if hasattr(self.preview_manager, 'set_plant_modifiers'):
            try:
                self.preview_manager.set_plant_modifiers(status['plant_modifiers'])
            except ValueError:
                pass
        status.setdefault('actual_fps', 0)
        status.setdefault('uptime', 0)
        status['deploy_timestamp'] = self._deploy_timestamp()
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

    def _deploy_timestamp(self) -> Optional[float]:
        """Read the most recent successful fast-deploy timestamp from disk."""
        try:
            payload = json.loads(self.deployment_status_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None
        deploy_timestamp = payload.get('deploy_timestamp') if isinstance(payload, dict) else None
        if isinstance(deploy_timestamp, bool) or not isinstance(deploy_timestamp, (int, float)):
            return None
        return deploy_timestamp

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
            'animation_speed_scale': DEFAULT_ANIMATION_SPEED_SCALE,
            'plant_aware': DEFAULT_PLANT_AWARE,
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
            'deploy_timestamp': self._deploy_timestamp(),
            'timestamp': time.time()
        }


def create_app(control_channel: FileControlChannel = None,
               host: str = '0.0.0.0',
               port: int = 5000,
               strips: int = DEFAULT_STRIP_COUNT,
               leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
               animations_dir: str = None,
               animation_speed_scale: float = DEFAULT_ANIMATION_SPEED_SCALE,
               plant_aware: bool = DEFAULT_PLANT_AWARE):
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
        plant_aware=plant_aware,
        auto_start=False,
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
    parser.add_argument('--animation-speed-scale', type=float, default=DEFAULT_ANIMATION_SPEED_SCALE,
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
