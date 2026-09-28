# compute/ -- station scores from raw NOAA ISD data

Reads `isd-raw/<YEAR>/<USAF>-<WBAN>-<YEAR>.gz` (2010-2025) and `isd-history.csv`, and writes what the
map shows: `../data/stations.json` plus one `../data/details/<station>.json` per station.

## Pipeline (`python3 0_run_all.py`, or each step on its own)

| Step | What it does | Cache it writes |
|---|---|---|
| 1 `1_build_station_index.py` | lists every station-year file | `cache/station_index.json` |
| 2 `2_filter_hourly_stations.py` | keeps stations reporting at least ~3-hourly (line counts *estimated* from the gzip trailer + a 64 KB sample) | `cache/hourly_station_ids.json` |
| 3 `3_parse_station_year.py` | C parser: raw lines -> one merged record per hour, stored as flat arrays | `cache/parsed/<id>.pkl` |
| 4 `4_derive_station.py` | C scorer: daytime score / perfect days / indoor days per year, data-coverage rules, 3-year minimum, monthly profile | `cache/derived/<id>.pkl`, `cache/excluded/<id>.pkl` |
| 5 `5_build_output.py` | joins names/countries, writes the JSON the site loads | `../data/` |

Every step is incremental: bump `PARSE_VERSION` (step 3) or `scoring.FORMULA_VERSION` (step 4) when logic
changes and only that layer recomputes. `MAX_STATIONS` in `0_run_all.py` caps step 2's candidates for fast
iteration (fixed-seed random sample, so results are comparable between runs).

## Code layout

- `common/` -- pipeline code. `isd_fast.c/.py` (parser) and `isd_score.c/.py` (scorer) are C libraries built
  on first use with `cc` (needs a C compiler; no Python fallback). `scoring.py` holds every threshold and
  rule constant (the C scorer takes them all from here) plus the aggregation. `yeardata.py` is the array format.
- `reference/` -- plain-Python statements of what the C code does (`isd_format.py`, `parse_reference.py`,
  `scoring_reference.py`). Not used by the pipeline: they are the yardstick for the tests and the inspect tool.
  **A rule change goes in both the C file and its reference**, then run the tests.
- `tests/` -- `check_isd_fast.py` (C parser == reference), `check_isd_score.py` (C scorer == reference),
  `check_gap_rules.py` (how data gaps bias the scores). Each takes optional `N SEED` arguments.
- `tools/` -- `inspect_station.py <id>...` (per-year table for a station), `explain_exclusions.py [reason...]`
  (why stations were dropped).
- `archive/` -- retired files.

Run tests/tools from this directory, e.g. `python3 tests/check_isd_score.py 100`.
Why C, with measurements: `PARSER_ACCELERATION.md`.
