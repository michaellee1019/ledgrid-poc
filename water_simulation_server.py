"""
water_simulation_server.py
==========================

This module implements a standalone web server that streams a simple 2‑D
fluid simulation to connected browsers.  It is designed around the
requirements of an hourglass–like installation in which a tank roughly
5 feet (≈1.524 m) wide and 8 feet (≈2.438 m) tall is discretised into
square voxels of 5 cm × 5 cm.  Each voxel is represented on the web page
by a 100 × 100 pixel DOM element whose background colour depends on
the local water density and the presence of air bubbles.

The simulation itself is deliberately simple yet physically motivated.  It
uses a height/velocity formulation inspired by the linearised shallow
water equations【163198180929199†L24-L49】, with additional logic to model water
injection from the top of the tank, buoyant bubbles and dissipative
waves.  The goal is to produce a visually convincing animation rather
than an exact physical model, but the code is structured so that more
accurate schemes (such as SPH【136219194856276†L127-L144】 or hybrid MPM
approaches【136219194856276†L188-L195】) can be swapped in without
changing the client interface.

The web server uses FastAPI and its WebSocket support to push frames
to any number of clients at roughly 30 frames per second.  When a
browser connects to the “/ws” endpoint it receives an infinite
sequence of JSON messages, each containing an array of colour strings
for every cell in row major order.  The root endpoint ("/") serves a
minimal HTML document that builds the grid, opens the WebSocket
connection, and applies the received colours to the DOM elements.

Running this file directly with “python water_simulation_server.py”
will start the server on http://localhost:8000.  Visit that URL in a
modern browser to view the simulation.  A modern browser is required
because the page makes use of ES6 features and the Fetch API.
"""

import asyncio
import json
import math
import random
import time
from typing import List, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn


class Bubble:
    """A simple bubble model.

    Bubbles are represented by a position (x, y) measured in cell
    coordinates and a radius measured in cells.  At every simulation
    step the bubble ascends at a fixed buoyant velocity and drifts
    slightly in the horizontal direction.  When a bubble leaves the
    domain its entry is removed from the simulation.
    """

    def __init__(self, x: float, y: float, radius: float) -> None:
        self.x = x
        self.y = y
        self.radius = radius
        # Vertical velocity (cells per second) due to buoyancy
        self.vy = -0.5  # Negative sign moves up in our coordinate system
        # Random initial horizontal drift
        self.vx = random.uniform(-0.05, 0.05)

    def step(self, dt: float) -> None:
        """Advance the bubble by dt seconds."""
        # Apply small random horizontal drift to emulate turbulence
        self.vx += random.uniform(-0.01, 0.01) * dt
        self.x += self.vx * dt
        self.y += self.vy * dt


