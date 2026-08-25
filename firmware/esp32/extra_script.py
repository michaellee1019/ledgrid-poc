# PlatformIO injects the SCons ``Import`` helper and ``env`` construction scope.
# ruff: noqa: F821
Import("env")
import os

if os.environ.get("DEBUG") == "1":
    env.Append(CPPDEFINES=[("DEBUG_LOGGING", 1)])

# ESP-IDF component compilation is controlled by CMake rather than ordinary
# PlatformIO build_flags. Export the narrowly named rollout bit for CMake.
canary_environments = {
    "esp32-s3-devkitc-1-local-canary",
    "esp32-s3-devkitc-1-native-canary",
}
env["ENV"]["LEDGRID_LOCAL_BACKGROUND"] = (
    "1" if env.subst("$PIOENV") in canary_environments else "0"
)
env["ENV"]["LEDGRID_INSTALLATION_PROFILES"] = (
    "1" if env.subst("$PIOENV") in canary_environments else "0"
)
env["ENV"]["LEDGRID_RECEIVER_NATIVE_MODULES"] = (
    "1" if env.subst("$PIOENV") == "esp32-s3-devkitc-1-native-canary" else "0"
)
