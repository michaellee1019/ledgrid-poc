"""Errors raised by the repository-native background authoring lane."""


class NativeBackgroundError(RuntimeError):
    """Base class for native-background authoring failures."""


class NativeManifestError(NativeBackgroundError, ValueError):
    """A component or bundle manifest violates the frozen contract."""


class NativeBundleError(NativeBackgroundError, ValueError):
    """A native bundle is unsafe, corrupt, or noncanonical."""


class NativeElfError(NativeBundleError):
    """A target payload violates the supported ESP32-S3 ELF contract."""


class NativeBuildError(NativeBackgroundError):
    """A compiler, source-policy, or deterministic-build gate failed."""


class NativePreviewError(NativeBuildError):
    """The isolated host preview failed or violated its memory contract."""
