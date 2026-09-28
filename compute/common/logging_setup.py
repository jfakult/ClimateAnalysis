"""Shared console+file logging for all pipeline steps.

Each standalone script run gets its own timestamped log file in
compute/logs/. When 0_run_all.py drives all 5 steps, it sets
WEATHER2_LOG_FILE so every step appends to the same run-wide log instead of
each creating its own.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def setup_logging(step_name):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    log_path = os.environ.get("WEATHER2_LOG_FILE")
    if not log_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = str(LOGS_DIR / f"{timestamp}_{step_name}.log")

    logger = logging.getLogger(step_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s [%(name)s] %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger, log_path
