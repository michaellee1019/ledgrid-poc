"""Host SDK for signed receiver-local LED-grid animations."""

from .constants import ANIMATION_ABI, ESP32_TARGET
from .errors import (
    ActivePackageError,
    FirmwareAnimationError,
    NativeToolchainError,
    PackageValidationError,
    SignatureDependencyError,
)
from .library import FirmwareAnimationLibrary, InstalledPackage
from .manifest import canonical_json, validate_manifest, validate_parameter_schema, validate_parameters
from .package import ReceiverVerificationEnvelope, VerifiedPackage, build_frame_package, build_native_package, inspect_package
from .tracks import (
    DecodedTrack,
    ImageFrames,
    assemble_wall_frames,
    decode_track,
    encode_image_tracks,
    encode_track,
    load_image_frames,
    rgb565_to_rgb888,
    rgb888_to_rgb565,
    split_wall_frame,
)

__all__ = [
    "ANIMATION_ABI", "ESP32_TARGET", "ActivePackageError", "FirmwareAnimationError",
    "NativeToolchainError", "PackageValidationError", "SignatureDependencyError",
    "FirmwareAnimationLibrary", "InstalledPackage", "VerifiedPackage", "ReceiverVerificationEnvelope", "DecodedTrack",
    "ImageFrames", "canonical_json", "validate_manifest", "validate_parameter_schema",
    "validate_parameters", "build_frame_package", "build_native_package", "inspect_package",
    "assemble_wall_frames", "decode_track", "encode_image_tracks", "encode_track",
    "load_image_frames", "rgb565_to_rgb888", "rgb888_to_rgb565", "split_wall_frame",
]
