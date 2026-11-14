#!/usr/bin/env python3
"""Run an isolated, pixel-accurate Nimbus menu simulator on localhost."""

import argparse
from functools import partial
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import RLock
import time
from uuid import uuid4
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, request
import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.wall_menu_frame import HEIGHT, WIDTH, MenuStack, WallMenuFrame
from ipc.playlist_runtime import PlaylistRunner
from ipc.runtime_control import controller_activation_coordinator
from ipc.scene_contract import normalize_composer_scene
from scripts.start_server import nimbus_command, poll_wall_composer
from web.composer_final_preview import installed_plant_geometry
from web.starter_looks import get_starter
from web.wall_composer import WallComposer


class SimulatedWall(PreviewLEDController):
    inline_show = True

    def __init__(self):
        super().__init__(WIDTH, HEIGHT)
        self.pixels = np.zeros((WIDTH * HEIGHT, 3), dtype=np.uint8)

    def set_all_pixels(self, colors):
        self.pixels = np.asarray(colors, dtype=np.uint8).copy()

    def clear(self):
        self.pixels = np.zeros_like(self.pixels)


def create_simulator(data_dir):
    app = Flask(__name__, template_folder=str(ROOT / "web" / "templates"))
    wall = SimulatedWall()
    manager = AnimationManager(wall, auto_start=False)
    manager.set_output_brightness(80)
    manager.set_target_fps(30)
    owner = controller_activation_coordinator(manager)
    runner = PlaylistRunner(manager, owner)
    gamepad = SimpleNamespace(connected=True, shoulder_bank=0, poll=lambda *_args, **_kwargs: (),
                              discard_pending=lambda: None, handoff=lambda _target: None)
    command = partial(nimbus_command, manager, owner, gamepad)
    menu = WallComposer(manager, owner, runner, command, ROOT, data_dir, Path(data_dir))
    geometry = installed_plant_geometry(ROOT)
    renderer = WallMenuFrame()
    lock = RLock()
    starter = get_starter("aurora")["scene"]
    command("start_scene", {"scene": starter})
    menu.looks.save_as("Boreal Hush", normalize_composer_scene({"origin": "composer", "scene": starter}, menu.catalog))
    menu.playlists.save({"name": "Evening", "entries": [
        {"entry_id": str(uuid4()), "label": name, "duration_seconds": 30,
         "scene": get_starter(key)["scene"]}
        for key, name in (("aurora", "Boreal Hush"), ("aurora_clock", "Firefly Timekeeper"))
    ]})

    @app.get("/")
    def index():
        from flask import render_template
        return render_template("wall_composer_simulator.html")

    @app.route("/state", methods=["GET", "POST"])
    def state():
        from base64 import b64encode
        with lock:
            revision = owner.state_revision
            runner.advance()
            if owner.state_revision != revision:
                menu.refresh_scene()
            if request.method == "POST":
                button = (request.get_json(silent=True) or {}).get("button")
                if button not in ("menu", "up", "down", "left", "right", "a", "b", "y",
                                  "release-left", "release-right", "disconnect",
                                  "repeat-left", "repeat-right", "fast-left", "fast-right"):
                    return jsonify(error="Unknown Nimbus button"), 400
                gamepad.poll = lambda *_args, **_kwargs: (button,)
                poll_wall_composer(manager, gamepad, menu)
            card = menu.card()
            # Paint the card onto the same snapshot as the response caption.
            # The animation thread can present its next frame after this request.
            pixels = wall.pixels.copy()
            sections = menu.hud_sections()
            overlay = renderer.render_sections(sections)
            hud_height = 0
            if overlay is not None:
                overlay.apply(pixels)
                first_row = overlay.panels[-1].start if isinstance(overlay, MenuStack) else overlay.start
                hud_height = HEIGHT - first_row
            # Scale brightness perceptually for the desktop while retaining zero.
            scale = (manager.output_brightness / 255) ** .4
            pixels = np.rint(pixels * scale).astype(np.uint8)
            return jsonify(pixels=b64encode(pixels.tobytes()).decode(),
                           mask=b64encode((geometry.foliage | geometry.globes).astype(np.uint8).tobytes()).decode(),
                           hud_height=hud_height,
                           card=card.__dict__ if card else None, menu_open=menu.is_open, error=menu.error,
                           player_state=manager.nimbus_game()[1],
                           scene=(menu.scene() or {}).get("animation", {}).get("component_id"))

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="ledgrid-wall-simulator-") as directory:
        create_simulator(directory).run(host="127.0.0.1", port=args.port, threaded=True)
