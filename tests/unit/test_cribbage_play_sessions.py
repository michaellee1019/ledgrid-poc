"""Complete scorekeeping sessions through production HTTP/Nimbus and native APIs."""
import base64
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from tests.unit.cribbage_pairing import admit as pair_peer
from drivers.cribbage_session import Result
from scripts.simulate_wall_composer import create_simulator
from scripts.start_server import handle_command
from web.starter_looks import get_starter
from tests.unit import test_cribbage_authority as fixtures
from tests.unit.test_cribbage_authority import RED, RED_MAC, GREEN, GREEN_MAC

native = fixtures.native  # Reuse the compiled-core pytest fixture.

HANDS = (
    (8, 10, 6, 4), (12, 6, 8, 12), (4, 8, 10, 8), (10, 12, 4, 10),
    (6, 4, 12, 6), (16, 14, 8, 10), (8, 10, 10, 12), (12, 6, 12, 8),
    (14, 12, 6, 14), (10, 14, 16, 6), (12, 10, 12, 12), (9, 8, 4, 10),
)


class PlaySession:
    def __init__(self, root, library, name):
        self.root, self.library, self.name = root, library, name
        self.expected = [0] * 4
        self.events, self.frames = [], []
        self.buttons = self.submissions = self.restarts = 0
        self.open()

    def open(self):
        self.app = create_simulator(self.root, self.library._lib._name)
        self.client = self.app.test_client()
        self.manager = self.app.extensions["wall_manager"]
        self.session = self.app.extensions["cribbage"]
        self.scene = self.manager.get_scene_state()
        self.press("a")
        assert self.manager.nimbus_game()[1] == "playing"

    def close(self):
        self.manager.stop_animation(clear_leds=False)
        self.session.close()

    def state(self):
        return json.loads((self.root / "cribbage.json").read_text())["state"]

    def game(self):
        return self.manager.nimbus_game()[0]._runtime._animation.instance

    def press(self, *buttons):
        for button in buttons:
            response = self.client.post("/state", json={"button": button})
            assert response.status_code == 200 and not response.json["error"]
            self.buttons += 1

    def select(self, player):
        before = self.state()["turn"]
        for _ in range(4):
            if self.game()._selection == player:
                break
            self.press("down")
        assert self.game()._selection == player and self.state()["turn"] == before

    def edit(self, delta):
        self.press(*(["right" if delta > 0 else "left"] * abs(delta)))

    def configure(self, physical=(), players=2):
        for player in range(players):
            self.select(player)
            desired = 1 if player in physical else 0
            for _ in range(3):
                state = self.state()
                bit = 1 << player
                mode = 0 if state["local_control_mask"] & bit else 1 if state["lobby_enabled_mask"] & bit else 2
                if mode == desired:
                    break
                self.press("right")
            assert mode == desired
        self.press("a")
        assert not self.state()["started"]  # Review alone cannot start.
        self.record("review roster")
        self.press("a")
        assert self.state()["roster_mask"] == (1 << players) - 1
        assert self.state()["turn"] == 0
        self.record("confirmed Start", capture=True)

    def score(self, player, delta, *, passes=False):
        self.select(player)
        self.edit(delta)
        self.press("a" if passes else "x")
        assert self.session.feedback == "OK"
        self.expected[player] += delta
        self.submissions += 1
        assert self.game()._drafts[player] == 0
        self.record(f"Nimbus {player}: {delta:+d} {'OK' if passes else 'ADD'}")

    def record(self, label, capture=False):
        saved = self.state()
        wire = json.loads(self.session.wire())
        assert saved["scores"] == self.expected == wire["scores"]
        assert saved["last_operation"] == wire["operations"]
        assert saved["term"] == wire["term"] and saved["version"] == wire["version"]
        assert self.session.latest().scores == tuple(self.expected)
        assert not self.session.error
        self.events.append({"event": label, "scores": saved["scores"], "turn": saved["turn"],
                            "term": saved["term"], "version": saved["version"], "game": saved["game_id"],
                            "connected": saved["connected_mask"], "sleeping": saved["sleeping_mask"],
                            "operations": saved["last_operation"]})
        if capture:
            time.sleep(0.05)  # Let the real 30 Hz simulator present the committed frame.
            response = self.client.get("/state")
            self.frames.append((label, base64.b64decode(response.json["pixels"])))

    def restart(self):
        before = self.state()
        self.close()
        self.open()
        self.restarts += 1
        after = self.state()
        for field in ("scores", "game_id", "last_operation", "roster_mask", "turn", "version"):
            assert after[field] == before[field]
        assert after["term"] == before["term"] + 1 and after["connected_mask"] == 0
        self.record("authority restart preserved game and advanced term", capture=True)

    def save_evidence(self):
        destination = os.environ.get("CRIBBAGE_PLAY_EVIDENCE")
        if not destination:
            return
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        result = {"session": self.name, "candidate": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                  "native_core": "real ABI1", "transport": "simulated peers" if "mixed" in self.name else "disabled",
                  "score_submissions": self.submissions, "nimbus_buttons": self.buttons, "restarts": self.restarts,
                  "completed_match_scores": next((event["scores"] for event in self.events if event["scores"][0] >= 121), None),
                  "final_scores": self.expected, "events": self.events}
        (output / (self.name + ".json")).write_text(json.dumps(result, indent=2))
        from PIL import Image, ImageDraw
        import numpy as np
        images = []
        for label, raw in self.frames:
            wall = np.frombuffer(raw, dtype=np.uint8).reshape(33, 138, 3)[:, ::-1].transpose(1, 0, 2)
            canvas = Image.new("RGB", (340, 620), (12, 17, 25))
            canvas.paste(Image.fromarray(wall).resize((132, 552), Image.Resampling.NEAREST), (104, 60))
            draw = ImageDraw.Draw(canvas)
            draw.text((8, 8), self.name.replace("-", " "), fill="white")
            draw.text((8, 28), label[:53], fill=(150, 210, 255))
            images.append(canvas)
        images[0].save(output / (self.name + ".gif"), save_all=True, append_images=images[1:], duration=750, loop=0)


