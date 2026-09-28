#!/usr/bin/env python3
"""How much do data gaps bias the metrics? (validates the valid-day rules in
common/scoring.py)

    python3 tests/check_gap_rules.py [station_id:utc_offset ...]

Default stations are fully covered US airports. For each one the script
parses 4 years, scores them complete, then re-scores after deleting hours in
three patterns, printing (score, perfect days, indoor days, share of days valid):

  night-closed 16h   no reports 22:00-06:00 local: airports that close
                     overnight. Only daytime is scored, so this should be
                     (almost) identical to "full".
  random 50% / 75%   scattered gaps in the whole day. 50% should be rejected
                     (None); 75% is allowed but perfect days drop noticeably.

Read-only. Takes about a minute on the NAS (reads 12 raw files).
"""

import importlib.util
import json
import random
import sys
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMPUTE_DIR))

from common import scoring, yeardata  # noqa: E402
from reference import scoring_reference  # noqa: E402

spec = importlib.util.spec_from_file_location("step3", COMPUTE_DIR / "3_parse_station_year.py")
step3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(step3)

DEFAULT_STATIONS = ["722976-03166:-8", "724940-23234:-8", "722020-12839:-5"]  # Fullerton, SFO, Miami
YEARS = {2012, 2013, 2014, 2015}   # 4: a station needs >= 3 counted years


def load(index, station_id):
    return {y: yeardata.arrays_to_days(step3.parse_station_year_arrays(step3.ISD_RAW_DIR / p))
            for y, p in index[station_id] if y in YEARS}


def score(years, offset):
    results = []
    for year, days in years.items():
        r = scoring_reference.score_year(scoring_reference.effective_days(days, offset), year)
        if r:
            results.append(r)
    agg = scoring.aggregate_station(results)
    if agg is None:
        return "None (rejected)"
    return (f"score={agg['avg_score']:.3f} perfect={agg['avg_perfect_days']:6.1f} "
            f"indoor={agg['avg_indoor_days']:6.1f} valid_days={agg['coverage']:.0%}")


def mask(years, keep):
    return {y: {d: {h: r for h, r in hours.items() if keep(h)} for d, hours in days.items()}
            for y, days in years.items()}


def main():
    random.seed(0)
    with open(COMPUTE_DIR / "cache" / "station_index.json") as f:
        index = json.load(f)
    for spec_str in sys.argv[1:] or DEFAULT_STATIONS:
        station_id, tz = spec_str.split(":")
        tz = int(tz)
        base = load(index, station_id)
        print(station_id)
        print(f"  {'full':18s} {score(base, tz)}")
        local = lambda h: (h + tz) % 24
        print(f"  {'night-closed 16h':18s} {score(mask(base, lambda h: 6 <= local(h) < 22), tz)}")
        print(f"  {'random 75% kept':18s} {score(mask(base, lambda h: random.random() < 0.75), tz)}")
        print(f"  {'random 50% kept':18s} {score(mask(base, lambda h: random.random() < 0.5), tz)}")


if __name__ == "__main__":
    main()
