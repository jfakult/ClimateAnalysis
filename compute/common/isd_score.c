/* C implementation of common/scoring.py effective_days() + year_stats() for
 * one station-year of flat arrays (see common/yeardata.py). The Python code
 * stays the reference implementation and the fallback; check_isd_score.py
 * compares the two on real data.
 *
 * All thresholds come in through `params` (built by common/isd_score.py from
 * the scoring module's own constants), so there is one source of truth for
 * every number; only the LOGIC is duplicated here.
 *
 * Build:  cc -O2 -shared -fPIC -o libisd_score.so isd_score.c
 *
 * Returns 0 on success, -1 if the input is not what the scorer assumes
 * (keys not strictly ascending, absurd time span, allocation failure); the
 * caller then falls back to Python.
 */
#include <math.h>
#include <stdlib.h>

/* params[] layout -- keep in sync with common/isd_score.py PARAM_NAMES */
enum {
    P_TEMP_LOW, P_TEMP_IDEAL, P_TEMP_HIGH,
    P_DEW_LOW, P_DEW_IDEAL, P_DEW_HIGH,
    P_PRECIP_IDEAL, P_PRECIP_MAX, P_CLOUD_IDEAL, P_CLOUD_MAX,
    P_INDOOR_COLD, P_INDOOR_HOT, P_INDOOR_DEW, P_INDOOR_CLOUD, P_INDOOR_PRECIP, P_INDOOR_WIND,
    P_TOP_N_HOURS, P_MIN_CONSECUTIVE, P_DAY_START, P_DAY_END, P_MIN_FILL,
    P_WET_UNMEASURED, P_EVIDENCE_MIN_HOURS,
    P_GLOOM_CLOUD_MIN, P_GLOOM_CLOUD_MIN_HOURS, P_STREAK_MIN_DAYS
};

/* out[] layout: 20 scalars, then 12 months x 14, then a few more scalars (O_PRECIP...) */
enum {
    O_OBS, O_STEP, O_HAVE /* 5: temp, dew, precip, cloud, wind */ = 2,
    O_VALID_DAYS = 7, O_SCORE_SUM, O_PERFECT, O_INDOOR, O_SAMPLES,
    O_GLOOM_WEIGHTED, O_GLOOM_LONGEST, O_GLOOM_LEN_SUM, O_GLOOM_LEN_COUNT,
    O_PSTREAK_WEIGHTED, O_PSTREAK_LONGEST, O_PSTREAK_LEN_SUM, O_PSTREAK_LEN_COUNT,
    O_MONTHLY /* = 20 */
};
/* after the monthly table: annual precipitation estimate (mm, still to be
 * scaled to a full year by the caller like the other tallies) */
#define O_PRECIP (20 + 12 * 14)
#define O_GLOOMY (O_PRECIP + 1)   /* cloud-only gloomy days */
#define O_BAD (O_PRECIP + 2)      /* indoor OR gloomy days (union, not the sum) */
#define OUT_TOTAL (O_PRECIP + 3)

static const double NANV = __builtin_nan("");
static int is_nan(double x) { return x != x; }

