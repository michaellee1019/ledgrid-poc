# PlatformIO injects the SCons ``Import`` helper and ``env`` construction scope.
# ruff: noqa: F821
Import("env")
import os

if os.environ.get("DEBUG") == "1":
    env.Append(CPPDEFINES=[("DEBUG_LOGGING", 1)])
