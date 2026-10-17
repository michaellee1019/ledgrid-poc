"""Constants for the installed wall integration."""
from datetime import timedelta

DOMAIN = "led_grid_wall"
CONF_URL = "url"
PLATFORMS = ["light", "number", "sensor", "select", "button", "image"]
POLL_INTERVAL = timedelta(seconds=2)
PACE_BASELINE = 0.3

CATALOG_INTERVAL = timedelta(seconds=60)
