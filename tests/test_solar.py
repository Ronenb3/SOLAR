"""Tests for the solar tracker system — no hardware needed."""

import json
import math
import os
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Test motor math
# ---------------------------------------------------------------------------

class TestMotorMath(unittest.TestCase):
    """Test azimuth and altitude step calculations."""

    def test_azimuth_full_rotation(self):
        from solar_tracker.tracker import azimuth_steps
        # Full 360° rotation = 40 gear ratio × 1000 steps = 40000
        # But the function is: (angle/360) * 40 * 1000
        steps = azimuth_steps(360)
        self.assertEqual(steps, 40000)

    def test_azimuth_zero(self):
        from solar_tracker.tracker import azimuth_steps
        self.assertEqual(azimuth_steps(0), 0)

    def test_azimuth_negative(self):
        from solar_tracker.tracker import azimuth_steps
        steps = azimuth_steps(-90)
        self.assertEqual(steps, -10000)

    def test_azimuth_small_angle(self):
        from solar_tracker.tracker import azimuth_steps
        steps = azimuth_steps(1)
        self.assertAlmostEqual(steps, 111, delta=1)

    def test_altitude_zero(self):
        from solar_tracker.tracker import altitude_steps
        steps = altitude_steps(0)
        self.assertEqual(steps, 33912)  # 2 * 16956

    def test_altitude_positive(self):
        from solar_tracker.tracker import altitude_steps
        steps_0 = altitude_steps(0)
        steps_45 = altitude_steps(45)
        # Higher angle = fewer steps (panel tilts back)
        self.assertLess(steps_45, steps_0)

    def test_altitude_returns_int(self):
        from solar_tracker.tracker import altitude_steps
        self.assertIsInstance(altitude_steps(30.5), int)


# ---------------------------------------------------------------------------
# Test position store
# ---------------------------------------------------------------------------

