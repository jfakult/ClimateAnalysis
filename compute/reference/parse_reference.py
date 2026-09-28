"""PURE-PYTHON REFERENCE for the file parser in common/isd_fast.c: turns one
raw ISD file into one merged record per (date, hour), in the dict form
{"YYYY-MM-DD": {hour: record}}.

Not part of the pipeline: step 3 runs the C parser. tests/check_isd_fast.py
compares the two on real files. Change a parsing rule here AND in
common/isd_fast.c, then run that test.
"""

from reference import isd_format

REQUIRED_FIELDS = ["temp_c", "dew_c", "wind_mps", "precip_mm", "precip_acc", "wx_wet", "cloud_pct"]

# End-of-day / end-of-month summary records: all-missing values stamped at
# ~07:59 UTC that used to overwrite a real hourly observation every day.
SKIP_REPORT_TYPES = frozenset(("SOD", "SOM", "SOY"))


def merge_hour(records):
    """Collapse the (possibly several) reports that fall in one UTC hour into
    one record. Routine reports outrank SPECI (FM-16) specials; within a class
    the later report wins; a field missing from the winner is filled from the
    next-best report. Rain reported by ANY report makes the hour wet."""
    if len(records) == 1:
        r = records[0]
        return {f: r[f] for f in REQUIRED_FIELDS}

    routine = [r for r in records if r["report_type"] != "FM-16"]
    special = [r for r in records if r["report_type"] == "FM-16"]
    ordered = routine[::-1] + special[::-1]

    out = {}
    for f in ("temp_c", "dew_c", "wind_mps", "cloud_pct"):
        out[f] = next((r[f] for r in ordered if r[f] is not None), None)
    measured = [r["precip_mm"] for r in records if r["precip_mm"] is not None]
    out["precip_mm"] = max(measured) if measured else None
    out["precip_acc"] = next((r["precip_acc"] for r in ordered if r["precip_acc"] is not None), None)
    wx = [r["wx_wet"] for r in records]
    out["wx_wet"] = True if True in wx else (False if False in wx else None)
    return out


def parse_days(raw):
    """raw: decompressed ISD file bytes -> {"YYYY-MM-DD": {hour_int: record}}."""
    slots = {}
    for line in raw.decode("ascii", errors="replace").splitlines():
        record = isd_format.parse_line(line)
        if record is None or record["report_type"] in SKIP_REPORT_TYPES:
            continue
        slots.setdefault((record["date_str"], record["hour"]), []).append(record)

    days = {}
    for (date_str, hour), records in slots.items():
        days.setdefault(date_str, {})[hour] = merge_hour(records)
    return days
