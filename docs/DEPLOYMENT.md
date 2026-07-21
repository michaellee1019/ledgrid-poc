# 🚀 LED Grid Animation System - Deployment Guide

## Quick Deployment

### 0. Prepare Deploy Target (once)
```bash
just setup
```

This will:
- ✅ Ensure PlatformIO is installed on the Pi
- ✅ Add the deploy user to the serial group (dialout)
- ✅ Verify the ESP32 devices are visible
Note: adding to `dialout` may require a logout/login on the Pi.

### 1. Deploy to Raspberry Pi
```bash
./tools/deployment/deploy.sh
```

This single command will:
- ✅ Upload all animation system files
- ✅ Create Python virtual environment
- ✅ Install Python dependencies in venv
- ✅ Create startup scripts
- ✅ Start the animation system
- ✅ Display the web URL to access
- ✅ Flash ESP32 firmware when firmware sources change

### 2. Access Web Interface
After deployment, open your browser to the URL shown:
```
🌐 http://[PI_IP_ADDRESS]:5000/
```

## Prerequisites

### Raspberry Pi Setup
- ✅ Raspberry Pi with Raspberry Pi OS
- ✅ SSH enabled (`sudo systemctl enable ssh`)
- ✅ Passwordless SSH configured to `ledgridwall@ledgridwall.local` (`ssh-copy-id`)
- ✅ First deploy/setup prompts once for your Pi password to enable passwordless sudo
- ✅ Python 3 installed (default on Raspberry Pi OS)
- ✅ Network connectivity

### SSH Key Setup (if not done)
```bash
# Generate SSH key (if you don't have one)
ssh-keygen -t rsa -b 4096

# Copy key to Pi
ssh-copy-id ledgridwall@ledgridwall.local

# Test connection
ssh ledgridwall@ledgridwall.local "echo 'SSH working'"
```

### SPI Configuration (for LED hardware)
Enable SPI on the Raspberry Pi:
```bash
sudo raspi-config
# Navigate to: Interface Options > SPI > Enable
```

## Deployment Process

### What `tools/deployment/deploy.sh` Does

1. **Connection Test** - Verifies SSH connectivity
2. **Directory Setup** - Creates `~/ledgrid-pod/` on Pi
3. **File Upload** - Transfers all animation system files
4. **Virtual Environment** - Creates isolated Python environment
5. **Dependencies** - Installs Flask and other Python packages in venv
6. **SPI Check** - Verifies SPI devices are available
7. **Startup Script** - Creates `start.sh` for easy system management
8. **System Start** - Launches the animation server
9. **URL Display** - Shows web interface URLs
10. **ESP32 Flash** - Builds and flashes firmware when sources change

### Files Deployed
```
~/ledgrid-pod/
├── venv/                     # Python virtual environment
├── animation/core/          # Core plugin system
├── animation/plugins/               # Example animation plugins
├── web/templates/                # Web interface templates
├── animation/core/manager.py      # Animation coordination
├── web/app.py         # Flask web server
├── scripts/start_server.py # Main startup script
├── requirements.txt         # Python dependencies
├── start.sh                 # Convenience startup script
└── animation_system.log     # Runtime log file
```

## System Management

### Fetch Manually Saved Presets

Presets saved in the web interface live on the Raspberry Pi. Fetch newly saved
presets without overwriting curated local files:

```bash
just fetch-presets
```

The automatic `before-deploy` snapshot is excluded. Runtime presets remain
Git-ignored after fetching; inspect them with `git ls-files --others --ignored
--exclude-standard 'presets/animations/**/*.json'`, then explicitly stage a
preset selected for curation with `git add -f
presets/animations/<animation>/<preset>.json`.

The controller refreshes this same snapshot whenever the active animation,
animation parameters, global tempo, or target FPS changes. On service or system
restart, that saved animation and configuration are used as the startup default;
Sparkle is only the fallback when no valid saved state exists.

### Start/Stop/Restart
```bash
# Stop the system
./tools/deployment/stop_remote.sh stop

# Check status
./tools/deployment/stop_remote.sh status

# Restart the system
./tools/deployment/stop_remote.sh restart
```

