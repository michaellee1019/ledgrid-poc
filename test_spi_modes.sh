#!/bin/bash
# Test different SPI modes to find which works
# Run this on Raspberry Pi

echo "Testing SPI Mode 0..."
sudo python3 led_controller_spi.py --mode 0 --debug clear
sleep 2

echo ""
echo "Testing SPI Mode 1..."
sudo python3 led_controller_spi.py --mode 1 --debug clear
sleep 2

echo ""
echo "Testing SPI Mode 2..."
sudo python3 led_controller_spi.py --mode 2 --debug clear
sleep 2

echo ""
echo "Testing SPI Mode 3..."
sudo python3 led_controller_spi.py --mode 3 --debug clear
sleep 2

echo ""
echo "Check ESP32 serial monitor to see which mode had high SCK counts"

