"""PURE-PYTHON REFERENCE for the scoring in common/isd_score.c.

Not part of the pipeline: step 4 runs the C scorer. This is the readable,
line-by-line statement of the rules that the C code must reproduce, kept for
two jobs: tests/check_isd_score.py (proves C == this on real data) and
tools/inspect_station.py (explains a station year by year in plain Python).

If a scoring rule changes, change it here AND in common/isd_score.c (and its
constants in common/scoring.py), then run tests/check_isd_score.py.

Works on the dict form {"YYYY-MM-DD": {hour: record}}
(see common/yeardata.py arrays_to_days).
"""

import calendar
import collections
import math
from datetime import date

from common import geo
from common.scoring import *  # noqa: F401,F403  (every threshold, plus exclusion_detail / score_from_stats)


def _triangular_score(value, low, ideal, high):
    """1.0 at ideal, linear down to 0.0 at low/high, 0.0 beyond. None in,
    None out (missing input can't be scored)."""
    if value is None:
        return None
    if value <= low or value >= high:
        return 0.0
    if value < ideal:
        return (value - low) / (ideal - low)
    if value > ideal:
        return (high - value) / (high - ideal)
    return 1.0


def _one_sided_score(value, ideal, max_bound):
    """1.0 at ideal (the lower bound), linear down to 0.0 at max_bound, 0.0
    beyond. Used for precip/cloud where the ideal IS the minimum possible
    value."""
    if value is None:
        return None
    if value >= max_bound:
        return 0.0
    if value <= ideal:
        return 1.0
    return (max_bound - value) / (max_bound - ideal)


def hour_factor_scores(record):
    """record: dict with temp_c, dew_c, precip_mm, cloud_pct (any may be
    None). Returns the 4 individual factor scores, each None if its input
    was missing."""
    return {
        "temp": _triangular_score(record.get("temp_c"), TEMP_LOW_C, TEMP_IDEAL_C, TEMP_HIGH_C),
        "dew": _triangular_score(record.get("dew_c"), DEW_LOW_C, DEW_IDEAL_C, DEW_HIGH_C),
        "precip": _one_sided_score(record.get("precip_mm"), PRECIP_IDEAL_MM, PRECIP_MAX_MM),
        "cloud": _one_sided_score(record.get("cloud_pct"), CLOUD_IDEAL_PCT, CLOUD_MAX_PCT),
    }


def hour_is_complete(scores):
    return all(v is not None for v in scores.values())


def hour_is_good(scores):
    """A 'good hour' requires all 4 factors present AND within range (score
    > 0 on every factor). Missing data conservatively counts as not-good --
    a simplification that may change later if it proves too strict."""
    return hour_is_complete(scores) and all(v > 0 for v in scores.values())


def dominant_step(days):
    """Hours between observations for this station-year: the most common gap
    between consecutive observation hours within a day, clamped to 1-6."""
    gaps = collections.Counter()
    for hours in days.values():
        hs = sorted(hours)
        for a, b in zip(hs, hs[1:]):
            gaps[b - a] += 1
    if not gaps:
        return 1
    return max(1, min(gaps.most_common(1)[0][0], 6))


def daily_score(hour_records_by_hour, step=1):
    """hour_records_by_hour: dict {hour: record}. Returns a 0-1 score: the mean
    factor score of the day's best ceil(6/step) complete observations (i.e. its
    best ~6 hours), or None if the day has fewer complete observations than
    that (rather than padding missing ones with zeros)."""
    top_n = math.ceil(TOP_N_HOURS_FOR_DAILY_SCORE / step)
    complete = []
    for record in hour_records_by_hour.values():
        scores = hour_factor_scores(record)
        if hour_is_complete(scores):
            complete.append(scores)

    if len(complete) < top_n:
        return None

    complete.sort(key=lambda s: sum(s.values()), reverse=True)
    top = complete[:top_n]
    total = sum(sum(s.values()) for s in top)
    return total / (top_n * 4)


def is_perfect_day(hour_records_by_hour, step=1):
    """True if the day has a run of consecutive good observations long enough:
    MIN_CONSECUTIVE_GOOD_HOURS_FOR_PERFECT_DAY observations for hourly data,
    or (step > 1) max(2, ceil(4/step)) observations. Observations are
    consecutive when no more than `step` hours apart; a missing observation
    breaks the run. Runs never wrap across midnight (UTC day)."""
    if step == 1:
        need = MIN_CONSECUTIVE_GOOD_HOURS_FOR_PERFECT_DAY
    else:
        need = max(2, math.ceil(MIN_CONSECUTIVE_GOOD_HOURS_FOR_PERFECT_DAY / step))

    run = 0
    best = 0
    prev_hour = None
    for h in sorted(hour_records_by_hour):
        if hour_is_good(hour_factor_scores(hour_records_by_hour[h])):
            run = run + 1 if (run and h - prev_hour <= step) else 1
        else:
            run = 0
        prev_hour = h
        best = max(best, run)
    return best >= need


