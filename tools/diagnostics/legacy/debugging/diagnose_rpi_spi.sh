#!/bin/bash

echo "========================================"
echo "Raspberry Pi SPI Diagnostic Tool"
echo "========================================"

# Check if SPI is enabled
echo ""
echo "1. Checking if SPI is enabled..."
if lsmod | grep -q spi_bcm2835; then
    echo "   ✓ SPI kernel module loaded"
else
    echo "   ✗ SPI kernel module NOT loaded"
    echo "   → Run: sudo raspi-config"
    echo "   → Interface Options → SPI → Enable"
    exit 1
fi

# Check if SPI device exists
echo ""
echo "2. Checking SPI device files..."
if [ -e /dev/spidev0.0 ]; then
    echo "   ✓ /dev/spidev0.0 exists"
    ls -l /dev/spidev0.0
else
    echo "   ✗ /dev/spidev0.0 NOT found"
    echo "   → SPI may not be enabled in raspi-config"
    exit 1
fi

# Check if anything is using SPI
echo ""
echo "3. Checking if SPI is in use..."
if lsof /dev/spidev0.0 2>/dev/null; then
    echo "   ⚠️  SPI device is currently OPEN by another process"
    echo "   → Close that process first!"
else
    echo "   ✓ SPI device is available"
fi

# Check pin configuration
echo ""
echo "4. Checking GPIO pin modes..."
if command -v pinctrl &> /dev/null; then
    echo "   Using pinctrl:"
    pinctrl get 8,10,11  # CE0, MOSI, SCLK
elif command -v raspi-gpio &> /dev/null; then
    echo "   Using raspi-gpio:"
    raspi-gpio get 8,10,11
else
    echo "   ⚠️  No GPIO tool found"
fi

# Test Python SPI
echo ""
echo "5. Testing Python spidev import..."
if python3 -c "import spidev; print('   ✓ spidev module available')" 2>/dev/null; then
    true
else
    echo "   ✗ spidev module NOT found"
    echo "   → Run: pip3 install spidev"
    exit 1
fi

echo ""
echo "========================================"
echo "All checks passed!"
echo "SPI should be ready to use."
echo "========================================"

