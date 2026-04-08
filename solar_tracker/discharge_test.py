"""Discharge Test — controlled battery drain via CPC1718J SSR.

Runs one or two batteries simultaneously. Each battery has its own SSR
on a separate GPIO pin and its own Victron MPPT for voltage reading.

Wiring (per battery):
    Pi GPIO -[150Ω]-> CPC1718J Pin1 (LED+)
    Pi GND          -> CPC1718J Pin2 (LED-)
    Battery(+) -> Rheostat -> CPC1718J Pin3
    CPC1718J Pin4            -> Battery(-)

    Battery 1: GPIO24 (Row2 Pin9),  ttyUSB0
    Battery 2: GPIO25 (Row2 Pin11), ttyUSB1

Usage:
    python -m solar_tracker.discharge_test           # both batteries
    python -m solar_tracker.discharge_test --one     # battery 1 only
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
    """Controls one CPC1718J SSR via a GPIO pin."""

    def __init__(self, gpio_pin: int, label: str, simulate: bool = False):
        self.pin = gpio_pin
        self.label = label
        self.simulate = simulate
        self.is_on = False
        if not simulate and HAS_GPIO:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.OUT)
            GPIO.output(self.pin, GPIO.LOW)
            logger.info(f"[{label}] SSR initialized on GPIO {gpio_pin} (BCM)")

    def turn_on(self):
        if not self.simulate and HAS_GPIO:
            GPIO.output(self.pin, GPIO.HIGH)
        self.is_on = True
        logger.info(f"[{self.label}] SSR ON — discharging")

    def turn_off(self):
        if not self.simulate and HAS_GPIO:
            GPIO.output(self.pin, GPIO.LOW)
        self.is_on = False
        logger.info(f"[{self.label}] SSR OFF — stopped")

    def cleanup(self):
        self.turn_off()
        if not self.simulate and HAS_GPIO:
            GPIO.cleanup(self.pin)


class Battery:
    """One battery channel — SSR + Victron reader + data log."""

    def __init__(self, label: str, gpio_pin: int, ve_port: str,
                 cutoff_voltage: float, capacity_ah: float,
                 simulate: bool = False):
        self.label = label
        self.cutoff_voltage = cutoff_voltage
        self.capacity_ah = capacity_ah
        self.simulate = simulate
        self.done = False
        self.data = []

        self.switch = SSRSwitch(gpio_pin, label, simulate=simulate)

        self.ve = None
        if not simulate:
            try:
                from solar_tracker.vedirect import Vedirect
                self.ve = Vedirect(ve_port, timeout=5)
                logger.info(f"[{label}] VE.Direct connected on {ve_port}")
            except Exception as e:
                logger.warning(f"[{label}] Could not init VE.Direct on {ve_port}: {e}")

    def read_voltage(self, start_time: datetime) -> dict | None:
        if self.simulate:
            return self._simulate(start_time)
        if self.ve is None:
            return None
        try:
            data = self.ve.read_data_single()
            return {
                "voltage": int(data.get("V", 0)) / 1000.0,
                "current": int(data.get("I", 0)) / 1000.0,
            }
        except Exception as e:
            logger.error(f"[{self.label}] Read error: {e}")
            return None

    def _simulate(self, start_time: datetime) -> dict:
        elapsed_h = (datetime.now() - start_time).total_seconds() / 3600
        current = 12.5 / 2.0  # assume ~2Ω
        dod = min((current * elapsed_h) / self.capacity_ah, 1.0)
        v = 12.85 - dod * 1.5 - max(0, (dod - 0.8) ** 2 * 15)
        v += random.gauss(0, 0.02)
        return {"voltage": round(max(v, 9.0), 3), "current": round(-current, 3)}

    def stop(self):
        self.switch.cleanup()


class DischargeTest:

    def __init__(self, config: dict = None, simulate: bool = False,
                 single: bool = False):
        self.config = config or load_config()
        self.simulate = simulate
        self.running = False
        self._stop_event = threading.Event()

        dc = self.config.get("discharge", {})
        self.cutoff_voltage  = dc.get("cutoff_voltage", 10.5)
        self.read_interval   = dc.get("read_interval_seconds", 30)
        self.load_resistance = dc.get("load_resistance_ohms", 5.5)

        bat = self.config.get("battery", {})
        self.capacity_ah = bat.get("capacity_ah", 40.0)

        self.start_time = None
        self.test_id    = datetime.now().strftime("discharge_%Y%m%d_%H%M%S")

        db_path  = self.config.get("database", {}).get("path", "data/solar.db")
        self.db  = SolarDatabase(db_path)

        ve_cfg = self.config.get("vedirect", {})
        b1_port = ve_cfg.get("device1", {}).get("port", "/dev/ttyUSB0")
        b2_port = ve_cfg.get("device2", {}).get("port", "/dev/ttyUSB1")

        self.batteries = [
            Battery("Bat1", dc.get("ssr_tracking_pin", 24), b1_port,
                    self.cutoff_voltage, self.capacity_ah, simulate),
        ]
        if not single:
            self.batteries.append(
                Battery("Bat2", dc.get("ssr_fixed_pin", 25), b2_port,
                        self.cutoff_voltage, self.capacity_ah, simulate)
            )

        signal.signal(signal.SIGINT,  self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        print("\n  Ctrl+C — stopping immediately...")
        self.running = False
        for b in self.batteries:
            b.switch.turn_off()
        self._stop_event.set()

    def run(self):
        approx_current = 12.5 / self.load_resistance
        n = len(self.batteries)
        print("\n" + "=" * 60)
        print(f"  DISCHARGE TEST  ({n} batter{'y' if n==1 else 'ies'})")
        print("=" * 60)
        print(f"  Load resistance : {self.load_resistance} Ω")
        print(f"  Approx current  : {approx_current:.1f} A per battery")
        print(f"  Est. duration   : ~{self.capacity_ah / approx_current:.0f} hours")
        print(f"  Cutoff voltage  : {self.cutoff_voltage} V")
        print(f"  Read interval   : {self.read_interval} s")
        print(f"  Mode            : {'SIMULATION' if self.simulate else 'LIVE'}")
        print(f"  Test ID         : {self.test_id}")
        print("=" * 60)

        # Initial readings — abort if any Victron missing
        print("\n  Initial voltages:")
        for b in self.batteries:
            r = b.read_voltage(datetime.now())
            if r is None:
                print(f"    [{b.label}] ERROR: No Victron data on its port")
                for bat in self.batteries:
                    bat.stop()
                return
            print(f"    [{b.label}] {r['voltage']:.2f} V")
            if r["voltage"] < self.cutoff_voltage:
                print(f"    [{b.label}] Already below cutoff — aborting")
                for bat in self.batteries:
                    bat.stop()
                return

        if not self.simulate:
            print(f"\n  Starting in 5 seconds... (Ctrl+C to abort)")
            if self._stop_event.wait(timeout=5):
                print("  Aborted.")
                return

        self.running    = True
        self.start_time = datetime.now()

        for b in self.batteries:
            b.switch.turn_on()

        # Header
        bat_headers = "".join(f"  {b.label+' V':>10}  {'DoD':>7}" for b in self.batteries)
        print(f"\n  {'Time':>8}{bat_headers}")
        print(f"  {'-'*8}" + "".join(f"  {'-'*10}  {'-'*7}" for _ in self.batteries))

        try:
            while self.running and not all(b.done for b in self.batteries):
                elapsed     = datetime.now() - self.start_time
                elapsed_str = str(elapsed).split(".")[0]

                row = f"  {elapsed_str:>8}"
                for b in self.batteries:
                    if b.done:
                        row += f"  {'DONE':>10}  {'':>7}"
                        continue

                    r = b.read_voltage(self.start_time)
                    if r is None:
                        row += f"  {'ERR':>10}  {'':>7}"
                        continue

                    v   = r["voltage"]
                    i   = r["current"]
                    dod = estimate_dod(v, i, self.capacity_ah)

                    b.data.append({
                        "time":      elapsed.total_seconds(),
                        "time_str":  elapsed_str,
                        "voltage":   v,
                        "current":   i,
                        "dod":       dod,
                        "timestamp": datetime.now().isoformat(),
                    })
                    self.db.insert_reading(
                        b.label.lower(), v, i,
                        dod_percent=dod, session_id=self.test_id,
                    )
                    row += f"  {v:>9.2f}V  {dod:>6.1f}%"

                    if v <= self.cutoff_voltage:
                        b.done = True
                        b.switch.turn_off()
                        print(row)
                        print(f"\n  [{b.label}] Hit cutoff ({self.cutoff_voltage}V) — OFF")
                        row = None
                        break

                if row:
                    print(row)

                self._stop_event.wait(timeout=self.read_interval)

        finally:
            for b in self.batteries:
                b.stop()

        self._print_results()
        self._save_results()
        self._generate_chart()

    def _print_results(self):
        print("\n" + "=" * 60)
        print("  RESULTS")
        print("=" * 60)
        for b in self.batteries:
            if not b.data:
                continue
            duration_h = b.data[-1]["time"] / 3600
            avg_i      = abs(sum(d["current"] for d in b.data) / len(b.data))
            print(f"\n  [{b.label}]")
            print(f"    Duration    : {duration_h:.2f} h")
            print(f"    Start V     : {b.data[0]['voltage']:.2f} V")
            print(f"    End V       : {b.data[-1]['voltage']:.2f} V")
            print(f"    Avg current : {avg_i:.2f} A")
            print(f"    Energy out  : {avg_i * duration_h:.1f} Ah")

        if len(self.batteries) == 2:
            d1 = self.batteries[0].data
            d2 = self.batteries[1].data
            if d1 and d2:
                h1 = d1[-1]["time"] / 3600
                h2 = d2[-1]["time"] / 3600
                if h2 > 0:
                    diff = ((h1 - h2) / h2) * 100
                    print(f"\n  Bat1 lasted {diff:+.1f}% vs Bat2  ({h1:.2f}h vs {h2:.2f}h)")
        print("=" * 60)

    def _save_results(self):
        os.makedirs("data", exist_ok=True)
        csv_path  = f"data/{self.test_id}.csv"
        json_path = f"data/{self.test_id}.json"

        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            headers = ["time_seconds", "time_str"]
            for b in self.batteries:
                headers += [f"{b.label}_voltage", f"{b.label}_current", f"{b.label}_dod"]
            w.writerow(headers)
            max_len = max((len(b.data) for b in self.batteries), default=0)
            for i in range(max_len):
                row = []
                for bi, b in enumerate(self.batteries):
                    d = b.data[i] if i < len(b.data) else {}
                    if bi == 0:
                        row += [d.get("time", ""), d.get("time_str", "")]
                    row += [d.get("voltage", ""), d.get("current", ""), d.get("dod", "")]
                w.writerow(row)

        with open(json_path, "w") as f:
            json.dump({
                "test_id":    self.test_id,
                "start_time": self.start_time.isoformat() if self.start_time else None,
                "config": {
                    "load_resistance":  self.load_resistance,
                    "cutoff_voltage":   self.cutoff_voltage,
                    "capacity_ah":      self.capacity_ah,
                },
                "batteries": {b.label: b.data for b in self.batteries},
            }, f, indent=2)

        print(f"\n  Saved: {csv_path}")
        print(f"         {json_path}")

    def _generate_chart(self):
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            return

        colors = ["#38bdf8", "#fb923c", "#4ade80"]
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
        fig.suptitle("Battery Discharge Test", fontsize=14, fontweight="bold")

        for b, color in zip(self.batteries, colors):
            if not b.data:
                continue
            times = [d["time"] / 3600 for d in b.data]
            volts = [d["voltage"]     for d in b.data]
            dods  = [d["dod"]         for d in b.data]
            ax1.plot(times, volts, color=color, linewidth=2, label=b.label)
            ax2.plot(dods,  volts, color=color, linewidth=2, label=b.label)

        for ax in (ax1, ax2):
            ax.axhline(self.cutoff_voltage, color="red", linestyle="--",
                       alpha=0.7, label=f"Cutoff ({self.cutoff_voltage}V)")
            ax.legend()
            ax.grid(True, alpha=0.3)

        ax1.set_xlabel("Time (hours)")
        ax1.set_ylabel("Voltage (V)")
        ax1.set_title("Voltage Over Time")
        ax2.set_xlabel("Depth of Discharge (%)")
        ax2.set_ylabel("Voltage (V)")
        ax2.set_title("Voltage vs DoD")
        ax2.set_xlim(0, 105)

        plt.tight_layout()
        os.makedirs("reports", exist_ok=True)
        chart_path = f"reports/{self.test_id}.png"
        plt.savefig(chart_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"         {chart_path}")


def main():
    parser = argparse.ArgumentParser(description="Battery Discharge Test")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--one", action="store_true",
                        help="Run battery 1 only (default: both)")
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

    DischargeTest(config=config, simulate=args.simulate, single=args.one).run()


if __name__ == "__main__":
    main()
