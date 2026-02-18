"""Logging setup — file + console logging with rotation."""

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(name: str, config: dict) -> logging.Logger:
    """Create a logger that writes to both console and a rotating log file.
    
    Args:
        name: Logger name (e.g. 'tracker', 'monitor')
        config: Full config dict (uses config['logging'])
    
    Returns:
        Configured logger instance
    """
    log_cfg = config.get("logging", {})
    log_path = log_cfg.get("path", "logs/solar_tracker.log")
    log_level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    max_bytes = log_cfg.get("max_file_size_mb", 10) * 1024 * 1024
    backup_count = log_cfg.get("backup_count", 5)

    # Ensure log directory exists
    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Avoid duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(name)-12s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler with rotation
    fh = RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backup_count)
    fh.setLevel(log_level)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(log_level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger
