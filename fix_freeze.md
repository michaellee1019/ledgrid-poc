# Fix Script Freeze on New Raspberry Pi

## Quick Diagnostics

### Step 1: Check if SPI is enabled
```bash
ls -l /dev/spidev*
```

**Expected:** Should see `/dev/spidev0.0` and `/dev/spidev0.1`

**If not found:**
```bash
sudo raspi-config
# Navigate to: Interface Options → SPI → Enable
# Reboot after enabling
```

### Step 2: Check if another process is using SPI
```bash
# Check for stuck Python processes
ps aux | grep python

# Kill any stuck led_controller processes
sudo pkill -9 -f led_controller
```

### Step 3: Run minimal SPI test
```bash
sudo python3 test_spi_freeze.py
```

This will show exactly where the freeze happens:
- If it freezes at "Step 3: Opening SPI device" → SPI not enabled or in use
- If it freezes at "Step 5: Sending test data" → Hardware/wiring issue
- If it completes → Problem is in led_controller_spi.py logic

### Step 4: Check kernel modules
```bash
lsmod | grep spi
```

**Expected to see:**
- `spi_bcm2835` or similar
- `spidev`

**If missing:**
```bash
sudo modprobe spi-bcm2835
sudo modprobe spidev
```

### Step 5: Check for SPI conflicts
```bash
# See what's accessing SPI
sudo lsof | grep spi

# Check dmesg for SPI errors
dmesg | grep -i spi | tail -20
```

## Common Causes & Fixes

### Cause 1: SPI Not Enabled (New RPi)
**Symptom:** Script freezes immediately or fails to open device

**Fix:**
```bash
sudo raspi-config
# Interface Options → SPI → Enable → Reboot
```

### Cause 2: Previous Process Still Running
**Symptom:** "Device busy" or freeze at open

**Fix:**
```bash
# Kill all python processes
sudo pkill -9 python3

# Or reboot
sudo reboot
```

### Cause 3: Permission Issue
**Symptom:** Freeze or permission denied

**Fix:**
```bash
# Make sure running with sudo
sudo python3 led_controller_spi.py

# Or add user to spi group (requires logout)
sudo usermod -a -G spi $USER
```

### Cause 4: Different SPI Kernel Version
**Symptom:** Freeze during xfer2 call

**Fix:**
```bash
# Update system
sudo apt-get update
sudo apt-get upgrade

# Reinstall spidev
sudo pip3 install --upgrade spidev
```

## After Fixing

Once the test script works:

```bash
# Copy script to new RPi (if not already there)
scp led_controller_spi.py bedsidestreamdeck@<NEW_RPI_IP>:~/

# Run with debug to see progress
sudo python3 led_controller_spi.py --debug rainbow
```

## Still Freezing?

If `test_spi_freeze.py` works but `led_controller_spi.py` still freezes, press `Ctrl+C` when it freezes and note the error message. That will tell us exactly which line is hanging.

