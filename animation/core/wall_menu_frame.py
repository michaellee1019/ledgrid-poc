"""Pixel cards for the installed portrait wall, in logical strip order."""

from dataclasses import dataclass
import unicodedata

import numpy as np

from animation.libraries.clock_face import ClockFaceRenderer
from animation.libraries.pixel_art import EMOJI_PATTERNS


WIDTH, HEIGHT = 33, 138
CARD_HEIGHT = 28
STICK_CARD_HEIGHT = 17
TITLE_HEIGHT = 31
STICK_AXES = (1, 0, 3, 2)
WHITE = (245, 247, 255)
ACCENT = (89, 212, 255)
MUTED = (133, 166, 192)
DISABLED_EDGE = (38, 38, 38)
DISABLED_TEXT = (105, 105, 105)
# Each pair holds the negative and positive frames of a 3×5 arrow sprite.
STICK_ARROWS = (
    ((1, 2, 7, 2, 1), (4, 2, 7, 2, 4)),
    ((2, 2, 2, 7, 2), (2, 7, 2, 2, 2)),
)
FONT = {
    **ClockFaceRenderer.FONT,
    "B": (6, 5, 6, 5, 6), "J": (1, 1, 1, 5, 7), "K": (5, 5, 6, 5, 5),
    "Q": (7, 5, 5, 7, 1), "V": (5, 5, 5, 5, 2), "X": (5, 5, 2, 5, 5),
    "Z": (7, 1, 2, 4, 7), ".": (0, 0, 0, 0, 2), "/": (1, 1, 2, 4, 4),
    "+": (0, 2, 7, 2, 0), "%": (5, 1, 2, 4, 5), "&": (2, 5, 2, 5, 3),
    "<": (1, 2, 4, 2, 1), ">": (4, 2, 1, 2, 4), "?": (7, 1, 3, 0, 2),
}

PALETTE_SWATCHES = {
    "neutral": ("151515", "777777", "eeeeee"),
    "mist": ("172034", "7193c7", "e2efff"),
    "ember": ("2a0c07", "e0522d", "ffca70"),
    "spectrum": ("d6275c", "36c5f0", "f6d743"),
    "rainbow": ("2a0048", "ff285a", "28ff8c"),
    "xmas": ("125330", "c8101c", "ffc43a"),
    "fall": ("a82a12", "e85c12", "ffba40"),
    "easter": ("8d75df", "ff8cba", "baff8c"),
    "july4": ("2850dc", "dc1c24", "f0f4ff"),
}


def rgb(hex_color):
    return tuple(bytes.fromhex(hex_color.removeprefix("#")))


@dataclass(frozen=True)
class MenuCard:
    section: str
    label: str
    value: str = ""
    hint: str = "A OPEN"
    detail: str = ""
    swatches: tuple = ()
    glyph: str = ""
    marker: tuple[int, int] | None = None
    cells: tuple[tuple[int, int], ...] = ()
    level: float | None = None
    top_color: tuple[int, int, int] = ACCENT
    stick_axis: int | None = None
    stick_direction: int = 0
    stick_changed: bool = False


def _display_text(value):
    folded = unicodedata.normalize("NFKD", value.upper()).replace("_", " ")
    return "".join(character if character in FONT else " " for character in folded
                   if not unicodedata.combining(character)).strip()


def _line(value, limit, phase, *, scroll=False):
    value = _display_text(value)
    if not value or (not scroll and len(value) <= limit):
        return value
    # Hold the first characters before moving one glyph at a time.
    marquee = value + " " * max(3, limit - len(value) + 1)
    offset = max(0, phase - 3) % len(marquee)
    return (marquee + marquee)[offset:offset + limit]


def _text(canvas, value, top, color, scale=1):
    width = max(0, len(value) * 4 - 1) * scale
    start = (canvas.shape[0] - width) // 2
    for index, char in enumerate(value):
        for row, bits in enumerate(FONT.get(char, FONT[" "])):
            for column in range(3):
                if bits & (4 >> column):
                    x = start + (index * 4 + column) * scale
                    y = top + row * scale
                    canvas[x:x + scale, y:y + scale] = color


