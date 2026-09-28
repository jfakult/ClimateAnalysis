/* C implementation of "parse one raw ISD file into one merged record per
 * (date, hour)". It reproduces common/isd_format.py parse_line() plus
 * 3_parse_station_year.py merge_hour() exactly (the Python code stays the
 * reference implementation and the fallback); tests compare the two.
 *
 * Build:  cc -O2 -shared -fPIC -o libisd_fast.so isd_fast.c
 * (common/isd_fast.py does this automatically when a compiler is present.)
 *
 * Missing values are NaN. Returns the number of hour groups written, or -1
 * if the file contains something unexpected (non-digit where digits are
 * required, ...), in which case the caller falls back to the Python parser.
 */
#include <stdlib.h>
#include <string.h>

#define MANDATORY_LEN 105

typedef struct {
    int key;          /* YYYYMMDDHH */
    int idx;          /* line order, for stable sort / "later report wins" */
    int special;      /* FM-16 SPECI */
    int acc_period;   /* 0 = none, else 3 or 6 */
    int wet;          /* 0 unknown, 1 dry (False), 2 wet (True) */
    double temp, dew, wind, cloud, precip, acc_depth;
} Rec;

static const double NANV = __builtin_nan("");

static int is_nan(double x) { return x != x; }
static int dig(char c) { return (unsigned char)(c - '0') < 10; }
static int is_space(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r';
}

/* first index in [lo, hi) where needle occurs, or -1 */
static long find(const char *s, long lo, long hi, const char *needle, long nlen) {
    for (long i = lo; i + nlen <= hi; i++) {
        if (s[i] == needle[0] && memcmp(s + i, needle, nlen) == 0) return i;
    }
    return -1;
}

static int is_phenomenon(char a, char b) {
    return (a == 'D' && b == 'Z') || (a == 'R' && b == 'A') || (a == 'S' && b == 'N') ||
           (a == 'S' && b == 'G') || (a == 'I' && b == 'C') || (a == 'P' && b == 'L') ||
           (a == 'G' && b == 'R') || (a == 'G' && b == 'S') || (a == 'U' && b == 'P');
}

/* ^[+-]?(SH|TS|FZ)*(DZ|RA|SN|SG|IC|PL|GR|GS|UP)+$ */
static int wet_token(const char *t, int n) {
    int i = 0, cnt = 0;
    if (i < n && (t[i] == '+' || t[i] == '-')) i++;
    while (i + 1 < n && ((t[i] == 'S' && t[i + 1] == 'H') || (t[i] == 'T' && t[i + 1] == 'S') ||
                         (t[i] == 'F' && t[i + 1] == 'Z')))
        i += 2;
    while (i + 1 < n && is_phenomenon(t[i], t[i + 1])) { i += 2; cnt++; }
    return cnt > 0 && i == n;
}

/* METAR body [b0, b1): returns 1 if any token is wet; sets *cloud (NaN if no sky token) */
static int scan_metar_body(const char *s, long b0, long b1, double *cloud) {
    int wet = 0;
    double best = NANV;
    long i = b0;
    while (i < b1) {
        while (i < b1 && is_space(s[i])) i++;
        long ts = i;
        while (i < b1 && !is_space(s[i])) i++;
        int tl = (int)(i - ts);
        if (tl == 0) continue;
        const char *t = s + ts;
        if (!wet && wet_token(t, tl)) wet = 1;

        double pct = NANV;
        if ((tl == 3 && (memcmp(t, "CLR", 3) == 0 || memcmp(t, "SKC", 3) == 0 ||
                         memcmp(t, "NSC", 3) == 0 || memcmp(t, "NCD", 3) == 0)) ||
            (tl == 5 && memcmp(t, "CAVOK", 5) == 0)) {
            pct = 0.0;
        } else if (tl >= 5 && t[0] == 'V' && t[1] == 'V' && dig(t[2]) && dig(t[3]) && dig(t[4])) {
            pct = 100.0;
        } else if (tl >= 6 && dig(t[3]) && dig(t[4]) && dig(t[5])) {
            int oktas = 0;
            if (memcmp(t, "FEW", 3) == 0) oktas = 2;
            else if (memcmp(t, "SCT", 3) == 0) oktas = 4;
            else if (memcmp(t, "BKN", 3) == 0) oktas = 6;
            else if (memcmp(t, "OVC", 3) == 0) oktas = 8;
            if (oktas) pct = oktas / 8.0 * 100.0;
        }
        if (!is_nan(pct) && (is_nan(best) || pct > best)) best = pct;
    }
    *cloud = best;
    return wet;
}

