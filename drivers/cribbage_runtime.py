"""Controller-process ownership and visible startup failures for Scorebot."""
import json
import os


class UnavailableSession:
    def __init__(self, error):
        self.error = "Scorebot unavailable: " + str(error)
        self.feedback = self.error

    def latest(self):
        return None

    def request(self, *_args):
        raise RuntimeError(self.error)

    def close(self):
        pass


class ScorebotHost:
    def __init__(self, data_root):
        self.transport = None
        try:
            from drivers.cribbage_session import CribbageSession
            identity = json.loads((data_root / "config/scorebot_identity.json").read_text())
            self.session = CribbageSession(data_root / "run_state/cribbage.json", identity=identity)
        except Exception as error:
            self.session = UnavailableSession(error)
            return
        if os.environ.get("SCOREBOT_BLE", "1") != "0":
            from drivers.cribbage_ble import BlueZCentral
            self.transport = BlueZCentral(self.session, identity)
            self.session.transport = self.transport

    def status(self):
        state = self.session.latest()
        return {"pending": getattr(self.session, "pending", False), "error": self.session.error, "feedback": self.session.feedback,
                "ble_enabled": self.transport is not None,
                "ble_error": self.transport.error if self.transport else "",
                "scores": list(state.scores) if state else None,
                "turn": state.turn if state else None,
                "revision": state.revision() if state else None}

    def close(self):
        if self.transport:
            self.transport.close()
        self.session.close()
