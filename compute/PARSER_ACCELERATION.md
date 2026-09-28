# Why step 3 has a C line parser

Step 3 (`3_parse_station_year.py`) turns ~100 GB of raw ISD text into per-station
caches. It is the only expensive step in the pipeline, and it was CPU-bound in
Python. `common/isd_fast.c` (loaded by `common/isd_fast.py`) now does the
per-line parsing and per-hour merging in C. The plain-Python parser
(`reference/isd_format.py` + `reference/parse_reference.py`) is kept only as the
reference the tests compare against; the pipeline has no Python fallback.

## Measured result (real run, same 64 stations / 838 station-years, fresh cache, 5 workers)

| Parser | Wall time | Station-years/s |
|---|---|---|
| Pure Python (at the time, via a since-removed switch) | 22.3 s | 37.6 |
| C accelerator | 6.5 s | 129.4 |

**3.4x end to end.** Source: `logs/20260921_145550_3_parse_station_year.log`
(Python) and `logs/20260921_145544_3_parse_station_year.log` (C).

The parse-and-merge stage alone is faster than that (4.6-7.8x in my
in-memory benchmarks below); the end-to-end figure is lower because
decompression, pickling and disk I/O are unchanged (Amdahl's law).

## Where the time went (per-line cost, typical hourly stations, 30 station-years, 446,684 lines)

| Stage | us/line | Share |
|---|---|---|
| gunzip | 0.43 | 9% |
| decode + splitlines | 0.32 | 7% |
| group by hour | 0.35 | 7% |
| `parse_line` (regex extraction of temp/dew/wind/precip/cloud/weather + METAR text) | 3.47 | 67% |
| `merge_hour` | 0.60 | 12% |

Two thirds of the time was Python-level work repeated for every line, and most
files have 9-15k lines (more for stations reporting several times an hour,
5-minute stations ~100k). That is inherently per-line interpreter overhead,
which is why it needed to leave Python rather than be tuned within it.

## What was tried first, and why it was not enough

| Attempt | Result |
|---|---|
| Pickle + one file per station + avoid `datetime` objects (earlier work) | Step 3: 62.7 s -> 32.4 s on the 108-station set. Kept. |
| Bulk-read files | No effect on the NAS (my faster reads were a sandbox NFS artifact). |
| PyPy | Fresh run 38.6 s vs 32.4 s on CPython: slower here. An earlier "3.7x" figure was a warm-cache artifact. Removed. |
| Pure-Python micro-optimizations of `parse_line` (gate regexes with substring checks, one regex search instead of splitting METAR text into tokens) | 1.34-1.43x on `parse_line`, byte-identical output. Kept; it is the floor for Python. |
| Skip most sub-hourly reports (cap of 3 parsed per hour) | Rejected. On a dense station (063400, 2018) it changed `precip_mm` in 2,190 of 8,746 hours, 3/6-hour accumulations in 323 and rain flags in 228, and moved indoor days from 56 to 49. Precipitation is cumulative, so dropped reports lose events. Only ~19 of 554 filtered stations are dense, so it also barely helped overall. |
| numpy over the whole file | Not built. Each pattern needs its own full pass over the bytes, so it would cost about as much as the per-line Python it replaces. |

## Why C

- It removes the per-line interpreter overhead, which was ~80% of the cost
  (parse + merge), so all reports are parsed and nothing has to be skipped.
  That keeps full fidelity for cumulative quantities (precipitation), the
  concern with the report cap.
- No new runtime dependency beyond the standard library: `ctypes` loads a
  ~20 KB shared object. It needs only the oldest glibc symbols (2.2.5:
  `malloc`, `free`, `memchr`, `qsort`), so it loads on essentially any x86-64
  Linux, and `isd_fast.py` rebuilds it with `cc` if loading fails.
- Zero behaviour change: output is identical to the Python parser.
- Simple failure mode: the library is built on first use with `cc`; if it can't
  be built or loaded, or a file has a non-numeric value where a number is
  required, the step stops with a clear error instead of silently doing
  something slower.

## Correctness evidence

- The C parser compared with the Python reference on ~1.9M lines across ~150
  random station-years (including 5-minute and mixed METAR/SYNOP stations and
  stations outside the filtered set): 0 mismatches, 0 fallbacks.
- Step 3's `process_one` run both ways over 12 stations x 6 years: the written
  pickles compared equal.
- Reproduce on any machine: `python3 tests/check_isd_fast.py [N_FILES] [SEED]`
  parses random station-years both ways, requires identical output, and prints
  timings.

## Costs and trade-offs

- Two implementations of the same logic. Any change to
  `reference/isd_format.parse_line` or `reference/parse_reference.merge_hour`
  semantics must be mirrored in `common/isd_fast.c`, then
  `tests/check_isd_fast.py` must pass and `PARSE_VERSION` bumped.
- Needs `cc` to build if the prebuilt `.so` is missing or does not load.
- Output stays as Python dicts (one per hour), so building them is now a
  large share of what remains; the next speedups (unmeasured) would be
  decompression, pickling, and a more compact cache format.

## Logging

- Step 3 logs `Line parser: C (common/isd_fast.c)` when it starts.

---

# Step 4 scorer in C (added later)

Same idea applied to step 4 (`4_derive_station.py`). Profile of the Python path
on two real stations (9 and 15 years, 58k and 111k hours): year scoring
(`year_stats`) ~45-50% of the CPU, precipitation inference (`effective_days`)
~12-15%, the early coverage pre-check ~12-15%, unpickling the parsed cache
~12-15%; about half of the scoring time was the same per-hour factor scores
being computed twice.

What changed, and why each part:

1. **Factor scores once per hour**: plain-Python at the time, same results (40
   random synthetic station-years compared against the previous
   implementation: identical counts/coverage/monthly, scores within 1e-13).
   Superseded by step 3 below, which moved the whole computation to C.
2. **Skip-unpickle cache.** Derived results record the parsed file's size and
   mtime; a station whose record is current is skipped without opening its
   parsed file. Stations that fail the data rules get a small marker in
   `cache/excluded/`, so they are skipped too.
3. **C scorer** (`common/isd_score.c` + `isd_score.py`): `effective_days` +
   `year_stats` for one station-year. Marshalling Python dicts into C would
   have cost as much as the scoring itself, so step 3 now stores each year as
   flat typed arrays (`common/yeardata.py`, `PARSE_VERSION` 6): no dict building
   in step 3 either, near-free unpickling in step 4, and the arrays go to C as
   they are. Every threshold is passed in from `scoring.py`'s own constants, so
   only the logic exists twice.

Evidence (all on this machine; run `tests/check_isd_score.py` on the NAS):

- 120 random synthetic station-years (offsets -12..+12, hourly/2h/3h,
  gaps, accumulations, missing cloud/precip): C == Python on every count,
  coverage and monthly value; scores within 3e-14.
- 59 real station-years: 0 mismatches; the scoring stage ~60x faster
  (includes the array->dict conversion the Python path needs).
- Fullerton (16 hourly years), step 3 -> step 4 through the real functions:
  C result equals the pure-Python reference computed directly from the raw
  files (score 0.6716, 122.16 perfect days, 21.21 indoor days). Step 4 for that
  station took 0.11 s; the earlier profile of the Python path was 0.6-0.9 s per
  station of this size.

Costs: the scoring rules exist twice (`reference/scoring_reference.py` + C);
any rule change must be mirrored in `isd_score.c`, then
`tests/check_isd_score.py` must report 0 mismatches. There is no Python
fallback in the pipeline (the early-exit pre-check that only served the Python
path was removed with it).