/* 1 = record filled, 0 = skipped line, -1 = unexpected content */
static int parse_line(const char *s, long n, Rec *r) {
    if (n < MANDATORY_LEN) return 0;

    /* report type = line[41:46].strip() */
    {
        const char *t = s + 41;
        int a = 0, b = 5;
        while (a < b && is_space(t[a])) a++;
        while (b > a && is_space(t[b - 1])) b--;
        int tl = b - a;
        if (tl == 3 && t[a] == 'S' && t[a + 1] == 'O' &&
            (t[a + 2] == 'D' || t[a + 2] == 'M' || t[a + 2] == 'Y'))
            return 0; /* SOD / SOM / SOY summary records */
        r->special = (tl == 5 && memcmp(t + a, "FM-16", 5) == 0);
    }

    for (int i = 15; i < 25; i++) if (!dig(s[i])) return -1;
    long ymd = 0;
    for (int i = 15; i < 23; i++) ymd = ymd * 10 + (s[i] - '0');
    int hh = (s[23] - '0') * 10 + (s[24] - '0');
    r->key = (int)(ymd * 100 + hh);

    /* temperature / dew point: sign + 4 digits, "+9999" = missing */
    for (int f = 0; f < 2; f++) {
        const char *p = s + (f == 0 ? 87 : 93);
        double v;
        if (memcmp(p, "+9999", 5) == 0) {
            v = NANV;
        } else {
            if ((p[0] != '+' && p[0] != '-') || !dig(p[1]) || !dig(p[2]) || !dig(p[3]) || !dig(p[4])) return -1;
            int iv = (p[1] - '0') * 1000 + (p[2] - '0') * 100 + (p[3] - '0') * 10 + (p[4] - '0');
            if (p[0] == '-') iv = -iv;
            v = iv / 10.0;
        }
        if (f == 0) r->temp = v; else r->dew = v;
    }
    {
        const char *p = s + 65;
        if (memcmp(p, "9999", 4) == 0) {
            r->wind = NANV;
        } else {
            if (!dig(p[0]) || !dig(p[1]) || !dig(p[2]) || !dig(p[3])) return -1;
            r->wind = ((p[0] - '0') * 1000 + (p[1] - '0') * 100 + (p[2] - '0') * 10 + (p[3] - '0')) / 10.0;
        }
    }

    long rem = find(s, MANDATORY_LEN, n, "REM", 3);
    long lim = rem != -1 ? rem : n;   /* additional-data section is [105, lim) */

    r->precip = NANV;
    r->acc_period = 0;
    r->acc_depth = NANV;
    r->cloud = NANV;

    /* AA groups: precipitation */
    for (long i = MANDATORY_LEN; i + 11 <= lim;) {
        if (s[i] == 'A' && s[i + 1] == 'A' && dig(s[i + 2]) && dig(s[i + 3]) && dig(s[i + 4]) &&
            dig(s[i + 5]) && dig(s[i + 6]) && dig(s[i + 7]) && dig(s[i + 8]) && dig(s[i + 9]) &&
            dig(s[i + 10])) {
            int period = (s[i + 3] - '0') * 10 + (s[i + 4] - '0');
            int depth = (s[i + 5] - '0') * 1000 + (s[i + 6] - '0') * 100 + (s[i + 7] - '0') * 10 + (s[i + 8] - '0');
            if (depth != 9999) {
                if (period == 1) {
                    r->precip = depth / 10.0;
                } else if (period == 3 || period == 6 || period == 12 || period == 24) {
                    if (r->acc_period == 0 || period < r->acc_period) {
                        r->acc_period = period;
                        r->acc_depth = depth / 10.0;
                    }
                }
            }
            i += 11;
        } else {
            i++;
        }
    }

    /* GA groups: sky cover, max coverage */
    for (long i = MANDATORY_LEN; i + 16 <= lim;) {
        if (s[i] == 'G' && s[i + 1] == 'A' && dig(s[i + 2]) && dig(s[i + 3]) && dig(s[i + 4]) &&
            dig(s[i + 5]) && (s[i + 6] == '+' || s[i + 6] == '-') && dig(s[i + 7]) && dig(s[i + 8]) &&
            dig(s[i + 9]) && dig(s[i + 10]) && dig(s[i + 11]) && dig(s[i + 12]) && dig(s[i + 13]) &&
            dig(s[i + 14]) && dig(s[i + 15])) {
            int cov = (s[i + 3] - '0') * 10 + (s[i + 4] - '0');
            if (cov != 99 && cov != 10) {
                double pct = cov == 9 ? 100.0 : (cov / 8.0) * 100.0;
                if (is_nan(r->cloud) || pct > r->cloud) r->cloud = pct;
            }
            i += 16;
        } else {
            i++;
        }
    }

    /* MW (manual) / AW (automated) present-weather codes: >= 50 (AW: >= 40)
     * is precipitation; a code below that which is not "precipitation in the
     * preceding hour" (20-27, 29) is evidence of no precipitation now. */
    int wet_group = 0, dry_group = 0;
    for (int pass = 0; pass < 2; pass++) {
        char c0 = pass == 0 ? 'M' : 'A';
        int thresh = pass == 0 ? 50 : 40;
        for (long i = MANDATORY_LEN; i + 6 <= lim;) {
            if (s[i] == c0 && s[i + 1] == 'W' && dig(s[i + 2]) && dig(s[i + 3]) && dig(s[i + 4]) && dig(s[i + 5])) {
                int code = (s[i + 3] - '0') * 10 + (s[i + 4] - '0');
                if (code >= thresh) wet_group = 1;
                else if (code < 20 || code == 28 || (code >= 30 && code < thresh)) dry_group = 1;
                i += 6;
            } else {
                i++;
            }
        }
    }
    r->wet = wet_group ? 2 : (dry_group ? 1 : 0);

    /* raw METAR text in the remarks */
    if (rem != -1) {
        long t = find(s, rem, n, "METAR ", 6);
        if (t == -1) t = find(s, rem, n, "SPECI ", 6);
        if (t != -1) {
            long b0 = t + 6;
            long j = find(s, b0, n, " RMK", 4);
            long b1 = j != -1 ? j : n;
            double mc;
            int wet = scan_metar_body(s, b0, b1, &mc);
            if (r->wet != 2) {
                if (wet) r->wet = 2;
                else if (r->wet == 0) r->wet = 1;
            }
            if (is_nan(r->cloud)) r->cloud = mc;
        }
    }
    return 1;
}

