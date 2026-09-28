"""Scoring rules for the three station-level metrics: score, perfect_days,
indoor_days -- the CONSTANTS (every threshold and rule parameter), plus the
aggregation that turns per-year results into a station's numbers.

The per-hour/per-day/per-year computation itself runs in C
(common/isd_score.c, called through common/isd_score.py) and takes all of its
parameters from this module, so there is one source of truth for every number.
reference/scoring_reference.py is the plain-Python statement of the same logic,
used to verify the C code (tests/check_isd_score.py) and to explain a station
(tools/inspect_station.py).

Bumping FORMULA_VERSION invalidates the derived cache (compute/cache/derived)
so a scoring-rule change gets picked up without ever re-touching the raw
.gz files or the parsed cache.
"""

import collections

# v2: precip/cloud interpretation (effective_days), valid-day rule, year
# filtering at <1/3 data, and perfect/indoor tallies scaled to 365 days.
# v3: cadence-aware rules for stations that report every 2-6 hours, and
# 3/6-hour precip accumulations spread over the slots they cover.
# v9: gloom streaks (consecutive overcast/indoor days) and perfect streaks
# (consecutive perfect days).
# v10: perfect/indoor ratio and seasonal concentration of perfect days
# (station-level aggregates only; no change to per-year/C computation).
# v11: seasonal_concentration suppressed (None) below
# SEASONAL_CONCENTRATION_MIN_PERFECT_DAYS -- too few perfect days makes the
# month-to-month spread mostly noise, not a real seasonal pattern.
# v12: the daytime window (DAY_START_HOUR/DAY_END_HOUR) is now narrowed, per
# day, to actual sunrise-sunset at the station's latitude (see
# common/geo.py daylight_bounds) -- a "perfect" reading before sunrise or
# after sunset no longer counts, since nobody's outside enjoying it.
# v13: vegetation (annual-rainfall sweet spot) from a daytime-scaled rain estimate.
# v14: gloomy days are cloud-only (no longer include indoor days); new per-year tallies
# of gloomy days and of indoor-OR-gloomy days (union). "Gloom streaks" became "indoor streaks":
# same runs (indoor OR gloomy days), renamed.
# v15: dew point lower bound 40F -> 30F. (Vegetation moved to step 5: NDVI grid, rain fallback.)
FORMULA_VERSION = 15

# Written for hourly data; `step` (the station-year's dominant hours between
# observations) scales them so a 3-hourly station is judged on the same
# amount of TIME, not the same number of observations:
#   daily score   = mean of the best ceil(6/step) observations   (6 h of the day)
#   perfect day   = a run of good observations covering >= 4 h, but never
#                   fewer than 2 observations once step > 1 (so 3-hourly
#                   stations need two consecutive good reports, ~6 h)
TOP_N_HOURS_FOR_DAILY_SCORE = 6
MIN_CONSECUTIVE_GOOD_HOURS_FOR_PERFECT_DAY = 4

# --- data sufficiency / normalization ---

# The important criteria; a year where ANY of these exists for under this
# share of its hours (or fewer than this share of its days are valid) is
# filtered out for now.
CRITERIA = ("temp_c", "dew_c", "precip_mm", "cloud_pct", "wind_mps")
MIN_DATA_FRACTION = 1.0 / 3.0

# Only DAYTIME is scored. Everything below (daily score, perfect day, indoor
# day, and the data-coverage tests) looks only at observations whose LOCAL
# SOLAR hour is in [DAY_START_HOUR, DAY_END_HOUR); nights, and gaps at night,
# have no effect. Days are local solar days (see common/geo.py), so a daytime
# window is never split by the UTC date change.
DAY_START_HOUR = 7
DAY_END_HOUR = 21
DAY_LENGTH_HOURS = DAY_END_HOUR - DAY_START_HOUR

# A day is "valid" (evaluable for every metric) when its complete daytime
# observations (temp, dew point, precip AND cloud all available) cover at least
# this share of the daytime window: complete_obs * step >= fill * DAY_LENGTH.
# Measured on fully covered stations with hours randomly removed: at 75% random
# gaps the perfect-day count already drops ~30% (a 4-hour run is easily broken),
# so scattered gaps must not be tolerated much below that.
MIN_DAYTIME_FILL = 0.7

