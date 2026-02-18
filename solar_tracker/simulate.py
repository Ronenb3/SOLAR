"""Day simulator — run the tracking algorithm without hardware.

Simulates a full day of sun tracking for any date and location.
Shows where the panel would point at each interval, plots the sun path,
and estimates energy production. Great for testing changes safely.

Usage:
    python -m solar_tracker.simulate                    # Today, default location
    python -m solar_tracker.simulate --date 2026-06-21  # Summer solstice
    python -m solar_tracker.simulate --lat 34.05 --lon -118.25  # Los Angeles
"""

import math
import sys
from datetime import datetime, timedelta

import suncalc

from solar_tracker.tracker import azimuth_steps, altitude_steps


def simulate_day(lat: float = 42.25, lon: float = -71.82,
                 date: datetime = None, interval_sec: int = 300,
                 plot: bool = True) -> dict:
    """Simulate a full day of sun tracking.
    
    Args:
        lat: Latitude in degrees
        lon: Longitude in degrees  
        date: Date to simulate (default: today)
        interval_sec: Tracking interval in seconds
        plot: Whether to show matplotlib plots
    
    Returns:
        Dict with simulation results
    """
    if date is None:
        date = datetime.now().replace(hour=5, minute=0, second=0, microsecond=0)
    else:
        date = date.replace(hour=5, minute=0, second=0, microsecond=0)

    # Simulate from 5 AM to 9 PM
    end_time = date.replace(hour=21)
    
    times = []
    azimuths = []
    altitudes = []
    az_steps_list = []
    alt_steps_list = []
    total_az_steps = 0
    total_alt_steps = 0
    
    az_prev = 90.0   # Home position
    alt_prev = 91.0
    tracking_active = False
    sunrise_time = None
    sunset_time = None

    current = date
    while current <= end_time:
        pos = suncalc.get_position(current, lon, lat)
        az = round((math.degrees(pos["azimuth"]) + 180) * (1000 / 9)) * (9 / 1000)
        alt = math.degrees(pos["altitude"])

        times.append(current)
        azimuths.append(az)
        altitudes.append(alt)

        if alt >= 1:
            if not tracking_active:
                tracking_active = True
                sunrise_time = current
                print(f"  Sunrise: {current.strftime('%H:%M')} — az={az:.1f}°, alt={alt:.1f}°")

            # Calculate steps
            az_delta = az - az_prev
            az_s = azimuth_steps(az_delta)
            alt_s = altitude_steps(alt) - altitude_steps(alt_prev)

            total_az_steps += abs(az_s)
            total_alt_steps += abs(alt_s)
            az_steps_list.append(az_s)
            alt_steps_list.append(alt_s)

            az_prev = az
            alt_prev = alt
        else:
            if tracking_active:
                tracking_active = False
                sunset_time = current
                print(f"  Sunset:  {current.strftime('%H:%M')} — az={az:.1f}°, alt={alt:.1f}°")
            az_steps_list.append(0)
            alt_steps_list.append(0)

        current += timedelta(seconds=interval_sec)

    # Results
    daylight_hours = 0
    if sunrise_time and sunset_time:
        daylight_hours = (sunset_time - sunrise_time).total_seconds() / 3600

    peak_alt = max(altitudes)
    peak_time = times[altitudes.index(peak_alt)]
    num_moves = sum(1 for s in az_steps_list if s != 0)
    
    # Motor runtime estimate (at 500 Hz step rate, each step = 2ms)
    motor_time_az = total_az_steps * 0.002  # seconds
    motor_time_alt = total_alt_steps * 0.002

    results = {
        "date": date.strftime("%Y-%m-%d"),
        "location": f"{lat}°N, {abs(lon)}°W",
        "daylight_hours": daylight_hours,
        "sunrise": sunrise_time.strftime("%H:%M") if sunrise_time else "N/A",
        "sunset": sunset_time.strftime("%H:%M") if sunset_time else "N/A",
        "peak_altitude": peak_alt,
        "peak_time": peak_time.strftime("%H:%M"),
        "total_moves": num_moves,
        "total_az_steps": total_az_steps,
        "total_alt_steps": total_alt_steps,
        "motor_time_az_sec": motor_time_az,
        "motor_time_alt_sec": motor_time_alt,
    }

    print(f"\n{'='*50}")
    print(f"  Simulation: {results['date']}")  
    print(f"  Location:   {results['location']}")
    print(f"  Daylight:   {daylight_hours:.1f} hours ({results['sunrise']} — {results['sunset']})")
    print(f"  Peak:       {peak_alt:.1f}° at {results['peak_time']}")
    print(f"  Moves:      {num_moves} repositions")
    print(f"  Az steps:   {total_az_steps:,} total ({motor_time_az:.1f}s motor time)")
    print(f"  Alt steps:  {total_alt_steps:,} total ({motor_time_alt:.1f}s motor time)")
    print(f"{'='*50}")

    if plot:
        try:
            import matplotlib.pyplot as plt

            fig, axes = plt.subplots(2, 2, figsize=(14, 8))
            fig.suptitle(f"Sun Tracking Simulation — {results['date']} at {results['location']}", fontsize=13)
            
            hours = [(t - date).total_seconds() / 3600 for t in times]

            # Sun altitude over time
            ax = axes[0, 0]
            ax.plot(hours, altitudes, 'orange', linewidth=2)
            ax.axhline(y=1, color='red', linestyle='--', alpha=0.5, label='Min tracking altitude')
            ax.fill_between(hours, altitudes, alpha=0.15, color='orange')
            ax.set_title("Sun Altitude")
            ax.set_ylabel("Degrees")
            ax.set_xlabel("Hour of Day")
            ax.legend()
            ax.grid(True, alpha=0.3)

            # Sun azimuth over time
            ax = axes[0, 1]
            ax.plot(hours, azimuths, 'steelblue', linewidth=2)
            ax.set_title("Sun Azimuth")
            ax.set_ylabel("Degrees")
            ax.set_xlabel("Hour of Day")
            ax.grid(True, alpha=0.3)

            # Sun path (polar-ish plot)
            ax = axes[1, 0]
            tracking_az = [a for a, alt in zip(azimuths, altitudes) if alt >= 1]
            tracking_alt = [alt for alt in altitudes if alt >= 1]
            sc = ax.scatter(tracking_az, tracking_alt, c=range(len(tracking_az)), 
                          cmap='YlOrRd', s=20, zorder=5)
            ax.set_title("Sun Path (Az vs Alt)")
            ax.set_xlabel("Azimuth (°)")
            ax.set_ylabel("Altitude (°)")
            ax.grid(True, alpha=0.3)
            plt.colorbar(sc, ax=ax, label='Time progression')

            # Motor steps per move
            ax = axes[1, 1]
            tracking_hours = [h for h, s in zip(hours, az_steps_list) if s != 0]
            tracking_az_steps = [abs(s) for s in az_steps_list if s != 0]
            tracking_alt_steps_plot = [abs(s) for s in alt_steps_list if s != 0]
            if tracking_hours:
                ax.bar([h - 0.02 for h in tracking_hours], tracking_az_steps, 
                       width=0.04, label='Azimuth', color='steelblue', alpha=0.7)
                ax.bar([h + 0.02 for h in tracking_hours], tracking_alt_steps_plot,
                       width=0.04, label='Altitude', color='salmon', alpha=0.7)
            ax.set_title("Motor Steps Per Move")
            ax.set_xlabel("Hour of Day")
            ax.set_ylabel("Steps")
            ax.legend()
            ax.grid(True, alpha=0.3)

            plt.tight_layout()
            plt.show()

        except ImportError:
            print("(matplotlib not available — skipping plots)")

    return results


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Simulate a day of sun tracking")
    parser.add_argument("--lat", type=float, default=42.25, help="Latitude")
    parser.add_argument("--lon", type=float, default=-71.82, help="Longitude")
    parser.add_argument("--date", type=str, default=None, help="Date (YYYY-MM-DD)")
    parser.add_argument("--interval", type=int, default=300, help="Update interval (seconds)")
    parser.add_argument("--no-plot", action="store_true", help="Skip matplotlib plots")
    args = parser.parse_args()

    date = None
    if args.date:
        date = datetime.strptime(args.date, "%Y-%m-%d")

    simulate_day(lat=args.lat, lon=args.lon, date=date,
                 interval_sec=args.interval, plot=not args.no_plot)


if __name__ == "__main__":
    main()
