"""Yearly simulation — simulate tracking for every day of the year.

Produces charts showing:
  - Daylight hours across the year
  - Peak sun altitude by season
  - Daily motor usage (step counts)
  - Estimated energy advantage of tracking vs fixed
  - Optimal tracking gain by month
"""

import math
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import suncalc

from solar_tracker.tracker import azimuth_steps, altitude_steps


def estimate_daily_energy(lat: float, lon: float, date: datetime,
                          interval_sec: int = 300) -> dict:
    """Estimate energy metrics for one day at a given location.

    Uses a clear-sky radiation model with atmospheric attenuation:
      DNI ∝ 0.7^(AM^0.678)  (Meinel model)
      AM  = 1 / sin(altitude)  (air mass)
      Tracking panel always perpendicular to sun → captures full DNI.
      Fixed panel at latitude tilt, south-facing → DNI * cos(incidence).
    """
    start = date.replace(hour=5, minute=0, second=0, microsecond=0)
    end = date.replace(hour=21, minute=0, second=0, microsecond=0)

    current = start
    sunrise = sunset = None
    peak_alt = 0.0
    peak_time = start

    tracking_power_sum = 0.0
    fixed_power_sum = 0.0
    total_az_steps = 0
    total_alt_steps = 0
    az_prev, alt_prev = 90.0, 91.0
    n_intervals = 0

    # Fixed panel: south-facing at latitude tilt (common rule of thumb)
    fixed_tilt = lat  # degrees from horizontal
    fixed_azimuth = 180.0  # due south

    while current <= end:
        pos = suncalc.get_position(current, lon, lat)
        alt_deg = math.degrees(pos["altitude"])
        az_deg = math.degrees(pos["azimuth"]) + 180

        if alt_deg >= 2:
            if sunrise is None:
                sunrise = current

            sunset = current

            if alt_deg > peak_alt:
                peak_alt = alt_deg
                peak_time = current

            # Clear-sky Direct Normal Irradiance via Meinel model
            sin_alt = math.sin(math.radians(alt_deg))
            air_mass = 1.0 / max(sin_alt, 0.05)
            dni = 0.7 ** (air_mass ** 0.678)  # normalized 0-1

            # Tracking panel: always perpendicular → captures full DNI
            track_p = dni

            # Fixed panel: DNI * cos(angle of incidence)
            sun_az_rad = math.radians(az_deg)
            sun_alt_rad = math.radians(alt_deg)
            panel_tilt_rad = math.radians(fixed_tilt)
            panel_az_rad = math.radians(fixed_azimuth)

            cos_incidence = (
                math.sin(sun_alt_rad) * math.cos(panel_tilt_rad)
                + math.cos(sun_alt_rad) * math.sin(panel_tilt_rad)
                * math.cos(sun_az_rad - panel_az_rad)
            )
            fixed_p = max(0, dni * cos_incidence)

            tracking_power_sum += track_p
            fixed_power_sum += fixed_p
            n_intervals += 1

            # Motor steps
            az_delta = az_deg - az_prev
            total_az_steps += abs(azimuth_steps(az_delta))
            total_alt_steps += abs(altitude_steps(alt_deg) - altitude_steps(alt_prev))
            az_prev = az_deg
            alt_prev = alt_deg

        current += timedelta(seconds=interval_sec)

    daylight_hours = 0
    if sunrise and sunset:
        daylight_hours = (sunset - sunrise).total_seconds() / 3600

    advantage = 0
    if fixed_power_sum > 0:
        advantage = ((tracking_power_sum - fixed_power_sum) / fixed_power_sum) * 100

    return {
        "date": date,
        "daylight_hours": daylight_hours,
        "peak_altitude": peak_alt,
        "peak_time": peak_time,
        "sunrise": sunrise,
        "sunset": sunset,
        "tracking_energy": tracking_power_sum * (interval_sec / 3600),
        "fixed_energy": fixed_power_sum * (interval_sec / 3600),
        "advantage_pct": advantage,
        "total_az_steps": total_az_steps,
        "total_alt_steps": total_alt_steps,
    }


