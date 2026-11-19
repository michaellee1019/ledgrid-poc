"""Validation-only sitecustomize: packet-preserving raw Nimbus report transport.

Copy as sitecustomize.py into a private test PYTHONPATH directory. Production
decoding/routing stays unchanged; this replaces only opening the HID device.
"""
import os
from pathlib import Path
import socket
import stat
import sys
import time


if "--mode" in sys.argv and sys.argv[sys.argv.index("--mode") + 1] == "controller":
    from drivers import nimbus_gamepad

    class ReportInput(nimbus_gamepad.NimbusGamepad):
        def __init__(self):
            path = Path(os.environ["CRIBBAGE_TEST_INPUT"])
            if path.exists():
                if not stat.S_ISSOCK(path.lstat().st_mode):
                    raise RuntimeError("Refusing to replace a non-socket test path")
                path.unlink()
            self._reports = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self._reports.bind(str(path))
            self._reports.setblocking(False)
            super().__init__(str(path))

        def _open(self, now):
            if now < self._next_open:
                return False
            self._next_open = now + 1
            self._fd = os.dup(self._reports.fileno())
            return True

    nimbus_gamepad.NimbusGamepad = ReportInput
    Path(os.environ["CRIBBAGE_TEST_INPUT"] + ".shim-loaded").write_text(str(time.time()))
