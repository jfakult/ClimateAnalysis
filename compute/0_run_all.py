#!/usr/bin/env python3
"""Entry point: runs steps 1-5 in order. Each step can also be run standalone
for debugging a single stage (it'll get its own timestamped log file in
that case instead of sharing this run's log).

MAX_STATIONS caps step 2's candidate list to the first N station ids (sorted)
for fast local testing; set to None to process the full station list once
the pipeline's been validated.

PyPy was tested for step 3 and measured ~19% SLOWER than CPython on a real
(cache-cleared) run -- 38.6s vs 32.4s. An earlier "PyPy is 7x faster" reading
turned out to be a measurement artifact (that run's cache wasn't actually
cleared, so it was mostly fast cache-hit skips, not real parsing). Likely
cause: the dominant cost in step 3 is regex-based field extraction
(re.finditer() for the AA/GA groups), and CPython's `re` module is a mature,
heavily-optimized C implementation that PyPy's JIT doesn't beat here. All
steps run under the regular interpreter.
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(COMPUTE_DIR))

from common.logging_setup import setup_logging, LOGS_DIR  # noqa: E402

PYTHON = sys.executable

MAX_STATIONS = None # set to an int for fast local testing, or None to process all stations

STEPS = [
    ["1_build_station_index.py"],
    ["2_filter_hourly_stations.py"] + (["--max-stations", str(MAX_STATIONS)] if MAX_STATIONS else []),
    ["3_parse_station_year.py"],
    ["4_derive_station.py"],
    ["5_build_output.py"],
]


def main():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = str(LOGS_DIR / f"{timestamp}_run_all.log")

    logger, _ = setup_logging("0_run_all")
    # Route this and every child step into the same run-wide log file.
    logger.handlers[-1].close()
    logger.handlers.pop()
    import logging
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", "%H:%M:%S"))
    logger.addHandler(file_handler)

    env = os.environ.copy()
    env["WEATHER2_LOG_FILE"] = log_path

    logger.info(f"Logging this run to {log_path}")
    logger.info(f"MAX_STATIONS = {MAX_STATIONS}")

    run_start = time.time()
    step_times = []
    for step in STEPS:
        script = step[0]
        logger.info(f"=== Running {' '.join(step)} ===")
        step_start = time.time()
        result = subprocess.run([PYTHON, str(COMPUTE_DIR / script)] + step[1:], cwd=COMPUTE_DIR, env=env)
        step_elapsed = time.time() - step_start
        step_times.append((script, step_elapsed))
        if result.returncode != 0:
            logger.error(f"Step {script} failed with exit code {result.returncode} after {step_elapsed:.1f}s, stopping.")
            sys.exit(result.returncode)

    total_elapsed = time.time() - run_start
    logger.info("All steps completed successfully. Timing summary:")
    for script, elapsed in step_times:
        logger.info(f"  {script:<32} {elapsed:7.1f}s")
    logger.info(f"  {'TOTAL':<32} {total_elapsed:7.1f}s")


if __name__ == "__main__":
    main()
