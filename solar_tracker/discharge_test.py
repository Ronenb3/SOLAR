"""Discharge Test — controlled battery drain via CPC1718J SSR.

Fires GPIO24 to close the SSR, current flows through the rheostat,
Victron MPPT on ttyUSB0 measures voltage every 30s. Auto-stops at cutoff.

Wiring:
    Pi GPIO24 (Row2 Pin9) -[150Ω]-> CPC1718J Pin1 (LED+)
    Pi GND    (Row2 Pin7)        -> CPC1718J Pin2 (LED-)
    Battery(+) -> Rheostat -> CPC1718J Pin3
    CPC1718J Pin4             -> Battery(-)

Usage:
    python -m solar_tracker.discharge_test
    python -m solar_tracker.discharge_test --simulate
    python -m solar_tracker.discharge_test --cutoff 11.0 --interval 60
"""

import argparse
import csv
import json
import logging
import os
import random
import signal
import threading
import time
from datetime import datetime

from solar_tracker.config import load_config
from solar_tracker.logger import setup_logger
from solar_tracker.database import SolarDatabase
from solar_tracker.monitor import estimate_dod

logger = logging.getLogger("discharge")

try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False
    logger.info("RPi.GPIO not available — simulation mode only")


class SSRSwitch:
    """Controls the CPC1718J SSR via a GPIO pin."""

    def __init__(self, gpio_pin: int, simulate: bool = False):
        self.pin = gpio_pin
        self.simulate = simulate
        self.is_on = False
        if not simulate and HAS_GPIO:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.OUT)
            GPIO.output(self.pin, GPIO.LOW)
            logger.info(f"SSR initialized on GPIO {gpio_pin} (BCM)")

    def turn_on(self):
        if not self.simulate and HAS_GPIO:
            GPIO.output(self.pin, GPIO.HIGH)
        self.is_on = True
        logger.info("SSR ON — discharging")

    def turn_off(self):
        if not self.simulate and HAS_GPIO:
            GPIO.output(self.pin, GPIO.LOW)
        self.is_on = False
        logger.info("SSR OFF — stopped")

    def cleanup(self):
        self.turn_off()
        if not self.simulate and HAS_GPIO:
            GPIO.cleanup(self.pin)


