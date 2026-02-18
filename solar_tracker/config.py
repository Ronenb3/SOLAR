"""Configuration loader — reads config.yaml and provides defaults."""

import os
import yaml

DEFAULT_CONFIG = {
    "location": {"latitude": 42.25, "longitude": -71.82, "timezone": "America/New_York"},
    "motors": {
        "azimuth": {"enable_pin": 16, "direction_pin": 20, "pulse_pin": 21, "steps_per_revolution": 400, "gear_ratio": 40},
        "altitude": {"enable_pin": 26, "direction_pin": 19, "pulse_pin": 13},
    },
    "home_position": {"azimuth_degrees": 90, "altitude_degrees": 91},
    "tracking": {
        "update_interval_seconds": 300,
        "min_altitude_degrees": 1,
        "pulse_delay_seconds": 0.001,
        "jump_to_sunrise": True,
    },
    "database": {"path": "data/solar.db", "import_old_files": True},
    "logging": {"path": "logs/solar_tracker.log", "level": "INFO", "max_file_size_mb": 10, "backup_count": 5},
    "vedirect": {
        "device1": {"port": "/dev/ttyUSB0", "timeout": 60, "label": "Tracking Panel"},
        "device2": {"port": "/dev/ttyUSB1", "timeout": 60, "label": "Fixed Panel"},
    },
    "battery": {"capacity_ah": 40.0, "internal_resistance_ohms": 0.015},
    "weather": {"enabled": False, "api_key": "", "cloud_threshold_percent": 80},
    "dashboard": {"enabled": True, "host": "0.0.0.0", "port": 8080},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base dict."""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(config_path: str = None) -> dict:
    """Load config from YAML file, falling back to defaults for missing keys."""
    if config_path is None:
        # Look for config.yaml next to this package, then in cwd
        pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(pkg_dir, "config.yaml"),
            os.path.join(os.getcwd(), "config.yaml"),
        ]
        for c in candidates:
            if os.path.exists(c):
                config_path = c
                break

    if config_path and os.path.exists(config_path):
        with open(config_path, "r") as f:
            user_config = yaml.safe_load(f) or {}
        return _deep_merge(DEFAULT_CONFIG, user_config)

    return DEFAULT_CONFIG.copy()
