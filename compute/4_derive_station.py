#!/usr/bin/env python3
"""Step 4 (layer 2, cheap): compute each station's annual/averaged metrics
from the parsed cache only -- never touches the raw .gz files again.

Re-run is cheap and safe: a station is only recomputed if it has no current
record, its formula_version is stale (bump scoring.FORMULA_VERSION whenever a
scoring rule changes), or its parsed file changed (size/mtime, e.g. a new year
finished parsing). "Current" is decided from the record alone -- the parsed
file is not even opened -- and stations that fail the data rules get a small
marker in cache/excluded/ so they are skipped too.

Scoring runs in C (common/isd_score.c, ~60x faster than the plain-Python
reference in reference/scoring_reference.py, identical results -- see
tests/check_isd_score.py).

Reads the per-station pickle cache written by step 3 (one file per station,
all years together) and writes its own output as pickle too -- internal-only
cache, never touched by the browser, so JSON's text-encoding cost buys
nothing here.
"""

import collections
import json
import multiprocessing
import os
import pickle
import sys
import time
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(COMPUTE_DIR))

from common import geo, isd_score, scoring  # noqa: E402
from common.config import MAX_WORKERS  # noqa: E402
from common.logging_setup import setup_logging  # noqa: E402

CACHE_DIR = COMPUTE_DIR / "cache"
HOURLY_STATION_IDS_PATH = CACHE_DIR / "hourly_station_ids.json"
PARSED_DIR = CACHE_DIR / "parsed"
DERIVED_DIR = CACHE_DIR / "derived"
EXCLUDED_DIR = CACHE_DIR / "excluded"   # tiny "this station failed the data rules" markers


def parsed_path(station_id):
    return PARSED_DIR / f"{station_id}.pkl"


def derived_path(station_id):
    return DERIVED_DIR / f"{station_id}.pkl"


def load_parsed(station_id):
    path = parsed_path(station_id)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except (pickle.UnpicklingError, EOFError, OSError):
        return None


def excluded_path(station_id):
    return EXCLUDED_DIR / f"{station_id}.pkl"


def file_signature(path):
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def _load_small(path):
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except (FileNotFoundError, pickle.UnpicklingError, EOFError, OSError):
        return None


def cached_outcome(station_id, signature):
    """(status, detail) if a current derived result or exclusion marker exists
    for this exact parsed file, else None."""
    derived = _load_small(derived_path(station_id))
    if derived and derived.get("formula_version") == scoring.FORMULA_VERSION and derived.get("parsed_sig") == signature:
        return "skipped", None
    marker = _load_small(excluded_path(station_id))
    if marker and marker.get("formula_version") == scoring.FORMULA_VERSION and marker.get("parsed_sig") == signature:
        return "no-valid-years", marker.get("reason")
    return None


def drop_stale_derived(station_id):
    """A station that no longer passes the data-sufficiency rules must not
    keep publishing an older derived result."""
    try:
        derived_path(station_id).unlink()
    except FileNotFoundError:
        pass


def mark_excluded(station_id, signature, reason):
    drop_stale_derived(station_id)
    EXCLUDED_DIR.mkdir(parents=True, exist_ok=True)
    with open(excluded_path(station_id), "wb") as f:
        pickle.dump({"formula_version": scoring.FORMULA_VERSION, "parsed_sig": signature, "reason": reason}, f)
    return station_id, "no-valid-years", reason


def summarize_reasons(reasons):
    """Most common reason among per-year reasons (criterion names or
    "too few valid days"), for the run summary."""
    return reasons.most_common(1)[0][0] if reasons else "no data"


