"""Single serialized, durable Scorebot authority, independent of any Scene."""
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

from animation.plugins.cribbage_leaderboard.state import LeaderboardState

SOURCE = Path(__file__).resolve().parents[1] / "vendor/scorebot/native"
sys.path.insert(0, str(SOURCE / "python"))
from scorebot import Action, Library, LobbyMode, PeerKind, Player, Result, State  # noqa: E402

MAX = 0xFFFFFFFF


def durable_save(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(values, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Peer:
    node: int
    kind: PeerKind
    player: Player


class CribbageSession:
    def __init__(self, path, library=None):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.error = ""
        self.feedback = ""
        self.pending = False
        self.peers = {}
        self.bindings = {}
        self._view = None
        self._wire = None
        self._game = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lease = self.path.with_suffix(".lock").open("a+")
        with self.lock:
            try:
                fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.library = library or Library()
                if self.library.protocol_version != 5:
                    raise ValueError("Scorebot wire protocol mismatch")
                values = json.loads(self.path.read_text()) if self.path.exists() else None
                if self.path.exists() and type(values) is not dict:
                    raise ValueError("Invalid saved game record")
                pairing = values["pairing"] if values and "pairing" in values else {}
                if values and "pairing" in values and (set(pairing) != {"node_id", "session_id", "pairing_id"}
                        or set(values) != {"pairing", "state", "controllers", "bindings"}
                        or any(type(value) is not int for value in pairing.values())):
                    raise ValueError("Incomplete saved pairing")
                if values and "pairing" not in values:
                    pairing = {"node_id": State.from_dict(values).leader_id}  # Trusted v4 migration.
                self._game = self.library.game(**pairing)
                if values is not None:
                    controllers = set()
                    for packet, address, public in values.get("bindings", []):
                        if type(address) is not int or type(public) is not int:
                            raise ValueError("Invalid saved transport types")
                        raw = bytes.fromhex(packet)
                        node = int.from_bytes(raw[5:9], "little")
                        kind, _ = self._game.admit_peer(raw, address, public)
                        if kind == PeerKind.REJECTED or node in self.bindings:
                            raise ValueError("Invalid saved peer binding")
                        self.bindings[node] = [packet, address, public]
                        if kind == PeerKind.CONTROLLER:
                            controllers.add(node)
                    state = values["state"] if "pairing" in values else values
                    scalars = [item for value in state.values() for item in
                               (value if isinstance(value, list) else [value])]
                    if any(type(item) is not int for item in scalars):
                        raise ValueError("Invalid saved native state types")
                    self._require(self._game.restore(State.from_dict(state)))
                    if "pairing" in values:
                        if any(type(item) is not int for array in values["controllers"] for item in array):
                            raise ValueError("Invalid saved controller types")
                        if any(node and node not in controllers for node in values["controllers"][0]):
                            raise ValueError("Missing saved controller binding")
                        self._game.controller_marks(values["controllers"])
                self._require(self._game.resume_authority())
                self._commit(self._game.read())
            except Exception:
                self.close()
                raise

    @staticmethod
    def _require(result):
        if result != Result.OK:
            raise RuntimeError("Scorebot: " + result.name)

    def _ready(self):
        if self.error or self._game is None:
            raise RuntimeError(self.error or "Scorebot authority closed")

    def _commit(self, state):
        self.pending = True
        try:
            durable_save(self.path, {"pairing": self._game.pairing, "state": state.to_dict(),
                                     "controllers": self._game.controller_marks(),
                                     "bindings": list(self.bindings.values())})
        except Exception as error:
            self.error = "Scorebot save failed; restart after repair: " + str(error)
            self._wire = None
            self.pending = False
            raise RuntimeError(self.error) from error
        wire = state.to_wire(self.library.protocol_version)
        self._wire = json.dumps(wire, separators=(",", ":")).encode()
        self._discovery = self._game.pairing_packet()
        self._view = LeaderboardState(
            bool(state.started), wire["turn"], tuple(state.scores), state.connected_mask,
            state.roster_mask, state.term, state.version, state.local_control_mask,
            state.lobby_enabled_mask, state.sleeping_mask, self._game.pairing["pairing_id"],
        )
        self.pending = False

    def latest(self):
        return self._view

    def wire(self):
        with self.lock:
            self._ready()
            return self._wire

    def discovery(self):
        with self.lock:
            self._ready()
            return self._discovery

    def _apply(self, method, *args):
        self._ready()
        before = self._game.read().to_dict()
        if before["version"] == MAX:
            result = Result.REJECTED
        else:
            result = getattr(self._game, method)(*args)
        after = self._game.read()
        if after.to_dict() != before:
            self._commit(after)
        self.feedback = result.name
        return result

    def request(self, method, *args):
        with self.lock:
            if method == "local_action":
                player, delta, action = args
                args = (Player(player), delta, Action(action))
            elif method == "set_lobby_mode":
                player, mode = args
                args = (Player(player), LobbyMode(mode))
            elif method != "start":
                raise ValueError("Unsupported local request")
            return self._apply(method, *args) == Result.OK

    def new_game(self):
        with self.lock:
            self._ready()
            before = self._game.read()
            if before.game_id == MAX or before.version == MAX:
                self.feedback = "EPOCH EXHAUSTED"
                return False
            reset = self._game.reset()
            if reset not in (Result.OK, Result.NO_CHANGE):
                self.error = "New Game reset failed: " + reset.name
                raise RuntimeError(self.error)
            state = self._game.read()
            state.game_id = before.game_id + 1
            state.version = max(state.version, before.version + 1)
            restored = self._game.restore(state)
            if restored != Result.OK:
                self.error = "New Game restore failed: " + restored.name
                raise RuntimeError(self.error)
            self._commit(self._game.read())
            self.feedback = "NEW GAME"
            return True

    def admit(self, node, protocol, public, address, packet):
        with self.lock:
            self._ready()
            if (protocol != self.library.protocol_version or len(packet) != 22
                    or int.from_bytes(packet[5:9], "little") != node
                    or any(peer.node == node for peer in self.peers.values())):
                return None
            kind, player = self._game.admit_peer(packet, address, public)
            if kind == PeerKind.REJECTED:
                return None
            self.bindings[node] = [packet.hex(), address, public]
            self._commit(self._game.read())  # Bindings are durable before admitting input.
            token = object()
            if kind == PeerKind.PLAYER:
                self._apply("set_connected", player, 1)
                if not self._game.read().connected_mask & (1 << player):
                    return None
            self.peers[token] = Peer(node, kind, player)
            return token

    def disconnect(self, token):
        with self.lock:
            peer = self.peers.pop(token, None)
            if peer and peer.kind == PeerKind.PLAYER and not self.error:
                self._apply("set_connected", peer.player, 0)

    def receive(self, token, message):
        from drivers.cribbage_ble import decode_message
        with self.lock:
            self._ready()
            peer = self.peers.get(token)
            if peer is None or peer.kind == PeerKind.DISPLAY:
                return Result.WRONG_AUTHORITY
            try:
                data = decode_message(message, self.library.protocol_version)
                if data.get("from", data.get("fromNodeId")) != peer.node:
                    raise ValueError("Spoofed node")
                if data["type"] == "control" and peer.kind == PeerKind.CONTROLLER:
                    return self._apply("controller_action", peer.node, data["op"], data["command"],
                                       data["player"], data["value"], int(data["pass"]), data["game"], data["term"])
                if peer.kind != PeerKind.PLAYER or data["type"] == "control":
                    return Result.WRONG_AUTHORITY
                if data["type"] == "player":
                    return self._apply("player_action", peer.node, data["fromNodeId"], data["game"],
                                       data["score"], int(data["turnPassed"]), data["operationId"])
                if data["type"] == "status":
                    return self._apply("mark_sleeping", peer.player)
                if data["game"] == self._game.read().game_id:
                    return Result.NO_CHANGE
            except (ValueError, TypeError, KeyError):
                pass
            self.feedback = "INVALID MESSAGE"
            return Result.INVALID_ARGUMENT

    def close(self):
        with self.lock:
            if self._game is not None:
                self._game.close()
                self._game = None
            self._lease.close()
