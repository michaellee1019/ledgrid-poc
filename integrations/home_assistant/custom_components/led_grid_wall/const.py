"""Constants for the installed wall integration."""
from datetime import timedelta

DOMAIN = "led_grid_wall"
CONF_URL = "url"
PLATFORMS = ["light", "number", "sensor"]
POLL_INTERVAL = timedelta(seconds=2)
PACE_BASELINE = 0.3
