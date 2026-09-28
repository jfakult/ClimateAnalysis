#!/usr/bin/env python3
"""Check the C scorer against the Python reference on real station-years.

    python3 tests/check_isd_score.py [N_STATION_YEARS] [SEED]

Parses N random station-years (default 40), scores each with the C scorer
(common/isd_score.c) and with the Python reference (reference/scoring_reference.py), and
requires identical counts/coverage/monthly values (scores to 1e-9). Prints the
speed of each. Read-only.
"""

import importlib.util
import json
import random
import sys
import time
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMPUTE_DIR))

from common import geo, isd_score, scoring, yeardata  # noqa: E402
from reference import scoring_reference  # noqa: E402

spec = importlib.util.spec_from_file_location("step3", COMPUTE_DIR / "3_parse_station_year.py")
step3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step3)


def same(a, b):
    for k in ("step", "obs", "valid_days", "perfect_count", "indoor_count", "gloomy_count", "bad_count", "samples",
              "gloom_weighted", "gloom_longest", "gloom_len_sum", "gloom_len_count",
              "pstreak_weighted", "pstreak_longest", "pstreak_len_sum", "pstreak_len_count"):
        if a[k] != b[k]:
            return f"{k}: python {a[k]} vs C {b[k]}"
    for k in scoring.CRITERIA:
        if abs(a["coverage"][k] - b["coverage"][k]) > 1e-9:
            return f"coverage {k}"
    if abs(a["precip_mm"] - b["precip_mm"]) > 1e-6:
        return "precip_mm"
    if abs(a["score_sum"] - b["score_sum"]) > 1e-9:
        return "score_sum"
    for ra, rb in zip(a["monthly"], b["monthly"]):
        if any(abs(x - y) > 1e-6 for x, y in zip(ra, rb)):
            return "monthly"
    return None


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    random.seed(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    isd_score.load()

    with open(COMPUTE_DIR / "cache" / "station_index.json") as f:
        index = json.load(f)
    picks = []
    for sid in random.sample(sorted(index), n):
        year, rel = random.choice(index[sid])
        picks.append((sid, year, rel))

    t_py = t_c = 0.0
    bad = 0
    for sid, year, rel in picks:
        arrays = step3.parse_station_year_arrays(step3.ISD_RAW_DIR / rel)
        offset = geo.utc_offset_for_station(sid)
        lat = geo.latitude_for_station(sid)
        t = time.time()
        days = yeardata.arrays_to_days(arrays)
        expected = scoring_reference.year_stats(scoring_reference.effective_days(days, offset, lat), year, lat)
        t_py += time.time() - t
        t = time.time()
        got = isd_score.year_stats(arrays, year, offset, lat)
        t_c += time.time() - t
        problem = same(expected, got)
        if problem:
            bad += 1
            print("MISMATCH", sid, year, problem)
    print(f"{n} station-years, {bad} mismatches")
    print(f"Python (incl. array->dict conversion) {t_py:.2f}s   C {t_c:.3f}s   -> {t_py / max(t_c, 1e-9):.0f}x faster")


if __name__ == "__main__":
    main()