def year_detail(year, stats, excluded, result):
    """One row of the per-year breakdown shown in the map's "Data details"
    panel: what the station reported that year and whether/why it counted."""
    row = {
        "year": year,
        "obs": stats["obs"],
        "step": stats["step"],
        "coverage": {k: round(v, 3) for k, v in stats["coverage"].items()},
        "valid_days": round(stats["valid_days"] / stats["days_in_year"], 3),
    }
    if excluded:
        row["excluded"] = {**excluded, "value": round(excluded["value"], 3)}
    else:
        row["score"] = round(result["year_score"], 3)
        row["perfect"] = round(result["year_perfect_days"], 1)
        row["indoor"] = round(result["year_indoor_days"], 1)
        row["gloom"] = round(result["year_gloom_streaks"], 1)
        row["pstreak"] = round(result["year_perfect_streaks"], 1)
    return row


def process_one(station_id):
    """Returns (station_id, status, detail); detail is the exclusion reason
    for "no-valid-years", else None."""
    path = parsed_path(station_id)
    try:
        signature = file_signature(path)
    except FileNotFoundError:
        drop_stale_derived(station_id)
        return station_id, "no-valid-years", "no parsed data"

    hit = cached_outcome(station_id, signature)
    if hit is not None:
        return (station_id, *hit)

    parsed = load_parsed(station_id)
    if parsed is None:
        return station_id, "no-valid-years", "unreadable parsed cache (rerun step 3)"
    years = sorted(parsed["years"].keys())
    if years and "key" not in parsed["years"][years[0]]:
        return station_id, "no-valid-years", "outdated parsed cache (rerun step 3)"

    parse_version = parsed.get("parse_version")
    utc_offset = geo.utc_offset_for_station(station_id)
    lat = geo.latitude_for_station(station_id)

    year_results = []
    year_details = []
    monthly_totals = []
    reasons = collections.Counter()
    for year in years:
        stats = isd_score.year_stats(parsed["years"][year], year, utc_offset, lat)
        excluded = scoring.exclusion_detail(stats)
        result = None if excluded else scoring.score_from_stats(stats)
        if result is not None:
            year_results.append(result)
            monthly_totals.append(stats["monthly"])
        else:
            reasons[excluded["criterion"] if excluded["kind"] == "coverage" else "too few valid days"] += 1
        year_details.append(year_detail(year, stats, excluded, result))

    aggregated = scoring.aggregate_station(year_results, monthly_totals)
    if aggregated is None:
        reason = "fewer than 3 valid years" if year_results else summarize_reasons(reasons)
        return mark_excluded(station_id, signature, reason)

    out = {
        "formula_version": scoring.FORMULA_VERSION,
        "parse_version": parse_version,
        "parsed_years": years,
        "parsed_sig": signature,
        "year_details": year_details,
        "monthly": scoring.monthly_profile(monthly_totals),
        **aggregated,
    }
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    with open(derived_path(station_id), "wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    try:
        excluded_path(station_id).unlink()   # it qualifies now
    except FileNotFoundError:
        pass

    return station_id, "derived", None


def main():
    logger, log_path = setup_logging("4_derive_station")
    logger.info(f"Logging to {log_path}")
    start = time.time()

    with open(HOURLY_STATION_IDS_PATH) as f:
        station_ids = json.load(f)
    isd_score.load()
    logger.info("Scorer: C (common/isd_score.c)")
    logger.info(f"Deriving metrics for {len(station_ids)} stations using {MAX_WORKERS} workers...")

    counts = {"derived": 0, "skipped": 0, "no-valid-years": 0}
    excluded_because = collections.Counter()
    with multiprocessing.Pool(MAX_WORKERS) as pool:
        for i, (station_id, status, detail) in enumerate(pool.imap_unordered(process_one, station_ids), 1):
            counts[status] += 1
            if detail:
                excluded_because[detail] += 1
            if i % 25 == 0 or i == len(station_ids):
                logger.info(f"  {i}/{len(station_ids)} done {counts}")

    elapsed = time.time() - start
    rate = len(station_ids) / elapsed if elapsed else 0.0
    logger.info(f"Done. {counts}")
    if excluded_because:
        logger.info("No-valid-years, by main reason: "
                    + ", ".join(f"{k}: {v}" for k, v in excluded_because.most_common()))
    logger.info(f"Finished in {elapsed:.1f}s ({rate:.1f} stations/s)")


if __name__ == "__main__":
    main()
