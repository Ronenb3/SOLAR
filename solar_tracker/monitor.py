"""Data monitor — improved version of run3.py.

Reads from Victron charge controllers via VE.Direct and stores data
in SQLite database. Can run headless (no display) or with matplotlib plots.

Improvements over original:
  - SQLite database instead of text files
  - Headless mode for running as a service
  - Proper threading with logging
  - Depth of Discharge estimation with manufacturer curves
  - Feeds data to the web dashboard
"""

import sys
import time
import threading
import traceback
import logging
from datetime import datetime

import numpy as np

from solar_tracker.config import load_config
from solar_tracker.logger import setup_logger
from solar_tracker.database import SolarDatabase
from solar_tracker.vedirect import Vedirect

logger = logging.getLogger("monitor")

# ---------------------------------------------------------------------------
# Battery Depth of Discharge estimation
# ---------------------------------------------------------------------------

# Manufacturer discharge curves for a lead-acid battery
# X-axis: DoD percentage, Y-axis: voltage at that DoD
DOD_AXIS = np.array([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
CURVES = {
    "C2":  np.array([13.6, 13.4, 13.3, 13.2, 13.18, 13.1, 13.0, 12.9, 12.8, 12.6, 12.0]),
    "C3":  np.array([13.6, 13.45, 13.35, 13.28, 13.23, 13.15, 13.05, 12.95, 12.83, 12.65, 12.10]),
    "C5":  np.array([13.6, 13.5, 13.42, 13.35, 13.28, 13.20, 13.12, 13.00, 12.90, 12.70, 12.20]),
    "C10": np.array([13.6, 13.55, 13.48, 13.42, 13.35, 13.27, 13.18, 13.05, 12.92, 12.75, 12.30]),
}


def _interp_dod(voltage: float, curve: np.ndarray) -> float:
    """Interpolate DoD from voltage using a discharge curve."""
    return float(np.interp(voltage, curve[::-1], DOD_AXIS[::-1]))


def estimate_dod(voltage: float, current: float, capacity_ah: float = 40.0,
                 r_internal: float = 0.015) -> float:
    """Estimate Depth of Discharge from measured voltage and current.
    
    Compensates for internal resistance voltage drop and interpolates
    between discharge curves based on actual C-rate.
    
    Args:
        voltage: Measured battery voltage (V)
        current: Measured battery current (A, positive = charging)
        capacity_ah: Battery capacity in amp-hours
        r_internal: Internal resistance in ohms
    
    Returns:
        Estimated DoD in percent (0 = full, 100 = empty)
    """
    # Compensate for internal resistance to get open-circuit voltage
    v_oc = voltage + current * r_internal
    c_rate = abs(current) / capacity_ah

    dod10 = _interp_dod(v_oc, CURVES["C10"])
    dod5 = _interp_dod(v_oc, CURVES["C5"])
    dod3 = _interp_dod(v_oc, CURVES["C3"])
    dod2 = _interp_dod(v_oc, CURVES["C2"])

    # Interpolate between the two nearest C-rate curves
    if c_rate <= 0.10:
        return dod10
    elif c_rate <= 0.20:
        w = (c_rate - 0.10) / 0.10
        return (1 - w) * dod10 + w * dod5
    elif c_rate <= 0.33:
        w = (c_rate - 0.20) / 0.13
        return (1 - w) * dod5 + w * dod3
    elif c_rate <= 0.50:
        w = (c_rate - 0.33) / 0.17
        return (1 - w) * dod3 + w * dod2
    else:
        return dod2


# ---------------------------------------------------------------------------
# Device streaming threads
# ---------------------------------------------------------------------------

class DeviceReader:
    """Reads data from one VE.Direct charge controller in a background thread."""

    def __init__(self, port: str, label: str, device_name: str, 
                 db: SolarDatabase, config: dict, timeout: int = 60):
        self.port = port
        self.label = label
        self.device_name = device_name  # 'tracking' or 'fixed'
        self.db = db
        self.config = config
        self.timeout = timeout
        self.running = True
        self.latest = {}  # Most recent reading (for dashboard)
        self.session_id = f"live_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._thread = None

    def start(self):
        """Start reading in a background thread."""
        self._thread = threading.Thread(target=self._read_loop, daemon=True, name=self.label)
        self._thread.start()
        logger.info(f"[{self.label}] Reader thread started")

    def _read_loop(self):
        """Main reading loop with reconnection."""
        while self.running:
            try:
                ve = Vedirect(self.port, self.timeout, label=self.label)
                logger.info(f"[{self.label}] Connected, reading data...")

                while self.running:
                    packet = ve.read_data_single()
                    self._process_packet(packet)

            except Exception as e:
                logger.error(f"[{self.label}] Error: {e}")
                logger.info(f"[{self.label}] Reconnecting in 10s...")
                time.sleep(10)

    def _process_packet(self, packet: dict):
        """Process a VE.Direct data packet."""
        try:
            bat_cfg = self.config.get("battery", {})
            
            voltage = float(packet.get("V", 0)) / 1000
            current = float(packet.get("I", 0)) / 1000
            energy = voltage * current / 1000
            panel_voltage = float(packet.get("VPV", 0)) / 1000
            panel_power = float(packet.get("PPV", 0))
            dod = estimate_dod(
                voltage, current,
                capacity_ah=bat_cfg.get("capacity_ah", 40.0),
                r_internal=bat_cfg.get("internal_resistance_ohms", 0.015),
            )

            # Update latest reading
            self.latest = {
                "timestamp": datetime.now().isoformat(),
                "battery_voltage": voltage,
                "battery_current": current,
                "battery_energy": energy,
                "panel_voltage": panel_voltage,
                "panel_power": panel_power,
                "dod_percent": dod,
            }

            # Store in database
            self.db.insert_reading(
                device=self.device_name,
                battery_voltage=voltage,
                battery_current=current,
                battery_energy=energy,
                panel_voltage=panel_voltage,
                panel_power=panel_power,
                dod_percent=dod,
                session_id=self.session_id,
            )

            logger.debug(
                f"[{self.label}] V={voltage:.2f}V I={current:.2f}A "
                f"PV={panel_voltage:.2f}V P={panel_power:.0f}W DoD={dod:.1f}%"
            )

        except (ValueError, KeyError) as e:
            logger.warning(f"[{self.label}] Bad packet: {e}")

    def stop(self):
        """Stop reading."""
        self.running = False


# ---------------------------------------------------------------------------
# Main monitor
# ---------------------------------------------------------------------------

class SolarMonitor:
    """Main data monitoring system."""

    def __init__(self, config: dict = None, headless: bool = False):
        self.config = config or load_config()
        self.log = setup_logger("monitor", self.config)
        self.headless = headless
        self.db = SolarDatabase(self.config["database"]["path"])
        self.readers = []

    def start(self):
        """Start monitoring all configured devices."""
        ve_cfg = self.config["vedirect"]

        # Start device readers
        for key in ("device1", "device2"):
            if key in ve_cfg:
                dev = ve_cfg[key]
                device_name = "tracking" if key == "device1" else "fixed"
                reader = DeviceReader(
                    port=dev["port"],
                    label=dev.get("label", key),
                    device_name=device_name,
                    db=self.db,
                    config=self.config,
                    timeout=dev.get("timeout", 60),
                )
                reader.start()
                self.readers.append(reader)

        self.log.info(f"Monitor started with {len(self.readers)} device(s)")

        if self.headless:
            self._run_headless()
        else:
            self._run_with_plots()

    def _run_headless(self):
        """Run without display — just log to database."""
        self.log.info("Running in headless mode (no plots)")
        try:
            while True:
                # Print periodic status
                for reader in self.readers:
                    if reader.latest:
                        r = reader.latest
                        self.log.info(
                            f"[{reader.label}] V={r['battery_voltage']:.2f}V "
                            f"P={r['panel_power']:.0f}W DoD={r['dod_percent']:.1f}%"
                        )
                time.sleep(30)
        except KeyboardInterrupt:
            self.log.info("Monitor stopped by user")
        finally:
            self.stop()

    def _run_with_plots(self):
        """Run with matplotlib live plots (requires display)."""
        try:
            import matplotlib.pyplot as plt
            import matplotlib.animation as animation
        except ImportError:
            self.log.warning("matplotlib not available, falling back to headless mode")
            self._run_headless()
            return

        fig, axs = plt.subplots(3, 1, figsize=(10, 10))
        plt.tight_layout(pad=3)

        def animate(_):
            for ax in axs:
                ax.clear()

            data = self.db.get_latest_readings(n=200)

            # Plot 1: Panel Power
            for device, color, label in [("tracking", "steelblue", "Tracking"), ("fixed", "salmon", "Fixed")]:
                if data[device]:
                    powers = [r["panel_power"] for r in data[device] if r["panel_power"]]
                    if powers:
                        axs[0].plot(powers, color=color, label=f"{label} ({powers[-1]:.0f}W)")
            axs[0].set_title("Panel Power")
            axs[0].set_ylabel("Watts")
            axs[0].legend(loc="upper left")
            axs[0].grid(True, alpha=0.3)

            # Plot 2: DoD over time
            for device, color, label in [("tracking", "purple", "Tracking"), ("fixed", "green", "Fixed")]:
                if data[device]:
                    dods = [r["dod_percent"] for r in data[device] if r["dod_percent"]]
                    if dods:
                        axs[1].plot(dods, color=color, label=f"{label} ({dods[-1]:.1f}%)")
            axs[1].set_title("Depth of Discharge")
            axs[1].set_ylabel("DoD (%)")
            axs[1].legend(loc="upper left")
            axs[1].grid(True, alpha=0.3)

            # Plot 3: Battery Voltage
            for device, color, label in [("tracking", "steelblue", "Tracking"), ("fixed", "salmon", "Fixed")]:
                if data[device]:
                    volts = [r["battery_voltage"] for r in data[device] if r["battery_voltage"]]
                    if volts:
                        axs[2].plot(volts, color=color, label=f"{label} ({volts[-1]:.2f}V)")
            axs[2].set_title("Battery Voltage")
            axs[2].set_ylabel("Volts")
            axs[2].legend(loc="upper left")
            axs[2].grid(True, alpha=0.3)

        ani = animation.FuncAnimation(fig, animate, interval=5000, cache_frame_data=False)
        plt.show()
        self.stop()

    def stop(self):
        """Stop all readers and close database."""
        for reader in self.readers:
            reader.stop()
        self.db.close()
        self.log.info("Monitor shutdown complete")


def main():
    headless = "--headless" in sys.argv
    config = load_config()
    monitor = SolarMonitor(config=config, headless=headless)
    monitor.start()


if __name__ == "__main__":
    main()