### Virtual Environment Management
```bash
# Check virtual environment status
./tools/deployment/manage_venv.sh status

# Recreate virtual environment (if broken)
./tools/deployment/manage_venv.sh recreate

# Install additional packages
./tools/deployment/manage_venv.sh install numpy

# Update all packages
./tools/deployment/manage_venv.sh update

# Open interactive shell with venv activated
./tools/deployment/manage_venv.sh shell
```

### Manual Control on Pi
```bash
# SSH to Pi
ssh ledgridwall@ledgridwall.local

# Navigate to deployment
cd ledgrid-pod

# Start system (uses virtual environment)
./start.sh

# Activate virtual environment manually
source venv/bin/activate

# View logs
tail -f animation_system.log

# Stop system (Ctrl+C or)
pkill -f scripts/start_server.py
```

## Web Interface URLs

After deployment, access these URLs:

- **Dashboard**: `http://[PI_IP]:5000/`
  - View available animations
  - Start animations with one click
  - System status and performance

- **Control Panel**: `http://[PI_IP]:5000/control`
  - Real-time parameter adjustment
  - Animation switching
  - Live performance monitoring

## Troubleshooting

### Deployment Issues

**SSH Connection Failed**
```bash
# Check Pi is reachable
ping ledgridwall.local

# Test SSH manually
ssh ledgridwall@ledgridwall.local

# Check SSH key
ssh-copy-id ledgridwall@ledgridwall.local
```

**SPI Not Available**
```bash
# Enable SPI on Pi
sudo raspi-config
# Interface Options > SPI > Enable > Reboot

# Check SPI devices
ls /dev/spi*
```

**Dependencies Failed**
```bash
# SSH to Pi and recreate virtual environment
./tools/deployment/manage_venv.sh recreate

# Or manually:
ssh ledgridwall@ledgridwall.local
cd ledgrid-pod
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Virtual Environment Issues**
```bash
# Check virtual environment status
./tools/deployment/manage_venv.sh status

# Recreate if broken
./tools/deployment/manage_venv.sh recreate

# Get detailed info
./tools/deployment/manage_venv.sh info
```

### Runtime Issues

**Web Interface Not Accessible**
```bash
# Check if system is running
./tools/deployment/stop_remote.sh status

# Check Pi's IP address
ssh ledgridwall@ledgridwall.local "hostname -I"

# Check firewall (if enabled)
ssh ledgridwall@ledgridwall.local "sudo ufw status"
```

**Animation Not Working**
```bash
# Check logs
ssh ledgridwall@ledgridwall.local "cd ledgrid-pod && tail -f animation_system.log"

# Restart system
./tools/deployment/stop_remote.sh restart
```

**Low Performance**
- Check SPI speed settings in `scripts/start_server.py`
- Reduce animation complexity
- Lower target FPS

## Hardware Integration

### LED Controller Setup
Ensure your `drivers/spi_controller.py` is compatible:
```python
class LEDController:
    def __init__(self, bus=0, device=0, speed=10000000, **kwargs):
        # SPI setup
        
    def set_all_pixels(self, pixel_data):
        # Bulk pixel update
        
    def show(self):
        # Display frame
```

### Wiring
See `HARDWARE.md` for ESP32/SCORPIO connection details.

## Security Notes

- Web interface runs on port 5000 (HTTP, not HTTPS)
- No authentication by default
- Suitable for local network use
- For internet access, consider adding authentication

## Performance Optimization

### System Settings
```bash
# Increase SPI buffer size (optional)
echo 'dtparam=spi=on' | sudo tee -a /boot/config.txt
echo 'dtoverlay=spi0-hw-cs' | sudo tee -a /boot/config.txt

# GPU memory split (if needed)
sudo raspi-config
# Advanced Options > Memory Split > 16
```

### Animation Tips
- Use efficient algorithms
- Cache expensive calculations
- Minimize memory allocations in frame loops
- Test with `tools/dev/demo_animation_system.py` first

## Next Steps

1. **Deploy**: Run `./tools/deployment/deploy.sh`
2. **Test**: Open web interface and try animations
3. **Create**: Upload your own animation plugins
4. **Customize**: Modify parameters and create new effects
5. **Scale**: Add more LED strips or controllers

🎉 **Your LED grid animation system is now ready for action!**