class SimulatedBoard:
    def __init__(self, play, player, node, mac):
        self.play, self.player, self.node, self.mac = play, player, node, mac
        self.operation = 0
        self.rejoin()

    def rejoin(self):
        self.token = pair_peer(self.play.session, self.node, 5, 1, self.mac)
        assert self.token is not None
        wire = json.loads(self.play.session.wire())
        self.operation = self.play.library.reconcile_operation(self.operation, wire["operations"][self.player])
        assert not self.play.state()["sleeping_mask"] & (1 << self.player)

    def score(self, delta, *, passes=False, duplicate=False):
        self.operation += 1
        payload = json.dumps({"type": "player", "protocol": 5, "fromNodeId": self.node,
                              "game": self.play.state()["game_id"], "score": delta,
                              "turnPassed": passes, "operationId": self.operation}).encode()
        self.last_packet = payload
        result = self.play.session.receive(self.token, payload)
        assert result in (Result.OK, Result.ACCEPTED_WITHOUT_TURN_CHANGE)
        self.play.expected[self.player] += delta
        self.play.submissions += 1
        wire = json.loads(self.play.session.wire())
        assert self.play.library.operation_acknowledged(self.operation, wire["operations"][self.player])
        if duplicate:
            before = self.play.state()
            assert self.play.session.receive(self.token, payload) == Result.DUPLICATE
            assert self.play.state() == before
        self.play.record(f"simulated board {self.player}: {delta:+d} {'OK' if passes else 'ADD'}")
        return payload

    def disconnect(self, sleeping=False):
        if sleeping:
            payload = json.dumps({"type": "status", "protocol": 5, "fromNodeId": self.node, "state": "sleep"}).encode()
            assert self.play.session.receive(self.token, payload) == Result.OK
        before = self.play.state()
        self.play.session.disconnect(self.token)
        after = self.play.state()
        assert after["roster_mask"] == before["roster_mask"] and after["turn"] == before["turn"]
        self.play.record("validated sleep then disconnect" if sleeping else "unexpected board disconnect")


@pytest.mark.parametrize("players", [2, 4])
def test_complete_local_match_to_121_and_durable_resume(tmp_path, native, players):
    play = PlaySession(tmp_path, native, f"local-{players}-player")
    try:
        play.configure(players=players)
        for round_index, hands in enumerate(HANDS):
            for player in range(players):
                assert play.state()["turn"] == player
                if round_index == 2 and player == 0:
                    before = play.state()
                    play.select(1)
                    play.edit(5)
                    play.press("b", "b", "a")  # Clear, leave controls, re-enter.
                    assert play.state() == before and not any(play.game()._drafts)
                    play.score(1, -1)
                    play.score(1, 1)
                    assert play.state()["turn"] == 0
                if round_index == 4 and player == 0:
                    play.select(0)
                    play.edit(3)
                    play.press("menu", "menu", "a", "x")
                    assert play.session.feedback == "NO_CHANGE" and not any(play.game()._drafts)
                total = hands[player]
                play.score(player, 2)
                play.score(player, total - 2, passes=True)
                assert play.state()["turn"] == (player + 1) % players
                if player == 0 and play.expected[0] == 121:
                    break
            play.record(f"round {round_index + 1} complete", capture=True)
            if play.expected[0] == 121:
                break
            if round_index == 5:
                play.restart()
        assert play.expected == ([121, 106, 0, 0] if players == 2 else [121, 106, 104, 102])
        assert play.state()["connected_mask"] == 0
        play.save_evidence()
    finally:
        play.close()


