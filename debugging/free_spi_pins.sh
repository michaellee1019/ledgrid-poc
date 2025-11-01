#!/bin/bash
# Free SPI GPIO pins by unloading SPI kernel modules

echo "=== Freeing SPI GPIO Pins ==="
echo ""
echo "This will temporarily disable hardware SPI to allow software SPI."
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root: sudo bash free_spi_pins.sh"
    exit 1
fi

echo "1. Removing SPI kernel modules..."
rmmod spidev 2>/dev/null || echo "  spidev not loaded"
rmmod spi_bcm2835 2>/dev/null || echo "  spi_bcm2835 not loaded"
rmmod spi_bcm2835aux 2>/dev/null || echo "  spi_bcm2835aux not loaded"

sleep 1

echo ""
echo "2. Checking module status..."
if lsmod | grep -q spi; then
    echo "  ✗ SPI modules still loaded:"
    lsmod | grep spi
else
    echo "  ✓ SPI modules unloaded"
fi

echo ""
echo "3. GPIO pins should now be free for software control"
echo ""
echo "✓ Done!"
echo ""
echo "Now run: python3 led_controller_spi_bitbang.py test"
echo ""
echo "To restore hardware SPI later:"
echo "  sudo modprobe spi_bcm2835"
echo "  sudo modprobe spidev"
echo "Or just reboot."





