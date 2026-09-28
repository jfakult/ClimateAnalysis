#!/usr/bin/env python3
"""Why were these stations dropped? One line per excluded station.

    python3 tools/explain_exclusions.py [REASON ...]

REASON filters by main exclusion reason (temp_c, dew_c, precip_mm, cloud_pct,
wind_mps, "valid days", "few years" = 1-2 years passed but 3 are needed); default is all. Prints station, name, country,
reporting interval, mean per-criterion coverage over its years (share of
expected observations that exist) and the raw hour count that lets you tell
"station never measures this" (0%) from "measures it sometimes" (10-30%).

Uses the parsed cache for the stations in cache/hourly_station_ids.json that
have no derived result. Read-only; run on the NAS (5 workers).
"""

import collections
import csv
import json
import multiprocessing
import os
import pickle
import sys
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMPUTE_DIR))

from common import geo, isd_score, scoring  # noqa: E402
from common.config import MAX_WORKERS  # noqa: E402

CACHE_DIR = COMPUTE_DIR / "cache"


def explain(station_id):
    with open(CACHE_DIR / "parsed" / f"{station_id}.pkl", "rb") as f:
        parsed = pickle.load(f)
    reasons = collections.Counter()
    coverage = collections.defaultdict(list)
    step = None
    offset = geo.utc_offset_for_station(station_id)
    for year, arrays in parsed["years"].items():
        stats = isd_score.year_stats(arrays, year, offset)
        step = stats["step"]
        reason = scoring.exclusion_reason(stats) or "ok"
        reasons["valid days" if "days valid" in reason else reason.split(" present")[0]] += 1
        for k, v in stats["coverage"].items():
            coverage[k].append(v)
    ok_years = reasons.pop("ok", 0)
    if 0 < ok_years < scoring.MIN_VALID_YEARS:
        main = "few years"
    else:
        main = reasons.most_common(1)[0][0] if reasons else "ok"
    mean = {k: round(100 * sum(v) / len(v)) for k, v in coverage.items()}
    return station_id, main, step, len(parsed["years"]), mean


def main():
    wanted = set(sys.argv[1:])
    with open(CACHE_DIR / "hourly_station_ids.json") as f:
        station_ids = json.load(f)
    have = {p[:-4] for p in os.listdir(CACHE_DIR / "derived")}
    excluded = [s for s in station_ids if s not in have]

    info = {}
    with open(COMPUTE_DIR / "isd-history.csv", newline="") as f:
        for row in csv.DictReader(f):
            info[f"{row['USAF']}-{row['WBAN']}"] = (row["STATION NAME"].strip()[:24], row["CTRY"].strip())

    with multiprocessing.Pool(MAX_WORKERS) as pool:
        rows = pool.map(explain, excluded)

    print(f"{len(excluded)} excluded stations")
    for station_id, main_reason, step, years, cov in sorted(rows, key=lambda r: (r[1], r[0])):
        if wanted and main_reason not in wanted:
            continue
        name, country = info.get(station_id, ("?", "?"))
        cov_text = " ".join(f"{k.split('_')[0]}={v}%" for k, v in cov.items())
        print(f"{station_id} {name:24} {country:2} {step}h {years:2}y  {main_reason:11} | {cov_text}")


if __name__ == "__main__":
    main()