def test_complete_mixed_match_takeover_rejoin_scene_switch_and_new_game(tmp_path, native):
    play = PlaySession(tmp_path, native, "mixed-four-player")
    try:
        red = SimulatedBoard(play, 0, RED, RED_MAC)
        green = SimulatedBoard(play, 2, GREEN, GREEN_MAC)
        boards = {0: red, 2: green}
        play.configure(physical=boards, players=4)
        for round_index, hands in enumerate(HANDS):
            for player in range(4):
                assert play.state()["turn"] == player
                if round_index == 2 and player == 2:
                    old_token = green.token
                    green.disconnect()
                    play.score(2, 2)
                    play.score(2, hands[2] - 2, passes=True)
                    green.rejoin()
                    assert play.session.receive(old_token, b"{}") == Result.WRONG_AUTHORITY
                elif round_index == 3 and player == 0:
                    red.disconnect(sleeping=True)
                    play.score(0, 2)
                    play.score(0, hands[0] - 2, passes=True)
                    red.rejoin()
                elif player in boards:
                    board = boards[player]
                    if round_index == 4 and player == 2:
                        play.select(1)
                        play.edit(3)
                        assert handle_command(play.manager, "start_scene", {"scene": get_starter("aurora")["scene"]})
                        board.score(2, duplicate=True)
                        board.score(hands[player] - 2, passes=True)
                        assert handle_command(play.manager, "start_scene", {"scene": play.scene})
                        play.press("a", "x")
                        assert not any(play.game()._drafts)
                        play.record("board played while another Scene owned the wall", capture=True)
                    else:
                        board.score(2, duplicate=True)
                        board.score(hands[player] - 2, passes=True)
                else:
                    play.score(player, 2)
                    play.score(player, hands[player] - 2, passes=True)
                assert play.state()["turn"] == (player + 1) % 4
                if play.expected[0] == 121:
                    break
            play.record(f"mixed round {round_index + 1} complete", capture=True)
            if play.expected[0] == 121:
                break
            if round_index == 5:
                pending = json.loads(green.last_packet)
                play.restart()
                red.rejoin()
                green.rejoin()
                wire = json.loads(play.session.wire())
                assert native.operation_acknowledged(pending["operationId"], wire["operations"][2])
                assert play.session.receive(green.token, green.last_packet) == Result.DUPLICATE
                play.record("rejoined boards resynchronized without replaying scores")
        assert play.expected == [121, 106, 104, 102]
        old = play.state()
        play.press("y", "down", "a", "b")  # Cancel destructive confirmation.
        assert play.state() == old
        play.press("y", "down", "a", "a")
        fresh = play.state()
        assert fresh["game_id"] == old["game_id"] + 1 and fresh["version"] > old["version"]
        assert fresh["last_operation"] == old["last_operation"]
        assert fresh["scores"] == [0] * 4 and fresh["started"] == 0
        play.expected = [0] * 4
        delayed = json.loads(green.last_packet)
        delayed["operationId"] += 100
        assert play.session.receive(green.token, json.dumps(delayed).encode()) == Result.WRONG_GAME
        assert play.state() == fresh
        play.record("confirmed New Game rejects delayed old-game traffic", capture=True)
        play.save_evidence()
    finally:
        play.close()


def test_play_feedback_and_back_confirmation_render_complete_words(tmp_path, native):
    import numpy as np
    play = PlaySession(tmp_path, native, "feedback-glyphs")
    try:
        play.configure()
        play.score(0, 1)
        runtime = play.manager.nimbus_game()[0]
        with runtime._lock:
            canvas = play.game().generate_frame(1.0, 1).pixels.reshape(33, 138, 3)[:, ::-1].copy()
        assert np.any(canvas[17:20, 133:138]), "OK feedback is missing its K glyph"
        play.press("y", "down", "a")
        with runtime._lock:
            canvas = play.game().generate_frame(1.0, 2).pixels.reshape(33, 138, 3)[:, ::-1].copy()
        assert np.any(canvas[25:28, 133:138]), "B BACK confirmation is missing its K glyph"
    finally:
        play.close()
