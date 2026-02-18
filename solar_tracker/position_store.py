"""Position store — saves and loads motor position to survive crashes.

After every motor movement, the current azimuth/altitude is saved to a JSON file.
On restart, the tracker reads this file to know where the panel is pointing.
"""

import json
import os


POSITION_FILE = "data/panel_position.json"


def save_position(azimuth: float, altitude: float, filepath: str = POSITION_FILE):
    """Save current panel position to disk."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    data = {
        "azimuth_degrees": azimuth,
        "altitude_degrees": altitude,
    }
    # Write to temp file first, then rename — atomic on most filesystems
    tmp = filepath + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, filepath)


def load_position(filepath: str = POSITION_FILE) -> dict | None:
    """Load last saved position. Returns None if no saved position exists."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        if "azimuth_degrees" in data and "altitude_degrees" in data:
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def clear_position(filepath: str = POSITION_FILE):
    """Remove saved position (e.g. after successful homing)."""
    if os.path.exists(filepath):
        os.remove(filepath)
