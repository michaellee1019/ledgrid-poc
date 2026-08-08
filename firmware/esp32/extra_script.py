Import("env")
import os

if os.environ.get("DEBUG") == "1":
    env.Append(CPPDEFINES=[("DEBUG_LOGGING", 1)])
