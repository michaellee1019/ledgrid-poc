"""The Nimbus Composer menu. Navigation is independent of HID and wall output."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
import colorsys
import math
import time
import uuid

from animation.core.plant_awareness import FIELD_MODIFIERS, PLANT_MODIFIER_IDS, SURFACE_MODIFIERS
from animation.core.wall_menu_frame import HEIGHT, PRESET_COLUMNS, STICK_AXES, WIDTH, MenuCard, PALETTE_SWATCHES, rgb
from animation.libraries.pixel_art import EMOJI_PATTERNS
from animation.libraries.scene_palettes import SCENE_PALETTE_IDS
from ipc.playlist_runtime import PLAYLIST_COMMAND_SCHEMA, PLAYLIST_VERSION
from ipc.scene_contract import MAX_SCENE_PACE, SCENE_PACE_STEP, normalize_composer_scene
from web.composer_content import composer_presets, gallery_entries
from web.composer_playlist_store import ComposerPlaylistStore
from web.numbered_preset_store import PRESET_COUNT, NumberedPresetStore
from web.scene_look_store import SceneLookStore
from web.starter_looks import get_starter, list_starters


SCHEMA = "ledgrid.scene.v2"
DIRECTION = {"left": -1, "right": 1, "up": -1, "down": 1}
PALETTE_NAMES = {**{key: key.title() for key in SCENE_PALETTE_IDS}, "xmas": "Christmas", "july4": "4th July"}


# Four slots per held chord, ordered LX, LY, RX, RY. Each setting has one
# owner. Missing animation and Widget slots stay unassigned.
STICK_BANKS = (
    ("palette", None, 0, "light"),                    # None
    ("background", None, None, None),                 # L1
    (4, 1, 2, 3),                                    # R1
    (5, 6, 7, 8),                                    # L1 + R1
    ("widget_x", "widget_y", "widget_0", "widget_1"),  # L2
    ("widget_2", "widget_3", "widget_4", "widget_5"),  # L1 + L2
    (9, 10, 11, 12),                                 # R1 + L2
    (13, 14, 15, 16),                                # L1 + R1 + L2
    ("illuminate", "shadow", "hue_shift", None),       # R2
    ("refract", None, None, None),                   # L1 + R2
    (None, None, None, "pace"),                      # R1 + R2
    (17, 18, 19, 20),                                # L1 + R1 + R2
    ("widget_6", "widget_7", "widget_8", "widget_9"),  # L2 + R2
    ("widget_10", "widget_11", "widget_12", "widget_13"), # L1 + L2 + R2
    (21, 22, 23, 24),                                # R1 + L2 + R2
    (25, 26, 27, 28),                                # All four
)
STICK_SCENE_FIELDS = {
    "pace": (("look", "pace"), 0, MAX_SCENE_PACE, SCENE_PACE_STEP),
    "light": (("look", "presentation_brightness"), .1, 2, .05),
    "background": (("background", "parameters", "gain"), 0, 1, .05),
}
STICK_OPTICS = ("illuminate", "shadow", "hue_shift", "refract")
STICK_FEEDBACK_SECONDS = .6
STICK_BANK_COLORS = tuple(map(rgb, (
    "59d4ff", "ffc15d", "ff6567", "ab87ff", "53e7ac", "ff90c9", "e7ef64", "729cff",
    "ff953d", "d09c64", "49eee8", "fe6bfb", "7ee353", "ffcfc0", "a4c5eb", "f7f5f1",
)))
STICK_LABELS = {
    "pace": "Speed", "light": "Scene light", "background": "Background light",
    "illuminate": "Illuminate", "shadow": "Shadow", "hue_shift": "Hue shift", "refract": "Refraction",
    "widget_x": "Widget across", "widget_y": "Widget height",
}
def _parameter_bounds(parameters, key, definition):
    low = definition.get("ui_min", definition["min"])
    high = definition.get("ui_max", definition["max"])
    # A saved value outside the suggested range stays reachable.
    low, high = min(low, parameters[key]), max(high, parameters[key])
    if "min_parameter" in definition:
        low = max(low, parameters[definition["min_parameter"]])
    if "max_parameter" in definition:
        high = min(high, parameters[definition["max_parameter"]])
    return low, high


def _parameter_visible(parameters, definition):
    condition = definition.get("visible_when")
    if not condition:
        return True
    value = parameters[condition["parameter"]]
    return condition.get("at_least", -math.inf) <= value < condition.get("below", math.inf)


def _choice_labels(definition, options):
    labels = definition.get("option_labels", {})
    return {option: labels.get(str(option).lower(), str(option).replace("_", " ")) for option in options}


@dataclass(frozen=True)
class StickBinding:
    path: tuple
    minimum: float = 0
    maximum: float = 1
    step: float = .05
    integer: bool = False
    label: str = ""


@dataclass(frozen=True)
class StickFocus:
    binding: StickBinding | str | None
    bank: int
    since: float
    direction: int = 0
    direction_until: float = 0
    changed_until: float = 0


@dataclass
class Row:
    name: str
    value: str = ""
    select: Callable | None = None
    adjust: Callable | None = None
    swatches: tuple = ()
    hint: str = ""
    level: float | None = None


@dataclass
class Page:
    title: str
    rows: Callable
    index: int = 0
    playing: int | None = None
    playing_scene: dict | None = None
    stick_shortcut: bool = False


@dataclass
class CharacterPicker:
    label: str
    text: str
    limit: int
    submit: Callable
    groups: tuple
    group: int = 0
    index: int = 0

    def handle(self, button):
        if button in ("up", "down"):
            self.group = (self.group + DIRECTION[button]) % len(self.groups)
            self.index = 0
        elif button in ("left", "right"):
            self.index = (self.index + DIRECTION[button]) % len(self.groups[self.group][1])
        elif button == "a":
            char = self.groups[self.group][1][self.index]
            if char == "BACK":
                self.text = self.text[:-1]
            elif len(self.text) < self.limit:
                self.text += char

    def card(self):
        group, chars = self.groups[self.group]
        char = chars[self.index]
        return MenuCard(self.label, group, value=char if char == "BACK" else "",
                        glyph=char if char != "BACK" else "", detail=self.text[-8:],
                        hint="Y DONE")


@dataclass
class PositionPicker:
    label: str
    x: int
    y: int
    update: Callable
    cells: tuple | None = None

    def handle(self, button):
        if button in ("left", "right"):
            self.x = max(0, min(WIDTH - 1, self.x + DIRECTION[button]))
        elif button in ("up", "down"):
            self.y = max(0, min(HEIGHT - 1, self.y - DIRECTION[button]))
        if self.cells is None:
            if button in DIRECTION:
                self.update(self.x, self.y)
        elif button == "a":
            point = (self.x, self.y)
            cells = set(self.cells)
            cells.symmetric_difference_update((point,))
            candidate = tuple(sorted(cells))
            self.update(candidate)
            self.cells = candidate

    def card(self):
        return MenuCard("Tune", self.label, f"{self.x}/{self.y}",
                        hint="A CELL" if self.cells is not None else "D MOVE",
                        marker=(self.x, self.y), cells=self.cells or ())


@dataclass
class PresetPicker:
    save: bool
    occupied: tuple[bool, ...]
    index: int

    def move(self, button):
        row, column = divmod(self.index, PRESET_COLUMNS)
        if button in ("left", "right"):
            column = (column + DIRECTION[button]) % PRESET_COLUMNS
        else:
            row = (row + DIRECTION[button]) % (PRESET_COUNT // PRESET_COLUMNS)
        self.index = row * PRESET_COLUMNS + column

    def card(self):
        occupied = self.occupied[self.index]
        title = "SAVE" if self.save else "LOAD"
        if self.save:
            hint = "A WRITE" if occupied else "A SAVE"
        else:
            hint = "A LOAD" if occupied else "B BACK"
        value = "Filled" if occupied else "Empty"
        return MenuCard(title, f"Preset {self.index + 1:02d}", value, hint=hint,
                        preset_selection=self.index, preset_slots=self.occupied)


class WallComposer:
    """Translate buttons into ordered local controller actions."""

    def __init__(self, manager, coordinator, runner, command, root, state_dir, presets_dir,
                 *, clock=time.monotonic):
        self.manager = manager
        self.owner = coordinator
        self.runner = runner
        self.command = command
        self.root = root
        self.catalog = manager.scene_v2_component_catalog()
        self.presets = composer_presets(root)
        self.presets_dir = Path(presets_dir)
        self.looks = SceneLookStore(Path(state_dir) / "composer_looks.json")
        self.playlists = ComposerPlaylistStore(Path(state_dir) / "composer_playlists.json")
        self.numbered_presets = NumberedPresetStore(Path(state_dir) / "numbered_presets", self.catalog)
        self._preset_index = 0
        self._browser_playlist_adapter = None
        self.clock = clock
        self.pages = []
        self.picker = None
        self.feedback = None
        self.feedback_until = 0
        self.card_since = self.clock()
        self._gallery = None
        self.error = None
        self.saved_revision = 0
        self.playback_revision = 0
        self._undo_scene = None
        self._undo_group = None
        self._adjustment = None
        self._keep_undo = False
        self._undo_hint_until = 0.0
        self._redo = False
        self._horizontal_hold = None
        self._stick_focus: dict[int, StickFocus] = {}
        self.shoulder_bank = 0
        self.momentary = False
        self._aim = None
        self._animation_id = manager.current_animation_name
        self._animation_title = None
        self._animation_title_until = 0.0

    @property
    def is_open(self):
        return bool(self.pages)

    def scene(self):
        current = self.manager.get_scene_state() or self.owner.selected_scene
        return current if isinstance(current, dict) and current.get("schema") == SCHEMA else None

    def open(self):
        self.close()
        self.pages = [Page("Composer", self._root, 1 if self.scene() else 0)]
        self.card_since = self.clock()

    def close(self):
        self.pages.clear()
        self.momentary = False
        self._aim = None
        self._stick_focus.clear()
        self.picker = None
        self.feedback = None
        self._clear_undo()

    def _clear_undo(self):
        self._undo_scene = None
        self._undo_group = None
        self._undo_hint_until = 0.0
        self._redo = False
        self._horizontal_hold = None

    def _in_tune(self):
        return any(page.title == "Tune" for page in self.pages)

    def refresh_scene(self):
        self.leave_aim()
        if self.momentary:
            self.close()
            return
        self._clear_undo()
        for page in self.pages:
            page.playing = None
            page.playing_scene = None
        if self.is_open and (self.picker or self._in_tune()):
            if self.pages[-1].stick_shortcut:
                self.pages.pop()
                self._stick_focus.clear()
            self.pages = self.pages[:2]
            self.picker = None
            self.feedback = None
            self.card_since = self.clock()

    def _push(self, title, rows):
        self.pages.append(Page(title, rows if callable(rows) else lambda: rows))

    def _root(self):
        rows = [Row("Browse", select=self._browse), Row("Tune", select=self._tune),
                Row("Playback", select=self._playback), Row("Presets", select=self._numbered_presets)]
        if self.manager.nimbus_game()[0] is not None:
            rows.append(Row("Controls", select=self._game_controls))
        return rows

    def _game_controls(self):
        game, _state = self.manager.nimbus_game()
        if game is None:
            return
        rows = [Row(action.label, button.upper(), hint="UP/DOWN")
                for button, action in game.input_capability().actions.items() if not action.position]
        self._push("Controls", rows)

    def _numbered_presets(self):
        self._push("Presets", [Row("Save", select=partial(self._preset_picker, True)),
                               Row("Load", select=partial(self._preset_picker, False))])

    def _preset_picker(self, save):
        if save and self.scene() is None:
            raise ValueError("Choose a scene first")
        self.picker = PresetPicker(save, self.numbered_presets.occupied(), self._preset_index)

    def _confirm_preset(self):
        picker = self.picker
        slot = picker.index + 1
        self._preset_index = picker.index
        if picker.save:
            scene = normalize_composer_scene({"origin": "composer", "scene": self.scene()}, self.catalog)
            self.numbered_presets.save(slot, scene)
            self.picker = None
            self._feedback("Saved", f"Slot {slot:02d}")
        else:
            scene = self.numbered_presets.load(slot)
            self._play_scene(scene.scene, mark_playing=False)
            self.picker = None
            self._feedback("Loaded", f"Slot {slot:02d}")

    def _feedback(self, label, value="", *, error=None):
        self.feedback = MenuCard("Composer", label, value, hint="A OK")
        self.card_since = self.clock()
        self.feedback_until = self.card_since + (5 if error else 2)
        if error:
            self.error = str(error)

    def _row(self):
        page = self.pages[-1]
        rows = page.rows() or [Row("Empty", hint="B BACK")]
        page.index = max(0, min(page.index, len(rows) - 1))
        return page, rows

    def card(self):
        if self.feedback:
            if self.clock() < self.feedback_until:
                return self.feedback
            self.feedback = None
        if self._aim is not None:
            _, binding, x, y = self._aim
            if binding.horizontal_zones:
                zone = binding.horizontal_zones[x * len(binding.horizontal_zones) // WIDTH]
                return MenuCard("Aim", binding.label, zone, hint=f"{binding.button.upper()} APPLY",
                                marker=(x, y))
            coordinates = f"{x}/{y}" if "y" in binding.position_axes else str(x)
            return MenuCard("Aim", binding.label, coordinates, hint=f"{binding.button.upper()} APPLY",
                            marker=(x, y))
        if not self.is_open:
            if self.manager.output_brightness > 0:
                _, state = self.manager.nimbus_game()
                if state == "paused":
                    return MenuCard("Game", "Paused", hint="A RESUME")
                if state == "ready":
                    return MenuCard("Game", "A PLAY", hint="UP/DOWN")
            return None
        if self.picker:
            return self.picker.card()
        page, rows = self._row()
        card = self._row_card(page, rows)
        if page.stick_shortcut:
            return self._stick_card(card, tuple(self._stick_focus.values())[page.index])
        return card

    def hud_sections(self):
        now = self.clock()
        self.observe_animation()
        headers = ()
        if self.is_open:
            bank = self.shoulder_bank
            headers = ((MenuCard("Shoulders", "", top_color=STICK_BANK_COLORS[bank], shoulder_bank=bank), 0),)
        if self._animation_title is not None and now < self._animation_title_until:
            headers = (*headers, (self._animation_title, 0))
        card = self.card()
        if card is None:
            return headers
        page = self.pages[-1] if self.pages else None
        if self.feedback or self.picker or page is None or not page.stick_shortcut:
            phase = int((now - self.card_since) * 4)
            return (*headers, (card, phase))
        sections = []
        for slot, focus in self._stick_focus.items():
            section = card
            if slot != page.index:
                row = self._stick_row(self.scene(), focus.binding)
                section = self._stick_card(MenuCard("Tune", row.name, row.value, hint="",
                                                    swatches=row.swatches, level=row.level), focus)
            section = replace(section, stick_axis=STICK_AXES[slot],
                              stick_direction=focus.direction if now < focus.direction_until else 0,
                              stick_changed=now < focus.changed_until)
            sections.append((section, int((now - focus.since) * 4)))
        return (*headers, *sections)

    def observe_animation(self):
        animation = self.manager.current_animation_name if self.manager.is_running else None
        if animation == self._animation_id:
            return
        previous = self._animation_id
        self._animation_id = animation
        self._animation_title = None
        if animation is not None and previous is not None:
            plugin = self.manager.plugin_loader.get_plugin(animation)
            label = getattr(plugin, "ANIMATION_NAME", None) or animation.replace("_", " ").title()
            self._animation_title = MenuCard("Animation title", label)
            self._animation_title_until = self.clock() + 1.0

    @staticmethod
    def _stick_card(card, focus):
        return replace(card, top_color=STICK_BANK_COLORS[focus.bank])

    def _row_card(self, page, rows):
        row = rows[page.index]
        playing = page.index == page.playing
        hint = row.hint or ("A EDIT" if row.select and row.adjust else "< EDIT >" if row.adjust
                            else "A OPEN" if row.select else "B BACK")
        detail = f"{page.index + 1}/{len(rows)}"
        if playing:
            hint, detail = "Y TUNE", "PLAYING"
        elif self._undo_scene is not None and self._in_tune():
            undo_hint = "Y REDO" if self._redo else "Y UNDO"
            if self.clock() < self._undo_hint_until:
                hint = undo_hint
            else:
                detail = undo_hint
        return MenuCard(page.title, row.name, row.value, hint=hint, detail=detail,
                        swatches=row.swatches, level=row.level)

    def press(self, button):
        if button == "menu":
            self.close() if self.is_open else self.open()
            return
        if not self.is_open:
            return
        fast = button.startswith("fast-")
        repeated = fast or button.startswith("repeat-")
        if repeated:
            button = button.split("-", 1)[1]
        else:
            self._undo_group = None
            self._horizontal_hold = None
        if self.feedback:
            self.feedback = None
            if button in ("a", "b"):
                return
        try:
            if self.picker:
                if isinstance(self.picker, PresetPicker):
                    if button == "b":
                        self._preset_index = self.picker.index
                        self.picker = None
                    elif button == "a":
                        self._confirm_preset()
                    elif button in DIRECTION and (button not in ("left", "right") or self._accept_horizontal(
                            button, repeated, id(self.picker))):
                        self.picker.move(button)
                    return
                self.card_since = self.clock()
                if button == "b" or button == "y" and isinstance(self.picker, PositionPicker):
                    self.picker = None
                elif button == "y":
                    self.picker.submit(self.picker.text)
                    self.picker = None
                elif button not in ("left", "right") or self._accept_horizontal(
                        button, repeated, id(self.picker)):
                    self.picker.handle(button)
                return
            page, rows = self._row()
            row = rows[page.index]
            if button == "b":
                leaving_tune = page.title == "Tune"
                self.pages.pop()
                if page.stick_shortcut:
                    self._stick_focus.clear()
                self.card_since = self.clock()
                if leaving_tune:
                    self._clear_undo()
                if not self.pages:
                    self.close()
            elif button == "y":
                if self._in_tune() and self._undo_scene is not None:
                    self._undo()
                elif page.index == page.playing:
                    self._tune()
            elif button in ("up", "down"):
                page.index = (page.index + DIRECTION[button]) % len(rows)
                if not page.stick_shortcut and len(rows) > 1:
                    self.card_since = self.clock()
            elif button in ("left", "right"):
                focus = (id(page), page.index, row.name) if row.adjust else id(page)
                if not self._accept_horizontal(button, repeated, focus):
                    return
                if row.adjust:
                    self._adjustment = self._horizontal_hold
                    self._keep_undo = repeated and self._undo_group == self._adjustment
                    try:
                        row.adjust(DIRECTION[button], steps=5 if fast else 1)
                    finally:
                        self._adjustment = None
                        self._keep_undo = False
                elif len(rows) > 1:
                    step = 8 if len(rows) > 8 else 1
                    page.index = max(0, min(len(rows) - 1, page.index + step * DIRECTION[button]))
                    self.card_since = self.clock()
            elif button == "a" and row.select:
                if page.index != page.playing or page.playing_scene != self.scene():
                    self.card_since = self.clock()
                    row.select()
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
            reason = str(error)
            self._feedback("Unavailable" if "unsupported" in reason.lower() else "Error", reason, error=error)

    def _accept_horizontal(self, button, repeated, focus):
        hold = (focus, button)
        if repeated:
            return self._horizontal_hold == hold
        self._horizontal_hold = hold
        return True

    def _run(self, action, data, *, close=False, show_takeover=True):
        had_playlist = self.runner.status()["phase"] == "running"
        self.observe_animation()
        if self.command(action, data) is False:
            raise ValueError(getattr(self.manager, "_playback_error", None) or "Command rejected")
        self.observe_animation()
        self.error = None
        if close:
            self.close()
        if had_playlist and self.runner.advance()["phase"] == "overridden" and show_takeover:
            self._feedback("Manual", "Takeover")
        return True

    def _set_scene(self, scene, *, edit=False):
        canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, self.catalog)
        before = self.scene() if edit else None
        if canonical.scene == before:
            return False
        action = "tune_scene" if edit and self.manager.is_running else "start_scene"
        self._run(action, {"scene": canonical.scene},
                  show_takeover=not self.pages or not self.pages[-1].stick_shortcut)
        if before is not None and self.pages and self.pages[-1].stick_shortcut:
            until = self.clock() + STICK_FEEDBACK_SECONDS
            for slot, focus in self._stick_focus.items():
                if self._stick_value(before, focus.binding) != self._stick_value(canonical.scene, focus.binding):
                    self._stick_focus[slot] = replace(focus, changed_until=until)
        if before is not None and self._in_tune():
            if not self._keep_undo:
                self._undo_scene = before
            self._undo_group = self._adjustment
            self._redo = False
            self._undo_hint_until = self.clock() + 1.5
        return True

    def quick_browse(self, button):
        """Browse from the D-pad while the Composer HUD is closed."""
        if self.is_open or not self.manager.is_running:
            return False
        scene = self.scene()
        if scene is None:
            return False
        try:
            if button in ("left", "right"):
                self._cycle_palette(scene, DIRECTION[button])
                self._run("tune_scene", {"scene": scene}, show_takeover=False)
                return False
            gallery = self._animation_gallery()
            direction = DIRECTION[button]
            active = scene["animation"]["component_id"]
            index = next((index for index, item in enumerate(gallery) if item["component_id"] == active),
                         -1 if direction > 0 else 0)
            scene = self._with_animation(gallery[(index + direction) % len(gallery)], None)
            self._run("start_scene", {"scene": scene}, show_takeover=False)
            return True
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
            self.error = str(error)
            return False

    def play_game(self, target, state):
        try:
            if state == "ready" and self.runner.status()["phase"] == "running":
                # A player taking over a scheduled Scene owns the same
                # playback revision boundary as manual Scene edits.
                with self.owner.legacy_mutation_guard():
                    started = self.manager.dispatch_game_input(target, "play")
                self.runner.advance()
                return started
            return self.manager.dispatch_game_input(target, "play")
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._feedback("Game", str(error), error=error)
            return False

    @property
    def aiming(self):
        return self._aim is not None

    def begin_aim(self, basis, binding):
        if self._aim is None or self._aim[0] != basis:
            self.card_since = self.clock()
            self._aim = (basis, binding, WIDTH // 2, HEIGHT // 2)

    def leave_aim(self):
        self._aim = None

    def aim_sticks(self, motions):
        basis, binding, x, y = self._aim
        for motion in motions:
            if motion.axis == 0 and "x" in binding.position_axes:
                x = max(0, min(WIDTH - 1, x + motion.steps))
            elif motion.axis == 1 and "y" in binding.position_axes:
                y = max(0, min(HEIGHT - 1, y + 4 * motion.steps))
        self._aim = (basis, binding, x, y)

    def fire_aim(self, action_id):
        basis, binding, x, y = self._aim
        component_id, scene_digest = basis
        payload = dict(component_id=component_id, scene_digest=scene_digest, kind=action_id, x=x, y=y)
        try:
            if self.runner.status()["phase"] == "running":
                self._run("automation_interaction", payload, show_takeover=False)
            else:
                self.manager.dispatch_canonical_interaction(**payload)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self._feedback("Aim", str(error), error=error)

    def tune_sticks(self, motions):
        """Apply a stick gesture and focus its setting when the HUD is open."""
        if self.is_open:
            if self.picker:
                return
            if not self.pages[-1].stick_shortcut:
                motions = [motion for motion in motions if motion.bank and not motion.repeated]
        if not motions or not self.manager.is_running:
            return
        scene = self.manager.get_scene_state()
        if not isinstance(scene, dict) or scene.get("schema") != SCHEMA:
            return
        original = None if self.is_open else deepcopy(scene)
        try:
            bank = motions[-1].bank
            bindings = self._stick_bank(scene, bank)
            self._apply_stick_motions(scene, motions, bindings=bindings)
            if self.is_open:
                gesture = (bank, tuple(motion.axis for motion in motions))
                self._horizontal_hold = None
                self._show_stick_bank(bank, bindings)
                self._focus_stick_motions(bank, bindings, motions)
                self.feedback = None
                repeated = all(motion.repeated for motion in motions)
                self._adjustment = gesture
                self._keep_undo = self._undo_group == gesture and repeated
                try:
                    self._set_scene(scene, edit=True)
                finally:
                    self._adjustment = None
                    self._keep_undo = False
            elif scene != original:
                self._run("tune_scene", {"scene": scene}, show_takeover=False)
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
            self.error = str(error)

    @staticmethod
    def _cycle_palette(scene, direction):
        palette = scene["look"]["palette_id"]
        index = (SCENE_PALETTE_IDS.index(palette) if palette in SCENE_PALETTE_IDS
                 else -1 if direction > 0 else 0)
        scene["look"]["palette_id"] = SCENE_PALETTE_IDS[(index + direction) % len(SCENE_PALETTE_IDS)]

    def select_stick_bank(self, bank):
        """Expose the held chord's bindings without applying an edit."""
        self.shoulder_bank = bank
        if not bank and self.momentary:
            self.close()
            return
        if self.picker or (not bank and (not self.is_open or not self.pages[-1].stick_shortcut)):
            return
        scene = self.scene()
        if scene is None:
            return
        try:
            bindings = self._stick_bank(scene, bank)
            if not self.is_open:
                self.momentary = True
            self._show_stick_bank(bank, bindings)
        except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
            self.error = str(error)

    def _show_stick_bank(self, bank, bindings):
        if not self.pages or not self.pages[-1].stick_shortcut:
            self.pages.append(Page("Tune", self._stick_rows, stick_shortcut=True))
        if self._stick_focus and self._stick_focus[0].bank == bank:
            return
        now = self.clock()
        for slot, axis in enumerate(STICK_AXES):
            self._set_stick_focus(slot, bindings[axis], bank, now)
        self._horizontal_hold = None
        self.feedback = None

    def _set_stick_focus(self, slot, binding, bank, now):
        current = self._stick_focus.get(slot)
        since = current.since if current is not None and current.binding == binding else now
        self._stick_focus[slot] = StickFocus(binding, bank, since)

    def _focus_stick_motions(self, bank, bindings, motions):
        until = self.clock() + STICK_FEEDBACK_SECONDS
        for motion in motions:
            if motion.bank == bank and bindings[motion.axis] is not None:
                slot = STICK_AXES.index(motion.axis)
                self._stick_focus[slot] = replace(self._stick_focus[slot],
                                                  direction=1 if motion.steps > 0 else -1,
                                                  direction_until=until)
        active = [motion for motion in motions if not motion.repeated and motion.bank == bank
                  and bindings[motion.axis] is not None]
        if active:
            strongest = max(active, key=lambda motion: abs(motion.steps))
            self.pages[-1].index = STICK_AXES.index(strongest.axis)

    def _stick_rows(self):
        scene = self.scene()
        return [self._stick_row(scene, focus.binding) for focus in self._stick_focus.values()]

    def _stick_row(self, scene, binding):
        if binding is None:
            return Row("", "--", hint="B BACK")
        value = self._stick_value(scene, binding)
        if binding == "palette":
            return self._choice("Palette", value, SCENE_PALETTE_IDS,
                                 partial(self._edit, ("look", "palette_id")), labels=PALETTE_NAMES,
                                 swatches=tuple(map(rgb, PALETTE_SWATCHES.get(value, ()))))
        if binding in STICK_OPTICS:
            return self._number(STICK_LABELS[binding], value, 0, 1, .1,
                                 partial(self._edit_stick_optic, binding))
        return self._number(binding.label, value, binding.minimum, binding.maximum,
                             binding.step, partial(self._edit_stick_value, binding), integer=binding.integer)

    @staticmethod
    def _stick_value(scene, binding):
        if binding is None:
            return None
        if binding == "palette":
            return scene["look"]["palette_id"]
        if binding in STICK_OPTICS:
            return scene["plants"]["effects"]["strengths"].get(binding, 0)
        parent = scene
        for key in binding.path[:-1]:
            parent = parent[key]
        return parent.get(binding.path[-1], 0)

    def _edit_stick_optic(self, name, value):
        scene = self.scene()
        effects = scene["plants"]["effects"]
        current = effects["strengths"].get(name, 0)
        self._adjust_stick_optic(effects, name, round((value - current) / .1))
        self._set_scene(scene, edit=True)

    def _edit_stick_value(self, binding, value):
        scene = self.scene()
        self._set_stick_value(scene, binding, value)
        self._set_scene(scene, edit=True)

    def _apply_stick_motions(self, scene, motions, *, bindings=None):
        bank = motions[-1].bank
        if bindings is None:
            bindings = self._stick_bank(scene, bank)
        for motion in motions:
            if motion.bank != bank:
                continue
            binding = bindings[motion.axis]
            if binding is None:
                continue
            if binding == "palette":
                if not motion.repeated:
                    self._cycle_palette(scene, 1 if motion.steps > 0 else -1)
            elif binding in STICK_OPTICS:
                self._adjust_stick_optic(scene["plants"]["effects"], binding, motion.steps)
            else:
                self._adjust_stick_binding(scene, binding, motion.steps)

    def _stick_bank(self, scene, bank):
        component = scene["animation"]
        descriptor = self.catalog.require(provider=component["provider"],
                                          component_id=component["component_id"], version=component["version"])
        animation = self._numeric_stick_bindings(component, ("animation", "parameters"))
        candidates = {
            **{name: StickBinding(*fields, label=STICK_LABELS[name]) for name, fields in STICK_SCENE_FIELDS.items()},
            **{name: name for name in ("illuminate", "shadow", "hue_shift", "palette")},
            **dict(enumerate(animation)),
        }
        plugin = self.manager.plugin_loader.get_plugin(descriptor.component_id)
        if "refract" in getattr(plugin, "PLANT_MODIFIER_SUPPORT", ()):
            candidates["refract"] = "refract"
        if scene["widgets"]:
            widget = scene["widgets"][0]
            placement = ("widgets", 0, "placement")
            candidates.update(
                widget_x=StickBinding((*placement, "strip_translation"), -32, 32, 2, True, STICK_LABELS["widget_x"]),
                widget_y=StickBinding((*placement, "led_translation"), -137, 137, 4, True, STICK_LABELS["widget_y"]),
            )
            for index, binding in enumerate(self._numeric_stick_bindings(
                    widget["component"], ("widgets", 0, "component", "parameters"))):
                candidates[f"widget_{index}"] = binding

        return tuple(candidates.get(key) for key in STICK_BANKS[bank])

    def _numeric_stick_bindings(self, component, path):
        descriptor = self._descriptor(component)
        schema = descriptor.parameter_schema
        parameters = component["parameters"]
        bindings = []
        for key in dict.fromkeys((*descriptor.preferred_parameters, *schema)):
            definition = schema[key]
            if (key not in parameters or key == "seed" or "fps" in key or definition.get("options")
                    or definition.get("control") == "fixed"
                    or definition.get("type") not in ("int", "float")
                    or "min" not in definition or "max" not in definition):
                continue
            low, high = _parameter_bounds(parameters, key, definition)
            if low == high:
                continue
            if not _parameter_visible(parameters, definition):
                bindings.append(None)
                continue
            integer = definition["type"] == "int"
            bindings.append(StickBinding((*path, key), low, high, definition["coarse_step"],
                                         integer, definition["label"]))
        return bindings

    def _adjust_stick_binding(self, scene, binding, steps):
        parent = scene
        for key in binding.path[:-1]:
            parent = parent[key]
        key = binding.path[-1]
        value = parent.get(key, 0)
        updated = self._stick_number(value, steps, binding.minimum, binding.maximum, binding.step)
        if updated == value:
            return
        self._set_stick_value(scene, binding, updated)

    @staticmethod
    def _set_stick_value(scene, binding, value):
        parent = scene
        for key in binding.path[:-1]:
            parent = parent[key]
        key = binding.path[-1]
        if key in ("strip_translation", "led_translation"):
            parent.update(mode="manual")
            parent.setdefault("strip_translation", 0)
            parent.setdefault("led_translation", 0)
        parent[key] = int(value) if binding.integer else value

    def _adjust_stick_optic(self, effects, name, steps):
        value = effects["strengths"].get(name, 0)
        updated = self._stick_number(value, steps, 0, 1, .1)
        if updated == value:
            return
        if updated:
            effects["strengths"][name] = updated
            if name not in effects["active"]:
                effects["active"].append(name)
        else:
            effects["strengths"].pop(name, None)
            effects["active"].remove(name)

    @staticmethod
    def _stick_number(value, steps, low, high, step):
        if steps < 0:
            return min(value, max(low, round(value + steps * step, 6)))
        return max(value, min(high, round(value + steps * step, 6)))

    def _undo(self):
        redo = self._redo
        if self._set_scene(self._undo_scene, edit=True):
            self._redo = not redo

    def _play_scene(self, scene, *, mark_playing=True):
        self._set_scene(scene)
        self.playback_revision += 1
        self._clear_undo()
        for page in self.pages:
            page.playing = None
            page.playing_scene = None
        if mark_playing and self.pages:
            page = self.pages[-1]
            page.playing = page.index
            page.playing_scene = self.scene()

    def _edit(self, path, value):
        scene = self.scene()
        if scene is None:
            raise ValueError("Choose a scene")
        parent = scene
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value
        self._set_scene(scene, edit=True)

    def _browse(self):
        self._push("Browse", [Row("Looks", select=self._looks), Row("Starters", select=self._starters),
                              Row("Animate", select=self._animations)])

    def _looks(self):
        self._push("Looks", [Row(item["name"], select=partial(self._play_look, item["id"]), hint="A PLAY")
                             for item in self.looks.list()])

    def _play_look(self, look_id):
        self._play_scene(self.looks.get(look_id)["scene"])

    def _starters(self):
        self._push("Starters", [Row(item["name"], select=partial(self._play_starter, item["id"]), hint="A PLAY")
                                for item in list_starters()])

    def _play_starter(self, starter_id):
        self._play_scene(get_starter(starter_id)["scene"])

    def _animation_gallery(self):
        if self._gallery is None:
            self._gallery = gallery_entries(self.catalog, self.presets)
        return self._gallery

    def _animations(self):
        self._push("Animate", [Row(item["name"], select=partial(self._animation_presets, item))
                               for item in self._animation_gallery()])
        scene = self.scene()
        if scene:
            active = scene["animation"]["component_id"]
            self.pages[-1].index = next((index for index, item in enumerate(self._gallery)
                                         if item["component_id"] == active), 0)

    def _animation_presets(self, item):
        rows = [Row("Default", select=partial(self._play_animation, item, None), hint="A PLAY")]
        for choice in self.presets.operator_choices(item["component_id"], self.presets_dir):
            rows.append(Row(choice["name"], select=partial(self._play_animation, item, choice), hint="A PLAY"))
        self._push(item["name"], rows)

    def _play_animation(self, item, preset):
        self._play_scene(self._with_animation(item, preset))

    def _with_animation(self, item, preset):
        next_scene = self.scene() or get_starter("aurora")["scene"]
        next_scene["animation"] = {key: deepcopy(item[key]) for key in
                                   ("component_id", "version", "provider", "role", "parameters")}
        if preset:
            next_scene["animation"]["parameters"] = preset["parameters"]
            if "installation_effects" in preset:
                next_scene["plants"]["effects"] = preset["installation_effects"]
            presentation = preset.get("presentation", {})
            next_scene["look"].update(presentation.get("look", {}))
            if "background_gain" in presentation:
                next_scene["background"]["parameters"]["gain"] = presentation["background_gain"]
        return next_scene

    def _tune(self):
        self._push("Tune", self._tune_rows)

    def _tune_rows(self):
        scene = self.scene()
        if scene is None:
            return [Row("No scene", select=self._browse)]
        look = scene["look"]
        palette = look["palette_id"]
        return [
            self._choice("Palette", palette, SCENE_PALETTE_IDS,
                         partial(self._edit, ("look", "palette_id")),
                         labels=PALETTE_NAMES, swatches=tuple(map(rgb, PALETTE_SWATCHES.get(palette, ())))),
            self._speed_row(look["pace"]),
            self._number("Scene light", look["presentation_brightness"], 0, 2, .01,
                         partial(self._edit, ("look", "presentation_brightness"))),
            Row("Background", select=partial(self._push, "Backlight", self._background_rows)),
            Row("Animation", select=partial(self._push, "Animation", self._animation_rows)),
            Row("Widgets", select=partial(self._push, "Widgets", self._widget_rows)),
            Row("Plants", select=partial(self._push, "Plants", self._plant_rows)),
            Row("Save Look", select=self._save_picker),
        ]

    def _speed_row(self, pace):
        setter = partial(self._edit, ("look", "pace"))
        row = self._number("Speed", pace, 0, MAX_SCENE_PACE, SCENE_PACE_STEP, setter)
        row.select = partial(setter, 0 if pace > 0 else 1)
        row.hint = "A FREEZE" if pace > 0 else "A 1X"
        return row

    def _background_rows(self):
        return self._parameter_rows(("background",), self.scene()["background"])

    def _animation_rows(self):
        return self._parameter_rows(("animation",), self.scene()["animation"])

    def _descriptor(self, component):
        return self.catalog.require(provider=component["provider"], component_id=component["component_id"],
                                    version=component["version"])

    def _parameter_rows(self, path, component):
        rows = []
        descriptor = self._descriptor(component)
        schema = descriptor.parameter_schema
        params = component["parameters"]
        order = dict.fromkeys((*descriptor.preferred_parameters, *params))
        player_state = self.manager.nimbus_game()[1] if path == ("animation",) else None
        for key in sorted(order, key=lambda key: key == "seed" or "fps" in key
                          or (player_state == "paused" and schema[key].get("autoplay_only", False))):
            definition = schema[key]
            if key not in params or not _parameter_visible(params, definition):
                continue
            value = params[key]
            name = definition.get("label", key.replace("_", " "))
            if definition.get("control") == "fixed":
                rows.append(Row(name, _choice_labels(definition, (value,))[value], hint="FIXED"))
                continue
            setter = partial(self._edit, (*path, "parameters", key))
            kind = definition.get("type")
            options = definition.get("options")
            if options:
                labels = _choice_labels(definition, options) if definition.get("option_labels") else None
                rows.append(self._choice(name, value, options, setter, labels=labels, select=len(options) > 8))
            elif kind == "bool":
                rows.append(self._choice(name, value, (False, True), setter))
            elif kind in ("int", "float"):
                low, high = _parameter_bounds(params, key, definition)
                row = self._number(name, value, low, high, definition["step"], setter, integer=kind == "int")
                if kind == "int" and high - low > 200:
                    row.select = partial(self._integer_picker, name, low, high, setter)
                rows.append(row)
            elif kind == "cells":
                rows.append(Row(name, str(len(value)), select=partial(self._cells_picker, name, value, setter)))
            elif kind == "color":
                rows.append(Row(name, str(value), select=partial(self._color_picker, name, value, setter)))
            elif kind == "str":
                rows.append(Row(name, value, select=partial(
                    self._text_picker, name, value, definition.get("max_length", 48),
                    setter, definition.get("control") == "emoji_text",
                )))
        return rows

    def _choice(self, name, value, options, setter, *, labels=None, swatches=(), select=False):
        def adjust(direction, *, steps):
            index = options.index(value) if value in options else (-1 if direction > 0 else 0)
            setter(options[(index + direction) % len(options)])

        display = {True: "On", False: "Off"}.get(value, value) if type(value) is bool else value
        if labels:
            display = labels.get(value, display)
        choose = partial(self._option_picker, name, value, options, setter, labels) if select else None
        return Row(name, str(display), adjust=adjust, select=choose, swatches=swatches)

    def _option_picker(self, name, value, options, setter, labels):
        def choose(option):
            setter(option)
            self.pages.pop()
        rows = [Row(str((labels or {}).get(option, option)), select=partial(choose, option), hint="A SET")
                for option in options]
        self._push(name, rows)
        self.pages[-1].index = options.index(value) if value in options else 0

    @staticmethod
    def _number(name, value, low, high, step, setter, *, integer=False):
        def adjust(direction, *, steps):
            next_value = max(low, min(high, round(float(value) + direction * step * steps, 6)))
            if next_value != value:
                setter(int(next_value) if integer else next_value)

        level = max(0.0, min(1.0, (value - low) / (high - low))) if high > low else 0.0
        return Row(name, f"{value:g}", adjust=adjust, level=level)

    def _integer_picker(self, label, low, high, setter):
        def submit(text):
            candidate = int(text)
            if not low <= candidate <= high:
                raise ValueError(f"Use {low} to {high}")
            setter(candidate)

        groups = (("Number", "0123456789"), ("Edit", ("-", "BACK")))
        self.picker = CharacterPicker(label, "", len(str(max(abs(low), abs(high)))) + 1,
                                      submit, groups)

    def _text_picker(self, label, value, limit, setter, emoji=False):
        groups = [
            ("A-F", "ABCDEF"), ("G-L", "GHIJKL"), ("M-R", "MNOPQR"),
            ("S-Z", "STUVWXYZ"), ("Numbers", "0123456789"),
        ]
        if emoji:
            groups.append(("Emoji", tuple(char for char in EMOJI_PATTERNS if len(char) == 1 and not char.isascii())))
        else:
            groups.extend([
                ("a-f", "abcdef"), ("g-l", "ghijkl"), ("m-r", "mnopqr"),
                ("s-z", "stuvwxyz"), ("Marks", "-.?!/&+_:@"),
            ])
        groups.append(("Edit", (" ", "BACK")))
        self.picker = CharacterPicker(label, value, limit, setter, tuple(groups))

    def _save_picker(self):
        self._text_picker("Save Look", "", 80, self._save_look)

    def _save_look(self, name):
        canonical = normalize_composer_scene({"origin": "composer", "scene": self.scene()}, self.catalog)
        self.looks.save_as(name, canonical)
        self.saved_revision += 1
        self.error = None
        self._feedback("Saved", name)

    def _cells_picker(self, name, cells, setter):
        self.picker = PositionPicker(name, WIDTH // 2, HEIGHT // 2,
                                     setter, tuple(map(tuple, cells)))

    def _color_picker(self, name, value, setter):
        channels = list(colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb(value))))

        def change(index, next_value):
            candidate = channels.copy()
            candidate[index] = next_value
            result = colorsys.hsv_to_rgb(*candidate)
            setter("#" + bytes(round(channel * 255) for channel in result).hex())
            channels[:] = candidate

        def rows():
            color = tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(*channels))
            result = []
            for index, label in enumerate(("Hue", "Sat", "Value")):
                row = self._number(label, channels[index], 0, 1, .05, partial(change, index))
                row.swatches = (color,)
                result.append(row)
            return result

        self._push(name, rows)

    def _widget_rows(self):
        scene = self.scene()
        rows = []
        for descriptor in self.catalog.descriptors:
            if descriptor.role.value != "widget":
                continue
            component_id = descriptor.component_id
            name = descriptor.name
            widgets = [widget for widget in scene["widgets"]
                       if widget["component"]["component_id"] == component_id]
            if widgets:
                for index, widget in enumerate(widgets, 1):
                    label = name if len(widgets) == 1 else f"{name} {index}"
                    rows.append(Row(label, "On" if widget["visible"] else "Off",
                                    select=partial(self._open_widget, label, widget["id"])))
            else:
                rows.append(Row(f"Add {name}", select=partial(self._add_widget, name, descriptor)))
        return rows

    def _add_widget(self, name, descriptor):
        scene = self.scene()
        component_id = descriptor.component_id
        widget_id = f"nimbus-{component_id}"
        if any(widget["id"] == widget_id for widget in scene["widgets"]):
            widget_id = f"nimbus-{uuid.uuid4().hex}"
        scene["widgets"].append({
            "id": widget_id,
            "component": {"component_id": component_id, "version": descriptor.version,
                          "provider": descriptor.provider.value, "role": "widget",
                          "parameters": descriptor.default_parameters()},
            "visible": True,
            "placement": dict(descriptor.default_placement) or {
                "mode": "manual", "strip_translation": 0, "led_translation": 0,
            },
        })
        self._set_scene(scene, edit=True)
        self._open_widget(name, widget_id)

    def _open_widget(self, name, widget_id):
        self._push(name, partial(self._widget_settings, widget_id))

    def _widget_settings(self, widget_id):
        scene = self.scene()
        for index, widget in enumerate(scene["widgets"]):
            if widget["id"] == widget_id:
                break
        else:
            return [Row("No widget")]
        path = ("widgets", index)
        placement = widget["placement"]
        rows = [
            self._choice("Visible", widget["visible"], (False, True), partial(self._edit, (*path, "visible"))),
            self._choice("Place", placement["mode"], ("auto", "manual"), partial(self._placement_mode, path)),
        ]
        if placement["mode"] == "manual":
            rows.extend([
                Row("Move", f'{placement["strip_translation"]}/{placement["led_translation"]}',
                    select=partial(self._position_picker, path, placement)),
                self._number("Across", placement["strip_translation"], -32, 32, 1,
                             partial(self._edit, (*path, "placement", "strip_translation")), integer=True),
                self._number("Vertical", placement["led_translation"], -137, 137, 1,
                             partial(self._edit, (*path, "placement", "led_translation")), integer=True),
            ])
        rows.extend(self._parameter_rows((*path, "component"), widget["component"]))
        if widget["component"]["component_id"] == "clock_overlay":
            rows.append(Row("Presets", select=partial(self._widget_presets, path, widget["component"])))
        return rows

    def _widget_presets(self, path, component):
        choices = self.presets.operator_choices(component["component_id"], self.presets_dir)
        self._push("Presets", [Row(choice["name"], select=partial(
            self._edit, (*path, "component", "parameters"), choice["parameters"]), hint="A APPLY")
            for choice in choices])

    def _placement_mode(self, path, mode):
        value = {"mode": mode}
        if mode == "manual":
            value.update(strip_translation=0, led_translation=0)
        self._edit((*path, "placement"), value)

    def _position_picker(self, path, placement):
        # Start the cursor near the current translation while keeping it visible.
        x = max(0, min(WIDTH - 1, WIDTH // 2 + placement["strip_translation"]))
        y = max(0, min(HEIGHT - 1, HEIGHT // 2 + placement["led_translation"]))

        def update(column, led):
            self._edit((*path, "placement"), {
                "mode": "manual",
                "strip_translation": max(-32, min(32, placement["strip_translation"] + column - x)),
                "led_translation": max(-137, min(137, placement["led_translation"] + led - y)),
            })

        self.picker = PositionPicker("Move", x, y, update)

    def _plant_rows(self):
        effects = self.scene()["plants"]["effects"]
        rows = []
        for modifier in PLANT_MODIFIER_IDS:
            enabled = modifier in effects["active"]
            rows.append(self._choice(modifier, enabled, (False, True), partial(self._plant_enable, modifier)))
            if enabled:
                rows.append(self._number(f"{modifier} %", effects["strengths"][modifier], 0, 1, .05,
                                         partial(self._edit, ("plants", "effects", "strengths", modifier))))
        return rows

    def _plant_enable(self, modifier, enabled):
        effects = self.scene()["plants"]["effects"]
        active = set(effects["active"])
        if enabled:
            for exclusive in (FIELD_MODIFIERS, SURFACE_MODIFIERS):
                if modifier in exclusive:
                    active.difference_update(exclusive)
            active.add(modifier)
        else:
            active.discard(modifier)
        effects["active"] = [name for name in PLANT_MODIFIER_IDS if name in active]
        self._edit(("plants", "effects"), effects)

    def _playback(self):
        self._push("Playback", self._playback_rows)

    def _playback_rows(self):
        status = self.runner.status()
        rows = [Row("Playlist", select=self._playlist_choices)]
        if status["phase"] == "running":
            remaining = max(0, int(status["remaining_seconds"]))
            entry = (status["current_entry"] or {}).get("label", "Playing")
            rows.extend([Row(entry, f"{remaining // 60}:{remaining % 60:02}"),
                         Row("Stop list", select=partial(self._stop_playlist, status["run_id"]))])
        else:
            rows.extend([Row("List", status["phase"]), Row("Stop list", "Idle")])
        rows.extend([
            self._number("Wall light", self.manager.output_brightness, 0, 255, 8,
                         self._wall_brightness, integer=True),
            self._number("Target FPS", self.manager.target_fps, 1, 200, 1,
                         lambda value: self._run("set_target_fps", {"target_fps": value}), integer=True),
            Row("Wall off", select=self._power_off, hint="A OFF"),
            Row("Tetris", select=partial(self._run, "start_tetris_player", {}, close=True), hint="A PLAY"),
        ])
        return rows

    def _wall_brightness(self, value):
        self._run("set_output_brightness", {"brightness": value}, close=value == 0)
        if value == 0:
            self.feedback = None

    def _power_off(self):
        self._run("automation_device_state", {"power": False})
        self.close()

    def _playlist_choices(self):
        self._push("Playlist", [Row(item["name"], select=partial(self._start_playlist, item["id"]))
                                for item in self.playlists.list()])

    def _playlist_scene(self, scene):
        from ipc.scene_contract import BROWSER_SCENE_SCHEMA

        if scene.get("schema") == "ledgrid.browser-scene-v2":
            return scene["scene"]
        if scene.get("schema") == BROWSER_SCENE_SCHEMA:
            # Retained browser documents use the deployed Composer catalog to
            # validate provider, preset and runtime identities.
            from web.app import AnimationWebInterface

            if self._browser_playlist_adapter is None:
                self._browser_playlist_adapter = AnimationWebInterface(None, self.manager, project_root=self.root)
            return self._browser_playlist_adapter._validated_browser_activation_scene(scene)[1]
        return scene

    def _playlist_command(self, action, **fields):
        result = self.runner.dispatch({
            "schema": PLAYLIST_COMMAND_SCHEMA, "schema_version": PLAYLIST_VERSION,
            "request_id": str(uuid.uuid4()), "requested_at": time.time(), "action": action,
            **fields,
        })
        if result["phase"] == "rejected":
            raise ValueError(result["error"])
        return result

    def _start_playlist(self, playlist_id):
        definition = self.playlists.get(playlist_id)
        entries = [{**entry, "scene": self._playlist_scene(entry["scene"])}
                   for entry in definition["entries"]]
        self._playlist_command("start", run_id=str(uuid.uuid4()), playlist_id=playlist_id,
                               playlist_name=definition["name"], entries=entries,
                               expected_controller_session_id=self.owner.session_id,
                               expected_controller_state_revision=self.owner.state_revision)
        self.close()

    def _stop_playlist(self, run_id):
        self._playlist_command("stop", run_id=run_id)
        self._feedback("Stopped", "Playlist")