class WaterSimulation:
    """Discrete fluid simulation on a rectangular grid.

    The simulation uses a simple height–velocity scheme inspired by
    the linearised shallow water equations【163198180929199†L24-L49】.  The grid
    contains ``nx`` columns and ``ny`` rows; cell (i, j) corresponds
    to column i and row j counting downwards.  Each cell stores a
    height ``h`` (representing the local water volume fraction) and
    horizontal/vertical velocities ``u`` and ``v``.  The simulation
    updates these fields using finite differences and a forward
    Euler time integrator, then injects water randomly at the top and
    spawns bubbles with a small probability.

    Attributes
    ----------
    nx : int
        Number of columns in the grid.
    ny : int
        Number of rows in the grid.
    cell_size : float
        Physical size of a cell (metres).  It is set to 0.05 (5 cm) by
        default but can be overridden when constructing the simulator.
    h : List[List[float]]
        Current water height per cell.  Values are typically between
        0 and 1 (where 1 means the cell is completely filled with
        water) but can temporarily exceed those bounds during
        computation; after each update heights are clamped to [0, 1].
    u, v : List[List[float]]
        Horizontal and vertical velocities per cell.  Units are cells
        per second.  These fields capture the local flow of water and
        create ripples.
    bubbles : List[Bubble]
        Active bubbles in the domain.
    g : float
        Gravitational acceleration (metres per second squared).  Used
        for updating velocities via pressure gradients.
    damping : float
        Velocity damping factor applied at every step to model
        viscosity and prevent instabilities.
    injection_rate : float
        Average water volume injected per cell per second at the top
        boundary.  A small random component is added to create
        irregular flow and ripples.
    bubble_spawn_chance : float
        Probability of spawning a bubble during each injection event.
    """

    def __init__(
        self,
        nx: int,
        ny: int,
        cell_size: float = 0.05,
        g: float = 9.81,
        damping: float = 0.99,
        injection_rate: float = 0.5,
        bubble_spawn_chance: float = 0.1,
    ) -> None:
        self.nx = nx
        self.ny = ny
        self.cell_size = cell_size
        self.h = [[0.0 for _ in range(nx)] for _ in range(ny)]
        self.u = [[0.0 for _ in range(nx)] for _ in range(ny)]
        self.v = [[0.0 for _ in range(nx)] for _ in range(ny)]
        self.bubbles: List[Bubble] = []
        self.g = g
        self.damping = damping
        self.injection_rate = injection_rate
        self.bubble_spawn_chance = bubble_spawn_chance

    def _in_bounds(self, i: int, j: int) -> bool:
        return 0 <= i < self.ny and 0 <= j < self.nx

    def step(self, dt: float) -> None:
        """Advance the entire simulation by dt seconds."""
        nx, ny = self.nx, self.ny
        cell_size = self.cell_size
        g = self.g
        damping = self.damping

        # 1. Update velocities based on height gradients (pressure term)
        # Use temporary arrays to avoid in‑place updates interfering with neighbours
        new_u = [[0.0 for _ in range(nx)] for _ in range(ny)]
        new_v = [[0.0 for _ in range(nx)] for _ in range(ny)]
        for i in range(ny):
            for j in range(nx):
                # Horizontal pressure gradient d/dx h
                dh_dx = 0.0
                if j < nx - 1:
                    dh_dx = (self.h[i][j] - self.h[i][j + 1]) / cell_size
                # Vertical pressure gradient d/dy h (positive downwards)
                dh_dy = 0.0
                if i < ny - 1:
                    dh_dy = (self.h[i][j] - self.h[i + 1][j]) / cell_size
                # Update velocities (u decreases when gradient is positive to push flow down the gradient)
                new_u[i][j] = (self.u[i][j] - g * dh_dx * dt) * damping
                new_v[i][j] = (self.v[i][j] - g * dh_dy * dt) * damping
        self.u = new_u
        self.v = new_v

        # 2. Update heights based on divergence of velocity
        new_h = [[0.0 for _ in range(nx)] for _ in range(ny)]
        for i in range(ny):
            for j in range(nx):
                # Compute divergence: differences of velocities across cell boundaries
                div = 0.0
                # Horizontal divergence (u at right minus u at left)
                if j < nx - 1:
                    div += (self.u[i][j + 1] - self.u[i][j]) / cell_size
                if j > 0:
                    div += (self.u[i][j] - self.u[i][j - 1]) / cell_size
                # Vertical divergence (v at bottom minus v at top)
                if i < ny - 1:
                    div += (self.v[i + 1][j] - self.v[i][j]) / cell_size
                if i > 0:
                    div += (self.v[i][j] - self.v[i - 1][j]) / cell_size
                # Height update
                new_h[i][j] = self.h[i][j] - div * dt
        # Clamp heights and apply simple non‑negative constraint
        for i in range(ny):
            for j in range(nx):
                h_ij = new_h[i][j]
                if h_ij < 0.0:
                    h_ij = 0.0
                # Limit water volume to 1 for stability
                if h_ij > 1.0:
                    h_ij = 1.0
                new_h[i][j] = h_ij
        self.h = new_h

        # 3. Inject water at the top boundary with randomness to create ripples
        for j in range(nx):
            # Base injection scaled by dt and random factor
            if random.random() < 0.6:  # reduce number of injection events
                continue
            amount = self.injection_rate * dt * random.uniform(0.5, 1.5)
            # Add water to the first row
            self.h[0][j] = min(1.0, self.h[0][j] + amount)
            # Create bubbles occasionally when injecting water
            if random.random() < self.bubble_spawn_chance:
                # Spawn a bubble at random x within this column (in cell coordinates)
                x_pos = j + random.uniform(0.0, 1.0)
                # Bubbles start near the top row (y slightly greater than 0)
                y_pos = random.uniform(0.5, 2.0)
                radius = random.uniform(0.3, 0.8)
                self.bubbles.append(Bubble(x_pos, y_pos, radius))

        # 4. Update bubbles
        updated_bubbles = []
        for bubble in self.bubbles:
            bubble.step(dt)
            # Remove bubble if it exits the domain or if there is no water below
            if bubble.y <= 0.0 or bubble.x < -1 or bubble.x > nx + 1:
                continue
            updated_bubbles.append(bubble)
        self.bubbles = updated_bubbles

    def _calculate_lighten(self) -> List[List[float]]:
        """Compute per‑cell lighten factors based on bubble proximity.

        For each cell, we accumulate a lighten value proportional to
        the contribution of nearby bubbles.  Each bubble has a
        Gaussian‑like influence decaying quadratically with distance.
        The resulting matrix is used when computing the final cell
        colours to give the illusion of air pockets.
        """
        ny, nx = self.ny, self.nx
        lighten = [[0.0 for _ in range(nx)] for _ in range(ny)]
        for bubble in self.bubbles:
            # Precompute squared radius for influence falloff
            r2 = bubble.radius * bubble.radius
            # Determine affected bounding box in cell coordinates
            min_i = max(0, int(bubble.y - bubble.radius) - 2)
            max_i = min(ny - 1, int(bubble.y + bubble.radius) + 2)
            min_j = max(0, int(bubble.x - bubble.radius) - 2)
            max_j = min(nx - 1, int(bubble.x + bubble.radius) + 2)
            for i in range(min_i, max_i + 1):
                for j in range(min_j, max_j + 1):
                    # Compute squared distance from cell centre to bubble centre
                    dx = (j + 0.5) - bubble.x
                    dy = (i + 0.5) - bubble.y
                    d2 = dx * dx + dy * dy
                    if d2 < r2:
                        # Lighten factor is stronger near the centre
                        factor = max(0.0, 1.0 - d2 / r2)
                        lighten[i][j] += factor * 0.5  # accumulate lighten
        # Clamp lighten values to [0, 1]
        for i in range(ny):
            for j in range(nx):
                if lighten[i][j] > 1.0:
                    lighten[i][j] = 1.0
        return lighten

    def get_color_grid(self) -> List[str]:
        """Return a flat list of hex colour strings for the current state.

        Each cell’s colour is determined by its water height and the
        presence of bubbles.  The mapping is designed to convey depth
        (darker blues for denser water) and to add highlights where
        bubbles occur.  A simple HSL‑to‑RGB conversion could be used
        here, but for efficiency we perform the calculation directly
        in RGB space.
        """
        ny, nx = self.ny, self.nx
        lighten = self._calculate_lighten()
        # Output colours in row‑major order
        colours: List[str] = []
        for i in range(ny):
            for j in range(nx):
                density = self.h[i][j]
                # Base colour: blend between light sky blue and deep ocean blue
                # Light: (173, 216, 230)   Deep: (0, 51, 102)
                r_base = 173 * (1.0 - density) + 0 * density
                g_base = 216 * (1.0 - density) + 51 * density
                b_base = 230 * (1.0 - density) + 102 * density
                # Apply bubble lighten factor: mix with white based on lighten
                l = lighten[i][j]
                r = r_base * (1 - l) + 255 * l
                g = g_base * (1 - l) + 255 * l
                b = b_base * (1 - l) + 255 * l
                # Clamp and convert to integer
                r_int = int(max(0, min(255, r)))
                g_int = int(max(0, min(255, g)))
                b_int = int(max(0, min(255, b)))
                colours.append(f"#{r_int:02x}{g_int:02x}{b_int:02x}")
        return colours


