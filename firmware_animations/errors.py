class FirmwareAnimationError(Exception):
    """Base error for the host firmware-animation SDK."""


class PackageValidationError(FirmwareAnimationError):
    """A package is unsafe, malformed, untrusted, or incompatible."""


class SignatureDependencyError(FirmwareAnimationError):
    """The optional signing dependency is unavailable."""


class ActivePackageError(FirmwareAnimationError):
    """An operation would remove the active firmware animation."""


class NativeToolchainError(FirmwareAnimationError):
    """A native build contract could not be fulfilled."""
