"""Analysis tools — process battery data files and generate reports.

Provides functions to:
  - Load and parse all BatteryDat_*.txt files
  - Compare tracking vs fixed panel performance
  - Generate summary statistics and charts
  - Export results for reports/presentations
"""

import os
import glob

import numpy as np
import pandas as pd


def load_battery_file(filepath: str) -> dict | None:
    """Load a single BatteryDat_*.txt file and split into device 1 and device 2.
    
    Returns:
        Dict with 'dev1' (tracking), 'dev2' (fixed) DataFrames, or None if empty.
    """
    try:
        raw = pd.read_csv(filepath, header=None, skiprows=1)
        if len(raw) < 2:
            return None

        cols = ["BatV", "BatI", "BatEn", "PanelV", "PanelP", "Hours"]
        raw.columns = cols[: len(raw.columns)]

        dev1 = raw.iloc[0::2].reset_index(drop=True).copy()
        dev2 = raw.iloc[1::2].reset_index(drop=True).copy()

        return {"dev1": dev1, "dev2": dev2, "filename": os.path.basename(filepath)}
    except Exception:
        return None


def load_all_battery_files(directory: str = ".") -> dict:
    """Load all BatteryDat_*.txt files from a directory.
    
    Returns:
        Dict mapping file number (str) to {dev1, dev2, filename}.
    """
    files = sorted(glob.glob(os.path.join(directory, "BatteryDat_*.txt")))
    all_data = {}
    for f in files:
        result = load_battery_file(f)
        if result:
            key = os.path.basename(f).split("_")[1].split(".")[0]
            all_data[key] = result
    return all_data


def compute_session_energy(dev: pd.DataFrame) -> float:
    """Compute total energy (Wh) from a device DataFrame using trapezoidal integration."""
    if len(dev) < 2 or "PanelP" not in dev or "Hours" not in dev:
        return 0.0
    dt = dev["Hours"].diff().median()
    return float((dev["PanelP"] * dt).sum())


def compare_tracking_vs_fixed(all_data: dict) -> pd.DataFrame:
    """Generate a comparison table of tracking vs fixed performance.
    
    Returns:
        DataFrame with one row per session and columns for energy, power, advantage.
    """
    rows = []
    for key, data in all_data.items():
        d1, d2 = data["dev1"], data["dev2"]
        energy1 = compute_session_energy(d1)
        energy2 = compute_session_energy(d2)
        duration = d1["Hours"].max() if "Hours" in d1 else 0
        advantage = ((energy1 - energy2) / energy2) * 100 if energy2 > 0 else 0

        rows.append({
            "File": key,
            "Duration (hrs)": round(duration, 2),
            "Tracking Energy (Wh)": round(energy1, 1),
            "Fixed Energy (Wh)": round(energy2, 1),
            "Tracking Advantage (%)": round(advantage, 1),
            "Avg Tracking Power (W)": round(d1["PanelP"].mean(), 1),
            "Avg Fixed Power (W)": round(d2["PanelP"].mean(), 1),
            "Peak Tracking Power (W)": round(d1["PanelP"].max(), 1),
            "Peak Fixed Power (W)": round(d2["PanelP"].max(), 1),
        })

    return pd.DataFrame(rows)


