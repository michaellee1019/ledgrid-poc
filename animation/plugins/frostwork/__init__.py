"""Border-seeded DLA-inspired frost that grows, ages, and sublimates."""
import numpy as np

from animation.libraries.procedural_sculptures import CadencedSculpture

SEMANTIC_PALETTE_ROLES = ("background_low", "primary", "accent")


class FrostworkAnimation(CadencedSculpture):
    ANIMATION_NAME = "Frostwork"
    ANIMATION_DESCRIPTION = "Branching ice advances from a cold border, sparkles at its tips, and melts"
    PLANT_MODIFIER_SUPPORT = frozenset(("emitter", "obstacle", "illuminate"))
    SOURCE_FPS = 20.0

    def __init__(self, controller, config=None):
        super().__init__(controller, config)
        self.default_params.update({"temperature": 0.35, "melt_cycle": 0.55})
        self.params = {**self.default_params, **(config or {})}
        self.occupied = np.zeros(self._shape, bool)
        self.age = np.zeros(self._shape, np.float32)
        self.occupied[:, -1] = True

    def get_parameter_schema(self):
        s = super().get_parameter_schema()
        s.update({
            "temperature": {"type":"float","min":0,"max":1,"default":0.35,"description":"Cold growth versus sublimation"},
            "melt_cycle": {"type":"float","min":0,"max":1,"default":0.55,"description":"Frequency of warm melt fronts"},
        }); return s

    def _step(self, tick):
        self.age[self.occupied] += 1
        neighbors = np.zeros_like(self.occupied)
        neighbors[1:] |= self.occupied[:-1]; neighbors[:-1] |= self.occupied[1:]
        neighbors[:, 1:] |= self.occupied[:, :-1]; neighbors[:, :-1] |= self.occupied[:, 1:]
        frontier = neighbors & ~self.occupied
        obstacle = 0.0
        if self.plant_modifier_strength("obstacle") > 0:
            obstacle = self.get_plant_masks().obstacle
            frontier &= ~obstacle
        emitter = self.plant_modifier_strength("emitter")
        if emitter > 0:
            frontier |= self.get_plant_masks().obstacle_edge & ~self.occupied
        count = max(1, int((1 + 5 * float(self.params["density"])) * (0.4 + float(self.params["motion"]))))
        candidates = np.flatnonzero(frontier)
        if candidates.size:
            # DLA attachment: random frontier walkers stick preferentially upward.
            weights = 1.0 + 2.0 * (candidates % self._shape[1]) / self._shape[1]
            weights /= weights.sum()
            chosen = self.rng.choice(candidates, min(count, candidates.size), replace=False, p=weights)
            self.occupied.flat[chosen] = True; self.age.flat[chosen] = 0
        warm = float(self.params["melt_cycle"])
        if warm > 0 and tick and tick % max(25, int(130 - 90 * warm)) == 0:
            old = self.occupied & (self.age > 35 + 45 * float(self.params["temperature"]))
            melt = np.flatnonzero(old)
            if melt.size:
                self.occupied.flat[self.rng.choice(melt, max(1, melt.size // 8), replace=False)] = False
        self.occupied[:, -1] = True

    def reset_simulation(self):
        super().reset_simulation(); self.occupied.fill(False); self.age.fill(0); self.occupied[:, -1] = True

    def palette(self, mood: str):
        authored = super().palette(mood)
        context = self.presentation_context
        if (
            context is None
            or context.vibe_id == "neutral"
            or self.VIBE_COLOR_POLICY != "semantic"
            or "palette_roles" not in self.VIBE_CAPABILITIES
        ):
            return authored
        return np.asarray(
            [
                context.palette_roles.get(role, authored[index])
                for index, role in enumerate(SEMANTIC_PALETTE_ROLES)
            ],
            dtype=np.float32,
        )

    def on_presentation_context_changed(self, old_context, new_context) -> None:
        """Repaint at the same source tick without advancing frost growth."""
        self._render_key = None

    def generate_frame(self, time_elapsed, frame_count):
        tick, cached = self.begin_frame(time_elapsed)
        if cached: return cached
        self.advance_bounded(tick, self._step)
        tips = self.occupied & (self.age < 8)
        value = self.occupied.astype(np.float32) * (0.38 + 0.5 * np.exp(-self.age / 45.0))
        illum = self.plant_modifier_strength("illuminate")
        if illum > 0: value += self.get_plant_masks().obstacle_edge * tips * illum
        return self.finish_frame(tick, self.colorize(value, tips.astype(np.float32)))
