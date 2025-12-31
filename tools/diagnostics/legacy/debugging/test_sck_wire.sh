#!/bin/bash
# Test SCK wire by temporarily disabling SPI and toggling the pin

echo "=== SCK Wire Test ==="
echo ""
echo "This will:"
echo "  1. Temporarily disable SPI"
echo "  2. Toggle GPIO 11 (SCK) HIGH/LOW"
echo "  3. Re-enable SPI"
echo ""
echo "Watch SCORPIO serial monitor for SCK state changes!"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root: sudo bash test_sck_wire.sh"
    exit 1
fi

# Disable SPI
echo "1. Disabling SPI..."
modprobe -r spi_bcm2835 2>/dev/null || true
modprobe -r spi_bcm2835aux 2>/dev/null || true
sleep 1

# Export GPIO 11 (SCK)
echo "2. Exporting GPIO 11 (SCK)..."
echo 11 > /sys/class/gpio/export 2>/dev/null || true
sleep 0.5

# Set as output
echo "3. Configuring as output..."
echo out > /sys/class/gpio/gpio11/direction

# Toggle the pin
echo ""
echo "4. Toggling SCK (GPIO 11)..."
echo "   Watch SCORPIO monitor for SCK changes!"
echo ""

for i in {1..10}; do
    echo 1 > /sys/class/gpio/gpio11/value
    echo "   [$i] SCK -> HIGH"
    sleep 1
    
    echo 0 > /sys/class/gpio/gpio11/value
    echo "   [$i] SCK -> LOW"
    sleep 1
done

# Clean up
echo ""
echo "5. Cleaning up..."
echo 11 > /sys/class/gpio/unexport 2>/dev/null || true

# Re-enable SPI
echo "6. Re-enabling SPI..."
modprobe spi_bcm2835 2>/dev/null || true
sleep 1

echo ""
echo "✓ Test complete!"
echo ""
echo "Check SCORPIO serial monitor:"
echo "  - If SCK toggled HIGH/LOW: Wire is good, SPI hardware issue"
echo "  - If SCK stayed LOW: Wire is faulty or wrong pin"
echo ""
echo "You may need to reboot to fully restore SPI functionality."





