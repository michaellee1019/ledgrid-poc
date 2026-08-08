"""Wire and package constants for firmware-resident animations."""

PACKAGE_FORMAT = 1
ANIMATION_ABI = "lga-animation-v1"
ANIMATION_ABI_ID = 1
ESP32_TARGET = "esp32s3-elf-loader-1.3.2"
ESP32_TARGET_ID = 1
WALL_STRIPS = 32
LEDS_PER_STRIP = 138
RECEIVER_COUNT = 4
LOCAL_STRIPS = 8

MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_NATIVE_BYTES = 512 * 1024
MAX_TRACK_BYTES = 5 * 1024 * 1024 // 2
FRAME_PARAMETER_NAMES = frozenset({
    "asset_brightness",
    "loop",
    "pause",
    "playback_speed",
})
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 32
MAX_COMPRESSION_RATIO = 250
MAX_SIGNING_KEY_ID_BYTES = 32
P256_SIGNATURE_BYTES = 64

MANIFEST_PATH = "manifest.json"
INDEX_PATH = "index.lgix"
SIGNATURE_PATH = "signature.p256"
PREVIEW_PATH = "preview/preview.webp"
NATIVE_PAYLOAD_PATH = "payload/native/module.so"

# ABI v1 exposes its bounded color, math, and randomness functions through the
# callback helper table. Target shared objects must therefore be self-contained
# and have no undefined imports.
DEFAULT_IMPORT_ALLOWLIST: frozenset[str] = frozenset()


def track_path(device_index: int) -> str:
    if not 0 <= device_index < RECEIVER_COUNT:
        raise ValueError("device index must be in [0, 3]")
    return f"payload/frames/device-{device_index}.lgft"
