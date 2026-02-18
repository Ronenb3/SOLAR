"""Web dashboard — check your solar system from your phone.

A lightweight Flask web app that shows:
  - Live power, voltage, and DoD readings
  - Historical charts
  - Tracking vs fixed comparison
  - System status

Access from any device on the same network at http://<pi-ip>:8080
"""

import json
import os
import threading
from datetime import datetime

try:
    from flask import Flask, render_template_string, jsonify
except ImportError:
    Flask = None

from solar_tracker.config import load_config
from solar_tracker.database import SolarDatabase

# ---------------------------------------------------------------------------
# HTML Template (embedded so no separate template files needed)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Solar Tracker Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f172a; color: #e2e8f0; padding: 16px;
        }
        h1 { text-align: center; color: #fbbf24; margin-bottom: 20px; font-size: 1.5em; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; max-width: 800px; margin: 0 auto; }
        .card {
            background: #1e293b; border-radius: 12px; padding: 16px;
            border: 1px solid #334155;
        }
        .card.full { grid-column: 1 / -1; }
        .card h2 { font-size: 0.85em; color: #94a3b8; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 1px; }
        .big-number { font-size: 2em; font-weight: 700; }
        .unit { font-size: 0.5em; color: #94a3b8; }
        .tracking { color: #60a5fa; }
        .fixed { color: #f87171; }
        .good { color: #34d399; }
        .warn { color: #fbbf24; }
        .compare-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #334155; }
        .compare-row:last-child { border: none; }
        .label { color: #94a3b8; }
        canvas { width: 100% !important; height: 200px !important; }
        .status { text-align: center; font-size: 0.8em; color: #64748b; margin-top: 16px; }
        @media (max-width: 500px) { .grid { grid-template-columns: 1fr; } }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
    <h1>☀️ Solar Tracker</h1>
    <div class="grid">
        <div class="card">
            <h2>Tracking Panel</h2>
            <div class="big-number tracking" id="track-power">--<span class="unit">W</span></div>
            <div style="margin-top:8px; font-size:0.9em;">
                <span id="track-voltage">--</span>V &nbsp;|&nbsp; 
                <span id="track-current">--</span>A &nbsp;|&nbsp; 
                DoD: <span id="track-dod">--</span>%
            </div>
        </div>
        <div class="card">
            <h2>Fixed Panel</h2>
            <div class="big-number fixed" id="fixed-power">--<span class="unit">W</span></div>
            <div style="margin-top:8px; font-size:0.9em;">
                <span id="fixed-voltage">--</span>V &nbsp;|&nbsp; 
                <span id="fixed-current">--</span>A &nbsp;|&nbsp; 
                DoD: <span id="fixed-dod">--</span>%
            </div>
        </div>
        <div class="card full">
            <h2>Power Over Time</h2>
            <canvas id="powerChart"></canvas>
        </div>
        <div class="card full">
            <h2>Battery Voltage</h2>
            <canvas id="voltageChart"></canvas>
        </div>
        <div class="card full">
            <h2>Session Comparison</h2>
            <div id="comparison">Loading...</div>
        </div>
    </div>
    <div class="status">Last updated: <span id="last-update">--</span></div>

    <script>
        // Chart setup
        const chartOpts = {
            responsive: true, animation: false,
            scales: { x: { display: false }, y: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } } },
            plugins: { legend: { labels: { color: '#e2e8f0' } } }
        };

        const powerChart = new Chart(document.getElementById('powerChart'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    { label: 'Tracking', data: [], borderColor: '#60a5fa', borderWidth: 2, pointRadius: 0, fill: false },
                    { label: 'Fixed', data: [], borderColor: '#f87171', borderWidth: 2, pointRadius: 0, fill: false }
                ]
            },
            options: chartOpts
        });

        const voltageChart = new Chart(document.getElementById('voltageChart'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    { label: 'Tracking', data: [], borderColor: '#60a5fa', borderWidth: 2, pointRadius: 0, fill: false },
                    { label: 'Fixed', data: [], borderColor: '#f87171', borderWidth: 2, pointRadius: 0, fill: false }
                ]
            },
            options: chartOpts
        });

        async function fetchData() {
            try {
                const res = await fetch('/api/latest');
                const data = await res.json();

                // Update cards
                if (data.tracking) {
                    document.getElementById('track-power').innerHTML = (data.tracking.panel_power || 0).toFixed(0) + '<span class="unit">W</span>';
                    document.getElementById('track-voltage').textContent = (data.tracking.battery_voltage || 0).toFixed(2);
                    document.getElementById('track-current').textContent = (data.tracking.battery_current || 0).toFixed(2);
                    document.getElementById('track-dod').textContent = (data.tracking.dod_percent || 0).toFixed(1);
                }
                if (data.fixed) {
                    document.getElementById('fixed-power').innerHTML = (data.fixed.panel_power || 0).toFixed(0) + '<span class="unit">W</span>';
                    document.getElementById('fixed-voltage').textContent = (data.fixed.battery_voltage || 0).toFixed(2);
                    document.getElementById('fixed-current').textContent = (data.fixed.battery_current || 0).toFixed(2);
                    document.getElementById('fixed-dod').textContent = (data.fixed.dod_percent || 0).toFixed(1);
                }

                // Update charts
                if (data.history) {
                    const labels = data.history.tracking.map((_, i) => i);
                    powerChart.data.labels = labels;
                    powerChart.data.datasets[0].data = data.history.tracking.map(r => r.panel_power);
                    powerChart.data.datasets[1].data = data.history.fixed.map(r => r.panel_power);
                    powerChart.update();

                    voltageChart.data.labels = labels;
                    voltageChart.data.datasets[0].data = data.history.tracking.map(r => r.battery_voltage);
                    voltageChart.data.datasets[1].data = data.history.fixed.map(r => r.battery_voltage);
                    voltageChart.update();
                }

                document.getElementById('last-update').textContent = new Date().toLocaleTimeString();

                // Comparison table
                if (data.comparison) {
                    let html = '';
                    data.comparison.forEach(s => {
                        const adv = s.advantage ? s.advantage.toFixed(1) + '%' : 'N/A';
                        const cls = s.advantage > 0 ? 'good' : 'warn';
                        html += '<div class="compare-row"><span class="label">' + s.session + 
                                '</span><span>Track: ' + (s.tracking_avg || 0).toFixed(0) + 'W</span>' +
                                '<span>Fixed: ' + (s.fixed_avg || 0).toFixed(0) + 'W</span>' +
                                '<span class="' + cls + '">' + adv + '</span></div>';
                    });
                    document.getElementById('comparison').innerHTML = html || 'No data yet';
                }
            } catch(e) { console.error('Fetch error:', e); }
        }

        fetchData();
        setInterval(fetchData, 5000);
    </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def create_app(config: dict = None) -> "Flask":
    """Create the Flask web dashboard app."""
    if Flask is None:
        raise ImportError("Flask is not installed. Run: pip install flask")

    config = config or load_config()
    app = Flask(__name__)
    db = SolarDatabase(config["database"]["path"])

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/latest")
    def api_latest():
        """Return latest readings and history for the dashboard."""
        history = db.get_latest_readings(n=200)

        # Get latest single reading for each device
        latest = {}
        for device in ("tracking", "fixed"):
            if history[device]:
                latest[device] = history[device][-1]

        # Get session comparisons
        comparisons = []
        daily = db.get_daily_comparison()
        sessions = {}
        for row in daily:
            sid = row["session_id"]
            if sid not in sessions:
                sessions[sid] = {"session": row.get("source_file", sid)}
            sessions[sid][f"{row['device']}_avg"] = row["avg_power"]

        for sid, s in sessions.items():
            t = s.get("tracking_avg", 0) or 0
            f = s.get("fixed_avg", 0) or 0
            s["advantage"] = ((t - f) / f * 100) if f > 0 else 0
            comparisons.append(s)

        return jsonify({
            "tracking": latest.get("tracking"),
            "fixed": latest.get("fixed"),
            "history": history,
            "comparison": comparisons[-10:],  # Last 10 sessions
        })

    return app


def run_dashboard(config: dict = None):
    """Start the web dashboard server."""
    config = config or load_config()
    dash_cfg = config.get("dashboard", {})
    host = dash_cfg.get("host", "0.0.0.0")
    port = dash_cfg.get("port", 8080)

    app = create_app(config)
    print(f"Dashboard running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)


def start_dashboard_thread(config: dict = None):
    """Start dashboard in a background thread (used by monitor)."""
    t = threading.Thread(target=run_dashboard, args=(config,), daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    run_dashboard()