# A station is only published if at least this many years pass the data tests;
# with fewer, the "average" is a single lucky/unlucky year.
MIN_VALID_YEARS = 3

# Perfect/indoor day tallies are scaled from "valid days observed" up to a
# full year so stations with gaps stay comparable with complete ones.
TARGET_DAYS_PER_YEAR = 365

# Precip inference (see effective_days).
WET_UNMEASURED_MM = 0.5            # amount assumed for a wet hour with no measured depth
PRECIP_EVIDENCE_MIN_HOURS = 12     # hours of precip evidence needed before "no rain reported" means dry


def _f_to_c(f):
    return (f - 32) * 5.0 / 9.0


def _mph_to_mps(mph):
    return mph * 0.44704


def _in_to_mm(inches):
    return inches * 25.4


# --- per-factor score thresholds (converted once, at import time) ---

TEMP_LOW_C, TEMP_IDEAL_C, TEMP_HIGH_C = _f_to_c(65), _f_to_c(75), _f_to_c(82)
DEW_LOW_C, DEW_IDEAL_C, DEW_HIGH_C = _f_to_c(30), _f_to_c(50), _f_to_c(60)
PRECIP_IDEAL_MM, PRECIP_MAX_MM = 0.0, 0.1
CLOUD_IDEAL_PCT, CLOUD_MAX_PCT = 0.0, 50.0

# --- indoor-day thresholds ---

INDOOR_COLD_MAX_C = _f_to_c(20)          # high temp BELOW this -> too cold
INDOOR_HOT_MIN_C = _f_to_c(95)           # high temp ABOVE this -> too hot
INDOOR_DEWPOINT_MIN_C = _f_to_c(68)      # dew point ABOVE this -> muggy
INDOOR_CLOUD_MIN_PCT = 85.0              # cloud cover ABOVE this ...
INDOOR_PRECIP_MIN_MM = _in_to_mm(0.2)    # ... AND precip ABOVE this -> overcast+wet
INDOOR_WIND_MIN_MPS = _mph_to_mps(25)    # wind speed ABOVE this -> too windy

# --- gloom / perfect streaks: consecutive-day runs ---

# A day is "gloomy" if at least this many of its daytime hours are more than
# this cloudy -- clouds only, independent of the indoor overcast+wet condition
# (INDOOR_CLOUD_MIN_PCT), which also needs rain. Gloomy and indoor days overlap;
# the "indoor streaks" run over days that are indoor OR gloomy, and the Score
# uses the count of that union (year_bad_days), never indoor + gloomy summed.
GLOOM_CLOUD_MIN_PCT = 80.0
GLOOM_CLOUD_MIN_HOURS = 6

# A run shorter than this isn't a "streak" (gloom or perfect days alike). A
# day with too little data to judge (not a valid day) breaks a run in
# progress -- we don't assume it continued a streak we can't see. Weighted 1
# per 3 days (rounded up), so a 10-day streak counts more than three separate
# 3-day ones: 3d->1, 4-6d->2, 7-9d->3, ...
STREAK_MIN_DAYS = 3


def streak_weight(streak_len):
    """How much a streak_len-day run counts toward its yearly tally: 0 below
    STREAK_MIN_DAYS, else ceil(streak_len / 3)."""
    if streak_len < STREAK_MIN_DAYS:
        return 0
    return (streak_len + 2) // 3


def _sum_monthly(monthly_totals):
    """Sum year_stats()["monthly"] (one 12x14 table per counted year) into a
    single 12x14 table, element-wise, across every counted year."""
    total = [[0.0] * 14 for _ in range(12)]
    for monthly in monthly_totals:
        for i, row in enumerate(monthly):
            for j, v in enumerate(row):
                total[i][j] += v
    return total


