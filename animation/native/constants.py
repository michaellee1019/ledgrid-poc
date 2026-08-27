"""Frozen constants for unsigned repository-native background bundles."""

from __future__ import annotations

ABI_SCHEMA = "ledgrid.native-background-abi"
ABI_VERSION = 2
COMPONENT_ENTRYPOINT = f"{ABI_SCHEMA}:{ABI_VERSION}"
ELF_ENTRYPOINT = "ledgrid_native_background_v2"

BUNDLE_SCHEMA = "ledgrid.native-background-bundle"
BUNDLE_VERSION = 1
TARGET = "esp32-s3"

# Final installed wall geometry.  ``LOCAL_STRIPS`` is the maximum receiver
# lane width; the fifth receiver owns the one-strip tail explicitly recorded in
# ``RECEIVER_VIEWS``.  Keep logical receiver identity separate from physical
# left-to-right order and native strip direction.
GLOBAL_STRIPS = 33
LOCAL_STRIPS = 8
LEDS_PER_STRIP = 138
RECEIVER_OFFSETS = (0, 8, 16, 24, 32)
# (logical receiver ID, global strip offset, local width, native reversal).
# Physical strip origins 0/8/16/24/32 now map to logical IDs 0/1/2/3/4.
RECEIVER_VIEWS = (
    (0, 0, 8, False),
    (1, 8, 8, False),
    (2, 16, 8, True),
    (3, 24, 8, True),
    (4, 32, 1, False),
)

MANIFEST_PATH = "manifest.json"
PAYLOAD_PATH = "payload/module.so"
PREVIEW_PATH = "preview/preview.webp"
BUNDLE_MEMBERS = frozenset((MANIFEST_PATH, PAYLOAD_PATH, PREVIEW_PATH))

MAX_PAYLOAD_BYTES = 512 * 1024
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 3 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 3 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 3

EXPECTED_PLATFORMIO_VERSION = "6.1.19"
TARGET_TOOLCHAIN_PACKAGE = "toolchain-xtensa-esp-elf"
EXPECTED_TARGET_TOOLCHAIN_VERSION = "14.2.0+20260121"
TARGET_COMPILER_NAME = "xtensa-esp-elf-g++"
TARGET_DYNCONFIG_NAME = "xtensa_esp32s3.so"

TARGET_IDENTITY_FLAGS = (
    f"-mdynconfig={TARGET_DYNCONFIG_NAME}",
    "-std=c++17",
    "-fPIC",
    "-fno-exceptions",
    "-fno-rtti",
    "-fno-ident",
    "-fvisibility=hidden",
    "-ffunction-sections",
    "-fdata-sections",
    "-Os",
    "-shared",
    "-nostdlib",
    "-frandom-seed=ledgrid-native-background-v2",
    "-Wl,--gc-sections",
    "-Wl,--build-id=none",
    "-Wl,--no-undefined",
    "-I",
    "firmware/esp32/include",
)

HOST_IDENTITY_FLAGS = (
    "-std=c++17",
    "-fPIC",
    "-fno-exceptions",
    "-fno-rtti",
    "-fno-ident",
    "-fvisibility=hidden",
    "-ffunction-sections",
    "-fdata-sections",
    "-O2",
    "-shared",
    "-DLG_HOST_PREVIEW=1",
    "-I",
    "firmware/esp32/include",
)
HOST_LINK_FLAGS = {
    # Modern Darwin's content-derived LC_UUID is deterministic and required by
    # the hardened dynamic loader; suppressing it makes the preview unloadable.
    "darwin": ("-Wl,-install_name,@rpath/ledgrid-native-preview.so",),
    "linux": ("-Wl,--build-id=none",),
}

ABI_HEADER_PATH = "firmware/esp32/include/ledgrid/native_background_abi_v2.h"
PLUGIN_ROOT = "animation/plugins"

PARAMETER_TYPES = frozenset(("bool", "float", "int", "str"))
MAX_PARAMETERS = 31
MAX_STATE_BYTES = 64 * 1024
MAX_STATE_ALIGNMENT = 64

PALETTE_ROLE_ORDER = (
    "background_low",
    "background_mid",
    "background_high",
    "primary",
    "secondary",
    "accent",
    "hud",
    "warning",
)

NEUTRAL_PALETTE = (
    (8, 10, 16),
    (32, 38, 52),
    (92, 104, 128),
    (224, 228, 236),
    (152, 164, 184),
    (255, 184, 72),
    (240, 244, 252),
    (255, 72, 64),
)
