"""Discharge Test Controller — controlled battery discharge via MOSFET switch.

This is the switching system that connects to the existing battery monitoring.
Uses a MOSFET electronic switch to control current flow through a load resistor,
while the Victron charge controllers measure voltage as the battery drains.

Produces discharge curves: Voltage vs Time (and Voltage vs Depth of Discharge).

Hardware setup (per battery):
    Pi GPIO pin → MOSFET gate (signal)
    Pi GND      → MOSFET ground
    Battery +   → Resistor → MOSFET drain
    Battery -   → MOSFET source

Usage:
    # Run discharge test on both batteries simultaneously:
    python -m solar_tracker.discharge_test

    # Simulate without hardware (for demo):
    python -m solar_tracker.discharge_test --simulate

    # Custom settings:
    python -m solar_tracker.discharge_test --cutoff 10.5 --interval 30
"""

import argparse
import csv
import json
import logging
import math
import os
import random
import signal
import sys
import time
from datetime import datetime, timedelta

from solar_tracker.config import load_config
from solar_tracker.logger import setup_logger
from solar_tracker.database import SolarDatabase
from solar_tracker.monitor import estimate_dod

logger = logging.getLogger("discharge")

# Try to import GPIO — will fail on non-Pi systems
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False
    logger.info("RPi.GPIO not available — simulation mode only")


class MOSFETSwitch:
    """Controls a MOSFET electronic switch via GPIO.
    
    The MOSFET acts as a gate between the battery and load resistor.
    When the GPIO pin goes HIGH, the MOSFET turns ON and current flows
    through the resistor, discharging the battery.
    """

    def __init__(self, gpio_pin: int, label: str = "", simulate: bool = False):
        self.pin = gpio_pin
        self.label = label
        self.simulate = simulate
        self.is_on = False

        if not simulate and HAS_GPIO:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.OUT)
            GPIO.output(self.pin, GPIO.LOW)
            logger.info(f"MOSFET [{label}] initialized on GPIO {gpio_pin}")
        else:
            logger.info(f"MOSFET [{label}] GPIO {gpio_pin} (simulated)")

    def turn_on(self):
        """Close the switch — current flows, battery discharges."""
        if not self.simulate and HAS_GPIO:
            GPIO.output(self.pin, GPIO.HIGH)
        self.is_on = True
        logger.info(f"MOSFET [{self.label}] ON — discharging")

    def turn_off(self):
        """Open the switch — stops discharge."""
        if not self.simulate and HAS_GPIO:
            GPIO.output(self.pin, GPIO.LOW)
        self.is_on = False
        logger.info(f"MOSFET [{self.label}] OFF — stopped")

    def cleanup(self):
        """Safe shutdown — always turn off first."""
        self.turn_off()
        if not self.simulate and HAS_GPIO:
            GPIO.cleanup(self.pin)


