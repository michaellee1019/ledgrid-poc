Import("env")
import os

if os.environ.get("DEBUG") == "1":
    env.Append(CPPDEFINES=[("DEBUG_LOGGING", 1)])

# ESP-IDF component compilation is controlled by CMake rather than ordinary
# PlatformIO build_flags. Export the narrowly named rollout bit for CMake.
env["ENV"]["LEDGRID_LOCAL_BACKGROUND"] = (
    "1" if env.subst("$PIOENV") == "esp32-s3-devkitc-1-local-canary" else "0"
)
