#!/usr/bin/env python3
"""Anomaly inspection: why does a station score the way it does?

Uses the pure-Python reference scorer on purpose (readable, and the yardstick
the C scorer is checked against).

    python3 tools/inspect_station.py 722975-53141 722976-03166

For each station prints one row per year -- observations, reporting interval,
how much of each important criterion actually exists (as a share of expected
observations), how many days were usable, and either the year's metrics or the
reason the year was excluded -- then the station's aggregate, exactly as the
pipeline computes it (it imports the same scoring code).

Reads the parsed cache when it is current; otherwise parses the raw files for
that station on the fly (nothing is written). Read-only, so safe to run any time.
"""

import importlib.util
import json
import pickle
import sys
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMPUTE_DIR))

from common import geo, yeardata  # noqa: E402
from reference import scoring_reference as scoring  # noqa: E402

CACHE_DIR = COMPUTE_DIR / "cache"


def _load_step3():
    spec = importlib.util.spec_from_file_location("step3", COMPUTE_DIR / "3_parse_station_year.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def station_years(step3, station_id, index):
    """{year: {"YYYY-MM-DD": {hour: parsed record}}}, from cache if current."""
    cached = {}
    path = CACHE_DIR / "parsed" / f"{station_id}.pkl"
    if path.exists():
        with open(path, "rb") as f:
            data = pickle.load(f)
        if data.get("parse_version") == step3.PARSE_VERSION and \
                set(step3.REQUIRED_FIELDS).issubset(data.get("fields", [])):
            cached = data["years"]

    years = {}
    for year, rel_path in index.get(station_id, []):
        if year in cached:
            years[year] = yeardata.arrays_to_days(cached[year])   # cache holds flat arrays
        else:
            years[year] = yeardata.arrays_to_days(step3.parse_station_year_arrays(step3.ISD_RAW_DIR / rel_path))
    return years


def inspect(step3, station_id, index):
    print("=" * 118)
    print(station_id)
    header = (f"{'year':>4} {'obs':>6} {'step':>4} | {'temp':>5} {'dew':>5} {'precip':>6} {'cloud':>5} {'wind':>5} "
              f"| {'valid':>5} | result")
    print(header)
    results = []
    monthly_totals = []
    offset = geo.utc_offset_for_station(station_id)
    lat = geo.latitude_for_station(station_id)
    lat_note = f"{lat:.1f} deg" if lat is not None else "unknown -- civil window used unnarrowed"
    print(f"daytime = local solar {scoring.DAY_START_HOUR}:00-{scoring.DAY_END_HOUR}:00 (UTC{offset:+d}h), "
          f"narrowed to actual daylight (latitude {lat_note}); "
          f"percentages are shares of expected DAYTIME observations")
    for year, days in sorted(station_years(step3, station_id, index).items()):
        eff = scoring.effective_days(days, offset, lat)
        stats = scoring.year_stats(eff, year, lat)
        cov = stats["coverage"]
        valid = stats["valid_days"] / stats["days_in_year"]
        reason = scoring.exclusion_reason(stats)
        if reason:
            outcome = f"EXCLUDED: {reason}"
        else:
            result = scoring.score_year(eff, year, lat)
            results.append(result)
            monthly_totals.append(stats["monthly"])
            outcome = (f"score={result['year_score']:.3f} perfect={result['year_perfect_days']:5.1f} "
                       f"indoor={result['year_indoor_days']:5.1f}")
        print(f"{year:>4} {stats['obs']:>6} {stats['step']:>3}h | {cov['temp_c']:>5.0%} {cov['dew_c']:>5.0%} "
              f"{cov['precip_mm']:>6.0%} {cov['cloud_pct']:>5.0%} {cov['wind_mps']:>5.0%} | {valid:>5.0%} | {outcome}")

    agg = scoring.aggregate_station(results, monthly_totals)
    if agg is None:
        print(f"\n=> {len(results)} usable year(s), need {scoring.MIN_VALID_YEARS}: this station would not appear on the map")
    else:
        print("\n=> " + ", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in agg.items()))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    step3 = _load_step3()
    with open(CACHE_DIR / "station_index.json") as f:
        index = json.load(f)
    for station_id in sys.argv[1:]:
        if station_id not in index:
            print(f"{station_id}: not in station_index.json")
            continue
        inspect(step3, station_id, index)


if __name__ == "__main__":
    main()
