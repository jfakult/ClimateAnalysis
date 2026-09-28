#!/usr/bin/env python3
"""Step 2: from the candidate station list, keep only near-hourly-reporting
stations (average >= MIN_OBS_PER_DAY observations/day across 2010-2025).

Full ISD mixes near-hourly airports with synoptic-only stations that report
every 3-6 hours. Stations reporting at least ~3-hourly are kept (scoring
scales its perfect-day/daily-score rules to the reporting interval); sparser
ones (6-hourly and worse) are filtered out for now -- this threshold may be
loosened later.

Observation counts are ESTIMATED, not counted: a gzip file stores its
uncompressed size in its last 4 bytes, and decompressing just the first 64 KB
gives the average line length, so lines ~= size / average line length. That
reads ~64 KB per file instead of decompressing all of it, and measured within
about +/-5% of the exact count (95th percentile, single files; the per-station
average over its years is tighter) -- far finer than this coarse filter needs.
--max-stations still caps the candidates for fast local testing. Parallelized
across processes since it's an independent per-station check.
"""

import argparse
import calendar
import json
import multiprocessing
import random
import struct
import sys
import time
import zlib
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(COMPUTE_DIR))

from common.config import MAX_WORKERS  # noqa: E402
from common.logging_setup import setup_logging  # noqa: E402

ISD_RAW_DIR = COMPUTE_DIR / "isd-raw"
CACHE_DIR = COMPUTE_DIR / "cache"
STATION_INDEX_PATH = CACHE_DIR / "station_index.json"
OUTPUT_PATH = CACHE_DIR / "hourly_station_ids.json"

# ~3-hourly reporting (8/day, minus gaps) is enough: scoring adapts its rules to
# each station's reporting interval (see common/scoring.py `step`), so only
# 6-hourly-or-sparser stations are dropped here. Was 18.0 (hourly only).
MIN_OBS_PER_DAY = 7.0

# Alphabetical USAF-id order clusters by WMO numbering block (e.g. ids
# starting "00"/"01" are mostly older European/Arctic SYNOP stations that
# often only report precip at 6-hourly synoptic times and don't always
# encode structured cloud groups) -- so --max-stations takes a fixed-seed
# random sample instead of a plain prefix, to get a geographically/network
# representative test set rather than one biased toward sparse stations.
SAMPLE_SEED = 42


SAMPLE_COMPRESSED_BYTES = 64 * 1024


def estimate_lines(path):
    """Estimated line count of a .gz file without decompressing all of it."""
    with open(path, "rb") as f:
        head = f.read(SAMPLE_COMPRESSED_BYTES)
        f.seek(-4, 2)
        uncompressed_size = struct.unpack("<I", f.read(4))[0]  # gzip trailer: size mod 2^32
    sample = zlib.decompressobj(wbits=31).decompress(head)
    newlines = sample.count(b"\n")
    if not sample or not newlines:
        return 0
    return uncompressed_size * newlines / len(sample)


def days_in_year(year):
    return 366 if calendar.isleap(year) else 365


def check_one(args):
    station_id, entries = args
    total_lines = 0
    total_days = 0
    for year, rel_path in entries:
        total_lines += estimate_lines(ISD_RAW_DIR / rel_path)
        total_days += days_in_year(year)

    avg_obs_per_day = total_lines / total_days if total_days else 0.0
    return station_id, avg_obs_per_day >= MIN_OBS_PER_DAY


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-stations", type=int, default=None,
                         help="Only consider a fixed-seed random sample of N station ids, for fast local testing.")
    args = parser.parse_args()

    logger, log_path = setup_logging("2_filter_hourly_stations")
    logger.info(f"Logging to {log_path}")
    start = time.time()

    with open(STATION_INDEX_PATH) as f:
        station_index = json.load(f)

    station_ids = sorted(station_index.keys())
    if args.max_stations is not None:
        rng = random.Random(SAMPLE_SEED)
        rng.shuffle(station_ids)
        station_ids = sorted(station_ids[:args.max_stations])
    logger.info(f"Checking {len(station_ids)} candidate stations (min {MIN_OBS_PER_DAY} obs/day to pass) "
                f"using {MAX_WORKERS} workers...")

    tasks = [(sid, station_index[sid]) for sid in station_ids]

    kept = []
    with multiprocessing.Pool(MAX_WORKERS) as pool:
        for i, (station_id, passed) in enumerate(pool.imap_unordered(check_one, tasks), 1):
            if passed:
                kept.append(station_id)
            if i % 10 == 0 or i == len(tasks):
                logger.info(f"  checked {i}/{len(tasks)} candidates, {len(kept)} pass so far")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(sorted(kept), f)

    elapsed = time.time() - start
    rate = len(station_ids) / elapsed if elapsed else 0.0
    logger.info(f"{len(kept)}/{len(station_ids)} candidate stations pass the hourly-reporting filter -> {OUTPUT_PATH}")
    logger.info(f"Finished in {elapsed:.1f}s ({rate:.1f} stations/s)")


if __name__ == "__main__":
    main()