def simulate_year(lat: float = 42.25, lon: float = -71.82, year: int = 2026):
    """Simulate every day of a year and generate analysis charts."""
    print(f"Simulating {year} at {lat}°N, {abs(lon)}°W...")

    results = []
    date = datetime(year, 1, 1)
    end = datetime(year, 12, 31)

    day_count = 0
    while date <= end:
        r = estimate_daily_energy(lat, lon, date)
        results.append(r)
        day_count += 1
        if day_count % 30 == 0:
            print(f"  {date.strftime('%b %d')}... ({day_count} days)")
        date += timedelta(days=1)

    print(f"Done! {day_count} days simulated.\n")

    # Extract arrays
    dates = [r["date"] for r in results]
    daylight = [r["daylight_hours"] for r in results]
    peak_alt = [r["peak_altitude"] for r in results]
    track_e = [r["tracking_energy"] for r in results]
    fixed_e = [r["fixed_energy"] for r in results]
    advantage = [r["advantage_pct"] for r in results]
    az_steps_arr = [r["total_az_steps"] for r in results]
    alt_steps_arr = [r["total_alt_steps"] for r in results]

    # Monthly averages
    months = {}
    for r in results:
        m = r["date"].month
        if m not in months:
            months[m] = {"track": [], "fixed": [], "adv": [], "daylight": []}
        months[m]["track"].append(r["tracking_energy"])
        months[m]["fixed"].append(r["fixed_energy"])
        months[m]["adv"].append(r["advantage_pct"])
        months[m]["daylight"].append(r["daylight_hours"])

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    # Print summary table
    print(f"{'Month':<6} {'Daylight':>9} {'Peak Alt':>9} {'Track Adv':>10}")
    print("-" * 36)
    for m in range(1, 13):
        avg_day = np.mean(months[m]["daylight"])
        avg_adv = np.mean(months[m]["adv"])
        # Find peak alt for mid-month
        mid = [r for r in results if r["date"].month == m and r["date"].day == 15]
        pa = mid[0]["peak_altitude"] if mid else 0
        print(f"{month_names[m-1]:<6} {avg_day:>8.1f}h {pa:>8.1f}° {avg_adv:>9.1f}%")

    yearly_adv = ((sum(track_e) - sum(fixed_e)) / sum(fixed_e)) * 100
    print(f"\n{'YEARLY TRACKING ADVANTAGE:':>36} {yearly_adv:.1f}%")
    print(f"{'Total daylight hours:':>36} {sum(daylight):.0f}")

    # ====================================================================
    # PLOTS
    # ====================================================================
    fig, axes = plt.subplots(3, 2, figsize=(16, 14))
    fig.suptitle(
        f"Solar Tracker Year Simulation — {year} at {lat}°N, {abs(lon)}°W",
        fontsize=15, fontweight="bold",
    )

    # 1. Daylight hours
    ax = axes[0, 0]
    ax.fill_between(dates, daylight, alpha=0.3, color="orange")
    ax.plot(dates, daylight, color="orange", linewidth=1.5)
    ax.set_title("Daylight Hours Per Day")
    ax.set_ylabel("Hours")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, 18)

    # 2. Peak sun altitude
    ax = axes[0, 1]
    ax.fill_between(dates, peak_alt, alpha=0.3, color="red")
    ax.plot(dates, peak_alt, color="red", linewidth=1.5)
    ax.set_title("Peak Sun Altitude")
    ax.set_ylabel("Degrees")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(True, alpha=0.3)

    # 3. Tracking vs Fixed energy
    ax = axes[1, 0]
    ax.plot(dates, track_e, color="steelblue", linewidth=1, label="Tracking")
    ax.plot(dates, fixed_e, color="salmon", linewidth=1, label="Fixed")
    ax.fill_between(dates, fixed_e, track_e, alpha=0.2, color="green",
                    where=[t > f for t, f in zip(track_e, fixed_e)])
    ax.set_title("Daily Energy (relative units)")
    ax.set_ylabel("Energy")
    ax.legend()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(True, alpha=0.3)

    # 4. Tracking advantage %
    ax = axes[1, 1]
    ax.bar(dates, advantage, width=1, color="green", alpha=0.6)
    ax.axhline(y=yearly_adv, color="darkgreen", linestyle="--", linewidth=2,
               label=f"Yearly avg: {yearly_adv:.1f}%")
    ax.set_title("Tracking Advantage Per Day")
    ax.set_ylabel("Advantage (%)")
    ax.legend()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(True, alpha=0.3)

    # 5. Monthly average advantage (bar chart)
    ax = axes[2, 0]
    monthly_adv = [np.mean(months[m]["adv"]) for m in range(1, 13)]
    colors = plt.cm.RdYlGn([a / max(monthly_adv) for a in monthly_adv])
    ax.bar(month_names, monthly_adv, color=colors, edgecolor="black", linewidth=0.5)
    ax.axhline(y=yearly_adv, color="darkgreen", linestyle="--", linewidth=2)
    ax.set_title("Monthly Average Tracking Advantage")
    ax.set_ylabel("Advantage (%)")
    ax.grid(True, alpha=0.3, axis="y")
    for i, v in enumerate(monthly_adv):
        ax.text(i, v + 0.3, f"{v:.0f}%", ha="center", fontsize=9, fontweight="bold")

    # 6. Motor wear (total steps per day)
    ax = axes[2, 1]
    total_steps = [a + b for a, b in zip(az_steps_arr, alt_steps_arr)]
    ax.fill_between(dates, total_steps, alpha=0.3, color="purple")
    ax.plot(dates, total_steps, color="purple", linewidth=1)
    ax.set_title("Daily Motor Steps (wear indicator)")
    ax.set_ylabel("Total Steps")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    # Save
    import os
    os.makedirs("reports", exist_ok=True)
    out_path = f"reports/yearly_simulation_{year}.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\nChart saved to {out_path}")
    plt.show()

    return results


if __name__ == "__main__":
    simulate_year()
