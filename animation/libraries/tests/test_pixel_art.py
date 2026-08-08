"""Tests for the shared emoji glyph catalog."""

import hashlib
import json
import unittest

from animation.libraries.pixel_art import EMOJI_PATTERNS
from animation.plugins.emoji import EmojiAnimation
from animation.plugins.emoji_arranger import EmojiArrangerAnimation


class _Controller:
    strip_count = 8
    leds_per_strip = 16
    total_leds = strip_count * leds_per_strip
    debug = False


class PixelArtTests(unittest.TestCase):
    def test_catalog_contents_are_stable(self):
        serialized = json.dumps(
            EMOJI_PATTERNS,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        self.assertEqual(len(EMOJI_PATTERNS), 44)
        self.assertEqual(
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "812f10d848b08bc4621334ebfbe115f1d5e8bb97decf047d7b1ffa56043f6715",
        )

    def test_plugins_share_the_canonical_catalog_object(self):
        arranger = EmojiArrangerAnimation(_Controller())

        self.assertIs(EmojiAnimation.EMOJI_PATTERNS, EMOJI_PATTERNS)
        self.assertIs(arranger.emoji_patterns, EMOJI_PATTERNS)


if __name__ == "__main__":
    unittest.main()
