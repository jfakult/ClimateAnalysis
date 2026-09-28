"""Flat-array form of one station-year of parsed hours.

The parsed cache used to hold {date: {hour: {field: value}}} dicts: slow to
build in step 3, slow to unpickle in step 4, and impossible to hand to C
without converting. It now holds parallel typed arrays, one entry per merged
(date, hour), sorted by time:

    key         'i'  YYYYMMDDHH (UTC)
    temp, dew, wind, cloud, precip, acc_depth   'd'  (NaN = missing)
    acc_period  'b'  0 = none, else 3/6/12/24
    wet         'b'  0 = unknown, 1 = reported dry, 2 = reported wet

This is exactly what the C parser (isd_fast.c) produces and the C scorer
(isd_score.c) consumes. The dict form ({"YYYY-MM-DD": {hour: record}}) is still
what common/scoring.py's Python reference implementation works on; the two
functions here convert between them.
"""

import array

NAN = float("nan")
DOUBLE_FIELDS = ("temp", "dew", "wind", "cloud", "precip", "acc_depth")
_RECORD_FIELDS = (("temp", "temp_c"), ("dew", "dew_c"), ("wind", "wind_mps"), ("cloud", "cloud_pct"), ("precip", "precip_mm"))
_WET_FROM_CODE = (None, False, True)


def empty():
    arrays = {"key": array.array("i"), "acc_period": array.array("b"), "wet": array.array("b")}
    for name in DOUBLE_FIELDS:
        arrays[name] = array.array("d")
    return arrays


def days_to_arrays(days):
    """{"YYYY-MM-DD": {hour: record}} -> flat arrays, sorted by time."""
    arrays = empty()
    for date_str in sorted(days):
        ymd = int(date_str[0:4]) * 10000 + int(date_str[5:7]) * 100 + int(date_str[8:10])
        hours = days[date_str]
        for hour in sorted(hours):
            r = hours[hour]
            arrays["key"].append(ymd * 100 + hour)
            for name, field in _RECORD_FIELDS:
                v = r[field]
                arrays[name].append(NAN if v is None else v)
            acc = r["precip_acc"]
            arrays["acc_period"].append(acc[0] if acc else 0)
            arrays["acc_depth"].append(acc[1] if acc else NAN)
            wx = r["wx_wet"]
            arrays["wet"].append(0 if wx is None else (2 if wx else 1))
    return arrays


def arrays_to_days(arrays):
    """Flat arrays -> {"YYYY-MM-DD": {hour: record}} (NaN -> None)."""
    days = {}
    day = None
    last_ymd = None
    keys = arrays["key"]
    cols = {name: arrays[name].tolist() for name in DOUBLE_FIELDS}
    periods = arrays["acc_period"].tolist()
    wets = arrays["wet"].tolist()
    for i, key in enumerate(keys):
        ymd, hour = divmod(key, 100)
        if ymd != last_ymd:
            s = str(ymd)
            day = days.setdefault(f"{s[0:4]}-{s[4:6]}-{s[6:8]}", {})
            last_ymd = ymd
        rec = {}
        for name, field in _RECORD_FIELDS:
            v = cols[name][i]
            rec[field] = None if v != v else v
        rec["precip_acc"] = (periods[i], cols["acc_depth"][i]) if periods[i] else None
        rec["wx_wet"] = _WET_FROM_CODE[wets[i]]
        day[hour] = rec
    return days
