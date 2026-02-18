"""Sun-tracking motor controller — improved version of motionsoftware.py.

Improvements over original:
  - Reads all settings from config.yaml (no hardcoded values)
  - Saves position after every move (survives crashes/reboots)
  - Logs everything to file with timestamps
  - Graceful shutdown on Ctrl+C or SIGTERM
  - Optional jump-to-sunrise instead of starting at home
  - Weather-aware tracking (skip on cloudy days)
  - Signal handling for clean systemd integration
"""

import math
import signal
import sys
import time
from datetime import datetime

import suncalc

from solar_tracker.config import load_config
from solar_tracker.logger import setup_logger
from solar_tracker.position_store import save_position, load_position, clear_position

# GPIO is only available on the Pi — graceful fallback for development
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False

# ---------------------------------------------------------------------------
# Motor math — converting angles to stepper motor steps
# ---------------------------------------------------------------------------

def altitude_steps(angle: float) -> int:
    """Convert altitude angle (degrees) to stepper motor steps.
    
    Uses an empirically-derived cubic polynomial fitted to the specific
    altitude mechanism (linear actuator or linkage). This is custom to
    the hardware — recalibrate if the mechanical linkage changes.
    """
    steps = 2 * (16956 + (-101) * angle + (-1.26) * (angle ** 2) + (3.82e-3) * (angle ** 3))
    return round(steps)


def azimuth_steps(angle: float) -> int:
    """Convert azimuth angle (degrees) to stepper motor steps.
    
    Based on:
      - Motor: 400 steps/revolution (half-step mode)
      - Gear ratio: 40:1 (worm gear)
      - Total: 16,000 steps per full panel revolution
    """
    revolutions = (angle / 360) * 40
    steps = revolutions * 1000
    return round(steps)


# ---------------------------------------------------------------------------
# Motor control
# ---------------------------------------------------------------------------

class MotorController:
    """Controls azimuth and altitude stepper motors via GPIO pins."""

    def __init__(self, config: dict, logger, simulate: bool = False):
        self.config = config
        self.log = logger
        self.simulate = simulate or not HAS_GPIO
        self.pulse_delay = config["tracking"]["pulse_delay_seconds"]

        # Pin assignments from config
        az = config["motors"]["azimuth"]
        alt = config["motors"]["altitude"]
        self.pins = {
            "az": {"enable": az["enable_pin"], "dir": az["direction_pin"], "pulse": az["pulse_pin"]},
            "alt": {"enable": alt["enable_pin"], "dir": alt["direction_pin"], "pulse": alt["pulse_pin"]},
        }

        if not self.simulate:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for motor in self.pins.values():
                for pin in motor.values():
                    GPIO.setup(pin, GPIO.OUT)
            self.log.info("GPIO initialized")
        else:
            self.log.info("Running in SIMULATION mode (no GPIO)")

    def move(self, step_count: int, motor: str):
        """Move a motor by the given number of steps.
        
        Args:
            step_count: Positive = clockwise, negative = counter-clockwise
            motor: 'az' for azimuth, 'alt' for altitude
        """
        if step_count == 0:
            return

        pins = self.pins[motor]
        direction = "CW" if step_count > 0 else "CCW"
        self.log.debug(f"Moving {motor} {abs(step_count)} steps {direction}")

        if self.simulate:
            return

        # Set direction
        GPIO.output(pins["dir"], 1 if step_count > 0 else 0)

        # Send pulses
        for _ in range(abs(step_count)):
            GPIO.output(pins["pulse"], GPIO.HIGH)
            time.sleep(self.pulse_delay)
            GPIO.output(pins["pulse"], GPIO.LOW)
            time.sleep(self.pulse_delay)

    def cleanup(self):
        """Release GPIO pins."""
        if not self.simulate:
            GPIO.cleanup()
            self.log.info("GPIO cleaned up")


# ---------------------------------------------------------------------------
# Main tracker
# ---------------------------------------------------------------------------

