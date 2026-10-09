"""Keep current renderer output stable while retiring historical simulations."""
import hashlib
import unittest

from animation.core.manager import PreviewLEDController
from animation.plugins.fluid_tank import FluidTankAnimation
from animation.plugins.living_ecosystem import LivingEcosystemAnimation


class RetainedSimulationTests(unittest.TestCase):
    def test_existing_host_frames_survive_legacy_removal(self):
        # Captured from the public renderers before deleting their unused predecessors.
        expected = {
            FluidTankAnimation: "35e13cfe0bfb3e37aae4d807bca6f33a01ed6262ee07ff245b8de1146c63b390",
            LivingEcosystemAnimation: "97ccdeea675e7564fa3445469da58d9966f22135b6f82c470fbd04ddcd3e2755",
        }
        for renderer, digest in expected.items():
            with self.subTest(renderer=renderer.__name__):
                animation = renderer(PreviewLEDController(33, 138), {"seed": 77})
                result = hashlib.sha256()
                for frame in range(90):
                    result.update(animation.generate_frame(frame / 20, frame).pixels.tobytes())
                self.assertEqual(result.hexdigest(), digest)
