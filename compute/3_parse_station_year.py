#!/usr/bin/env python3
"""Step 3 (layer 1, the expensive step): parse each filtered station's raw
.gz files for 2010-2025 into ONE cache/parsed/<station_id>.pkl per station
(all years together): per year, one merged record per (date, hour) as flat
typed arrays (common/yeardata.py). Parsing is done by the C parser
(common/isd_fast.c).

Incremental by design, at per-station-year granularity even though storage
is consolidated per-station: a station's cache is only touched if it's
missing years the station_index says should exist, or missing one of
REQUIRED_FIELDS entirely (which forces a full reparse of every year for that
station, since fields apply to the whole cached file) -- so adding a new raw
field later only reparses stations that actually lack it, and a station that
gains a new year of raw data only parses that one new year, not everything
it already had cached.

Internal cache uses pickle, not JSON, and nothing outside this pipeline reads
these files directly (the browser-facing data/stations.json built in step 5 is
plain JSON).

One file per station instead of one per station-year (was ~1572 small files
for a 108-station run) cuts filesystem overhead (open/mkdir/close/metadata
per file) substantially -- confirmed via profiling that JSON-write plus this
per-file overhead was ~47% of step 3's total time.
"""

import gzip
import json
import multiprocessing
import pickle
import sys
import time
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(COMPUTE_DIR))

from common import isd_fast  # noqa: E402
from common.config import MAX_WORKERS  # noqa: E402
from common.logging_setup import setup_logging  # noqa: E402

ISD_RAW_DIR = COMPUTE_DIR / "isd-raw"
CACHE_DIR = COMPUTE_DIR / "cache"
STATION_INDEX_PATH = CACHE_DIR / "station_index.json"
HOURLY_STATION_IDS_PATH = CACHE_DIR / "hourly_station_ids.json"
PARSED_DIR = CACHE_DIR / "parsed"

REQUIRED_FIELDS = ["temp_c", "dew_c", "wind_mps", "precip_mm", "precip_acc", "wx_wet", "cloud_pct"]

# Bump whenever parsing/merging LOGIC changes in a way that alters the cached
# values without changing the field list -- caches written under another
# version are reparsed in full. (v2: skip SOD/SOM summary records, merge
# duplicate hourly reports, add wx_wet, METAR-text clear-sky/cloud fallback.
# v3: keep 3/6-hour precip accumulations (precip_acc).
# v4: same values as v3, cache rebuilt cleanly after the C accelerator landed.
# v5: present-weather codes below "precipitation" now count as dry evidence
# (wx_wet False); 12/24-hour precip accumulations are kept.
# v6: same values, stored as flat typed arrays per year (common/yeardata.py)
# instead of dicts: no dict building here, near-free unpickling in step 4, and
# directly consumable by the C scorer.)
PARSE_VERSION = 6


def cache_path(station_id):
    return PARSED_DIR / f"{station_id}.pkl"


def load_cache(station_id):
    empty = {"fields": [], "years": {}, "parse_version": None}
    path = cache_path(station_id)
    if not path.exists():
        return empty
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except (pickle.UnpicklingError, EOFError, OSError):
        return empty


def parse_station_year_arrays(gz_path):
    """One station-year file -> the flat arrays stored in the cache."""
    with gzip.open(gz_path, "rb") as f:
        raw = f.read()
    return isd_fast.parse_arrays(raw)


def process_one(args):
    station_id, entries = args  # entries: [[year, rel_path], ...]
    cached = load_cache(station_id)

    fields_ok = (
        set(REQUIRED_FIELDS).issubset(set(cached.get("fields", [])))
        and cached.get("parse_version") == PARSE_VERSION
    )
    expected_years = {year for year, _ in entries}
    cached_years = set(cached.get("years", {}).keys()) if fields_ok else set()
    years_to_parse = [(y, p) for y, p in entries if y not in cached_years]

    if not years_to_parse:
        return station_id, "skipped"

    years = dict(cached.get("years", {})) if fields_ok else {}
    for year, rel_path in years_to_parse:
        years[year] = parse_station_year_arrays(ISD_RAW_DIR / rel_path)

    cache_path(station_id).parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path(station_id), "wb") as f:
        pickle.dump(
            {"fields": REQUIRED_FIELDS, "parse_version": PARSE_VERSION, "years": years},
            f, protocol=pickle.HIGHEST_PROTOCOL,
        )

    return station_id, "parsed"


def main():
    logger, log_path = setup_logging("3_parse_station_year")
    logger.info(f"Logging to {log_path}")
    start = time.time()

    with open(STATION_INDEX_PATH) as f:
        station_index = json.load(f)
    with open(HOURLY_STATION_IDS_PATH) as f:
        station_ids = json.load(f)

    tasks = [(sid, station_index.get(sid, [])) for sid in station_ids]
    total_station_years = sum(len(entries) for _, entries in tasks)
    logger.info(f"{len(tasks)} stations ({total_station_years} station-years) to check using {MAX_WORKERS} workers")

    PARSED_DIR.mkdir(parents=True, exist_ok=True)

    # Builds the accelerator once here, before the workers start.
    isd_fast.load()
    logger.info("Line parser: C (common/isd_fast.c)")

    parsed_count = 0
    skipped_count = 0
    with multiprocessing.Pool(MAX_WORKERS) as pool:
        for i, (station_id, status) in enumerate(pool.imap_unordered(process_one, tasks), 1):
            if status == "parsed":
                parsed_count += 1
            else:
                skipped_count += 1
            if i % 10 == 0 or i == len(tasks):
                logger.info(f"  {i}/{len(tasks)} stations done (parsed={parsed_count}, skipped={skipped_count})")

    elapsed = time.time() - start
    rate = total_station_years / elapsed if elapsed else 0.0
    logger.info(f"Done. Parsed {parsed_count} stations (some/all years), skipped {skipped_count} fully-cached stations.")
    logger.info(f"Finished in {elapsed:.1f}s ({rate:.1f} station-years/s)")


if __name__ == "__main__":
    main()
