#!/bin/bash
# Check Raspberry Pi SPI configuration and status

echo "=== Raspberry Pi SPI Configuration Check ==="
echo ""

echo "1. Checking if SPI is enabled in kernel..."
if lsmod | grep -q spi_bcm2835; then
    echo "   ✓ SPI kernel module loaded (spi_bcm2835)"
else
    echo "   ✗ SPI kernel module NOT loaded!"
    echo "   Run: sudo raspi-config -> Interface Options -> SPI -> Enable"
fi
echo ""

echo "2. Checking SPI device files..."
if ls /dev/spidev* &>/dev/null; then
    echo "   ✓ SPI devices found:"
    ls -l /dev/spidev*
else
    echo "   ✗ No /dev/spidev* devices found!"
fi
echo ""

echo "3. Checking GPIO pin states (requires gpio utility)..."
if command -v gpio &>/dev/null; then
    echo "   GPIO 8  (CS):   $(gpio -g read 8)"
    echo "   GPIO 9  (MISO): $(gpio -g read 9)"
    echo "   GPIO 10 (MOSI): $(gpio -g read 10)"
    echo "   GPIO 11 (SCK):  $(gpio -g read 11)"
else
    echo "   (gpio utility not installed - optional)"
fi
echo ""

echo "4. Checking SPI mode and settings..."
if [ -f /sys/module/spi_bcm2835/parameters/polling_limit_us ]; then
    echo "   SPI polling limit: $(cat /sys/module/spi_bcm2835/parameters/polling_limit_us) us"
fi
echo ""

echo "5. Testing basic SPI with spidev..."
if command -v python3 &>/dev/null; then
    python3 - <<'EOF'
try:
    import spidev
    spi = spidev.SpiDev()
    spi.open(0, 0)
    print(f"   ✓ SPI open successful")
    print(f"   - Max speed: {spi.max_speed_hz} Hz")
    print(f"   - Mode: {spi.mode}")
    print(f"   - Bits per word: {spi.bits_per_word}")
    spi.close()
except Exception as e:
    print(f"   ✗ Error: {e}")
EOF
else
    echo "   (python3 not available)"
fi
echo ""

echo "=== Summary ==="
echo "If SPI devices exist but SCK doesn't toggle:"
echo "  1. Try the direct wire test: python3 test_wire_direct.py"
echo "  2. Check for a faulty wire (even if continuity tests pass)"
echo "  3. Try a different jumper wire for SCK"
echo ""