# --- vegetation: how green the land is, and for how much of the year ---
#
# Primary source: satellite NDVI by calendar month (see common/ndvi.py and
# tools/build_ndvi_grid.py). Two steps:
#
# 1. A plain ramp on the mean of the 12 months: bare ground (NDVI <= 0.10) scores 0,
#    dense green (>= 0.70) scores 1. Dense rainforest is genuinely the greenest, so
#    there is no "too wet" penalty. One lush month among 11 dead ones averages low.
# 2. A seasonal relief, because this is about how much a person would want to be in
#    that landscape through a year. Take the longest run of consecutive "lush" months
#    (NDVI >= VEG_LUSH_NDVI, wrapping across New Year). The plain average is the full
#    punishment; the relief shrinks that punishment (1 - score) by up to
#    VEG_SEASON_RELIEF, in a tent shape: none at <= 3 lush months in a row, the most at
#    6 (a real green season and a real off season), back to none at >= 9 (already
#    mostly green, or the average already says so).
VEG_NDVI_ZERO = 0.10
VEG_NDVI_FULL = 0.70
VEG_LUSH_NDVI = 0.45
VEG_SEASON_RELIEF = 0.5        # max share of the punishment removed (at a 6-month lush run)
VEG_RUN_MIN, VEG_RUN_PEAK, VEG_RUN_MAX = 3, 6, 9
# The satellite product is gap-filled, and under winter snow it fills in green values
# (e.g. Yakutsk reads ~0.6 in January), so a month whose station daytime-mean
# temperature is below this is treated as dormant (NDVI 0) whatever the satellite says.
VEG_MIN_TEMP_C = 5.0

# Fallback for stations the NDVI grid can't place (or if the grid hasn't been
# built): a saturating rain ramp -- vegetation rises with annual rain and then
# plateaus rather than falling (Holdridge: ~125 mm is the desert boundary, ~1000
# mm is moist forest). Rough: our rain figure is daytime-scaled and may undercount snow.
VEG_RAIN_ZERO_MM = 125.0
VEG_RAIN_FULL_MM = 1000.0


def longest_lush_run(monthly_ndvi):
    """Longest run of consecutive months with NDVI >= VEG_LUSH_NDVI, wrapping
    across December -> January (12 if every month is lush)."""
    lush = [v >= VEG_LUSH_NDVI for v in monthly_ndvi]
    if all(lush):
        return 12
    best = run = 0
    for flag in lush + lush:   # doubled so a run can wrap the year end; not all lush, so no run exceeds 11
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def season_relief(run):
    """0-1 tent: 0 at <= VEG_RUN_MIN and >= VEG_RUN_MAX lush months in a row, 1 at VEG_RUN_PEAK."""
    if run <= VEG_RUN_MIN or run >= VEG_RUN_MAX:
        return 0.0
    if run <= VEG_RUN_PEAK:
        return (run - VEG_RUN_MIN) / (VEG_RUN_PEAK - VEG_RUN_MIN)
    return (VEG_RUN_MAX - run) / (VEG_RUN_MAX - VEG_RUN_PEAK)


def vegetation_from_monthly_ndvi(monthly_ndvi, monthly_temp_c=None):
    """(score 0-1, longest lush run in months, mean NDVI) from 12 monthly NDVI values.
    monthly_temp_c (12 daytime-mean temperatures, None where unknown) marks months
    colder than VEG_MIN_TEMP_C as dormant."""
    if monthly_temp_c:
        monthly_ndvi = [0.0 if t is not None and t < VEG_MIN_TEMP_C else v for v, t in zip(monthly_ndvi, monthly_temp_c)]
    mean = sum(monthly_ndvi) / len(monthly_ndvi)
    base = max(0.0, min(1.0, (mean - VEG_NDVI_ZERO) / (VEG_NDVI_FULL - VEG_NDVI_ZERO)))
    run = longest_lush_run(monthly_ndvi)
    score = 1.0 - (1.0 - base) * (1.0 - VEG_SEASON_RELIEF * season_relief(run))
    return score, run, mean


def vegetation_from_rain(annual_precip_mm):
    """0-1 saturating ramp on annual rain (fallback only)."""
    return max(0.0, min(1.0, (annual_precip_mm - VEG_RAIN_ZERO_MM) / (VEG_RAIN_FULL_MM - VEG_RAIN_ZERO_MM)))


