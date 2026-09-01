"""Compatibility guard: Fireworks now receives plant effects only through Scene v2."""

import unittest

from animation.plugins.fireworks import FireworksAnimation


class FireworksPlantContractTests(unittest.TestCase):
    def test_declares_effect_intent_without_legacy_plant_parameters(self) -> None:
        descriptor = FireworksAnimation.component_descriptor()
        self.assertEqual(tuple(capability.value for capability in descriptor.plant_capabilities), ("effect_intent",))
        self.assertFalse(FireworksAnimation.PLANT_MODIFIER_SUPPORT)
        with self.assertRaisesRegex(ValueError, "non-local parameters"):
            FireworksAnimation(None, {"plant_modifiers": {}})


if __name__ == "__main__":
    unittest.main()