class TestPositionStore(unittest.TestCase):
    """Test saving and loading panel position."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.filepath = os.path.join(self.tmpdir, "test_position.json")

    def test_save_and_load(self):
        from solar_tracker.position_store import save_position, load_position
        save_position(123.4, 56.7, filepath=self.filepath)
        pos = load_position(filepath=self.filepath)
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos["azimuth_degrees"], 123.4)
        self.assertAlmostEqual(pos["altitude_degrees"], 56.7)

    def test_load_missing_file(self):
        from solar_tracker.position_store import load_position
        pos = load_position(filepath="/tmp/nonexistent_position.json")
        self.assertIsNone(pos)

    def test_clear_position(self):
        from solar_tracker.position_store import save_position, clear_position, load_position
        save_position(10, 20, filepath=self.filepath)
        clear_position(filepath=self.filepath)
        self.assertIsNone(load_position(filepath=self.filepath))

    def test_atomic_write(self):
        from solar_tracker.position_store import save_position, load_position
        # Write twice — second should overwrite cleanly
        save_position(1, 2, filepath=self.filepath)
        save_position(3, 4, filepath=self.filepath)
        pos = load_position(filepath=self.filepath)
        self.assertAlmostEqual(pos["azimuth_degrees"], 3)


# ---------------------------------------------------------------------------
# Test VE.Direct parser
# ---------------------------------------------------------------------------

class TestVedirectParser(unittest.TestCase):
    """Test the VE.Direct protocol parser with fake serial data."""

    def _build_frame(self, data: dict) -> bytes:
        """Build a valid VE.Direct text frame from a dict."""
        frame = b""
        checksum = 0
        for key, value in data.items():
            line = f"\r\n{key}\t{value}"
            frame += line.encode()
            for b in line.encode():
                checksum += b
        
        # Add checksum
        check_line = f"\r\nChecksum\t"
        frame += check_line.encode()
        for b in check_line.encode():
            checksum += b
        
        # Calculate checksum byte
        check_byte = (256 - (checksum % 256)) % 256
        frame += bytes([check_byte])
        
        return frame

    def test_parse_valid_frame(self):
        from solar_tracker.vedirect import Vedirect
        # Create a parser without serial connection
        parser = Vedirect.__new__(Vedirect)
        parser.ser = None
        parser.label = "test"
        parser._reset_parser()
        
        frame_data = {"V": "12500", "I": "1500", "VPV": "19000", "PPV": "45"}
        raw = self._build_frame(frame_data)
        
        result = None
        for byte in raw:
            packet = parser._parse_byte(byte)
            if packet is not None:
                result = packet
                break
        
        self.assertIsNotNone(result)
        self.assertEqual(result["V"], "12500")
        self.assertEqual(result["I"], "1500")
        self.assertEqual(result["PPV"], "45")


# ---------------------------------------------------------------------------
# Test config
# ---------------------------------------------------------------------------

class TestConfig(unittest.TestCase):
    """Test configuration loading."""

    def test_default_config(self):
        from solar_tracker.config import load_config
        config = load_config("/tmp/nonexistent_config.yaml")
        self.assertEqual(config["location"]["latitude"], 42.25)
        self.assertIn("motors", config)
        self.assertIn("tracking", config)

    def test_deep_merge(self):
        from solar_tracker.config import _deep_merge
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        override = {"a": {"b": 99}, "e": 4}
        result = _deep_merge(base, override)
        self.assertEqual(result["a"]["b"], 99)
        self.assertEqual(result["a"]["c"], 2)  # Preserved from base
        self.assertEqual(result["e"], 4)


# ---------------------------------------------------------------------------
# Test database
# ---------------------------------------------------------------------------

class TestDatabase(unittest.TestCase):
    """Test SQLite database operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def test_create_and_insert(self):
        from solar_tracker.database import SolarDatabase
        db = SolarDatabase(self.db_path)
        db.insert_reading("tracking", 12.5, 1.2, panel_power=45.0, session_id="test1")
        db.insert_reading("fixed", 13.4, 3.1, panel_power=40.0, session_id="test1")
        
        readings = db.get_readings()
        self.assertEqual(len(readings), 2)
        db.close()

    def test_session_summary(self):
        from solar_tracker.database import SolarDatabase
        db = SolarDatabase(self.db_path)
        for i in range(10):
            db.insert_reading("tracking", 12.5, 1.0, panel_power=50.0, session_id="s1")
            db.insert_reading("fixed", 13.4, 3.0, panel_power=40.0, session_id="s1")
        
        summary = db.get_session_summary("s1")
        self.assertAlmostEqual(summary["tracking"]["avg_power"], 50.0)
        self.assertAlmostEqual(summary["fixed"]["avg_power"], 40.0)
        db.close()

    def test_import_battery_file(self):
        from solar_tracker.database import SolarDatabase
        # Create a fake battery data file
        fake_file = os.path.join(self.tmpdir, "BatteryDat_99.txt")
        with open(fake_file, "w") as f:
            f.write("BatV1, BatI1, BatEn1, PanelV1, PanelP1, Hours\n")
            f.write("12.50, 1.00, 0.01, 19.00, 45.00, 0.001\n")
            f.write("13.40, 3.00, 0.01, 19.50, 40.00, 0.001\n")
        
        db = SolarDatabase(self.db_path)
        count = db.import_battery_file(fake_file)
        self.assertEqual(count, 2)
        
        # Importing again should skip
        count2 = db.import_battery_file(fake_file)
        self.assertEqual(count2, 0)
        db.close()


# ---------------------------------------------------------------------------
# Test DoD estimation
# ---------------------------------------------------------------------------

class TestDoD(unittest.TestCase):
    """Test Depth of Discharge estimation."""

    def test_full_battery(self):
        from solar_tracker.monitor import estimate_dod
        # 13.6V at low current should be ~0% DoD
        dod = estimate_dod(13.6, 0.1)
        self.assertLess(dod, 5)

    def test_empty_battery(self):
        from solar_tracker.monitor import estimate_dod
        # 12.0V should be ~100% DoD
        dod = estimate_dod(12.0, 0.1)
        self.assertGreater(dod, 90)

    def test_mid_range(self):
        from solar_tracker.monitor import estimate_dod
        # ~13.0V should be somewhere in the middle
        dod = estimate_dod(13.0, 1.0)
        self.assertGreater(dod, 20)
        self.assertLess(dod, 80)


if __name__ == "__main__":
    unittest.main()