/* days since 1970-01-01 (Howard Hinnant's algorithm) */
static long days_from_civil(long y, long m, long d) {
    y -= m <= 2;
    long era = (y >= 0 ? y : y - 399) / 400;
    long yoe = y - era * 400;
    long doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

static int civil_month(long z) {
    z += 719468;
    long era = (z >= 0 ? z : z - 146096) / 146097;
    long doe = z - era * 146097;
    long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    long mp = (5 * doy + 2) / 153;
    return (int)(mp < 10 ? mp + 3 : mp - 9);
}

static long floordiv(long a, long b) { long q = a / b; return (a % b != 0 && ((a < 0) != (b < 0))) ? q - 1 : q; }
static long floormod(long a, long b) { return a - floordiv(a, b) * b; }

static double triangular(double v, double low, double ideal, double high) {
    if (v <= low || v >= high) return 0.0;
    if (v < ideal) return (v - low) / (ideal - low);
    if (v > ideal) return (high - v) / (high - ideal);
    return 1.0;
}

static double one_sided(double v, double ideal, double max_bound) {
    if (v >= max_bound) return 0.0;
    if (v <= ideal) return 1.0;
    return (max_bound - v) / (max_bound - ideal);
}

/* Closes out a run of `run` consecutive flagged days: below min_days it's
 * just noise and is dropped; at or above, it counts once, weighted 1 per 3
 * days rounded up (3d->1, 4-6d->2, 7-9d->3, ...: (run+2)/3 for run>=3). */
static void streak_flush(long run, int min_days, double *weighted, double *len_sum, long *len_count, long *longest) {
    if (run < min_days) return;
    *weighted += (run + 2) / 3;
    *len_sum += run;
    (*len_count)++;
    if (run > *longest) *longest = run;
}

/* Mirrors common/geo.py's daylight_bounds(): narrows [day_start, day_end)
 * to the hours actually between sunrise and sunset on this day, at this
 * latitude (standard sunrise/sunset approximation from the sun's
 * declination; ignores the equation of time and refraction, consistent with
 * the whole-hour "local solar time" this whole pipeline already uses).
 * is_nan(lat) (unknown) leaves the civil window untouched. */
#define EARTH_TILT_DEG 23.44
#define EQUINOX_DAY_OF_YEAR 81
#define PI_CONST 3.14159265358979323846
static void daylight_bounds(double lat, long day_of_year, int day_start, int day_end, int *eff_start, int *eff_end) {
    if (is_nan(lat)) { *eff_start = day_start; *eff_end = day_end; return; }
    double decl = (EARTH_TILT_DEG * PI_CONST / 180.0) * sin(2.0 * PI_CONST / 365.0 * (double)(day_of_year - EQUINOX_DAY_OF_YEAR));
    double cos_h = -tan(lat * PI_CONST / 180.0) * tan(decl);
    double half_day;
    if (cos_h <= -1.0) half_day = 12.0;        /* polar day: sun never sets */
    else if (cos_h >= 1.0) half_day = 0.0;     /* polar night: sun never rises */
    else half_day = acos(cos_h) * 180.0 / PI_CONST / 15.0;
    double sunrise = 12.0 - half_day, sunset = 12.0 + half_day;
    int rise = (int)ceil(sunrise), set = (int)floor(sunset);
    *eff_start = day_start > rise ? day_start : rise;
    *eff_end = day_end < set ? day_end : set;
}

long isd_year_stats(long n, const int *key, const double *temp, const double *dew, const double *wind,
                    const double *cloud, const double *precip, const double *acc_depth,
                    const signed char *acc_period, const signed char *wet,
                    int year, int utc_offset, double lat, const double *P, double *out) {
    for (int i = 0; i < OUT_TOTAL; i++) out[i] = 0.0;
    if (n == 0) { out[O_STEP] = 1; return 0; }

    const int day_start = (int)P[P_DAY_START], day_end = (int)P[P_DAY_END];
    const long jan1 = days_from_civil(year, 1, 1), dec31 = days_from_civil(year, 12, 31);

    long *absolute = malloc(sizeof(long) * n);
    long *dt = malloc(sizeof(long) * n);          /* indices of daytime hours */
    long *dt_ord = malloc(sizeof(long) * n);      /* their local day ordinal */
    int *dt_hour = malloc(sizeof(int) * n);       /* their local hour */
    double *eff = malloc(sizeof(double) * n);     /* effective precip per daytime hour */
    if (!absolute || !dt || !dt_ord || !dt_hour || !eff) { free(absolute); free(dt); free(dt_ord); free(dt_hour); free(eff); return -1; }
    int rc = -1;
    int *slot_map = NULL;

    /* absolute local hour of every report; keys must be strictly ascending */
    long evidence = 0;
    for (long i = 0; i < n; i++) {
        long ymd = key[i] / 100, hh = key[i] % 100;
        if (i > 0 && key[i] <= key[i - 1]) goto done;
        absolute[i] = days_from_civil(ymd / 10000, (ymd / 100) % 100, ymd % 100) * 24 + hh + utc_offset;
        if (!is_nan(precip[i]) || wet[i] == 2 || acc_period[i] != 0) evidence++;
    }
    const int capable = evidence >= (long)P[P_EVIDENCE_MIN_HOURS];

    long span = absolute[n - 1] - absolute[0] + 1;
    if (span < 1 || span > 200000) goto done;
    slot_map = malloc(sizeof(int) * span);
    if (!slot_map) goto done;
    for (long i = 0; i < span; i++) slot_map[i] = -1;

    /* daytime hours + effective precipitation. absolute[] (and so lo) is
     * non-decreasing (ascending keys were already checked above), so caching
     * the daylight bounds for just the last day seen covers every hour of
     * that day without recomputing the trig per hour. */
    long nd = 0;
    long bounds_lo = 0;
    int bounds_valid = 0, eff_day_start = day_start, eff_day_end = day_end;
    for (long i = 0; i < n; i++) {
        long lo = floordiv(absolute[i], 24);
        int lh = (int)floormod(absolute[i], 24);
        if (!bounds_valid || lo != bounds_lo) {
            daylight_bounds(lat, lo - jan1 + 1, day_start, day_end, &eff_day_start, &eff_day_end);
            bounds_lo = lo;
            bounds_valid = 1;
        }
        if (lh < eff_day_start || lh >= eff_day_end) continue;
        double p = precip[i];
        if (is_nan(p)) {
            if (wet[i] == 2) p = P[P_WET_UNMEASURED];
            else if (wet[i] == 1 && capable) p = 0.0;
        }
        dt[nd] = i; dt_ord[nd] = lo; dt_hour[nd] = lh; eff[nd] = p;
        slot_map[absolute[i] - absolute[0]] = (int)nd;
        nd++;
    }

    /* accumulations (night reports included) fill still-unknown daytime slots */
    for (long i = 0; i < n; i++) {
        if (acc_period[i] == 0) continue;
        int period = acc_period[i];
        double depth = acc_depth[i];
        if (period > 6 && depth != 0.0) continue;
        double value = 0.0;
        if (depth != 0.0) {
            value = depth / period;
            if (value < P[P_WET_UNMEASURED]) value = P[P_WET_UNMEASURED];
        }
        for (long a = absolute[i] - period + 1; a <= absolute[i]; a++) {
            long off = a - absolute[0];
            if (off < 0 || off >= span) continue;
            int j = slot_map[off];
            if (j >= 0 && is_nan(eff[j])) eff[j] = value;
        }
    }

    /* reporting step: most common gap between consecutive daytime hours within
     * a local day (ties: the gap seen first, like collections.Counter) */
    long gap_count[25] = {0};
    int gap_order[25];
    int seen = 0;
    for (long j = 1; j < nd; j++) {
        if (dt_ord[j] != dt_ord[j - 1]) continue;
        int g = dt_hour[j] - dt_hour[j - 1];
        if (g < 1 || g > 24) continue;
        if (gap_count[g] == 0) gap_order[g] = seen++;
        gap_count[g]++;
    }
    int step = 1;
    {
        int best_g = 0;
        for (int g = 1; g <= 24; g++) {
            if (gap_count[g] == 0) continue;
            if (best_g == 0 || gap_count[g] > gap_count[best_g] ||
                (gap_count[g] == gap_count[best_g] && gap_order[g] < gap_order[best_g]))
                best_g = g;
        }
        if (best_g) step = best_g > 6 ? 6 : (best_g < 1 ? 1 : best_g);
    }
    out[O_STEP] = step;

    const int top_n_hours = (int)P[P_TOP_N_HOURS];
    const int top_n = (top_n_hours + step - 1) / step;
    const int min_consec = (int)P[P_MIN_CONSECUTIVE];
    int need = min_consec;
    if (step != 1) { need = (min_consec + step - 1) / step; if (need < 2) need = 2; }
    const int streak_min_days = (int)P[P_STREAK_MIN_DAYS];

    double obs = 0, have[5] = {0, 0, 0, 0, 0};
    double valid_days = 0, score_sum = 0, perfect_n = 0, indoor_n = 0, samples = 0, precip_mm = 0, gloomy_n = 0, bad_n = 0;

    /* Gloom and perfect-day streaks: consecutive VALID days (see below) that
     * are gloomy / perfect. They share one contiguity tracker (both run over
     * the identical set of valid days); each has its own run length. A day
     * that isn't valid (not enough data) is simply absent from this
     * tracking, so it silently breaks any run in progress -- we don't assume
     * a day we can't judge continued a streak. */
    long streak_last_ord = 0;
    int streak_have_last = 0;
    long gloom_run = 0, pstreak_run = 0;
    double gloom_weighted = 0, gloom_len_sum = 0, pstreak_weighted = 0, pstreak_len_sum = 0;
    long gloom_len_count = 0, gloom_longest = 0, pstreak_len_count = 0, pstreak_longest = 0;

    for (long g0 = 0; g0 < nd;) {
        long g1 = g0 + 1;
        while (g1 < nd && dt_ord[g1] == dt_ord[g0]) g1++;
        long ord = dt_ord[g0];
        if (ord >= jan1 && ord <= dec31) {
            int complete = 0;
            for (long j = g0; j < g1; j++) {
                long i = dt[j];
                obs++;
                if (!is_nan(temp[i])) have[0]++;
                if (!is_nan(dew[i])) have[1]++;
                if (!is_nan(eff[j])) have[2]++;
                if (!is_nan(cloud[i])) have[3]++;
                if (!is_nan(wind[i])) have[4]++;
                if (!is_nan(temp[i]) && !is_nan(dew[i]) && !is_nan(eff[j]) && !is_nan(cloud[i])) complete++;
            }
            /* The fill ratio is judged against THIS day's actual daylight
             * length, not the fixed civil one -- a short winter day at high
             * latitude shouldn't need as many hours reported to count as
             * "fully covered" as a long summer one. */
            int this_day_start, this_day_end;
            daylight_bounds(lat, ord - jan1 + 1, day_start, day_end, &this_day_start, &this_day_end);
            double this_day_len = this_day_end - this_day_start;
            if (!(this_day_len <= 0 || (double)complete * step < P[P_MIN_FILL] * this_day_len)) {
                /* daily score + perfect-day run, factor scores computed once */
                double sums[32];
                int ns = 0, run = 0, best = 0, prev = 0;
                for (long j = g0; j < g1; j++) {
                    long i = dt[j];
                    if (is_nan(temp[i]) || is_nan(dew[i]) || is_nan(eff[j]) || is_nan(cloud[i])) {
                        run = 0;
                    } else {
                        double t = triangular(temp[i], P[P_TEMP_LOW], P[P_TEMP_IDEAL], P[P_TEMP_HIGH]);
                        double d = triangular(dew[i], P[P_DEW_LOW], P[P_DEW_IDEAL], P[P_DEW_HIGH]);
                        double p = one_sided(eff[j], P[P_PRECIP_IDEAL], P[P_PRECIP_MAX]);
                        double c = one_sided(cloud[i], P[P_CLOUD_IDEAL], P[P_CLOUD_MAX]);
                        if (ns < 32) sums[ns++] = t + d + p + c;
                        if (t > 0 && d > 0 && p > 0 && c > 0) run = (run && dt_hour[j] - prev <= step) ? run + 1 : 1;
                        else run = 0;
                    }
                    prev = dt_hour[j];
                    if (run > best) best = run;
                }
                if (ns >= top_n) {
                    /* top_n largest, accumulated in descending order */
                    double total = 0.0;
                    for (int k = 0; k < top_n; k++) {
                        int m = k;
                        for (int q = k + 1; q < ns; q++) if (sums[q] > sums[m]) m = q;
                        double tmp = sums[k]; sums[k] = sums[m]; sums[m] = tmp;
                        total += sums[k];
                    }
                    double score = total / (top_n * 4);
                    int is_perfect = best >= need;

                    /* indoor day: aggregates over the day's daytime hours */
                    int has_t = 0, has_d = 0, has_c = 0, has_p = 0, has_w = 0;
                    double max_t = 0, max_d = 0, max_c = 0, tot_p = 0, max_w = 0;
                    int cloudy_hours = 0;
                    for (long j = g0; j < g1; j++) {
                        long i = dt[j];
                        if (!is_nan(temp[i])) { if (!has_t || temp[i] > max_t) max_t = temp[i]; has_t = 1; }
                        if (!is_nan(dew[i])) { if (!has_d || dew[i] > max_d) max_d = dew[i]; has_d = 1; }
                        if (!is_nan(cloud[i])) {
                            if (!has_c || cloud[i] > max_c) max_c = cloud[i];
                            has_c = 1;
                            if (cloud[i] > P[P_GLOOM_CLOUD_MIN]) cloudy_hours++;
                        }
                        if (!is_nan(eff[j])) { tot_p += eff[j]; has_p = 1; }
                        if (!is_nan(wind[i])) { if (!has_w || wind[i] > max_w) max_w = wind[i]; has_w = 1; }
                    }
                    int is_indoor = (has_t && max_t < P[P_INDOOR_COLD]) || (has_t && max_t > P[P_INDOOR_HOT]) ||
                                 (has_d && max_d > P[P_INDOOR_DEW]) ||
                                 (has_c && has_p && max_c > P[P_INDOOR_CLOUD] && tot_p > P[P_INDOOR_PRECIP]) ||
                                 (has_w && max_w > P[P_INDOOR_WIND]);
                    /* "gloomy": already an indoor day, or overcast (> GLOOM_CLOUD_MIN)
                     * for at least GLOOM_CLOUD_MIN_HOURS of the day's daytime hours. */
                    int is_gloomy = cloudy_hours >= (int)P[P_GLOOM_CLOUD_MIN_HOURS];   /* clouds only */
                    /* the day you'd stay in: indoor weather OR gloomy (the two overlap, so
                     * this is a union count, not indoor + gloomy); drives the "indoor streaks" */
                    int is_gloom = is_indoor || is_gloomy;

                    valid_days++;
                    /* daytime-only rain, scaled from this day's daylight hours to 24 */
                    precip_mm += tot_p * 24.0 / this_day_len;
                    score_sum += score;
                    samples += complete;
                    double *mo = out + O_MONTHLY + 14 * (civil_month(ord) - 1);
                    mo[0] += 1;
                    if (is_perfect) { perfect_n++; mo[1] += 1; }
                    if (is_indoor) { indoor_n++; mo[2] += 1; }
                    if (is_gloomy) { gloomy_n++; mo[13] += 1; }
                    if (is_gloom) bad_n++;
                    for (long j = g0; j < g1; j++) {
                        long i = dt[j];
                        if (!is_nan(temp[i])) { mo[3] += temp[i]; mo[4] += 1; }
                        if (!is_nan(dew[i])) { mo[5] += dew[i]; mo[6] += 1; }
                        if (!is_nan(cloud[i])) { mo[7] += cloud[i]; mo[8] += 1; }
                        if (!is_nan(wind[i])) { mo[9] += wind[i]; mo[10] += 1; }
                        if (!is_nan(eff[j])) { mo[11] += 1; if (eff[j] > 0) mo[12] += 1; }
                    }

                    int contiguous = streak_have_last && (ord == streak_last_ord + 1);
                    if (is_gloom) {
                        if (!contiguous) { streak_flush(gloom_run, streak_min_days, &gloom_weighted, &gloom_len_sum, &gloom_len_count, &gloom_longest); gloom_run = 0; }
                        gloom_run++;
                    } else {
                        streak_flush(gloom_run, streak_min_days, &gloom_weighted, &gloom_len_sum, &gloom_len_count, &gloom_longest);
                        gloom_run = 0;
                    }
                    if (is_perfect) {
                        if (!contiguous) { streak_flush(pstreak_run, streak_min_days, &pstreak_weighted, &pstreak_len_sum, &pstreak_len_count, &pstreak_longest); pstreak_run = 0; }
                        pstreak_run++;
                    } else {
                        streak_flush(pstreak_run, streak_min_days, &pstreak_weighted, &pstreak_len_sum, &pstreak_len_count, &pstreak_longest);
                        pstreak_run = 0;
                    }
                    streak_last_ord = ord;
                    streak_have_last = 1;
                }
            }
        }
        g0 = g1;
    }
    streak_flush(gloom_run, streak_min_days, &gloom_weighted, &gloom_len_sum, &gloom_len_count, &gloom_longest);
    streak_flush(pstreak_run, streak_min_days, &pstreak_weighted, &pstreak_len_sum, &pstreak_len_count, &pstreak_longest);

    out[O_OBS] = obs;
    for (int k = 0; k < 5; k++) out[O_HAVE + k] = have[k];
    out[O_VALID_DAYS] = valid_days;
    out[O_PRECIP] = precip_mm;
    out[O_GLOOMY] = gloomy_n;
    out[O_BAD] = bad_n;
    out[O_SCORE_SUM] = score_sum;
    out[O_PERFECT] = perfect_n;
    out[O_INDOOR] = indoor_n;
    out[O_SAMPLES] = samples;
    out[O_GLOOM_WEIGHTED] = gloom_weighted;
    out[O_GLOOM_LONGEST] = (double)gloom_longest;
    out[O_GLOOM_LEN_SUM] = gloom_len_sum;
    out[O_GLOOM_LEN_COUNT] = (double)gloom_len_count;
    out[O_PSTREAK_WEIGHTED] = pstreak_weighted;
    out[O_PSTREAK_LONGEST] = (double)pstreak_longest;
    out[O_PSTREAK_LEN_SUM] = pstreak_len_sum;
    out[O_PSTREAK_LEN_COUNT] = (double)pstreak_len_count;
    rc = 0;

done:
    free(slot_map); free(absolute); free(dt); free(dt_ord); free(dt_hour); free(eff);
    return rc;
}
