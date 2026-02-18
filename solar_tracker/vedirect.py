"""VE.Direct protocol parser — reads data from Victron charge controllers.

Victron Energy charge controllers send data over serial (USB) using the
VE.Direct text protocol. This module parses that byte stream into Python
dictionaries with keys like 'V' (voltage), 'I' (current), 'VPV' (panel voltage),
'PPV' (panel power).

Improved from original:
  - Proper error handling and reconnection
  - Logging instead of silent failures
  - Timeout protection
  - Clean class structure
"""

import logging
import time

try:
    import serial
except ImportError:
    serial = None  # Allow import on machines without pyserial (e.g. Mac for testing)

logger = logging.getLogger("vedirect")

# State machine states
WAIT_HEADER = 0
IN_KEY = 1
IN_VALUE = 2
IN_CHECKSUM = 3
HEX = 4


class Vedirect:
    """Parser for Victron VE.Direct serial text protocol.
    
    Args:
        serialport: Serial port path (e.g. '/dev/ttyUSB0')
        timeout: Serial read timeout in seconds
        label: Human-readable label for logging (e.g. 'Tracking Panel')
    """

    def __init__(self, serialport: str, timeout: int = 60, label: str = ""):
        self.serialport = serialport
        self.timeout = timeout
        self.label = label or serialport
        self.ser = None
        self._connect()
        self._reset_parser()

    def _connect(self):
        """Open serial connection with error handling."""
        if serial is None:
            raise ImportError("pyserial is not installed. Run: pip install pyserial")
        try:
            self.ser = serial.Serial(self.serialport, 19200, timeout=self.timeout)
            logger.info(f"[{self.label}] Connected to {self.serialport}")
        except serial.SerialException as e:
            logger.error(f"[{self.label}] Failed to connect to {self.serialport}: {e}")
            raise

    def _reset_parser(self):
        """Reset the state machine to initial state."""
        self.state = WAIT_HEADER
        self.key = ""
        self.value = ""
        self.bytes_sum = 0
        self.dict = {}

    def _parse_byte(self, byte: int) -> dict | None:
        """Process a single byte through the state machine.
        
        Returns a complete data packet (dict) when checksum passes, else None.
        """
        # Hex protocol marker — switch to HEX state
        if byte == ord(":") and self.state != IN_CHECKSUM:
            self.state = HEX

        if self.state == WAIT_HEADER:
            self.bytes_sum += byte
            if byte == ord("\r"):
                pass  # Stay in WAIT_HEADER
            elif byte == ord("\n"):
                self.state = IN_KEY
            return None

        elif self.state == IN_KEY:
            self.bytes_sum += byte
            if byte == ord("\t"):
                if self.key == "Checksum":
                    self.state = IN_CHECKSUM
                else:
                    self.state = IN_VALUE
            else:
                self.key += chr(byte)
            return None

        elif self.state == IN_VALUE:
            self.bytes_sum += byte
            if byte == ord("\r"):
                self.state = WAIT_HEADER
                self.dict[self.key] = self.value
                self.key = ""
                self.value = ""
            else:
                self.value += chr(byte)
            return None

        elif self.state == IN_CHECKSUM:
            self.bytes_sum += byte
            self.key = ""
            self.value = ""
            self.state = WAIT_HEADER
            if self.bytes_sum % 256 == 0:
                result = self.dict.copy()
                self.bytes_sum = 0
                self.dict = {}
                return result
            else:
                logger.debug(f"[{self.label}] Checksum failed, discarding packet")
                self.bytes_sum = 0
                self.dict = {}
            return None

        elif self.state == HEX:
            self.bytes_sum = 0
            if byte == ord("\n"):
                self.state = WAIT_HEADER
            return None

        else:
            logger.warning(f"[{self.label}] Unknown parser state: {self.state}")
            self._reset_parser()
            return None

    def read_data_single(self) -> dict:
        """Block until one complete valid data packet is received.
        
        Returns:
            Dict with keys like 'V', 'I', 'VPV', 'PPV', etc.
        
        Raises:
            ConnectionError: If serial connection is lost
        """
        if self.ser is None:
            raise ConnectionError(f"[{self.label}] Not connected")

        while True:
            try:
                data = self.ser.read()
                if not data:
                    logger.warning(f"[{self.label}] Serial read timeout")
                    continue
                for single_byte in data:
                    packet = self._parse_byte(single_byte)
                    if packet is not None:
                        return packet
            except Exception as e:
                logger.error(f"[{self.label}] Read error: {e}")
                raise ConnectionError(f"[{self.label}] Lost connection: {e}")

    def read_data_callback(self, callback):
        """Continuously read data and call callback(packet) for each valid packet."""
        while True:
            try:
                packet = self.read_data_single()
                callback(packet)
            except ConnectionError:
                logger.warning(f"[{self.label}] Disconnected, retrying in 5s...")
                time.sleep(5)
                try:
                    self._connect()
                    self._reset_parser()
                except Exception:
                    pass

    def close(self):
        """Close serial connection."""
        if self.ser and self.ser.is_open:
            self.ser.close()
            logger.info(f"[{self.label}] Connection closed")
