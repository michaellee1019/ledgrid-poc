"""Fixed-point quantization retained by host plant color effects."""
import math

Q8_8_ONE = 256
Q8_8_MAX = 0xFFFF


def quantize_q8_8(value: float, *, name: str = "value", maximum: int = Q8_8_MAX) -> int:
    """Round a finite non-negative scalar to unsigned Q8.8, half upward."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite non-negative number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"{name} must be a finite non-negative number")
    quantized = math.floor(numeric * Q8_8_ONE + 0.5)
    if quantized > maximum:
        raise ValueError(f"{name} overflows unsigned Q8.8")
    return quantized