def is_gloomy_day(hour_records_by_hour):
    """"Gloomy" is clouds only: at least GLOOM_CLOUD_MIN_HOURS of the day's
    daytime hours are more than GLOOM_CLOUD_MIN_PCT cloudy. (An indoor day
    can be gloomy too; the two overlap.)"""
    cloudy_hours = sum(
        1 for r in hour_records_by_hour.values()
        if r.get("cloud_pct") is not None and r["cloud_pct"] > GLOOM_CLOUD_MIN_PCT
    )
    return cloudy_hours >= GLOOM_CLOUD_MIN_HOURS


def is_indoor_day(hour_records_by_hour):
    """True if ANY of the indoor-day conditions hold, based on the day's
    aggregate (max/max/max/sum/max) values. Missing factors simply can't
    trigger their own condition; a day with literally no data returns False
    (it can't be flagged as indoor without evidence)."""
    temps = [r["temp_c"] for r in hour_records_by_hour.values() if r.get("temp_c") is not None]
    dews = [r["dew_c"] for r in hour_records_by_hour.values() if r.get("dew_c") is not None]
    clouds = [r["cloud_pct"] for r in hour_records_by_hour.values() if r.get("cloud_pct") is not None]
    precips = [r["precip_mm"] for r in hour_records_by_hour.values() if r.get("precip_mm") is not None]
    winds = [r["wind_mps"] for r in hour_records_by_hour.values() if r.get("wind_mps") is not None]

    max_temp = max(temps) if temps else None
    max_dew = max(dews) if dews else None
    max_cloud = max(clouds) if clouds else None
    total_precip = sum(precips) if precips else None
    max_wind = max(winds) if winds else None

    cold = max_temp is not None and max_temp < INDOOR_COLD_MAX_C
    hot = max_temp is not None and max_temp > INDOOR_HOT_MIN_C
    muggy = max_dew is not None and max_dew > INDOOR_DEWPOINT_MIN_C
    overcast_wet = (
        max_cloud is not None and total_precip is not None
        and max_cloud > INDOOR_CLOUD_MIN_PCT and total_precip > INDOOR_PRECIP_MIN_MM
    )
    windy = max_wind is not None and max_wind > INDOOR_WIND_MIN_MPS

    return cold or hot or muggy or overcast_wet or windy


