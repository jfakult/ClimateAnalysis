#!/usr/bin/env python3
"""Step 5 (layer 3): assemble data/stations.json from whatever's currently
in the derived cache, joined against isd-history.csv for name/location.

Bare-minimum output per station: id, name, country, lat, lon, and the metrics
(score, perfect/indoor days and their difference, gloom streaks, perfect
streaks). Richer per-station detail is intentionally NOT included here -- a
future on-demand endpoint would read compute/cache/{parsed,derived}/<id>/
directly when a popup asks for more. Regenerated fresh every run, so it
always reflects exactly which stations have been processed so far.

avg_score is the one metric that is NOT purely a property of the station
itself: it's a composite of the other per-station metrics, each normalized
against every other published station (see add_composite_score), so it can
only be computed here, once every station's other numbers are in hand --
never in 4_derive_station.py, and never per-station alone.
"""

import csv
import json
import os
import pickle
import sys
import time
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(COMPUTE_DIR))

from common import ndvi, scoring  # noqa: E402
from common.logging_setup import setup_logging  # noqa: E402

WEATHER2_DIR = COMPUTE_DIR.parent
CACHE_DIR = COMPUTE_DIR / "cache"
HOURLY_STATION_IDS_PATH = CACHE_DIR / "hourly_station_ids.json"
DERIVED_DIR = CACHE_DIR / "derived"
ISD_HISTORY_PATH = COMPUTE_DIR / "isd-history.csv"
COUNTRY_LIST_PATH = COMPUTE_DIR / "country-list.txt"   # NOAA: FIPS code -> country name
OUTPUT_PATH = WEATHER2_DIR / "data" / "stations.json"
DETAILS_DIR = WEATHER2_DIR / "data" / "details"   # one small file per station, fetched by the popup's "Data details"


def load_country_names():
    """{FIPS code: display name} from NOAA's country-list.txt (fixed width:
    2-letter code, then the upper-case name). Missing file -> {} and the map
    falls back to showing the code."""
    names = {}
    if not COUNTRY_LIST_PATH.exists():
        return names
    small = {"And", "Of", "The", "On", "Da", "De"}
    with open(COUNTRY_LIST_PATH) as f:
        for line in f.read().splitlines()[2:]:
            code, name = line[:12].strip(), line[12:].strip()
            if code and name:
                words = name.title().split()
                names[code] = " ".join(w.lower() if i and w in small else w for i, w in enumerate(words))
    return names


def minmax_norm(values):
    """Min-max normalize a list of numbers to [0, 1] (0.5 for everyone if
    they're all equal -- there's nothing to rank them by)."""
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


VEGETATION_WEIGHT = 0.5   # vegetation counts half as much as outside days in the Score


def add_composite_score(stations):
    """avg_score = Outside days + 0.5 x Vegetation - (Indoor OR gloomy days).

    "Indoor or gloomy" is ONE count of days that are either (they overlap, so
    it is the union tallied per day in step 4, never indoor days + gloomy days
    added together). Outside days and that bad-day count are each min-max
    normalized across every published station FIRST (so a station's rank on
    each, not its raw units, is what gets combined); Vegetation is already an
    absolute 0-1 score, so it goes in as-is, scaled by VEGETATION_WEIGHT. The sum is
    then itself min-max normalized across all stations -> a final 0-1 score.
    Needs the whole station list at once, so this runs as a second pass after
    every station's other metrics are known -- unlike them, it isn't a
    property of one station alone, only of how it compares to the rest."""
    if not stations:
        return
    norm_outside = minmax_norm([s["avg_perfect_days"] for s in stations])   # "outside days" in the UI
    norm_bad = minmax_norm([s["avg_bad_days"] for s in stations])
    veg = [s["avg_vegetation"] for s in stations]
    composite = [o + VEGETATION_WEIGHT * v - b for o, v, b in zip(norm_outside, veg, norm_bad)]
    for station, score in zip(stations, minmax_norm(composite)):
        station["avg_score"] = round(score, 4)


def load_manifest():
    manifest = {}
    with open(ISD_HISTORY_PATH, newline="") as f:
        for row in csv.DictReader(f):
            station_id = f"{row['USAF']}-{row['WBAN']}"
            try:
                lat = float(row["LAT"])
                lon = float(row["LON"])
            except ValueError:
                continue
            if lat == 0.0 and lon == 0.0:
                continue  # NOAA's placeholder for "no known location"
            try:
                elevation = float(row["ELEV(M)"])
                # NOAA's missing-elevation placeholder (documented as -999.9,
                # but -999.0 shows up too); no real station sits anywhere
                # near that low, so treat anything down there as "unknown".
                if elevation <= -900:
                    elevation = None
            except ValueError:
                elevation = None
            manifest[station_id] = {
                "name": row["STATION NAME"].strip(),
                "country": row["CTRY"].strip(),
                "lat": lat,
                "lon": lon,
                "elevation": elevation,
            }
    return manifest


