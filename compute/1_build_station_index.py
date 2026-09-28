#!/usr/bin/env python3
"""Step 1: scan isd-raw/{2010..2025} once and group files by station id
("USAF-WBAN") -> cache/station_index.json.

Cheap: just a directory listing + filename parsing, no decompression. Safe
to re-run any time (e.g. after more of isd-raw syncs) -- always rebuilds from
scratch since a full scan is fast.
"""

import json
import re
import sys
import time
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(COMPUTE_DIR))

from common.logging_setup import setup_logging  # noqa: E402

ISD_RAW_DIR = COMPUTE_DIR / "isd-raw"
CACHE_DIR = COMPUTE_DIR / "cache"
OUTPUT_PATH = CACHE_DIR / "station_index.json"

YEARS = range(2010, 2026)

FILENAME_RE = re.compile(r"^(\d{6})-(\d{5})-(\d{4})\.gz$")


def build_index():
    """Paths are stored relative to isd-raw/ (e.g. "2023/010010-99999-2023.gz"),
    not absolute -- isd-raw can be reached via different mount points
    (e.g. a slow network mount vs. running directly on the NAS box), so the
    cache must not bake in one specific absolute prefix."""
    index = {}
    for year in YEARS:
        year_dir = ISD_RAW_DIR / str(year)
        if not year_dir.is_dir():
            continue
        for path in year_dir.glob("*.gz"):
            m = FILENAME_RE.match(path.name)
            if not m:
                continue
            usaf, wban, file_year = m.group(1), m.group(2), int(m.group(3))
            station_id = f"{usaf}-{wban}"
            index.setdefault(station_id, []).append([file_year, f"{year}/{path.name}"])
    return index


def main():
    logger, log_path = setup_logging("1_build_station_index")
    logger.info(f"Logging to {log_path}")
    start = time.time()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Scanning {ISD_RAW_DIR} for years {YEARS.start}-{YEARS.stop - 1}...")
    index = build_index()
    for entries in index.values():
        entries.sort(key=lambda e: e[0])

    with open(OUTPUT_PATH, "w") as f:
        json.dump(index, f)

    elapsed = time.time() - start
    logger.info(f"Indexed {len(index)} stations across {sum(len(v) for v in index.values())} station-years -> {OUTPUT_PATH}")
    logger.info(f"Finished in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