class DischargeTest:

    def __init__(self, config: dict = None, simulate: bool = False):
        self.config = config or load_config()
        self.simulate = simulate
        self.running = False
        self._stop_event = threading.Event()  # used for interruptible sleep

        dc = self.config.get("discharge", {})
        self.cutoff_voltage   = dc.get("cutoff_voltage", 10.5)
        self.read_interval    = dc.get("read_interval_seconds", 30)
        self.load_resistance  = dc.get("load_resistance_ohms", 5.5)
        self.gpio_pin         = dc.get("ssr_tracking_pin", 24)

        bat = self.config.get("battery", {})
        self.capacity_ah = bat.get("capacity_ah", 40.0)

        self.data       = []
        self.start_time = None
        self.test_id    = datetime.now().strftime("discharge_%Y%m%d_%H%M%S")

        db_path  = self.config.get("database", {}).get("path", "data/solar.db")
        self.db  = SolarDatabase(db_path)
        self.switch = SSRSwitch(self.gpio_pin, simulate=simulate)

        self.ve = None
        if not simulate:
            try:
                from solar_tracker.vedirect import Vedirect
                port = self.config.get("vedirect", {}).get("device1", {}).get("port", "/dev/ttyUSB0")
                self.ve = Vedirect(port, timeout=5)
                logger.info(f"VE.Direct connected on {port}")
            except Exception as e:
                logger.warning(f"Could not init VE.Direct: {e}")

        signal.signal(signal.SIGINT,  self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        print("\n  Ctrl+C — stopping discharge immediately...")
        self.running = False
        self.switch.turn_off()   # kill GPIO right now, don't wait for loop
        self._stop_event.set()   # wake up from sleep

    def read_voltage(self) -> dict | None:
        """Returns {voltage, current} or None on failure."""
        if self.simulate:
            return self._simulate_reading()
        if self.ve is None:
            return None
        try:
            data = self.ve.read_data_single()
            return {
                "voltage": data.get("V", 0) / 1000.0,
                "current": data.get("I", 0) / 1000.0,
            }
        except Exception as e:
            logger.error(f"VE.Direct read error: {e}")
            return None

    def _simulate_reading(self) -> dict:
        if not self.start_time:
            return {"voltage": 12.8, "current": -2.4}
        elapsed_h = (datetime.now() - self.start_time).total_seconds() / 3600
        current   = 12.5 / self.load_resistance
        dod       = min((current * elapsed_h) / self.capacity_ah, 1.0)
        v = 12.85 - dod * 1.5 - max(0, (dod - 0.8) ** 2 * 15)
        v += random.gauss(0, 0.02)
        return {"voltage": round(max(v, 9.0), 3), "current": round(-current, 3)}

    def run(self):
        approx_current = 12.5 / self.load_resistance
        print("\n" + "=" * 52)
        print("  DISCHARGE TEST")
        print("=" * 52)
        print(f"  Load resistance : {self.load_resistance} Ω")
        print(f"  Approx current  : {approx_current:.1f} A")
        print(f"  Est. duration   : ~{self.capacity_ah / approx_current:.0f} hours")
        print(f"  Cutoff voltage  : {self.cutoff_voltage} V")
        print(f"  Read interval   : {self.read_interval} s")
        print(f"  GPIO pin        : {self.gpio_pin} (BCM)")
        print(f"  Mode            : {'SIMULATION' if self.simulate else 'LIVE'}")
        print(f"  Test ID         : {self.test_id}")
        print("=" * 52)

        # Check voltage before starting
        r = self.read_voltage()
        if r is None:
            print("\n  ERROR: No voltage reading from Victron — is ttyUSB0 connected?")
            return
        print(f"\n  Battery voltage : {r['voltage']:.2f} V")
        if r["voltage"] < self.cutoff_voltage:
            print(f"  ERROR: Battery already below cutoff ({self.cutoff_voltage} V) — aborting")
            return

        if not self.simulate:
            print(f"\n  Starting in 5 seconds... (Ctrl+C to abort)")
            if self._stop_event.wait(timeout=5):
                print("  Aborted.")
                return

        self.running    = True
        self.start_time = datetime.now()
        self.switch.turn_on()

        print(f"\n  {'Time':>8}  {'Voltage':>9}  {'Current':>9}  {'DoD':>8}")
        print(f"  {'-'*8}  {'-'*9}  {'-'*9}  {'-'*8}")

        try:
            while self.running:
                elapsed     = datetime.now() - self.start_time
                elapsed_str = str(elapsed).split(".")[0]

                r = self.read_voltage()
                if r is None:
                    logger.warning("No voltage reading — retrying in 5s")
                    self._stop_event.wait(timeout=5)
                    continue

                v   = r["voltage"]
                i   = r["current"]
                dod = estimate_dod(v, i, self.capacity_ah)

                self.data.append({
                    "time":      elapsed.total_seconds(),
                    "time_str":  elapsed_str,
                    "voltage":   v,
                    "current":   i,
                    "dod":       dod,
                    "timestamp": datetime.now().isoformat(),
                })

                self.db.insert_reading(
                    "discharge", v, i,
                    dod_percent=dod, session_id=self.test_id,
                )

                print(f"  {elapsed_str:>8}  {v:>8.2f}V  {i:>8.2f}A  {dod:>7.1f}%")

                if v <= self.cutoff_voltage:
                    print(f"\n  Battery hit cutoff ({self.cutoff_voltage} V) — stopping")
                    break

                # Interruptible sleep — Ctrl+C wakes this immediately
                self._stop_event.wait(timeout=self.read_interval)

        finally:
            self.switch.cleanup()

        self._print_results()
        self._save_results()
        self._generate_chart()

    def _print_results(self):
        if not self.data:
            return
        duration_h = self.data[-1]["time"] / 3600
        avg_i      = abs(sum(d["current"] for d in self.data) / len(self.data))
        print("\n" + "=" * 52)
        print("  RESULTS")
        print("=" * 52)
        print(f"  Duration    : {duration_h:.2f} h")
        print(f"  Start V     : {self.data[0]['voltage']:.2f} V")
        print(f"  End V       : {self.data[-1]['voltage']:.2f} V")
        print(f"  Avg current : {avg_i:.2f} A")
        print(f"  Energy out  : {avg_i * duration_h:.1f} Ah")
        print("=" * 52)

    def _save_results(self):
        os.makedirs("data", exist_ok=True)
        csv_path  = f"data/{self.test_id}.csv"
        json_path = f"data/{self.test_id}.json"

        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_seconds", "time_str", "voltage", "current", "dod"])
            for d in self.data:
                w.writerow([d["time"], d["time_str"], d["voltage"], d["current"], d["dod"]])

        with open(json_path, "w") as f:
            json.dump({
                "test_id":    self.test_id,
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "config": {
                    "load_resistance":  self.load_resistance,
                    "cutoff_voltage":   self.cutoff_voltage,
                    "capacity_ah":      self.capacity_ah,
                },
                "data": self.data,
            }, f, indent=2)

        print(f"\n  Saved: {csv_path}")
        print(f"         {json_path}")

    def _generate_chart(self):
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning("matplotlib not available — skipping chart")
            return
        if not self.data:
            return

        times = [d["time"] / 3600 for d in self.data]
        volts = [d["voltage"]     for d in self.data]
        dods  = [d["dod"]         for d in self.data]

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
        fig.suptitle("Battery Discharge Test", fontsize=14, fontweight="bold")

        ax1.plot(times, volts, color="#38bdf8", linewidth=2)
        ax1.axhline(self.cutoff_voltage, color="red", linestyle="--",
                    alpha=0.7, label=f"Cutoff ({self.cutoff_voltage}V)")
        ax1.set_xlabel("Time (hours)")
        ax1.set_ylabel("Voltage (V)")
        ax1.set_title("Voltage Over Time")
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        ax2.plot(dods, volts, color="#38bdf8", linewidth=2)
        ax2.axhline(self.cutoff_voltage, color="red", linestyle="--", alpha=0.7)
        ax2.set_xlabel("Depth of Discharge (%)")
        ax2.set_ylabel("Voltage (V)")
        ax2.set_title("Voltage vs DoD")
        ax2.set_xlim(0, 105)
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        os.makedirs("reports", exist_ok=True)
        chart_path = f"reports/{self.test_id}.png"
        plt.savefig(chart_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"         {chart_path}")


def main():
    parser = argparse.ArgumentParser(description="Battery Discharge Test")
    parser.add_argument("--simulate", action="store_true",
                        help="Simulate without hardware")
    parser.add_argument("--cutoff", type=float,
                        help="Cutoff voltage (default from config)")
    parser.add_argument("--interval", type=int,
                        help="Seconds between readings (default from config)")
    args = parser.parse_args()

    config = load_config()
    setup_logger("discharge", config)

    if args.cutoff is not None:
        config.setdefault("discharge", {})["cutoff_voltage"] = args.cutoff
    if args.interval is not None:
        config.setdefault("discharge", {})["read_interval_seconds"] = args.interval

    DischargeTest(config=config, simulate=args.simulate).run()


if __name__ == "__main__":
    main()