def generate_report(directory: str = ".", output_dir: str = "reports") -> str:
    """Generate a full analysis report with charts saved to files.
    
    Args:
        directory: Where to find BatteryDat_*.txt files
        output_dir: Where to save chart images
    
    Returns:
        Path to the summary text file
    """
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend for saving files
    import matplotlib.pyplot as plt

    os.makedirs(output_dir, exist_ok=True)

    all_data = load_all_battery_files(directory)
    if not all_data:
        print("No data files found!")
        return ""

    summary = compare_tracking_vs_fixed(all_data)

    # --- Chart 1: Power comparison per session ---
    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(summary))
    width = 0.35
    ax.bar([i - width / 2 for i in x], summary["Tracking Energy (Wh)"], width,
           label="Tracking", color="steelblue")
    ax.bar([i + width / 2 for i in x], summary["Fixed Energy (Wh)"], width,
           label="Fixed", color="salmon")
    ax.set_xlabel("Session")
    ax.set_ylabel("Energy (Wh)")
    ax.set_title("Total Energy: Tracking vs Fixed Panel")
    ax.set_xticks(x)
    ax.set_xticklabels([f"File {r}" for r in summary["File"]])
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "energy_comparison.png"), dpi=150)
    plt.close()

    # --- Chart 2: Power over time for each session ---
    fig, axes = plt.subplots(len(all_data), 1, figsize=(12, 3.5 * len(all_data)), squeeze=False)
    for i, (key, data) in enumerate(all_data.items()):
        ax = axes[i, 0]
        d1, d2 = data["dev1"], data["dev2"]
        ax.plot(d1["Hours"] * 60, d1["PanelP"], "b-", alpha=0.7, label="Tracking")
        ax.plot(d2["Hours"] * 60, d2["PanelP"], "r-", alpha=0.7, label="Fixed")
        avg1, avg2 = d1["PanelP"].mean(), d2["PanelP"].mean()
        adv = ((avg1 - avg2) / avg2) * 100 if avg2 > 0 else 0
        ax.set_title(f"File {key} — Tracking={avg1:.0f}W, Fixed={avg2:.0f}W ({adv:+.1f}%)")
        ax.set_xlabel("Time (minutes)")
        ax.set_ylabel("Power (W)")
        ax.legend()
        ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "power_over_time.png"), dpi=150)
    plt.close()

    # --- Chart 3: Combined histogram ---
    all_d1 = pd.concat([d["dev1"] for d in all_data.values()], ignore_index=True)
    all_d2 = pd.concat([d["dev2"] for d in all_data.values()], ignore_index=True)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(all_d1["PanelP"], bins=30, alpha=0.6, label="Tracking", color="steelblue", edgecolor="black")
    ax.hist(all_d2["PanelP"], bins=30, alpha=0.6, label="Fixed", color="salmon", edgecolor="black")
    ax.axvline(all_d1["PanelP"].mean(), color="blue", linestyle="--",
               label=f"Tracking Avg: {all_d1['PanelP'].mean():.0f}W")
    ax.axvline(all_d2["PanelP"].mean(), color="red", linestyle="--",
               label=f"Fixed Avg: {all_d2['PanelP'].mean():.0f}W")
    ax.set_xlabel("Power (W)")
    ax.set_ylabel("Count")
    ax.set_title("Power Distribution — All Sessions")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "power_distribution.png"), dpi=150)
    plt.close()

    # --- Summary text ---
    overall_adv = ((all_d1["PanelP"].mean() - all_d2["PanelP"].mean()) / all_d2["PanelP"].mean()) * 100
    summary_path = os.path.join(output_dir, "summary.txt")
    with open(summary_path, "w") as f:
        f.write("SOLAR TRACKER ANALYSIS REPORT\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Sessions analyzed: {len(all_data)}\n")
        f.write(f"Total tracking readings: {len(all_d1)}\n")
        f.write(f"Total fixed readings: {len(all_d2)}\n\n")
        f.write(f"Overall tracking avg power: {all_d1['PanelP'].mean():.1f} W\n")
        f.write(f"Overall fixed avg power:    {all_d2['PanelP'].mean():.1f} W\n")
        f.write(f"Overall tracking advantage: {overall_adv:.1f}%\n\n")
        f.write("Per-session breakdown:\n")
        f.write(summary.to_string(index=False))
        f.write("\n\nCharts saved to:\n")
        f.write(f"  - {output_dir}/energy_comparison.png\n")
        f.write(f"  - {output_dir}/power_over_time.png\n")
        f.write(f"  - {output_dir}/power_distribution.png\n")

    print(f"\nReport saved to {output_dir}/")
    print(f"  Summary: {summary_path}")
    print(f"  Charts:  3 PNG files")
    return summary_path


if __name__ == "__main__":
    generate_report()