def effective_days(days, utc_offset=0, lat=None):
    """Turn a station-year of parsed hours (the raw facts each report stated)
    into scoring-ready hours, deciding what "missing" means. Precip, in order:

      1. measured 1-hour depth, if reported;
      2. WET_UNMEASURED_MM if the report says it was precipitating;
      3. 0.0 if a METAR was present and reported no precipitation -- but ONLY
         when this station-year shows real precip evidence (>= PRECIP_EVIDENCE_
         MIN_HOURS hours of measured depth or reported precipitation). Stations
         that never report any (e.g. automated stations with no precip
         discriminator) stay unknown rather than "always dry";
      4. a 3/6/12/24-hour accumulation covering this slot: 0.0 if the
         accumulation was 0. A non-zero 3/6-hour one becomes max(depth/period,
         WET_UNMEASURED_MM) -- any measurable precipitation in the window makes
         each covered slot wet (conservative: we can't tell which hours it fell
         in). A non-zero 12/24-hour one is too coarse and is ignored.
    Cloud is already resolved at parse time (cloud group, else METAR text).

    Also moves the hours from UTC to local solar time (utc_offset hours) and
    keeps only the hours that are both within the civil DAY_START_HOUR/
    DAY_END_HOUR window AND actually daylight that day (see
    common.geo.daylight_bounds) -- lat=None skips the daylight narrowing and
    uses the civil window alone, same as before latitude was tracked.

    days: {"YYYY-MM-DD": {hour_int: parsed record}}, UTC
    returns {"YYYY-MM-DD": {hour_int: {temp_c, dew_c, wind_mps, precip_mm, cloud_pct}}},
      local solar dates/hours, daytime only
    """
    evidence = 0
    for hours in days.values():
        for r in hours.values():
            if r["precip_mm"] is not None or r["wx_wet"] or r["precip_acc"] is not None:
                evidence += 1
    precip_capable = evidence >= PRECIP_EVIDENCE_MIN_HOURS

    out = {}
    slot_at = {}       # absolute local hour -> effective record, for accumulation fill
    accs = []          # (absolute local hour, period, depth) for every report carrying one
    iso = {}           # local day ordinal -> "YYYY-MM-DD"
    bounds_by_ord = {}  # local day ordinal -> (eff_start, eff_end), computed once per day
    for date_str, hours in days.items():
        base = date.fromisoformat(date_str).toordinal() * 24 + utc_offset
        for h, r in hours.items():
            absolute = base + h
            if r["precip_acc"] is not None:
                # Night reports still count: a 24-hour total reported at 3 am
                # says the whole previous daytime was dry.
                accs.append((absolute, r["precip_acc"][0], r["precip_acc"][1]))
            local_ord, local_hour = divmod(absolute, 24)
            bounds = bounds_by_ord.get(local_ord)
            if bounds is None:
                day_of_year = date.fromordinal(local_ord).timetuple().tm_yday
                bounds = bounds_by_ord[local_ord] = geo.daylight_bounds(lat, day_of_year, DAY_START_HOUR, DAY_END_HOUR)
            if local_hour < bounds[0] or local_hour >= bounds[1]:
                continue
            p = r["precip_mm"]
            if p is None:
                wx = r["wx_wet"]
                if wx:
                    p = WET_UNMEASURED_MM
                elif wx is False and precip_capable:
                    p = 0.0
            rec = {
                "temp_c": r["temp_c"],
                "dew_c": r["dew_c"],
                "wind_mps": r["wind_mps"],
                "precip_mm": p,
                "cloud_pct": r["cloud_pct"],
            }
            slot_at[absolute] = rec
            day = iso.get(local_ord)
            if day is None:
                day = iso[local_ord] = date.fromordinal(local_ord).isoformat()
            if day in out:
                out[day][local_hour] = rec
            else:
                out[day] = {local_hour: rec}

    for end, period, depth in sorted(accs):
        if period > 6 and depth != 0:
            continue  # "some rain in the last 12/24h" says nothing about which hours
        value = 0.0 if depth == 0 else max(depth / period, WET_UNMEASURED_MM)
        for a in range(end - period + 1, end + 1):
            rec = slot_at.get(a)
            if rec is not None and rec["precip_mm"] is None:
                rec["precip_mm"] = value
    return out


def _streaks(flags_by_ordinal):
    """flags_by_ordinal: [(local day ordinal, bool), ...] for the valid days
    of one year, ANY order. Returns (weighted, longest, len_sum, len_count)
    for the runs of consecutive (ordinal N, N+1, N+2, ...) True flags -- a day
    simply absent from the list (not valid) breaks a run in progress, same as
    common/isd_score.c."""
    weighted = len_sum = longest = 0
    len_count = 0
    run = 0
    last_ord = None
    for ord_, flag in sorted(flags_by_ordinal):
        contiguous = last_ord is not None and ord_ == last_ord + 1
        if flag:
            if not contiguous:
                if run >= STREAK_MIN_DAYS:
                    weighted += streak_weight(run); len_sum += run; len_count += 1; longest = max(longest, run)
                run = 0
            run += 1
        else:
            if run >= STREAK_MIN_DAYS:
                weighted += streak_weight(run); len_sum += run; len_count += 1; longest = max(longest, run)
            run = 0
        last_ord = ord_
    if run >= STREAK_MIN_DAYS:
        weighted += streak_weight(run); len_sum += run; len_count += 1; longest = max(longest, run)
    return weighted, longest, len_sum, len_count


