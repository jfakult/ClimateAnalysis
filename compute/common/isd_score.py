"""C scorer for step 4: effective_days + year_stats for one station-year of
flat arrays (common/yeardata.py).

common/isd_score.c does the whole computation. Every threshold and rule
parameter is passed in from common/scoring.py's own constants, so there is one
source of truth for the numbers; the library is built on first use with `cc`
(like the parser, see common/isd_fast.py). There is no Python fallback: if the
library can't be built or loaded, or the input is malformed, this raises.
reference/scoring_reference.py is the plain-Python statement of the same rules;
tests/check_isd_score.py proves the two agree on real data.
"""

import array
import calendar
import ctypes
import os
import subprocess
from pathlib import Path

from common import scoring

_DIR = Path(__file__).resolve().parent
_SRC = _DIR / "isd_score.c"
_LIB = _DIR / "libisd_score.so"

_lib = None
_PARAMS = None
_OUT = (ctypes.c_double * (20 + 12 * 14 + 3))()   # keep in sync with isd_score.c OUT_TOTAL


def _params():
    """Order must match the P_* enum in isd_score.c."""
    s = scoring
    return array.array("d", [
        s.TEMP_LOW_C, s.TEMP_IDEAL_C, s.TEMP_HIGH_C,
        s.DEW_LOW_C, s.DEW_IDEAL_C, s.DEW_HIGH_C,
        s.PRECIP_IDEAL_MM, s.PRECIP_MAX_MM, s.CLOUD_IDEAL_PCT, s.CLOUD_MAX_PCT,
        s.INDOOR_COLD_MAX_C, s.INDOOR_HOT_MIN_C, s.INDOOR_DEWPOINT_MIN_C,
        s.INDOOR_CLOUD_MIN_PCT, s.INDOOR_PRECIP_MIN_MM, s.INDOOR_WIND_MIN_MPS,
        s.TOP_N_HOURS_FOR_DAILY_SCORE, s.MIN_CONSECUTIVE_GOOD_HOURS_FOR_PERFECT_DAY,
        s.DAY_START_HOUR, s.DAY_END_HOUR, s.MIN_DAYTIME_FILL,
        s.WET_UNMEASURED_MM, s.PRECIP_EVIDENCE_MIN_HOURS,
        s.GLOOM_CLOUD_MIN_PCT, s.GLOOM_CLOUD_MIN_HOURS, s.STREAK_MIN_DAYS,
    ])


def _build():
    tmp = _LIB.with_suffix(f".{os.getpid()}.tmp")
    subprocess.run(["cc", "-O2", "-shared", "-fPIC", "-o", str(tmp), str(_SRC), "-lm"], check=True, capture_output=True)
    os.replace(tmp, _LIB)


def load():
    """Build (if needed) and load the library; raises RuntimeError if it can't."""
    global _lib
    if _lib is not None:
        return _lib
    try:
        if not _LIB.exists() or _LIB.stat().st_mtime < _SRC.stat().st_mtime:
            _build()
        try:
            lib = ctypes.CDLL(str(_LIB))
        except OSError:
            _build()
            lib = ctypes.CDLL(str(_LIB))
    except (OSError, subprocess.CalledProcessError, FileNotFoundError) as e:
        detail = (getattr(e, "stderr", b"") or b"").decode(errors="replace")[:300]
        raise RuntimeError(f"cannot build/load the C scorer ({type(e).__name__}: {e} {detail}). "
                           f"It needs a C compiler: install gcc/cc, then rerun.") from e
    lib.isd_year_stats.restype = ctypes.c_long
    lib.isd_year_stats.argtypes = ([ctypes.c_long] + [ctypes.c_void_p] * 9 + [ctypes.c_int, ctypes.c_int, ctypes.c_double]
                                    + [ctypes.c_void_p] * 2)
    _lib = lib
    return lib


def year_stats(arrays, year, utc_offset, lat=None):
    """year_stats for one station-year: the dict of
      days_in_year, step, obs, coverage{criterion: share}, valid_days,
      score_sum, perfect_count, indoor_count, samples,
      gloom_weighted/gloom_longest/gloom_len_sum/gloom_len_count,
      pstreak_weighted/pstreak_longest/pstreak_len_sum/pstreak_len_count,
      monthly[12][14].

    lat: station latitude in degrees, used to narrow the fixed civil daytime
    window (DAY_START_HOUR/DAY_END_HOUR) to actual daylight that day (see
    common/geo.py daylight_bounds); None (unknown) skips that narrowing."""
    global _PARAMS
    lib = load()
    if _PARAMS is None:
        _PARAMS = _params()
    n = len(arrays["key"])
    ptr = lambda name: arrays[name].buffer_info()[0]
    rc = lib.isd_year_stats(n, ptr("key"), ptr("temp"), ptr("dew"), ptr("wind"), ptr("cloud"), ptr("precip"),
                            ptr("acc_depth"), ptr("acc_period"), ptr("wet"), year, utc_offset,
                            float("nan") if lat is None else lat,
                            _PARAMS.buffer_info()[0], ctypes.addressof(_OUT))
    if rc != 0:
        raise ValueError(f"C scorer rejected the {year} data (keys not strictly ascending, or an absurd time span)")
    o = list(_OUT)
    days_in_year = 366 if calendar.isleap(year) else 365
    step = int(o[1])
    # Coverage is intentionally denominated against the fixed civil window,
    # not each day's narrower actual-daylight one -- it's already a rough
    # "how complete is this feed" gauge, and a per-day-accurate denominator
    # would need summing daylight_bounds() over the whole year for little
    # practical gain.
    expected_obs = days_in_year * scoring.DAY_LENGTH_HOURS / step
    return {
        "days_in_year": days_in_year,
        "step": step,
        "obs": int(o[0]),
        "coverage": {k: o[2 + i] / expected_obs for i, k in enumerate(scoring.CRITERIA)},
        "valid_days": int(o[7]),
        "score_sum": o[8],
        "perfect_count": int(o[9]),
        "indoor_count": int(o[10]),
        "samples": int(o[11]),
        "gloom_weighted": o[12],
        "gloom_longest": int(o[13]),
        "gloom_len_sum": o[14],
        "gloom_len_count": int(o[15]),
        "pstreak_weighted": o[16],
        "pstreak_longest": int(o[17]),
        "pstreak_len_sum": o[18],
        "pstreak_len_count": int(o[19]),
        "monthly": [o[20 + 14 * m: 34 + 14 * m] for m in range(12)],
        "precip_mm": o[20 + 12 * 14],
        "gloomy_count": int(o[20 + 12 * 14 + 1]),
        "bad_count": int(o[20 + 12 * 14 + 2]),
    }