def monthly_profile(monthly_totals):
    """Per-month values for the details charts (None where a month has no
    valid day): daytime mean temp/dew (C), cloud (%), wind (m/s); share of
    daytime hours with measurable rain (%); share of valid days that were
    perfect / indoor days (%)."""
    total = _sum_monthly(monthly_totals)

    def mean(row, slot):
        return round(row[slot] / row[slot + 1], 2) if row[slot + 1] else None

    profile = {k: [] for k in ("temp", "dew", "cloud", "wind", "rain", "perfect", "indoor", "gloom")}
    for row in total:
        days = row[0]
        profile["temp"].append(mean(row, 3) if days else None)
        profile["dew"].append(mean(row, 5) if days else None)
        profile["cloud"].append(mean(row, 7) if days else None)
        profile["wind"].append(mean(row, 9) if days else None)
        profile["rain"].append(round(100 * row[12] / row[11], 1) if days and row[11] else None)
        profile["perfect"].append(round(100 * row[1] / days, 1) if days else None)
        profile["indoor"].append(round(100 * row[2] / days, 1) if days else None)
        profile["gloom"].append(round(100 * row[13] / days, 1) if days else None)
    return profile


def perfect_indoor_ratio(avg_perfect_days, avg_indoor_days):
    """(perfect - indoor) / (perfect + indoor), bounded [-1, +1]: +1 = every
    classified day is perfect, -1 = every one is an indoor day, 0 for a
    50/50 split OR when neither ever happens (nothing to divide by, and
    "neutral" is the right read either way)."""
    denom = avg_perfect_days + avg_indoor_days
    return (avg_perfect_days - avg_indoor_days) / denom if denom else 0.0


SEASONAL_CONCENTRATION_MIN_MONTHS = 6   # below this, too few months have any data to judge a pattern
SEASONAL_CONCENTRATION_MIN_PERFECT_DAYS = 10   # below this, the metric is suppressed -- see aggregate_station


def seasonal_concentration(monthly_totals):
    """0 (perfect days spread evenly across the year) to 1 (every one of
    them falls in a single month): the coefficient of variation (std dev /
    mean) of each month's perfect-day RATE (% of that month's valid days,
    already normalizing for stations with more/fewer years of data in a
    given month), divided by its maximum possible value for that many
    buckets (sqrt(n-1), reached when all the weight sits in one bucket) so
    the result is a plain 0-1 score comparable across stations regardless of
    total perfect days. Usually all 12 months have data (MIN_VALID_YEARS
    already requires 3+ full years); None only if a station's coverage
    genuinely never includes enough distinct calendar months to judge."""
    total = _sum_monthly(monthly_totals)
    rates = [100 * row[1] / row[0] for row in total if row[0]]
    n = len(rates)
    if n < SEASONAL_CONCENTRATION_MIN_MONTHS:
        return None
    mean = sum(rates) / n
    if mean == 0:
        return 0.0
    variance = sum((r - mean) ** 2 for r in rates) / n
    cv = (variance ** 0.5) / mean
    return min(1.0, cv / ((n - 1) ** 0.5))


def exclusion_detail(stats):
    """None if the year has enough data to trust, else a structured reason.
    A criterion that is mostly absent is reported first -- it is the root cause
    when days fail to qualify as valid.
      {"kind": "coverage", "criterion": "cloud_pct", "value": 0.2}
      {"kind": "days", "value": 0.29}"""
    worst = min(stats["coverage"], key=stats["coverage"].get)
    if stats["coverage"][worst] < MIN_DATA_FRACTION:
        return {"kind": "coverage", "criterion": worst, "value": stats["coverage"][worst]}
    valid_fraction = stats["valid_days"] / stats["days_in_year"]
    if valid_fraction < MIN_DATA_FRACTION:
        return {"kind": "days", "value": valid_fraction}
    return None


def exclusion_reason(stats):
    """exclusion_detail as a one-line string (or None)."""
    detail = exclusion_detail(stats)
    if detail is None:
        return None
    if detail["kind"] == "coverage":
        return f"{detail['criterion']} present for only {detail['value']:.0%} of observations"
    return f"only {detail['value']:.0%} of days valid"


