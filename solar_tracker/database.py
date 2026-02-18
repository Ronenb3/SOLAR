"""SQLite database for solar data — replaces text file logging.

Stores all charge controller readings in a structured database.
Can import old BatteryDat_*.txt files.
Supports queries like "show me days where tracking beat fixed by >30%".
"""

import os
import re
import sqlite3
import glob
import logging
from datetime import datetime

logger = logging.getLogger("database")


class SolarDatabase:
    """SQLite database for storing and querying solar panel data."""

    def __init__(self, db_path: str = "data/solar.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info(f"Database opened: {db_path}")

    def _create_tables(self):
        """Create tables if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                device TEXT NOT NULL,           -- 'tracking' or 'fixed'
                battery_voltage REAL,
                battery_current REAL,
                battery_energy REAL,
                panel_voltage REAL,
                panel_power REAL,
                dod_percent REAL,
                session_id TEXT                 -- groups readings from same run
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                start_time TEXT,
                end_time TEXT,
                source_file TEXT,               -- original filename if imported
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS tracker_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                sun_azimuth REAL,
                sun_altitude REAL,
                panel_azimuth REAL,
                panel_altitude REAL,
                az_steps INTEGER,
                alt_steps INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_readings_device ON readings(device);
            CREATE INDEX IF NOT EXISTS idx_readings_session ON readings(session_id);
            CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON readings(timestamp);
        """)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Insert data
    # ------------------------------------------------------------------

    def insert_reading(self, device: str, battery_voltage: float, battery_current: float,
                       battery_energy: float = None, panel_voltage: float = None,
                       panel_power: float = None, dod_percent: float = None,
                       session_id: str = None):
        """Insert a single charge controller reading."""
        self.conn.execute(
            """INSERT INTO readings 
               (timestamp, device, battery_voltage, battery_current, battery_energy,
                panel_voltage, panel_power, dod_percent, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().isoformat(), device, battery_voltage, battery_current,
             battery_energy, panel_voltage, panel_power, dod_percent, session_id),
        )
        self.conn.commit()

    def log_tracker_move(self, sun_az: float, sun_alt: float,
                         panel_az: float, panel_alt: float,
                         az_steps: int, alt_steps: int):
        """Log a tracker movement."""
        self.conn.execute(
            """INSERT INTO tracker_log 
               (timestamp, sun_azimuth, sun_altitude, panel_azimuth, panel_altitude, az_steps, alt_steps)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().isoformat(), sun_az, sun_alt, panel_az, panel_alt, az_steps, alt_steps),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Import old text files
    # ------------------------------------------------------------------

    def import_battery_file(self, filepath: str) -> int:
        """Import a BatteryDat_*.txt file into the database.
        
        Returns number of readings imported.
        """
        filename = os.path.basename(filepath)
        
        # Check if already imported
        existing = self.conn.execute(
            "SELECT session_id FROM sessions WHERE source_file = ?", (filename,)
        ).fetchone()
        if existing:
            logger.info(f"  {filename}: already imported, skipping")
            return 0

        session_id = f"import_{filename.replace('.txt', '')}"
        count = 0

        with open(filepath, "r") as f:
            header = f.readline().strip()
            
            # Detect format by header
            is_dod_format = header.startswith("DoD1")
            
            for line_num, line in enumerate(f, start=2):
                line = line.strip()
                if not line:
                    continue
                
                parts = [p.strip() for p in line.split(",")]
                
                try:
                    if is_dod_format:
                        # DoD format: DoD1,DoD2,BatV1,BatV2,Hours1,Hours2
                        dod1 = float(parts[0]) if parts[0] else None
                        batv1 = float(parts[2]) if len(parts) > 2 and parts[2] else None
                        if dod1 is not None or batv1 is not None:
                            self.conn.execute(
                                """INSERT INTO readings 
                                   (timestamp, device, battery_voltage, battery_current, 
                                    battery_energy, panel_voltage, panel_power, dod_percent, session_id)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (f"{session_id}_line{line_num}", "tracking",
                                 batv1, None, None, None, None, dod1, session_id),
                            )
                            count += 1
                    else:
                        # Standard format — alternating rows: device1, device2
                        if len(parts) >= 6:
                            batv = float(parts[0]) if parts[0] else None
                            bati = float(parts[1]) if parts[1] else None
                            baten = float(parts[2]) if parts[2] else None
                            panelv = float(parts[3]) if parts[3] else None
                            panelp = float(parts[4]) if parts[4] else None
                            hours = float(parts[5]) if parts[5] else None

                            # Determine device by row number (odd=tracking, even=fixed)
                            device = "tracking" if line_num % 2 == 0 else "fixed"
                            
                            self.conn.execute(
                                """INSERT INTO readings 
                                   (timestamp, device, battery_voltage, battery_current,
                                    battery_energy, panel_voltage, panel_power, dod_percent, session_id)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (f"{session_id}_h{hours:.4f}" if hours else f"{session_id}_line{line_num}",
                                 device, batv, bati, baten, panelv, panelp, None, session_id),
                            )
                            count += 1
                except (ValueError, IndexError) as e:
                    logger.debug(f"  {filename} line {line_num}: parse error — {e}")
                    continue

        # Record the session
        self.conn.execute(
            "INSERT INTO sessions (session_id, source_file, notes) VALUES (?, ?, ?)",
            (session_id, filename, f"Imported {count} readings from {filename}"),
        )
        self.conn.commit()
        logger.info(f"  {filename}: imported {count} readings")
        return count

    def import_all_battery_files(self, directory: str = ".") -> int:
        """Import all BatteryDat_*.txt files from a directory."""
        files = sorted(glob.glob(os.path.join(directory, "BatteryDat_*.txt")))
        total = 0
        logger.info(f"Found {len(files)} battery data files to import")
        for f in files:
            total += self.import_battery_file(f)
        logger.info(f"Import complete: {total} total readings")
        return total

    # ------------------------------------------------------------------
    # Query data
    # ------------------------------------------------------------------

    def get_sessions(self) -> list[dict]:
        """Get all recording sessions."""
        rows = self.conn.execute(
            "SELECT session_id, source_file, notes FROM sessions ORDER BY session_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_session_summary(self, session_id: str) -> dict:
        """Get summary statistics for a session."""
        result = {}
        for device in ("tracking", "fixed"):
            row = self.conn.execute(
                """SELECT 
                     COUNT(*) as readings,
                     AVG(panel_power) as avg_power,
                     MAX(panel_power) as peak_power,
                     AVG(battery_voltage) as avg_voltage,
                     SUM(panel_power) as total_power_sum
                   FROM readings 
                   WHERE session_id = ? AND device = ?""",
                (session_id, device),
            ).fetchone()
            result[device] = dict(row) if row else {}
        return result

    def get_daily_comparison(self) -> list[dict]:
        """Compare tracking vs fixed panel across all sessions."""
        rows = self.conn.execute(
            """SELECT 
                 r.session_id,
                 s.source_file,
                 r.device,
                 COUNT(*) as readings,
                 AVG(r.panel_power) as avg_power,
                 MAX(r.panel_power) as peak_power,
                 AVG(r.battery_voltage) as avg_voltage
               FROM readings r
               JOIN sessions s ON r.session_id = s.session_id
               WHERE r.panel_power IS NOT NULL
               GROUP BY r.session_id, r.device
               ORDER BY r.session_id, r.device"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_readings(self, device: str = None, session_id: str = None, limit: int = 1000) -> list[dict]:
        """Get readings with optional filters."""
        query = "SELECT * FROM readings WHERE 1=1"
        params = []
        if device:
            query += " AND device = ?"
            params.append(device)
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_latest_readings(self, n: int = 100) -> dict:
        """Get the latest n readings for each device."""
        result = {}
        for device in ("tracking", "fixed"):
            rows = self.conn.execute(
                """SELECT battery_voltage, battery_current, panel_voltage, panel_power, dod_percent, timestamp
                   FROM readings WHERE device = ? ORDER BY id DESC LIMIT ?""",
                (device, n),
            ).fetchall()
            result[device] = [dict(r) for r in reversed(rows)]
        return result

    def close(self):
        """Close database connection."""
        self.conn.close()
        logger.info("Database closed")
