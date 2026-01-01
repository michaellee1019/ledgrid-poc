# Drivers Layer

Purpose: hardware communication and frame transport to ESP32 devices.

Key files:
- spi_controller.py: Single ESP32 device controller over SPI
- multi_device.py: Splits frames and coordinates multiple devices
- frame_codec.py: Frame encoding/decoding utilities
- led_layout.py: Strip and pixel layout constants

Usage notes:
- Controllers expect a full-frame list sized to total LEDs.
- MultiDeviceLEDController mirrors the single-device API for compatibility.
- Keep transport details here; animations should stay hardware-agnostic.
