#!/bin/bash
# Direct GPIO toggle test using raspi-gpio command
# Tests if GPIO 11 wire is connected to SCORPIO GPIO 14

echo "============================================================"
echo "Direct GPIO Wire Test for SCLK"
echo "============================================================"
echo "This tests the physical wire connection"
echo "RPi GPIO 11 <--> SCORPIO GPIO 14"
echo ""
echo "We'll toggle GPIO 11 HIGH and LOW"
echo "Watch SCORPIO serial monitor to see if GPIO 14 changes!"
echo "============================================================"
echo ""

# Check if raspi-gpio is installed
if ! command -v raspi-gpio &> /dev/null; then
    echo "ERROR: raspi-gpio not found!"
    echo "Install it with: sudo apt-get install raspi-gpio"
    exit 1
fi

echo "Setting GPIO 11 to OUTPUT mode..."
raspi-gpio set 11 op

echo ""
echo "Now toggling GPIO 11 slowly (10 cycles)..."
echo "Watch SCORPIO serial monitor - GPIO 14 should toggle!"
echo ""

for i in {1..10}; do
    # Set HIGH
    raspi-gpio set 11 dh
    echo "Round $i: GPIO 11 = HIGH (1)"
    echo "  → SCORPIO GPIO 14 should show: 1"
    sleep 2
    
    # Set LOW
    raspi-gpio set 11 dl
    echo "Round $i: GPIO 11 = LOW (0)"
    echo "  → SCORPIO GPIO 14 should show: 0"
    sleep 2
    echo ""
done

echo "============================================================"
echo "Test complete!"
echo ""
echo "Check SCORPIO serial output:"
echo "  ✓ If GPIO 14 toggled 0→1→0→1: Wire IS connected!"
echo "  ✗ If GPIO 14 stayed at 0: Wire NOT connected or wrong pins"
echo "============================================================"
echo ""
echo "Restoring GPIO 11 to SPI function..."
raspi-gpio set 11 a0  # ALT0 = SPI function
echo "Done!"