def score_from_stats(stats):
    """Per-year metrics from year_stats() of a year that passed exclusion_detail.

    Consistency rule: year_score is an average over valid days, while the
    perfect/indoor tallies (which shrink when days are missing) are scaled
    from the valid days observed up to TARGET_DAYS_PER_YEAR."""
    valid_days = stats["valid_days"]
    scale = TARGET_DAYS_PER_YEAR / valid_days
    return {
        "year_score": stats["score_sum"] / valid_days,
        "year_perfect_days": stats["perfect_count"] * scale,
        "year_indoor_days": stats["indoor_count"] * scale,
        "year_precip_mm": stats["precip_mm"] * scale,
        "year_gloomy_days": stats["gloomy_count"] * scale,
        "year_bad_days": stats["bad_count"] * scale,
        "year_gloom_streaks": stats["gloom_weighted"] * scale,
        "gloom_longest": stats["gloom_longest"],
        "gloom_len_sum": stats["gloom_len_sum"],
        "gloom_len_count": stats["gloom_len_count"],
        "year_perfect_streaks": stats["pstreak_weighted"] * scale,
        "pstreak_longest": stats["pstreak_longest"],
        "pstreak_len_sum": stats["pstreak_len_sum"],
        "pstreak_len_count": stats["pstreak_len_count"],
        "valid_day_fraction": valid_days / stats["days_in_year"],
        "coverage": stats["coverage"],
        "step": stats["step"],
        "samples": stats["samples"],
    }


def aggregate_station(year_results, monthly_totals):
    """year_results: list of score_year() outputs (already filtered to drop
    Nones by the caller). monthly_totals: that same year's monthly table
    (year_stats()["monthly"]) for each of those years, same order -- used
    only for seasonal_concentration. Returns the station's final averaged
    metrics, or None if fewer than MIN_VALID_YEARS years passed.
      coverage  mean share of days that were valid (for flagging reliability)
      step      the station's usual hours between observations
      samples   complete observations the numbers are built from"""
    if len(year_results) < MIN_VALID_YEARS:
        return None
    n = len(year_results)

    def streak_summary(weighted_key, longest_key, len_sum_key, len_count_key):
        """(avg yearly weighted count, longest single run ever seen, mean run
        length across every counted streak) for one streak metric."""
        total_len = sum(y[len_sum_key] for y in year_results)
        total_n = sum(y[len_count_key] for y in year_results)
        return {
            "avg": sum(y[weighted_key] for y in year_results) / n,
            "longest": max(y[longest_key] for y in year_results),
            "avg_length": total_len / total_n if total_n else None,
        }

    gloom = streak_summary("year_gloom_streaks", "gloom_longest", "gloom_len_sum", "gloom_len_count")
    pstreak = streak_summary("year_perfect_streaks", "pstreak_longest", "pstreak_len_sum", "pstreak_len_count")
    avg_perfect_days = sum(y["year_perfect_days"] for y in year_results) / n
    avg_indoor_days = sum(y["year_indoor_days"] for y in year_results) / n
    avg_precip_mm = sum(y["year_precip_mm"] for y in year_results) / n
    avg_gloomy_days = sum(y["year_gloomy_days"] for y in year_results) / n
    avg_bad_days = sum(y["year_bad_days"] for y in year_results) / n
    # Below the cutoff, a handful of perfect days landing in 1-2 months is
    # mostly luck, not a real seasonal pattern -- report "not enough data"
    # rather than a number that looks precise but isn't.
    concentration = None
    if avg_perfect_days >= SEASONAL_CONCENTRATION_MIN_PERFECT_DAYS:
        concentration = seasonal_concentration(monthly_totals)
    return {
        "avg_score": sum(y["year_score"] for y in year_results) / n,
        "avg_perfect_days": avg_perfect_days,
        "avg_indoor_days": avg_indoor_days,
        "avg_annual_precip_mm": avg_precip_mm,
        "avg_gloomy_days": avg_gloomy_days,
        "avg_bad_days": avg_bad_days,
        "perfect_indoor_ratio": perfect_indoor_ratio(avg_perfect_days, avg_indoor_days),
        "seasonal_concentration": concentration,
        "avg_gloom_streaks": gloom["avg"],
        "gloom_longest_run": gloom["longest"],
        "gloom_avg_length": gloom["avg_length"],
        "avg_perfect_streaks": pstreak["avg"],
        "perfect_streak_longest_run": pstreak["longest"],
        "perfect_streak_avg_length": pstreak["avg_length"],
        "years_included": n,
        "coverage": sum(y["valid_day_fraction"] for y in year_results) / n,
        "step": collections.Counter(y["step"] for y in year_results).most_common(1)[0][0],
        "samples": sum(y["samples"] for y in year_results),
    }