class ConnectionManager:
    """Manage active WebSocket connections and broadcast frames to them."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []
        self.simulation: WaterSimulation | None = None
        # Flag to ensure the simulation loop runs only once
        self._simulation_task: asyncio.Task | None = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        # Start simulation loop if not already running
        if self._simulation_task is None:
            self._simulation_task = asyncio.create_task(self._run_simulation())

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.remove(websocket)
        # Stop simulation when no clients are connected
        if not self.active_connections and self._simulation_task is not None:
            self._simulation_task.cancel()
            self._simulation_task = None

    async def broadcast(self, message: str) -> None:
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except WebSocketDisconnect:
                self.disconnect(connection)

    async def _run_simulation(self) -> None:
        """Continuously step the simulation and broadcast frames."""
        # Create a simulation sized to 5ft × 8ft with 5 cm voxels
        # Convert from feet to metres: 1 ft ≈ 0.3048 m
        width_m = 5.0 * 0.3048
        height_m = 8.0 * 0.3048
        cell_size = 0.05  # 5 cm
        nx = int(width_m / cell_size + 0.5)
        ny = int(height_m / cell_size + 0.5)
        self.simulation = WaterSimulation(nx=nx, ny=ny)
        dt = 1.0 / 30.0
        # Warm‑up to avoid initial blank frame
        for _ in range(5):
            self.simulation.step(dt)
        while True:
            start_time = time.perf_counter()
            # Step simulation
            assert self.simulation is not None
            self.simulation.step(dt)
            colours = self.simulation.get_color_grid()
            payload = json.dumps({"colours": colours})
            await self.broadcast(payload)
            # Sleep to maintain ~30 fps
            elapsed = time.perf_counter() - start_time
            remaining = dt - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)


app = FastAPI()
manager = ConnectionManager()


@app.get("/")
async def get_html() -> HTMLResponse:
    """Serve the main page with inline CSS and JavaScript."""
    # Build the HTML for the grid.  We defer the actual creation of
    # the cell elements to JavaScript once we know the grid size from
    # the first message from the server.  This keeps the HTML short
    # and avoids hardcoding dimensions.
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <title>Hourglass Water Simulation</title>
        <style>
            body {
                margin: 0;
                background-color: #111;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                font-family: sans-serif;
            }
            #container {
                display: grid;
                grid-gap: 0px;
                box-shadow: 0 0 20px rgba(0, 0, 0, 0.5);
            }
            .cell {
                width: 100px;
                height: 100px;
                background-color: #222;
            }
        </style>
    </head>
    <body>
        <div id="container"></div>
        <script>
            // Establish WebSocket connection and build grid dynamically
            const container = document.getElementById('container');
            let gridInitialised = false;
            const ws = new WebSocket(`ws://${location.host}/ws`);
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                const colours = data.colours;
                if (!gridInitialised) {
                    // Determine grid size from the number of colour entries
                    // We assume the grid is roughly square and the size is stable
                    const total = colours.length;
                    // The simulation runs on the server with known nx and ny; here
                    // we receive total = nx * ny.  We compute nx by rounding
                    // sqrt(total * (width/height) ) but the ratio is known.
                    // Instead, wait until the container has children after first
                    // message (should be 0) and create them now.
                    // We'll ask the server for the dimensions via the first
                    // payload.  To avoid sending another message, embed dims in
                    // colours array length: we compute factors that divide total
                    // into a plausible rectangle.  Try to find two integers
                    // around a 5:8 ratio.
                    let nx = 1;
                    let ny = total;
                    for (let i = 1; i <= Math.sqrt(total); i++) {
                        if (total % i === 0) {
                            const j = total / i;
                            // choose the pair with height > width
                            if (j > i) {
                                nx = i;
                                ny = j;
                            }
                        }
                    }
                    // Set CSS grid columns and rows
                    container.style.gridTemplateColumns = `repeat(${nx}, 100px)`;
                    container.style.gridTemplateRows = `repeat(${ny}, 100px)`;
                    // Create cell elements
                    for (let i = 0; i < total; i++) {
                        const cell = document.createElement('div');
                        cell.className = 'cell';
                        container.appendChild(cell);
                    }
                    gridInitialised = true;
                }
                // Update colours
                const cells = container.children;
                for (let i = 0; i < colours.length; i++) {
                    cells[i].style.backgroundColor = colours[i];
                }
            };
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Handle incoming WebSocket connections and manage lifecycle."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive by reading any incoming data.
            # Clients do not need to send messages.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)