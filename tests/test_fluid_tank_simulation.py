"""Fluid tank regression tests driven by the shared simulation harness."""

import unittest
import random

from animation.plugins.fluid_tank import FluidTankAnimation
from animation.core.manager import AnimationManager
from animation.core.plugin_loader import AnimationPluginLoader
from tools.diagnostics.fluid_tank_simulation import SimulationConfig, run_simulation
from web.app import AnimationWebInterface
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


class _Controller:
    def __init__(self, strips=DEFAULT_STRIP_COUNT, leds_per_strip=DEFAULT_LEDS_PER_STRIP):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip


class _RecordingChannel:
    def __init__(self):
        self.last_command = None

    def send_command(self, action, **data):
        self.last_command = {'action': action, 'data': data}
        return self.last_command

    def read_status(self):
        return None


class _PreviewManager:
    def list_animations(self):
        return []

    def get_animation_info(self, _name):
        return None


class FluidTankSimulationTests(unittest.TestCase):
    def setUp(self):
        random.seed(1234)

    def test_fill_curve_and_drain_cycle(self):
        samples = run_simulation(SimulationConfig(duration_s=130.0, sample_every_s=2.0))
        fill_before_60 = [s['stats']['fill_ratio'] for s in samples if s['timestamp'] <= 60.0]
        self.assertTrue(fill_before_60)
        self.assertGreaterEqual(max(fill_before_60), 0.85)
        self.assertTrue(any(s['stats'].get('hole_active') for s in samples), "hole never opened")
        drained_fill = [s['stats']['fill_ratio'] for s in samples if s['timestamp'] >= 90.0]
        self.assertTrue(drained_fill, "no samples captured after 90s")
        self.assertLessEqual(min(drained_fill), 0.2, "tank never drained after puncture")
        self.assertTrue(
            any(s['stats'].get('spawn_allowed') for s in samples if 65.0 <= s['timestamp'] <= 100.0),
            "tank remained stuck waiting for a cycle reset",
        )
        for sample in samples:
            stats = sample['stats']
            conserved_cc = stats['total_inflow_cc'] - stats['total_drained_cc']
            represented_cc = (
                stats['volume_cc']
                + stats['airborne_volume_cc']
                + stats['queued_inlet_volume_cells'] * stats['cc_per_cell']
            )
            self.assertAlmostEqual(conserved_cc, represented_cc, delta=0.01)

    def test_stats_schema_and_scene_features(self):
        samples = run_simulation(SimulationConfig(duration_s=110.0, sample_every_s=1.0))
        for sample in samples:
            self.assertEqual(sample['current_animation'], "Fluid Tank")
            self.assertIn('stats', sample)
            self.assertIsInstance(sample['stats'], dict)
        self.assertTrue(
            any(s['stats'].get('max_bubble_rise', 0.0) > 5.0 for s in samples),
            "bubbles never rose",
        )
        self.assertTrue(
            any(s['stats'].get('last_spray_time', 0.0) > 0.0 for s in samples),
            "no spray events recorded",
        )

    def test_volume_uses_physical_five_cc_cells(self):
        samples = run_simulation(SimulationConfig(
            duration_s=10.0,
            sample_every_s=10.0,
            animation_config={'auto_hole': False, 'target_fill_time': 100.0},
        ))
        stats = samples[-1]['stats']
        self.assertEqual(stats['cc_per_cell'], 5.0)
        self.assertEqual(
            stats['capacity_cc'],
            DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP * 5.0,
        )
        self.assertAlmostEqual(stats['volume_cc'], stats['volume_cells'] * 5.0)
        represented_ratio = (
            stats['volume_cells']
            + stats['airborne_volume_cells']
            + stats['queued_inlet_volume_cells']
        ) / stats['total_cells']
        self.assertAlmostEqual(represented_ratio, 0.1, delta=0.005)
        self.assertLess(stats['fill_ratio'], represented_ratio)

    def test_tank_stays_empty_until_a_real_drop_impacts(self):
        animation = FluidTankAnimation(_Controller(), {
            'auto_hole': False,
            'target_fill_time': 60.0,
            'cell_height_mm': 17.1,
        })
        animation.start()
        animation.generate_frame(0.0, 0)
        self.assertEqual(animation.volume_cells, 0.0)
        self.assertGreater(
            sum(p['volume_cells'] for p in animation.inlet_particles)
            + animation.inlet_reservoir_cells,
            0.0,
        )

        first_impact = None
        for frame in range(1, 301):
            animation.generate_frame(frame / 300.0, frame)
            if animation.volume_cells > 0.0:
                first_impact = frame / 300.0
                break
        self.assertIsNotNone(first_impact)
        self.assertGreater(first_impact, 0.4)
        self.assertLess(first_impact, 1.0)

    def test_plugin_loader_discovers_fluid_tank(self):
        loader = AnimationPluginLoader(allowed_plugins=AnimationManager.ALLOWED_PLUGINS)
        loader.load_all_plugins()
        self.assertIn('fluid_tank', loader.loaded_plugins)
        info = loader.get_plugin_info('fluid_tank')
        self.assertEqual(info['name'], 'Fluid Tank')
        self.assertNotIn('error', info)

    def test_hole_pressure_depends_on_depth_and_supports_multiple_holes(self):
        low = FluidTankAnimation(_Controller(), {'auto_hole': False})
        high = FluidTankAnimation(_Controller(), {'auto_hole': False})
        for animation in (low, high):
            animation.start()
            animation.volume_cells = animation.capacity_cells

        low.trigger_hole(8.0, 139.0, 1.5)
        high.trigger_hole(8.0, 70.0, 1.5)
        low_before = low.volume_cells
        high_before = high.volume_cells
        low._drain_holes(0.1, 0.1)
        high._drain_holes(0.1, 0.1)
        self.assertGreater(low_before - low.volume_cells, high_before - high.volume_cells)

        low.trigger_hole(20.0, 100.0, 1.0)
        self.assertEqual(len(low.holes), 2)

    def test_midwall_hole_cannot_drain_water_below_its_height(self):
        animation = FluidTankAnimation(_Controller(), {
            'auto_hole': False,
            'drop_rate': 0.1,
            'target_fill_time': 600.0,
        })
        animation.start()
        animation.volume_cells = animation.capacity_cells
        animation.trigger_hole(16.0, 70.0, 1.5)
        minimum_fill = 1.0
        for frame in range(1, 900):
            animation.generate_frame(frame / 30.0, frame)
            minimum_fill = min(minimum_fill, animation._fill_ratio())
        self.assertGreaterEqual(minimum_fill, 0.49)
        self.assertLessEqual(minimum_fill, 0.53)

    def test_hole_api_forwards_clicked_grid_coordinate(self):
        channel = _RecordingChannel()
        interface = AnimationWebInterface(channel, _PreviewManager())
        client = interface.app.test_client()
        response = client.post('/api/hole', json={'x': 7.5, 'y': 42.0, 'radius': 2.0})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['positioned'])
        self.assertEqual(channel.last_command, {
            'action': 'puncture_hole',
            'data': {'x': 7.5, 'y': 42.0, 'radius': 2.0},
        })


if __name__ == "__main__":
    unittest.main()