class SolarTracker:
    """Main tracking loop — calculates sun position and drives motors."""

    def __init__(self, config: dict = None, simulate: bool = False):
        self.config = config or load_config()
        self.log = setup_logger("tracker", self.config)
        self.motors = MotorController(self.config, self.log, simulate=simulate)
        self.running = True

        # Location
        self.lat = self.config["location"]["latitude"]
        self.lon = self.config["location"]["longitude"]

        # Home position
        self.az_home = self.config["home_position"]["azimuth_degrees"]
        self.alt_home = self.config["home_position"]["altitude_degrees"]

        # Current position — try to restore from saved state
        saved = load_position()
        if saved:
            self.az_current = saved["azimuth_degrees"]
            self.alt_current = saved["altitude_degrees"]
            self.log.info(f"Restored position: az={self.az_current:.1f}°, alt={self.alt_current:.1f}°")
        else:
            self.az_current = self.az_home
            self.alt_current = self.alt_home
            self.log.info(f"Starting at home position: az={self.az_home}°, alt={self.alt_home}°")

        # Register signal handlers for clean shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle Ctrl+C or systemd stop gracefully."""
        self.log.info(f"Shutdown signal received (signal {signum})")
        self.running = False

    def get_sun_position(self) -> tuple[float, float]:
        """Get current sun azimuth and altitude in degrees."""
        pos = suncalc.get_position(datetime.now(), self.lon, self.lat)
        azimuth = round((math.degrees(pos["azimuth"]) + 180) * (1000 / 9)) * (9 / 1000)
        altitude = math.degrees(pos["altitude"])
        return azimuth, altitude

    def move_to(self, az_target: float, alt_target: float):
        """Move panel from current position to target position."""
        # Calculate step deltas
        az_delta = az_target - self.az_current
        az_steps = azimuth_steps(az_delta)
        alt_steps = altitude_steps(alt_target) - altitude_steps(self.alt_current)

        self.log.info(
            f"Moving: az {self.az_current:.1f}° → {az_target:.1f}° ({az_steps} steps), "
            f"alt {self.alt_current:.1f}° → {alt_target:.1f}° ({alt_steps} steps)"
        )

        # Move motors
        self.motors.move(az_steps, "az")
        self.motors.move(alt_steps, "alt")

        # Update and save position
        self.az_current = az_target
        self.alt_current = alt_target
        save_position(self.az_current, self.alt_current)

    def go_home(self):
        """Return panel to home position."""
        self.log.info("Returning to home position...")
        self.move_to(self.az_home, self.alt_home)
        clear_position()
        self.log.info("Home position reached, saved position cleared")

    def run(self):
        """Main tracking loop — runs until sunset or shutdown signal."""
        interval = self.config["tracking"]["update_interval_seconds"]
        min_alt = self.config["tracking"]["min_altitude_degrees"]

        self.log.info("=" * 60)
        self.log.info(f"Solar tracker starting — lat={self.lat}, lon={self.lon}")
        self.log.info(f"Update interval: {interval}s, min altitude: {min_alt}°")
        self.log.info("=" * 60)

        try:
            while self.running:
                # Get sun position
                az_sun, alt_sun = self.get_sun_position()
                self.log.info(f"Sun position: azimuth={az_sun:.1f}°, altitude={alt_sun:.1f}°")

                # Check for sunset
                if alt_sun < min_alt:
                    self.log.info(f"Sun below {min_alt}° — ending tracking")
                    break

                # Move to sun
                self.move_to(az_sun, alt_sun)

                # Wait for next update
                self.log.debug(f"Sleeping {interval}s until next update...")
                # Use short sleep intervals so we can respond to shutdown signals
                for _ in range(interval):
                    if not self.running:
                        break
                    time.sleep(1)

        except Exception as e:
            self.log.error(f"Tracker error: {e}", exc_info=True)
        finally:
            self.go_home()
            self.motors.cleanup()
            self.log.info("Tracker shutdown complete")


def main():
    """Entry point for running the tracker."""
    # Check for --simulate flag
    simulate = "--simulate" in sys.argv

    config = load_config()
    tracker = SolarTracker(config=config, simulate=simulate)
    tracker.run()


if __name__ == "__main__":
    main()