def main():
    logger, log_path = setup_logging("5_build_output")
    logger.info(f"Logging to {log_path}")
    start = time.time()

    with open(HOURLY_STATION_IDS_PATH) as f:
        station_ids = json.load(f)

    if ndvi.load() is None:
        logger.warning("NDVI grid not found (run tools/build_ndvi_grid.py): vegetation falls back to the rain estimate for every station")
    manifest = load_manifest()
    country_names = load_country_names()

    stations = []
    detail_files = set()
    missing_manifest = 0
    missing_derived = 0
    for station_id in station_ids:
        derived_file = DERIVED_DIR / f"{station_id}.pkl"
        if not derived_file.exists():
            missing_derived += 1
            continue
        meta = manifest.get(station_id)
        if meta is None:
            missing_manifest += 1
            continue

        with open(derived_file, "rb") as f:
            derived = pickle.load(f)

        # Vegetation: satellite greenness (by month) at the station if we have it, else the rain-based fallback.
        monthly = ndvi.monthly_ndvi_at(meta["lat"], meta["lon"])
        if monthly is not None:
            vegetation, lush_run, station_ndvi = scoring.vegetation_from_monthly_ndvi(monthly, (derived.get("monthly") or {}).get("temp"))
        else:
            vegetation, lush_run, station_ndvi = scoring.vegetation_from_rain(derived["avg_annual_precip_mm"]), None, None

        stations.append({
            "id": station_id,
            "name": meta["name"],
            "country": meta["country"],
            "country_name": country_names.get(meta["country"], meta["country"]),
            "lat": meta["lat"],
            "lon": meta["lon"],
            "elevation": round(meta["elevation"]) if meta["elevation"] is not None else None,
            # avg_score is filled in below, after every station is collected --
            # see add_composite_score.
            "avg_annual_precip_mm": round(derived["avg_annual_precip_mm"]),
            "avg_ndvi": round(station_ndvi, 3) if station_ndvi is not None else None,
            "avg_vegetation": round(vegetation, 3),
            "vegetation_lush_months": lush_run,
            "vegetation_source": "ndvi" if station_ndvi is not None else "rain",
            "avg_perfect_days": round(derived["avg_perfect_days"], 2),
            "avg_gloomy_days": round(derived["avg_gloomy_days"], 2),
            "avg_bad_days": round(derived["avg_bad_days"], 2),
            "avg_indoor_days": round(derived["avg_indoor_days"], 2),
            # Best-minus-worst balance; linear, so the average of the yearly
            # differences equals the difference of the averages.
            "avg_net_days": round(derived["avg_perfect_days"] - derived["avg_indoor_days"], 2),
            # Same idea, weighed against gloom instead of indoor days -- note
            # the units don't actually match (days vs. weighted streak
            # count), it's a deliberately rough "good vs. bad" balance, not a
            # rate of anything.
            "avg_perfect_minus_gloom": round(derived["avg_perfect_days"] - derived["avg_gloom_streaks"], 2),
            "avg_gloom_streaks": round(derived["avg_gloom_streaks"], 2),
            "avg_perfect_streaks": round(derived["avg_perfect_streaks"], 2),
            "perfect_indoor_ratio": round(derived["perfect_indoor_ratio"], 3),
            "seasonal_concentration": (
                round(derived["seasonal_concentration"], 3) if derived["seasonal_concentration"] is not None else None
            ),
            "years_included": derived["years_included"],
            "coverage": round(derived["coverage"], 3),
            "step": derived["step"],
            "samples": derived["samples"],
        })

        if "year_details" in derived:
            DETAILS_DIR.mkdir(parents=True, exist_ok=True)
            with open(DETAILS_DIR / f"{station_id}.json", "w") as f:
                json.dump({
                    "id": station_id,
                    "min_data_fraction": round(scoring.MIN_DATA_FRACTION, 4),
                    "years": derived["year_details"],
                    "monthly": derived.get("monthly"),
                    "gloom_longest_run": derived["gloom_longest_run"],
                    "gloom_avg_length": round(derived["gloom_avg_length"], 1) if derived["gloom_avg_length"] is not None else None,
                    "perfect_streak_longest_run": derived["perfect_streak_longest_run"],
                    "perfect_streak_avg_length": round(derived["perfect_streak_avg_length"], 1) if derived["perfect_streak_avg_length"] is not None else None,
                }, f, separators=(",", ":"))
            detail_files.add(f"{station_id}.json")

    # drop details of stations that are no longer published
    if DETAILS_DIR.exists():
        for stale in set(os.listdir(DETAILS_DIR)) - detail_files:
            os.remove(DETAILS_DIR / stale)

    add_composite_score(stations)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(stations, f)

    elapsed = time.time() - start
    logger.info(f"Wrote {len(stations)} stations -> {OUTPUT_PATH} "
                f"(skipped {missing_derived} without derived data, {missing_manifest} without manifest match)")
    logger.info(f"Finished in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
