#!/usr/bin/env python3
import sys
from pathlib import Path

repo_root = Path(__file__).resolve()
while repo_root != repo_root.parent and not (repo_root / "animation").exists():
    repo_root = repo_root.parent
sys.path.insert(0, str(repo_root))
from animation.plugins.emoji_arranger import EmojiArrangerAnimation

# Create mock controller (same as in animation_manager.py)
class MockLEDController:
    def __init__(self, strips=8, leds_per_strip=140, **kwargs):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip
        self.debug = kwargs.get('debug', False)
        print(f"🔧 Mock LED Controller: {strips} strips × {leds_per_strip} LEDs = {self.total_leds} total")

    def set_all_pixels(self, pixel_data):
        """Mock set all pixels"""
        pass

controller = MockLEDController(strip_count=8, leds_per_strip=140)

# Create animation with test parameters (using new default)
params = {'text': 'HI🔥'}
animation = EmojiArrangerAnimation(controller, params)

# Also test with old 8-column setting
params_narrow = {'text': 'HI🔥', 'active_columns': 8}
animation_narrow = EmojiArrangerAnimation(controller, params_narrow)

# Generate a frame
frame = animation.generate_frame(0.0, 0)
print('Frame length:', len(frame))
print('First 20 pixels:', frame[:20])

# Check if any pixels are not background color
background = (2, 6, 12)
non_background = [i for i, color in enumerate(frame) if color != background]
print('Non-background pixels:', len(non_background))
if non_background:
    print('First few non-background pixels:', [(i, frame[i]) for i in non_background[:10]])

# Test character patterns
print('\nTesting character patterns:')
print('Available patterns:', list(animation.emoji_patterns.keys())[:10])

# Test text arrangement with different spacing
print('\nTesting text arrangement:')
arranged_1 = animation._arrange_text_with_wrapping('HI🔥', 8, 1)
print('Spacing 1:', arranged_1)

arranged_0 = animation._arrange_text_with_wrapping('HI🔥', 8, 0)
print('Spacing 0:', arranged_0)

# Test with just two characters
arranged_hi = animation._arrange_text_with_wrapping('HI', 8, 0)
print('Just HI (spacing 0):', arranged_hi)

# Test with default settings
print('Default active_columns:', animation.params.get('active_columns'))

# Debug the wrapping logic step by step
print('\nDebugging wrapping logic:')
print('Character widths:')
for char in 'HI🔥':
    if char in animation.emoji_patterns:
        width = animation._get_character_width(char)
        print(f'  {char}: {width} pixels')

# Test with different max_width values
print('\nTesting different max widths:')
for max_w in [8, 9, 16, 17]:
    result = animation._arrange_text_with_wrapping('HI', max_w, 1)
    print(f'  max_width={max_w}, spacing=1: {result}')

# Test individual characters
print('H in patterns:', 'H' in animation.emoji_patterns)
print('I in patterns:', 'I' in animation.emoji_patterns)
print('🔥 in patterns:', '🔥' in animation.emoji_patterns)

# Test character widths
if 'H' in animation.emoji_patterns:
    print('H width:', animation._get_character_width('H'))
if 'I' in animation.emoji_patterns:
    print('I width:', animation._get_character_width('I'))
if '🔥' in animation.emoji_patterns:
    print('🔥 width:', animation._get_character_width('🔥'))
