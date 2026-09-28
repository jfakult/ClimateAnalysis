#!/usr/bin/env python3
"""One-time data prep: build the global vegetation (NDVI) grids the map and the
Vegetation metric use.

    pip install pillow numpy          # if not already installed
    python3 tools/build_ndvi_grid.py               # 2015-2019, all 12 months (~0.9 GB downloaded)
    python3 tools/build_ndvi_grid.py --years 2018-2019      # smaller/faster
    python3 tools/build_ndvi_grid.py --years 2019 --out-dir /tmp/t   # quick test (12 files)

Source: "Monthly Global NDVI at 5 km based on MODIS and AVHRR products" (OpenGeoHub /
OpenLandMap, CC BY 4.0), https://zenodo.org/records/4305975. Each monthly file is the
90th-percentile NDVI of that month's cloud-screened daily images, outlier-removed and
gap-filled, x10000 as int, -32767 = no data (ocean). About 15 MB per file.

Downloads are CACHED in compute/cache/ndvi_src/ (about 0.9 GB for the default 5 years), so
re-running -- to change --years, retune, or after an interrupted run -- only fetches what
is missing (files are checked against the size Zenodo reports; partial downloads are
discarded). Use --no-keep to delete each file after use instead (low disk space), or
delete the folder when you're done with it.

What it does: builds a 12-month climatology (for each calendar month, the mean over
the chosen years), one calendar month at a time so memory stays modest. Everything is area-averaged to a 0.1 degree
global grid (3600 x 1800, row 0 = 90N, col 0 = 180W; byte 0 = no data/ocean, else
1..255 = NDVI 0..1) and written as:

  compute/cache/ndvi_monthly_0p1.u8   the 12 monthly grids back to back (78 MB raw);
                                      read by step 5 (common/ndvi.py) to score
                                      vegetation from the year's greenness pattern
  data/ndvi.png                       the mean of the 12 months, for the browser layer

Re-running overwrites both. Step 5 (5_build_output.py) picks them up automatically.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

COMPUTE_DIR = Path(__file__).resolve().parent.parent
WEATHER2_DIR = COMPUTE_DIR.parent

ZENODO_RECORD = "https://zenodo.org/api/records/4305975"
NODATA = -32767
COLS, ROWS = 3600, 1800   # 0.1 degree; keep in sync with common/ndvi.py and js/ndvi.js


def parse_range(text):
    a, _, b = text.partition("-")
    return range(int(a), int(b or a) + 1)


def list_files(years, months):
    with urllib.request.urlopen(ZENODO_RECORD, timeout=60) as r:
        record = json.load(r)
    pattern = re.compile(r"^veg_ndvi_avhrr\.mod09ga_p90_5km_s0\.\.0cm_(\d{4})(\d{2})01\.\.\d{8}_v1\.0\.tif$")
    picked = []
    for f in record["files"]:
        m = pattern.match(f["key"])
        if m and int(m.group(1)) in years and int(m.group(2)) in months:
            picked.append((f["key"], f["links"]["self"], f["size"]))
    return sorted(picked)


def fetch_cached(name, url, size, src_dir, attempts=4):
    """Path to the downloaded file, using the cache when the size matches what Zenodo reports."""
    dest = src_dir / name
    if dest.exists() and dest.stat().st_size == size:
        return dest, True
    part = dest.with_suffix(".part")
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(part, "wb") as out:
                while chunk := r.read(1 << 20):
                    out.write(chunk)
            if part.stat().st_size != size:
                raise IOError(f"got {part.stat().st_size} bytes, expected {size}")
            os.replace(part, dest)
            return dest, False
        except Exception as e:   # network hiccup: retry
            if attempt == attempts:
                raise
            print(f"    retry {attempt} after error: {e}")
            time.sleep(3 * attempt)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", default="2015-2019", help="e.g. 2015-2019 or 2019 (default 2015-2019)")
    ap.add_argument("--months", default="1-12", help="e.g. 1-12 or 7 (default all)")
    ap.add_argument("--out-dir", help="write outputs here instead of the repo (for testing)")
    ap.add_argument("--no-keep", action="store_true", help="delete each download after use instead of caching it")
    args = ap.parse_args()

    data_dir = Path(args.out_dir) if args.out_dir else WEATHER2_DIR / "data"
    cache_dir = Path(args.out_dir) if args.out_dir else COMPUTE_DIR / "cache"
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    src_dir = cache_dir / "ndvi_src"
    src_dir.mkdir(parents=True, exist_ok=True)

    files = list_files(parse_range(args.years), parse_range(args.months))
    if not files:
        sys.exit("no matching files in the Zenodo record")
    print(f"{len(files)} monthly files, {sum(f[2] for f in files) / 1e6:.0f} MB to download")

    by_month = {}
    for name, url, size in files:
        by_month.setdefault(int(re.search(r"_(\d{4})(\d{2})01\.\.", name).group(2)), []).append((name, url, size))

    monthly = np.zeros((12, ROWS, COLS), np.uint8)   # 0 = no data, else 1..255 = NDVI 0..1
    done = 0
    for month in sorted(by_month):
        val_sum = np.zeros((ROWS, COLS), np.float32)
        land_sum = np.zeros((ROWS, COLS), np.float32)
        for name, url, size in by_month[month]:
            done += 1
            path, cached = fetch_cached(name, url, size, src_dir)
            print(f"[{done}/{len(files)}] {name} ({size / 1e6:.0f} MB){' [cached]' if cached else ''}", flush=True)
            arr = np.array(Image.open(path))   # int, x10000
            if args.no_keep:
                os.remove(path)
            valid = arr != NODATA
            vals = np.where(valid, np.clip(arr, 0, 10000), 0).astype(np.float32) / 10000.0   # negatives -> 0
            del arr
            # area-average the values (over land only) and the land fraction down to 0.1 degrees
            val_sum += np.array(Image.fromarray(vals).resize((COLS, ROWS), Image.BOX))
            land_sum += np.array(Image.fromarray(valid.astype(np.float32)).resize((COLS, ROWS), Image.BOX))
            del vals, valid
        n = len(by_month[month])
        is_land = land_sum / n > 0.02
        ndvi = np.where(is_land, val_sum / np.maximum(land_sum, 1e-6), 0.0)
        monthly[month - 1] = np.where(is_land, 1 + np.rint(np.clip(ndvi, 0, 1) * 254), 0).astype(np.uint8)

    # A cell that is land in some months but not others (coast/sea-ice edge) takes the mean of its
    # valid months for the missing ones, so every land cell has all 12.
    present = monthly > 0
    counts = present.sum(axis=0)
    mean_enc = np.where(counts > 0, (monthly.astype(np.float32) * present).sum(axis=0) / np.maximum(counts, 1), 0)
    mean_enc = np.rint(mean_enc).astype(np.uint8)
    for m in range(12):
        gap = (~present[m]) & (counts > 0)
        monthly[m][gap] = mean_enc[gap]

    Image.fromarray(mean_enc, mode="L").save(data_dir / "ndvi.png", optimize=True)
    monthly.tofile(cache_dir / "ndvi_monthly_0p1.u8")

    land_cells = mean_enc > 0
    print(f"\nwrote {data_dir / 'ndvi.png'} ({(data_dir / 'ndvi.png').stat().st_size / 1e6:.2f} MB) "
          f"and {cache_dir / 'ndvi_monthly_0p1.u8'} ({monthly.nbytes / 1e6:.0f} MB)")
    print(f"land cells: {land_cells.sum():,}  mean NDVI over land: {((mean_enc[land_cells] - 1) / 254).mean():.3f}")


if __name__ == "__main__":
    main()