class DischargeTest:
    """Runs a controlled discharge test on one or both batteries.
    
    Turns on the MOSFET switches, reads voltage from the Victron charge
    controllers at regular intervals, and logs everything. Automatically 
    stops when voltage hits the cutoff (protects the battery).
    """

    def __init__(self, config: dict = None, simulate: bool = False):
        self.config = config or load_config()
        self.simulate = simulate
        self.running = False

        # Discharge settings (from config or defaults)
        dc = self.config.get("discharge", {})
        self.cutoff_voltage = dc.get("cutoff_voltage", 10.5)
        self.read_interval = dc.get("read_interval_seconds", 30)
        self.load_resistance = dc.get("load_resistance_ohms", 10.0)
        self.gpio_tracking = dc.get("ssr_tracking_pin", 24)
        self.gpio_fixed = dc.get("ssr_fixed_pin", 25)

        # Battery specs
        bat = self.config.get("battery", {})
        self.capacity_ah = bat.get("capacity_ah", 40.0)

        # Data storage
        self.data_tracking = []
        self.data_fixed = []
        self.start_time = None
        self.test_id = datetime.now().strftime("discharge_%Y%m%d_%H%M%S")

        # Database
        db_path = self.config.get("database", {}).get("path", "data/solar.db")
        self.db = SolarDatabase(db_path)

        # MOSFET switches
        self.switch_tracking = MOSFETSwitch(
            self.gpio_tracking, "Tracking", simulate=simulate)
        self.switch_fixed = MOSFETSwitch(
            self.gpio_fixed, "Fixed", simulate=simulate)

        # VE.Direct readers (only on Pi with real hardware)
        self.ve_tracking = None
        self.ve_fixed = None
        if not simulate:
            try:
                from solar_tracker.vedirect import Vedirect
                ve_cfg = self.config.get("vedirect", {})
                dev1 = ve_cfg.get("device1", {})
                dev2 = ve_cfg.get("device2", {})
                self.ve_tracking = Vedirect(dev1.get("port", "/dev/ttyUSB0"),
                                            dev1.get("timeout", 60))
                self.ve_fixed = Vedirect(dev2.get("port", "/dev/ttyUSB1"),
                                         dev2.get("timeout", 60))
            except Exception as e:
                logger.warning(f"Could not init VE.Direct: {e}")

        # Signal handling for clean shutdown
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        """Graceful shutdown — always turn off MOSFETs."""
        logger.warning("Shutdown signal received — stopping discharge")
        self.running = False

    def read_voltage(self, device: str) -> dict:
        """Read current voltage and current from a charge controller.
        
        In simulate mode, generates a realistic discharge curve.
        """
        if self.simulate:
            return self._simulate_reading(device)

        ve = self.ve_tracking if device == "tracking" else self.ve_fixed
        if ve is None:
            return {"voltage": 0, "current": 0}

        try:
            data = ve.read_data_single()
            voltage = data.get("V", 0) / 1000.0  # mV to V
            current = data.get("I", 0) / 1000.0  # mA to A
            return {"voltage": voltage, "current": current}
        except Exception as e:
            logger.error(f"Read error [{device}]: {e}")
            return {"voltage": 0, "current": 0}

    def _simulate_reading(self, device: str) -> dict:
        """Simulate a realistic battery discharge curve.
        
        Model: V = V0 - (I * R_internal) - k * DoD - steep_drop_at_end
        """
        if not self.start_time:
            return {"voltage": 12.8, "current": -1.2}

        elapsed_h = (datetime.now() - self.start_time).total_seconds() / 3600
        current = 12.5 / self.load_resistance  # I = V/R approximate

        # Tracking battery has more energy (simulates tracking advantage)
        if device == "tracking":
            capacity_factor = 1.0     # full capacity
            start_v = 12.85
        else:
            capacity_factor = 0.75    # 25% less energy (fixed panel charged less)
            start_v = 12.70

        # How much of the battery has been used
        ah_used = current * elapsed_h
        effective_capacity = self.capacity_ah * capacity_factor
        dod = min(ah_used / effective_capacity, 1.0)

        # Discharge curve shape: flat in middle, steep drop at end
        r_internal = 0.015
        v_drop_ir = current * r_internal
        v_drop_linear = dod * 1.5  # gradual drop
        v_drop_steep = max(0, (dod - 0.8) ** 2 * 15)  # steep drop after 80% DoD

        voltage = start_v - v_drop_ir - v_drop_linear - v_drop_steep
        voltage = max(voltage, 9.0)  # floor

        # Add small noise
        voltage += random.gauss(0, 0.02)

        return {"voltage": round(voltage, 3), "current": round(-current, 3)}

    def run(self):
        """Run the discharge test."""
        print("\n" + "=" * 56)
        print("  CONTROLLED DISCHARGE TEST")
        print("=" * 56)
        print(f"  Load resistance:  {self.load_resistance} Ω")
        approx_current = 12.5 / self.load_resistance
        print(f"  Approx current:   {approx_current:.1f} A")
        approx_hours = self.capacity_ah / approx_current
        print(f"  Est. duration:    ~{approx_hours:.0f} hours")
        print(f"  Cutoff voltage:   {self.cutoff_voltage} V")
        print(f"  Read interval:    {self.read_interval} sec")
        print(f"  Mode:             {'SIMULATION' if self.simulate else 'LIVE'}")
        print(f"  Test ID:          {self.test_id}")
        print("=" * 56)

        # Take initial readings before starting
        r_t = self.read_voltage("tracking")
        r_f = self.read_voltage("fixed")
        print(f"\n  Initial voltages:")
        print(f"    Tracking: {r_t['voltage']:.2f} V")
        print(f"    Fixed:    {r_f['voltage']:.2f} V")

        if not self.simulate:
            print(f"\n  Starting discharge in 5 seconds... (Ctrl+C to abort)")
            time.sleep(5)

        # START DISCHARGE
        self.running = True
        self.start_time = datetime.now()

        self.switch_tracking.turn_on()
        self.switch_fixed.turn_on()

        print(f"\n  {'Time':>8}  {'Track V':>9}  {'Fixed V':>9}  {'Track DoD':>10}  {'Fixed DoD':>10}")
        print(f"  {'-'*8}  {'-'*9}  {'-'*9}  {'-'*10}  {'-'*10}")

        tracking_done = False
        fixed_done = False

        try:
            while self.running and not (tracking_done and fixed_done):
                elapsed = datetime.now() - self.start_time
                elapsed_str = str(elapsed).split('.')[0]  # HH:MM:SS

                # Read both batteries
                r_t = self.read_voltage("tracking")
                r_f = self.read_voltage("fixed")

                v_t = r_t["voltage"]
                v_f = r_f["voltage"]
                i_t = r_t["current"]
                i_f = r_f["current"]

                # Calculate DoD
                dod_t = estimate_dod(v_t, i_t, self.capacity_ah)
                dod_f = estimate_dod(v_f, i_f, self.capacity_ah)

                # Store data
                reading_t = {
                    "time": elapsed.total_seconds(),
                    "time_str": elapsed_str,
                    "voltage": v_t,
                    "current": i_t,
                    "dod": dod_t,
                    "timestamp": datetime.now().isoformat(),
                }
                reading_f = {
                    "time": elapsed.total_seconds(),
                    "time_str": elapsed_str,
                    "voltage": v_f,
                    "current": i_f,
                    "dod": dod_f,
                    "timestamp": datetime.now().isoformat(),
                }

                if not tracking_done:
                    self.data_tracking.append(reading_t)
                if not fixed_done:
                    self.data_fixed.append(reading_f)

                # Log to database
                self.db.insert_reading(
                    "tracking", v_t, i_t, dod_percent=dod_t,
                    session_id=self.test_id)
                self.db.insert_reading(
                    "fixed", v_f, i_f, dod_percent=dod_f,
                    session_id=self.test_id)

                # Print status
                t_status = f"{v_t:>8.2f}V" if not tracking_done else "    DONE "
                f_status = f"{v_f:>8.2f}V" if not fixed_done else "    DONE "
                print(f"  {elapsed_str:>8}  {t_status}  {f_status}  {dod_t:>9.1f}%  {dod_f:>9.1f}%")

                # Check cutoff
                if v_t <= self.cutoff_voltage and not tracking_done:
                    tracking_done = True
                    self.switch_tracking.turn_off()
                    print(f"\n  ⚠ TRACKING battery hit cutoff ({self.cutoff_voltage}V) — switch OFF")
                    print(f"    Duration: {elapsed_str}, Final DoD: {dod_t:.1f}%\n")

                if v_f <= self.cutoff_voltage and not fixed_done:
                    fixed_done = True
                    self.switch_fixed.turn_off()
                    print(f"\n  ⚠ FIXED battery hit cutoff ({self.cutoff_voltage}V) — switch OFF")
                    print(f"    Duration: {elapsed_str}, Final DoD: {dod_f:.1f}%\n")

                # Wait for next reading
                if self.simulate:
                    # In simulation, advance time by ~30 min per tick
                    self.start_time -= timedelta(minutes=30)
                    time.sleep(0.1)
                else:
                    time.sleep(self.read_interval)

        finally:
            # ALWAYS turn off switches
            self.switch_tracking.turn_off()
            self.switch_fixed.turn_off()
            self.switch_tracking.cleanup()
            self.switch_fixed.cleanup()

        # Print results
        self._print_results()
        self._save_results()
        self._generate_chart()

    def _print_results(self):
        """Print final test summary."""
        print("\n" + "=" * 56)
        print("  DISCHARGE TEST RESULTS")
        print("=" * 56)

        for label, data in [("Tracking", self.data_tracking), ("Fixed", self.data_fixed)]:
            if not data:
                continue
            duration_h = data[-1]["time"] / 3600
            start_v = data[0]["voltage"]
            end_v = data[-1]["voltage"]
            avg_current = abs(sum(d["current"] for d in data) / len(data))
            ah_delivered = avg_current * duration_h

            print(f"\n  {label} Battery:")
            print(f"    Duration:       {duration_h:.1f} hours")
            print(f"    Start voltage:  {start_v:.2f} V")
            print(f"    End voltage:    {end_v:.2f} V")
            print(f"    Avg current:    {avg_current:.2f} A")
            print(f"    Energy out:     {ah_delivered:.1f} Ah")

        if self.data_tracking and self.data_fixed:
            t_hours = self.data_tracking[-1]["time"] / 3600
            f_hours = self.data_fixed[-1]["time"] / 3600
            if f_hours > 0:
                advantage = ((t_hours - f_hours) / f_hours) * 100
                print(f"\n  ★ TRACKING LASTED {advantage:+.1f}% LONGER")
                print(f"    ({t_hours:.1f}h vs {f_hours:.1f}h)")

        print("=" * 56)

    def _save_results(self):
        """Save discharge data to CSV and JSON."""
        os.makedirs("data", exist_ok=True)

        # CSV — easy to open in Excel
        csv_path = f"data/{self.test_id}.csv"
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_seconds", "time_str",
                        "tracking_voltage", "tracking_current", "tracking_dod",
                        "fixed_voltage", "fixed_current", "fixed_dod"])
            max_len = max(len(self.data_tracking), len(self.data_fixed))
            for i in range(max_len):
                t = self.data_tracking[i] if i < len(self.data_tracking) else {}
                f_data = self.data_fixed[i] if i < len(self.data_fixed) else {}
                w.writerow([
                    t.get("time", ""), t.get("time_str", ""),
                    t.get("voltage", ""), t.get("current", ""), t.get("dod", ""),
                    f_data.get("voltage", ""), f_data.get("current", ""), f_data.get("dod", ""),
                ])
        logger.info(f"CSV saved: {csv_path}")

        # JSON — for the dashboard
        json_path = f"data/{self.test_id}.json"
        with open(json_path, "w") as f:
            json.dump({
                "test_id": self.test_id,
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "config": {
                    "load_resistance": self.load_resistance,
                    "cutoff_voltage": self.cutoff_voltage,
                    "capacity_ah": self.capacity_ah,
                },
                "tracking": self.data_tracking,
                "fixed": self.data_fixed,
            }, f, indent=2)
        logger.info(f"JSON saved: {json_path}")

        print(f"\n  Data saved to:")
        print(f"    {csv_path}")
        print(f"    {json_path}")

    def _generate_chart(self):
        """Generate the discharge curve comparison chart."""
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning("matplotlib not available — skipping chart")
            return

        fig, axes = plt.subplots(2, 1, figsize=(12, 8))
        fig.suptitle("Controlled Discharge Test — Tracking vs Fixed",
                     fontsize=14, fontweight="bold")

        # Chart 1: Voltage vs Time
        ax = axes[0]
        if self.data_tracking:
            t_time = [d["time"] / 3600 for d in self.data_tracking]
            t_volts = [d["voltage"] for d in self.data_tracking]
            ax.plot(t_time, t_volts, color="#38bdf8", linewidth=2, label="Tracking")
        if self.data_fixed:
            f_time = [d["time"] / 3600 for d in self.data_fixed]
            f_volts = [d["voltage"] for d in self.data_fixed]
            ax.plot(f_time, f_volts, color="#fb923c", linewidth=2, label="Fixed")

        ax.axhline(y=self.cutoff_voltage, color="red", linestyle="--",
                    alpha=0.7, label=f"Cutoff ({self.cutoff_voltage}V)")
        ax.set_xlabel("Time (hours)")
        ax.set_ylabel("Battery Voltage (V)")
        ax.set_title("Voltage Over Time")
        ax.legend()
        ax.grid(True, alpha=0.3)

        # Chart 2: Voltage vs Depth of Discharge
        ax = axes[1]
        if self.data_tracking:
            t_dod = [d["dod"] for d in self.data_tracking]
            t_volts = [d["voltage"] for d in self.data_tracking]
            ax.plot(t_dod, t_volts, color="#38bdf8", linewidth=2, label="Tracking")
        if self.data_fixed:
            f_dod = [d["dod"] for d in self.data_fixed]
            f_volts = [d["voltage"] for d in self.data_fixed]
            ax.plot(f_dod, f_volts, color="#fb923c", linewidth=2, label="Fixed")

        ax.axhline(y=self.cutoff_voltage, color="red", linestyle="--", alpha=0.7)
        ax.set_xlabel("Depth of Discharge (%)")
        ax.set_ylabel("Battery Voltage (V)")
        ax.set_title("Voltage vs Depth of Discharge")
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.set_xlim(0, 105)

        plt.tight_layout()
        os.makedirs("reports", exist_ok=True)
        chart_path = f"reports/{self.test_id}.png"
        plt.savefig(chart_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"  Chart saved: {chart_path}")


def main():
    parser = argparse.ArgumentParser(description="Controlled Battery Discharge Test")
    parser.add_argument("--simulate", action="store_true",
                        help="Simulate discharge without hardware")
    parser.add_argument("--cutoff", type=float, default=10.5,
                        help="Cutoff voltage to stop discharge (default: 10.5V)")
    parser.add_argument("--interval", type=int, default=30,
                        help="Seconds between readings (default: 30)")
    parser.add_argument("--resistance", type=float, default=10.0,
                        help="Load resistance in ohms (default: 10)")
    args = parser.parse_args()

    config = load_config()
    setup_logger("discharge", config)

    # Override config with command line args
    if "discharge" not in config:
        config["discharge"] = {}
    config["discharge"]["cutoff_voltage"] = args.cutoff
    config["discharge"]["read_interval_seconds"] = args.interval
    config["discharge"]["load_resistance_ohms"] = args.resistance

    test = DischargeTest(config=config, simulate=args.simulate)
    test.run()


if __name__ == "__main__":
    main()