def _stick_arrow(pixels, axis, direction, phase):
    arrow = STICK_ARROWS[axis % 2][int(direction > 0) if direction else (phase // 2) % 2]
    color = WHITE if direction else MUTED
    for y, row in enumerate(arrow, 2):
        for x in range(3):
            if row & (4 >> x):
                pixels[x + 1, y] = color


def _glyph(canvas, glyph):
    pattern = EMOJI_PATTERNS.get(glyph)
    if pattern is None or len(glyph) == 1 and glyph.upper() in FONT:
        _text(canvas, glyph.upper() if glyph != " " else "-", 8, WHITE, 2)
        return
    colors = {"F": WHITE, "H": ACCENT, "M": (255, 136, 92), "E": MUTED}
    scale = 2 if max(map(len, pattern)) <= 15 and len(pattern) <= 6 else 1
    for row, marks in enumerate(pattern):
        start = (WIDTH - len(marks) * scale) // 2
        for column, mark in enumerate(marks):
            if mark in colors:
                x = start + column * scale
                y = 8 + row * scale
                canvas[x:x + scale, y:y + scale] = colors[mark]


class WallMenuFrame:
    def __init__(self):
        self._keys = [None] * 5
        self._pixels = [None] * 5
        self._stack = None

    def render_sections(self, sections):
        panels = []
        top = HEIGHT
        for slot, section in enumerate(sections):
            panel = self.render(*section, slot=slot, top=top)
            panels.append(panel)
            top = panel.start
        panels = tuple(panels)
        if len(panels) <= 1:
            return panels[0] if panels else None
        if self._stack is None or self._stack.panels != panels:
            self._stack = MenuStack(panels)
        return self._stack

    def render(self, card, phase=0, *, slot=0, top=HEIGHT):
        if card.stick_axis is not None:
            return self._render_stick(card, phase, slot, top)
        if card.section == "Animation title":
            return self._render_title(card)
        heading = _line(card.label if card.value or card.glyph else card.section, 8, phase)
        label = _line(card.label, 8, phase)
        value = _display_text(card.value)
        scale = 2 if len(value) <= 4 else 1
        value = _line(value, 8, phase)
        detail = _line(card.detail, 8, phase)
        hint = _line(card.hint, 8, phase)
        position = top - CARD_HEIGHT
        key = (card, heading, label, value, detail, hint, position)
        if key == self._keys[slot]:
            return self._pixels[slot]
        card_pixels = np.empty((WIDTH, CARD_HEIGHT, 3), dtype=np.uint8)
        card_pixels[:] = (5, 10, 19)
        card_pixels[1:-1, 0] = card.top_color
        _text(card_pixels, heading, 2, ACCENT)
        if card.glyph:
            _glyph(card_pixels, card.glyph)
        else:
            _text(card_pixels, value or label, 7, WHITE, scale if value else 1)
        if card.swatches:
            swatch_width = 6
            start = (WIDTH - len(card.swatches) * (swatch_width + 1) + 1) // 2
            for index, color in enumerate(card.swatches):
                x = start + index * (swatch_width + 1)
                card_pixels[x:x + swatch_width, 19:23] = color
        elif card.level is not None:
            card_pixels[4:29, 19:21] = (35, 57, 72)
            filled = round(card.level * 25)
            card_pixels[4:4 + filled, 19:21] = ACCENT
        else:
            _text(card_pixels, detail, 18, MUTED)
        _text(card_pixels, hint, 23, ACCENT)
        panel = np.ascontiguousarray(card_pixels[:, ::-1])
        panel.setflags(write=False)
        self._keys[slot] = key
        self._pixels[slot] = MenuPixels(position, panel, card.marker, card.cells)
        return self._pixels[slot]

    def _render_stick(self, card, phase, slot, top):
        # Three arrow pixels and one gap leave room for a seventh name glyph.
        assigned = bool(card.label)
        name = _line(card.label, 7, phase, scroll=True)
        value = _line(card.value, 8, 0)
        arrow_phase = (phase // 2) % 2 if assigned and not card.stick_direction else 0
        key = (card, name, value, arrow_phase, top)
        if key == self._keys[slot]:
            return self._pixels[slot]
        pixels = np.full((WIDTH, STICK_CARD_HEIGHT, 3), (5, 10, 19) if assigned else 9, dtype=np.uint8)
        edge = card.top_color if assigned else DISABLED_EDGE
        pixels[1:-1, 0] = edge
        pixels[0 if card.stick_axis < 2 else -1] = edge
        if not assigned:
            _text(pixels, value, 8, DISABLED_TEXT)
            return self._remember_panel(slot, key, pixels, top)
        _stick_arrow(pixels, card.stick_axis, card.stick_direction, phase)
        _text(pixels[5:-1], name, 2, WHITE if card.stick_changed else ACCENT)
        _text(pixels, value, 8, WHITE)
        if card.level is not None:
            pixels[4:29, 15] = (35, 57, 72)
            pixels[4:4 + round(card.level * 25), 15] = card.top_color
        elif card.swatches:
            for index, color in enumerate(card.swatches):
                x = 6 + index * 7
                pixels[x:x + 6, 15] = color
        return self._remember_panel(slot, key, pixels, top)

    def _render_title(self, card):
        if card == self._keys[0]:
            return self._pixels[0]
        pixels = np.full((WIDTH, TITLE_HEIGHT, 3), (5, 10, 19), dtype=np.uint8)
        pixels[1:-1, 0] = card.top_color
        title = _display_text(card.label)
        lines = []
        while title and len(lines) < 4:
            end = min(8, len(title))
            if len(title) > 8 and " " in title[:9]:
                end = title.rindex(" ", 0, 9)
            lines.append(title[:end])
            title = title[end:].lstrip()
        if title:
            lines[-1] = lines[-1][:7] + "."
        for index, line in enumerate(lines):
            _text(pixels, line, (TITLE_HEIGHT - 7 * len(lines) + 2) // 2 + index * 7, WHITE)
        return self._remember_panel(0, card, pixels, HEIGHT)

    def _remember_panel(self, slot, key, pixels, top):
        panel = np.ascontiguousarray(pixels[:, ::-1])
        panel.setflags(write=False)
        self._keys[slot] = key
        self._pixels[slot] = MenuPixels(top - panel.shape[1], panel, None, ())
        return self._pixels[slot]


@dataclass(frozen=True, eq=False)
class MenuStack:
    panels: tuple["MenuPixels", ...]

    def apply(self, frame):
        for panel in self.panels:
            panel.apply(frame)
        return frame


@dataclass(frozen=True, eq=False)
class MenuPixels:
    start: int
    panel: np.ndarray
    marker: tuple[int, int] | None
    cells: tuple[tuple[int, int], ...]

    def apply(self, frame):
        grid = frame.reshape(WIDTH, HEIGHT, 3)
        if self.cells:
            x, y = zip(*self.cells)
            grid[x, y] = MUTED
        grid[:, self.start:self.start + self.panel.shape[1]] = self.panel
        if self.marker is not None:
            x, y = self.marker
            grid[max(x - 1, 0):x + 2, y] = ACCENT
            grid[x, max(y - 1, 0):y + 2] = ACCENT
            grid[x, y] = WHITE
        return frame
