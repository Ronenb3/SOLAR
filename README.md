# Solar Tracker — Raspberry Pi Sun-Tracking Solar Panel System

A dual-axis solar tracker that follows the sun to maximize energy production, with real-time monitoring, data logging, and a web dashboard.

## What It Does

- **Tracks the sun** — calculates sun position every 5 minutes and moves two stepper motors (azimuth + altitude) to point the panel directly at the sun
- **Monitors power** — reads voltage, current, and power from Victron charge controllers via VE.Direct protocol
- **Compares performance** — logs data from both a tracking panel and a fixed panel to measure the advantage
- **Web dashboard** — check live stats from your phone at `http://<pi-ip>:8080`
- **Auto-starts** — systemd services start tracking and monitoring at boot

## Hardware

- Raspberry Pi (any model with GPIO)
- 2x stepper motors (azimuth + altitude) with drivers (ENA/DIR/PUL)
- 40:1 worm gear on azimuth axis
- 2x Victron solar charge controllers with VE.Direct USB cables
- Solar panels (tracking + fixed for comparison)
- 12V lead-acid batteries (40Ah)

## Project Structure

```
solar/
├── config.yaml                 ← All settings (location, pins, timing)
├── solar_tracker/
│   ├── tracker.py              ← Sun tracking motor controller
│   ├── monitor.py              ← Data logging from charge controllers
│   ├── vedirect.py             ← Victron VE.Direct protocol parser
│   ├── database.py             ← SQLite storage + old file import
│   ├── web_dashboard.py        ← Flask web UI
│   ├── weather.py              ← Weather-aware tracking
│   ├── simulate.py             ← Day simulation without hardware
│   ├── analysis.py             ← Battery data analysis + report generation
│   ├── config.py               ← Configuration loader
│   ├── logger.py               ← File + console logging
│   └── position_store.py       ← Crash-recovery position saving
├── services/                   ← Systemd auto-start files
├── tests/                      ← Unit tests (no hardware needed)
├── analysis.ipynb              ← Jupyter notebook for data exploration
├── requirements.txt
└── README.md
```

## Quick Start

### 1. Install dependencies
```bash
cd ~/solar
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Edit config
```bash
nano config.yaml
# Set your latitude/longitude, GPIO pins, etc.
```

### 3. Run the tracker
```bash
# On the Pi with motors connected:
python -m solar_tracker.tracker

# Simulate a day without hardware (works anywhere):
python -m solar_tracker.simulate
```

### 4. Run the data monitor
```bash
# With charge controllers connected:
python -m solar_tracker.monitor

# Headless mode (no display, for running as service):
python -m solar_tracker.monitor --headless
```

### 5. Start the web dashboard
```bash
python -m solar_tracker.web_dashboard
# Open http://<pi-ip>:8080 in your browser
```

### 6. Import old data files
```python
from solar_tracker.database import SolarDatabase
db = SolarDatabase("data/solar.db")
db.import_all_battery_files(".")  # Imports all BatteryDat_*.txt
```

### 7. Generate analysis report
```bash
python -m solar_tracker.analysis
# Creates reports/ folder with charts and summary
```

## Auto-Start on Boot (Systemd)

```bash
# Copy service files
sudo cp services/solar-tracker.service /etc/systemd/system/
sudo cp services/solar-monitor.service /etc/systemd/system/
sudo cp services/solar-dashboard.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable solar-tracker solar-monitor solar-dashboard
sudo systemctl start solar-tracker solar-monitor solar-dashboard

# Check status
sudo systemctl status solar-tracker
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Key Improvements Over Original

| Feature | Before | After |
|---------|--------|-------|
| Config | Hardcoded in Python | `config.yaml` file |
| Crash recovery | Lost position | Saves to JSON after every move |
| Logging | Print to console (lost) | Rotating log files |
| Data storage | Text files | SQLite database |
| Visualization | matplotlib on Pi screen | Web dashboard (phone accessible) |
| Auto-start | Manual | Systemd services |
| Weather | None | Skip tracking on cloudy days |
| Testing | None | Unit tests for all math + parsing |
| Error handling | Script crashes | Reconnection, graceful shutdown |

## Location

Default: Worcester, MA area (42.25°N, 71.82°W). Change in `config.yaml`.