def year_stats(days, year, lat=None):
    """Everything score_year needs (and the inspection tool shows) for one
    year of effective_days(): reporting step, per-criterion coverage,
    valid-day count, and the raw tallies over valid days. All of it is about
    daytime hours only (effective_days already dropped the rest).

    lat must match whatever was passed to effective_days() for this same
    `days` -- it's needed again here because the MIN_DAYTIME_FILL check below
    judges each day against ITS OWN actual daylight length, not the fixed
    civil one (see common/isd_score.c, which does the same)."""
    days_in_year = 366 if calendar.isleap(year) else 365
    step = dominant_step(days)
    expected_obs = days_in_year * DAY_LENGTH_HOURS / step
    prefix = f"{year}-"   # local dates spilling into the neighbouring year belong to that year

    have = dict.fromkeys(CRITERIA, 0)
    valid_days = 0
    score_sum = 0.0
    perfect_count = 0
    indoor_count = 0
    samples = 0
    precip_mm = 0.0
    gloomy_count = 0
    bad_count = 0
    obs = 0
    # Per month, over valid days, for the "typical year" charts:
    # [days, perfect, indoor, temp sum, n, dew sum, n, cloud sum, n, wind sum, n, precip n, wet n, gloom]
    monthly = [[0.0] * 14 for _ in range(12)]
    gloom_flags = []    # (local day ordinal, is_gloom) for valid days, for _streaks()
    perfect_flags = []  # (local day ordinal, is_perfect) for valid days, for _streaks()

    for date_str, hours in days.items():
        if not date_str.startswith(prefix):
            continue
        complete = 0
        for r in hours.values():
            obs += 1
            for k in CRITERIA:
                if r[k] is not None:
                    have[k] += 1
            if (r["temp_c"] is not None and r["dew_c"] is not None
                    and r["precip_mm"] is not None and r["cloud_pct"] is not None):
                complete += 1
        day_of_year = date.fromisoformat(date_str).timetuple().tm_yday
        eff_start, eff_end = geo.daylight_bounds(lat, day_of_year, DAY_START_HOUR, DAY_END_HOUR)
        this_day_len = eff_end - eff_start
        if this_day_len <= 0 or complete * step < MIN_DAYTIME_FILL * this_day_len:
            continue
        score = daily_score(hours, step)
        if score is None:
            continue
        valid_days += 1
        precip_mm += sum(v["precip_mm"] for v in hours.values() if v["precip_mm"] is not None) * 24.0 / this_day_len
        score_sum += score
        samples += complete
        m = monthly[int(date_str[5:7]) - 1]
        m[0] += 1
        perfect = is_perfect_day(hours, step)
        indoor = is_indoor_day(hours)
        gloomy = is_gloomy_day(hours)
        gloom = indoor or gloomy   # the day you'd stay in; drives the indoor streaks
        if perfect:
            perfect_count += 1
            m[1] += 1
        if indoor:
            indoor_count += 1
            m[2] += 1
        if gloomy:
            gloomy_count += 1
            m[13] += 1
        if gloom:
            bad_count += 1
        ordinal = date.fromisoformat(date_str).toordinal()
        gloom_flags.append((ordinal, gloom))
        perfect_flags.append((ordinal, perfect))
        for r in hours.values():
            for slot, key in ((3, "temp_c"), (5, "dew_c"), (7, "cloud_pct"), (9, "wind_mps")):
                v = r[key]
                if v is not None:
                    m[slot] += v
                    m[slot + 1] += 1
            p = r["precip_mm"]
            if p is not None:
                m[11] += 1
                if p > 0:
                    m[12] += 1

    gloom_weighted, gloom_longest, gloom_len_sum, gloom_len_count = _streaks(gloom_flags)
    pstreak_weighted, pstreak_longest, pstreak_len_sum, pstreak_len_count = _streaks(perfect_flags)

    return {
        "days_in_year": days_in_year,
        "step": step,
        "obs": obs,
        "coverage": {k: have[k] / expected_obs for k in CRITERIA},
        "valid_days": valid_days,
        "score_sum": score_sum,
        "perfect_count": perfect_count,
        "indoor_count": indoor_count,
        "samples": samples,
        "gloom_weighted": gloom_weighted,
        "gloom_longest": gloom_longest,
        "gloom_len_sum": gloom_len_sum,
        "gloom_len_count": gloom_len_count,
        "pstreak_weighted": pstreak_weighted,
        "pstreak_longest": pstreak_longest,
        "pstreak_len_sum": pstreak_len_sum,
        "pstreak_len_count": pstreak_len_count,
        "monthly": monthly,
        "precip_mm": precip_mm,
        "gloomy_count": gloomy_count,
        "bad_count": bad_count,
    }


def score_year(days, year, lat=None):
    """days: effective_days() output for one year (built with this same
    lat). Returns per-year aggregates, or None if the year has too little
    data to trust (see exclusion_detail): fewer than MIN_DATA_FRACTION of its
    days are valid, or any important criterion exists for fewer than
    MIN_DATA_FRACTION of its expected observations. Callers drop None years
    from the station."""
    stats = year_stats(days, year, lat)
    if exclusion_detail(stats) is not None:
        return None
    return score_from_stats(stats)