static int cmp_rec(const void *a, const void *b) {
    const Rec *x = a, *y = b;
    if (x->key != y->key) return x->key < y->key ? -1 : 1;
    return x->idx < y->idx ? -1 : (x->idx > y->idx);
}

static double first_valid(const Rec *g, int n, int field) {
    /* routine reports latest-first, then specials latest-first */
    for (int pass = 0; pass < 2; pass++) {
        for (int i = n - 1; i >= 0; i--) {
            if (g[i].special != pass) continue;
            double v = field == 0 ? g[i].temp : field == 1 ? g[i].dew : field == 2 ? g[i].wind : g[i].cloud;
            if (!is_nan(v)) return v;
        }
    }
    return NANV;
}

long isd_parse(const char *buf, long len, long cap,
               int *o_key, double *o_temp, double *o_dew, double *o_wind, double *o_cloud,
               double *o_precip, double *o_acc_depth, signed char *o_acc_period, signed char *o_wet) {
    Rec *recs = malloc(sizeof(Rec) * (cap > 0 ? cap : 1));
    if (!recs) return -1;
    long nrec = 0;
    long pos = 0;
    while (pos < len) {
        const char *nl = memchr(buf + pos, '\n', len - pos);
        long end = nl ? nl - buf : len;
        long n = end - pos;
        if (n > 0 && buf[pos + n - 1] == '\r') n--;
        if (nrec >= cap) { free(recs); return -1; }
        int rc = parse_line(buf + pos, n, &recs[nrec]);
        if (rc < 0) { free(recs); return -1; }
        if (rc == 1) { recs[nrec].idx = (int)nrec; nrec++; }
        pos = end + 1;
    }

    int sorted = 1;
    for (long i = 1; i < nrec; i++) if (recs[i].key < recs[i - 1].key) { sorted = 0; break; }
    if (!sorted) qsort(recs, nrec, sizeof(Rec), cmp_rec);

    long ng = 0;
    for (long i = 0; i < nrec;) {
        long j = i + 1;
        while (j < nrec && recs[j].key == recs[i].key) j++;
        int n = (int)(j - i);
        const Rec *g = recs + i;

        o_key[ng] = g[0].key;
        o_temp[ng] = first_valid(g, n, 0);
        o_dew[ng] = first_valid(g, n, 1);
        o_wind[ng] = first_valid(g, n, 2);
        o_cloud[ng] = first_valid(g, n, 3);

        double pmax = NANV;
        int any_true = 0, any_false = 0;
        for (int k = 0; k < n; k++) {
            if (!is_nan(g[k].precip) && (is_nan(pmax) || g[k].precip > pmax)) pmax = g[k].precip;
            if (g[k].wet == 2) any_true = 1; else if (g[k].wet == 1) any_false = 1;
        }
        o_precip[ng] = pmax;
        o_wet[ng] = any_true ? 2 : (any_false ? 1 : 0);

        o_acc_period[ng] = 0;
        o_acc_depth[ng] = NANV;
        for (int pass = 0; pass < 2 && !o_acc_period[ng]; pass++) {
            for (int k = n - 1; k >= 0; k--) {
                if (g[k].special != pass || g[k].acc_period == 0) continue;
                o_acc_period[ng] = (signed char)g[k].acc_period;
                o_acc_depth[ng] = g[k].acc_depth;
                break;
            }
        }
        ng++;
        i = j;
    }
    free(recs);
    return ng;
}
