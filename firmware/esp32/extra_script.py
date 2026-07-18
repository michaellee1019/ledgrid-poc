Import("env")
import os

if os.environ.get("RAINBOW") == "1":
    env.Append(CPPDEFINES=[("RAINBOW_MODE", 1)])

if os.environ.get("DEBUG") == "1":
    env.Append(CPPDEFINES=[("DEBUG_LOGGING", 1)])
