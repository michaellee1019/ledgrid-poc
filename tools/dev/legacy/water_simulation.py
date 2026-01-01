"""Shared 2-D shallow water style simulation for LED animations and demos."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import List


@dataclass
class Bubble:
    """Simple bubble particle that drifts upward through the grid."""

    x: float
    y: float
    radius: float
    vy: float = -0.5
    vx: float = 0.0

    def __post_init__(self) -> None:
        if self.vx == 0.0:
            self.vx = random.uniform(-0.05, 0.05)

    def step(self, dt: float) -> None:
        self.vx += random.uniform(-0.01, 0.01) * dt
        self.x += self.vx * dt
        self.y += self.vy * dt


class WaterSimulation:
    """Discrete shallow water simulation closely matching the web demo."""

    def __init__(
        self,
        nx: int,
        ny: int,
        *,
        cell_size: float = 0.05,
        g: float = 9.81,
        damping: float = 0.99,
        injection_rate: float = 0.5,
        bubble_spawn_chance: float = 0.1,
        injection_probability: float = 0.4,
        settle_iterations: int = 2,
        settle_rate: float = 0.35,
    ) -> None:
        self.nx = nx
        self.ny = ny
        self.cell_size = cell_size
        self.g = g
        self.damping = damping
        self.injection_rate = injection_rate
        self.bubble_spawn_chance = bubble_spawn_chance
        self.injection_probability = injection_probability
        self.settle_iterations = max(1, settle_iterations)
        self.settle_rate = max(0.0, min(1.0, settle_rate))
        self.h: List[List[float]] = [[0.0 for _ in range(nx)] for _ in range(ny)]
        self.u: List[List[float]] = [[0.0 for _ in range(nx)] for _ in range(ny)]
        self.v: List[List[float]] = [[0.0 for _ in range(nx)] for _ in range(ny)]
        self.bubbles: List[Bubble] = []

    # ------------------------------------------------------------------
    # Core physics loop
    # ------------------------------------------------------------------
    def step(self, dt: float) -> None:
        nx, ny = self.nx, self.ny
        cell_size = self.cell_size
        g = self.g
        damping = self.damping

        new_u = [[0.0 for _ in range(nx)] for _ in range(ny)]
        new_v = [[0.0 for _ in range(nx)] for _ in range(ny)]
        for i in range(ny):
            for j in range(nx):
                dh_dx = 0.0
                if j < nx - 1:
                    dh_dx = (self.h[i][j] - self.h[i][j + 1]) / cell_size
                dh_dy = 0.0
                if i < ny - 1:
                    dh_dy = (self.h[i][j] - self.h[i + 1][j]) / cell_size
                new_u[i][j] = (self.u[i][j] - g * dh_dx * dt) * damping
                new_v[i][j] = (self.v[i][j] - g * dh_dy * dt) * damping
        # Simple reflective boundaries to keep mass in the domain
        if nx > 0:
            for i in range(ny):
                new_u[i][0] = max(0.0, new_u[i][0])
                new_u[i][nx - 1] = min(0.0, new_u[i][nx - 1])
        if ny > 0:
            for j in range(nx):
                new_v[0][j] = max(0.0, new_v[0][j])
                new_v[ny - 1][j] = min(0.0, new_v[ny - 1][j])
        self.u = new_u
        self.v = new_v

        new_h = [[0.0 for _ in range(nx)] for _ in range(ny)]
        for i in range(ny):
            for j in range(nx):
                div = 0.0
                if j < nx - 1:
                    div += (self.u[i][j + 1] - self.u[i][j]) / cell_size
                if j > 0:
                    div += (self.u[i][j] - self.u[i][j - 1]) / cell_size
                if i < ny - 1:
                    div += (self.v[i + 1][j] - self.v[i][j]) / cell_size
                if i > 0:
                    div += (self.v[i][j] - self.v[i - 1][j]) / cell_size
                new_h[i][j] = self.h[i][j] - div * dt
        for i in range(ny):
            for j in range(nx):
                h_val = new_h[i][j]
                if h_val < 0.0:
                    h_val = 0.0
                if h_val > 1.0:
                    h_val = 1.0
                new_h[i][j] = h_val
        self.h = new_h

        self._inject_water(dt)
        self._settle_columns(iterations=self.settle_iterations, rate=self.settle_rate)
        self._update_bubbles(dt)

    def _inject_water(self, dt: float) -> None:
        if self.injection_rate <= 0:
            return
        ny = self.ny
        for j in range(self.nx):
            if random.random() > self.injection_probability:
                continue
            amount = self.injection_rate * dt * random.uniform(0.5, 1.5)
            self.h[0][j] += amount
            row = 0
            while row < ny - 1 and self.h[row][j] > 1.0:
                overflow = self.h[row][j] - 1.0
                available = max(0.0, 1.0 - self.h[row + 1][j])
                transfer = min(overflow, available)
                if transfer <= 0.0:
                    break
                self.h[row][j] -= transfer
                self.h[row + 1][j] += transfer
                row += 1
            if self.h[row][j] > 1.0:
                self.h[row][j] = 1.0
            if random.random() < self.bubble_spawn_chance:
                x_pos = j + random.uniform(0.0, 1.0)
                y_pos = random.uniform(0.5, 2.0)
                radius = random.uniform(0.3, 0.8)
                self.bubbles.append(Bubble(x_pos, y_pos, radius))

    def _settle_columns(self, iterations: int = 1, rate: float = 0.3) -> None:
        """Gently push water downward so columns evenly fill the tank."""
        if self.ny <= 1 or self.nx <= 0:
            return
        iterations = max(1, iterations)
        rate = max(0.0, min(1.0, rate))
        for _ in range(iterations):
            for i in range(self.ny - 1):
                current_row = self.h[i]
                next_row = self.h[i + 1]
                for j in range(self.nx):
                    diff = current_row[j] - next_row[j]
                    if diff <= 0.0:
                        continue
                    transfer = diff * rate
                    available = max(0.0, 1.0 - next_row[j])
                    transfer = min(transfer, available)
                    if transfer <= 0.0:
                        continue
                    current_row[j] -= transfer
                    next_row[j] += transfer

    def _update_bubbles(self, dt: float) -> None:
        updated: List[Bubble] = []
        for bubble in self.bubbles:
            bubble.step(dt)
            if bubble.y <= 0.0 or bubble.x < -1 or bubble.x > self.nx + 1:
                continue
            updated.append(bubble)
        self.bubbles = updated

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------
    def _calculate_lighten(self) -> List[List[float]]:
        ny, nx = self.ny, self.nx
        lighten = [[0.0 for _ in range(nx)] for _ in range(ny)]
        for bubble in self.bubbles:
            r2 = bubble.radius * bubble.radius
            min_i = max(0, int(bubble.y - bubble.radius) - 2)
            max_i = min(ny - 1, int(bubble.y + bubble.radius) + 2)
            min_j = max(0, int(bubble.x - bubble.radius) - 2)
            max_j = min(nx - 1, int(bubble.x + bubble.radius) + 2)
            for i in range(min_i, max_i + 1):
                for j in range(min_j, max_j + 1):
                    dx = (j + 0.5) - bubble.x
                    dy = (i + 0.5) - bubble.y
                    d2 = dx * dx + dy * dy
                    if d2 < r2:
                        factor = max(0.0, 1.0 - d2 / r2)
                        lighten[i][j] += factor * 0.5
        for i in range(ny):
            for j in range(nx):
                if lighten[i][j] > 1.0:
                    lighten[i][j] = 1.0
        return lighten

    def get_color_grid(self) -> List[str]:
        colours: List[str] = []
        lighten = self._calculate_lighten()
        for i in range(self.ny):
            for j in range(self.nx):
                density = self.h[i][j]
                r_base = 173 * (1.0 - density) + 0 * density
                g_base = 216 * (1.0 - density) + 51 * density
                b_base = 230 * (1.0 - density) + 102 * density
                l = lighten[i][j]
                r = r_base * (1 - l) + 255 * l
                g = g_base * (1 - l) + 255 * l
                b = b_base * (1 - l) + 255 * l
                r_int = int(max(0, min(255, r)))
                g_int = int(max(0, min(255, g)))
                b_int = int(max(0, min(255, b)))
                colours.append(f"#{r_int:02x}{g_int:02x}{b_int:02x}")
        return colours

    def get_lighten_grid(self) -> List[List[float]]:
        """Expose the bubble lighten matrix for custom renderers."""
        return self._calculate_lighten()

    # ------------------------------------------------------------------
    # Convenience helpers used by LED animation for telemetry.
    # ------------------------------------------------------------------
    def fill_ratio(self) -> float:
        if self.nx <= 0 or self.ny <= 0:
            return 0.0
        total = self.nx * self.ny
        volume = 0.0
        for row in self.h:
            volume += sum(row)
        return max(0.0, min(1.0, volume / total))

    def drain_circle(self, cx: float, cy: float, radius: float, rate: float, dt: float) -> float:
        """Remove water from a circular region, returning drained volume."""
        if radius <= 0 or rate <= 0:
            return 0.0
        removed = 0.0
        r2 = radius * radius
        for i in range(self.ny):
            dy = i - cy
            if abs(dy) > radius:
                continue
            for j in range(self.nx):
                dx = j - cx
                if dx * dx + dy * dy > r2:
                    continue
                before = self.h[i][j]
                if before <= 0.0:
                    continue
                self.h[i][j] = max(0.0, before - rate * dt)
                removed += before - self.h[i][j]
        return removed
