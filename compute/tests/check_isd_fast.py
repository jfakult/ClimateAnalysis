#!/usr/bin/env python3
"""Check the C parser (common/isd_fast.c) against the pure-Python reference
(reference/parse_reference.py) on real files.

    python3 tests/check_isd_fast.py [N_FILES] [SEED]

Parses N random station-years (default 40) both ways, requires identical
output, and prints the speed of each. Read-only.
"""

import gzip
import json
import random
import sys
import time
from pathlib import Path

COMPUTE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMPUTE_DIR))

from common import isd_fast, yeardata  # noqa: E402
from reference import parse_reference  # noqa: E402

ISD_RAW_DIR = COMPUTE_DIR / "isd-raw"


def main():
    n_files = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    random.seed(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    isd_fast.load()

    with open(COMPUTE_DIR / "cache" / "station_index.json") as f:
        index = json.load(f)
    picks = [random.choice(index[s]) for s in random.sample(sorted(index), n_files)]

    t_py = t_c = 0.0
    lines = bad = 0
    for year, rel in picks:
        with gzip.open(ISD_RAW_DIR / rel, "rb") as f:
            raw = f.read()
        lines += raw.count(b"\n")
        t = time.time(); expected = parse_reference.parse_days(raw); t_py += time.time() - t
        t = time.time(); got = yeardata.arrays_to_days(isd_fast.parse_arrays(raw)); t_c += time.time() - t
        if got != expected:
            bad += 1
            print("MISMATCH", rel)
    print(f"{n_files} files, {lines:,} lines, {bad} mismatches")
    print(f"pure Python {t_py:.2f}s   C {t_c:.2f}s   -> {t_py / t_c:.1f}x faster (parse+merge, incl. building dicts; files already in memory)")


if __name__ == "__main__":
    main()
