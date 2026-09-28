"""PURE-PYTHON REFERENCE for the line parser in common/isd_fast.c (not used by the
pipeline; see reference/parse_reference.py and tests/check_isd_fast.py).

Parser for raw NOAA ISD (full format, not ISD-Lite) hourly observation lines.

Layout reference (1-indexed positions from the NOAA ISD format document,
confirmed against sample data in isd-raw):
  5-10   USAF station id
  11-15  WBAN station id
  16-23  date YYYYMMDD
  24-27  time HHMM (UTC)
  42-46  report type (FM-15 METAR, FM-16 SPECI, FM-12 SYNOP, SOD/SOM summaries)
  66-69  wind speed, m/s x10, sentinel "9999"
  88-92  air temperature, C x10, signed, sentinel "+9999"
  94-98  dew point, C x10, signed, sentinel "+9999"
Positions 1-105 are the fixed mandatory section; everything after is the
variable-length "additional data" section, a concatenation of 3-letter-coded
groups (AA1=precip, GA1=cloud, MW/AW=present weather, ...), followed
optionally by a remarks section introduced by the literal "REM".

Rather than implementing NOAA's full code-length table for every possible
group, we regex-search directly for the few groups we need within the
additional-data substring (up to the "REM" marker).

For METAR/SPECI reports the remarks section carries the raw METAR text,
which is the only place some facts live:
  * Clear sky. Older/automated stations omit the GA cloud group entirely when
    the sky is clear; the METAR text says CLR/SKC/CAVOK.
  * Weather (and, for SYNOP, present-weather codes MW/AW). Many stations only emit a precip-amount (AA) group when it is
    actually precipitating, so a missing AA group can mean "dry"; the METAR
    text's present-weather tokens (-RA, SN, TSRA...) tell us which.
Deciding what "missing" means for precipitation is deliberately NOT done here
-- we only extract what each report says (see common/scoring.py
effective_days).
"""

import re

MANDATORY_LEN = 105

_AA_RE = re.compile(r"AA(\d)(\d{2})(\d{4})(\d)(\d)")
_GA_RE = re.compile(r"GA(\d)(\d{2})(\d)([+-]\d{5})(\d)(\d{2})(\d)")
_MW_RE = re.compile(r"MW\d(\d{2})\d")
_AW_RE = re.compile(r"AW\d(\d{2})\d")

# Precipitation actually falling at the station. Descriptors limited to
# SH/TS/FZ on purpose: BL/DR (blowing/drifting snow), VC (vicinity) and RE
# (recent) are not precipitation at the station right now.
# Searched over the whole METAR body (tokens are whitespace-separated, hence
# the (?<!\S)/(?!\S) token-boundary guards) instead of split() + a match per
# token: this is the hottest part of the parse.
_WET_TOKEN_RE = re.compile(r"(?<!\S)[+-]?(?:SH|TS|FZ)*(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP)+(?!\S)")
_LAYER_OKTAS = {"FEW": 2, "SCT": 4, "BKN": 6, "OVC": 8}
# group 1 = FEW/SCT/BKN/OVC layer, group 2 = vertical visibility (sky obscured),
# group 3 = clear-sky token (CLR, SKC, NSC, NCD, CAVOK)
_METAR_CLOUD_RE = re.compile(r"(?<!\S)(?:(FEW|SCT|BKN|OVC)\d{3}|(VV\d{3})|(?:CLR|SKC|NSC|NCD|CAVOK)(?!\S))")


def _parse_mandatory(line):
    date_raw = line[15:23]  # YYYYMMDD
    time_raw = line[23:27]  # HHMM
    wind_speed_raw = line[65:69]
    air_temp_raw = line[87:92]
    dew_point_raw = line[93:98]

    # We only ever want a "YYYY-MM-DD" string and an hour int, so slice the
    # raw fields instead of building a datetime object (~5x cheaper).
    date_str = date_raw[0:4] + "-" + date_raw[4:6] + "-" + date_raw[6:8]
    hour = int(time_raw[0:2])

    temp_c = int(air_temp_raw) / 10.0 if air_temp_raw != "+9999" else None
    dew_c = int(dew_point_raw) / 10.0 if dew_point_raw != "+9999" else None
    wind_mps = int(wind_speed_raw) / 10.0 if wind_speed_raw != "9999" else None

    return date_str, hour, temp_c, dew_c, wind_mps


def _extract_precip(add_section):
    """Returns (measured_1hr_mm, accumulation).

    measured_1hr_mm: depth from a 1-hour-period AA group, else None.
    accumulation: (period_hours, depth_mm) from the shortest 3-, 6-, 12- or
      24-hour AA group, else None. common/scoring.py spreads an accumulation
      across the observation slots it covers (12/24-hour totals only when
      they are zero: "nothing fell all day" is informative, "0.4 mm sometime
      today" is not)."""
    one_hr = None
    acc = None
    for m in _AA_RE.finditer(add_section):
        period, depth = m.group(2), m.group(3)
        if depth == "9999":
            continue
        if period == "01":
            one_hr = int(depth) / 10.0
        elif period in ("03", "06", "12", "24"):
            p = int(period)
            if acc is None or p < acc[0]:
                acc = (p, int(depth) / 10.0)
    return one_hr, acc


def _extract_cloud_pct(add_section):
    """Max coverage across all present GA groups (approximates total sky
    cover). Oktas (00-08) -> percent; 09 (obscured) -> 100%; 10 (partial
    obscuration, amount indeterminate) and 99 (missing) are skipped."""
    best = None
    for m in _GA_RE.finditer(add_section):
        coverage = m.group(2)
        if coverage == "99" or coverage == "10":
            continue
        pct = 100.0 if coverage == "09" else (int(coverage) / 8.0) * 100.0
        if best is None or pct > best:
            best = pct
    return best


def _metar_body(rem):
    """The raw METAR/SPECI text (up to the RMK section), or None."""
    for tag in ("METAR ", "SPECI "):
        i = rem.find(tag)
        if i != -1:
            body = rem[i + 6:]
            j = body.find(" RMK")
            return body[:j] if j != -1 else body
    return None


def _metar_cloud_pct(body):
    best = None
    for m in _METAR_CLOUD_RE.finditer(body):
        layer = m.group(1)
        if layer is not None:
            pct = _LAYER_OKTAS[layer] / 8.0 * 100.0
        elif m.group(2) is not None:
            pct = 100.0
        else:
            pct = 0.0
        if best is None or pct > best:
            best = pct
    return best


def parse_line(line):
    """Parse one raw ISD observation line into a flat dict, or None if the
    line is too short to contain a mandatory section at all.

    Keys: report_type, date_str, hour, temp_c, dew_c, wind_mps, and
      precip_mm  measured 1-hr depth (mm) or None
      wx_wet     True  = precipitation reported falling now
                 False = a METAR was present and reported none
                 None  = unknown (no METAR text and no weather group)
      cloud_pct  0-100 from GA groups, else from METAR text (CLR/SKC/layers),
                 else None
    """
    if len(line) < MANDATORY_LEN:
        return None

    date_str, hour, temp_c, dew_c, wind_mps = _parse_mandatory(line)

    rem_idx = line.find("REM", MANDATORY_LEN)
    if rem_idx != -1:
        add_section = line[MANDATORY_LEN:rem_idx]
        rem = line[rem_idx:]
    else:
        add_section = line[MANDATORY_LEN:]
        rem = ""

    # The plain substring checks are much cheaper than running a regex over
    # every line, and most lines lack most groups.
    precip_mm, precip_acc = _extract_precip(add_section) if "AA" in add_section else (None, None)
    cloud_pct = _extract_cloud_pct(add_section) if "GA" in add_section else None

    # Present-weather groups: a code saying "precipitation" makes the hour wet;
    # a code saying something else (clear, haze, fog...) is positive evidence
    # of no precipitation at the time -- the only dry evidence a SYNOP
    # station gives. Codes 20-27 and 29 mean precipitation in the PRECEDING
    # hour, so they prove nothing either way.
    wet_group = dry_group = False
    if "MW" in add_section:
        for m in _MW_RE.finditer(add_section):
            code = int(m.group(1))
            if code >= 50:
                wet_group = True
            elif code < 20 or code == 28 or 30 <= code < 50:
                dry_group = True
    if "AW" in add_section:
        for m in _AW_RE.finditer(add_section):
            code = int(m.group(1))
            if code >= 40:
                wet_group = True
            elif code < 20 or code == 28 or 30 <= code < 40:
                dry_group = True
    wx_wet = True if wet_group else (False if dry_group else None)

    body = _metar_body(rem) if rem else None
    if body is not None:
        if wx_wet is not True:
            if _WET_TOKEN_RE.search(body) is not None:
                wx_wet = True
            elif wx_wet is None:
                wx_wet = False
        if cloud_pct is None:
            cloud_pct = _metar_cloud_pct(body)

    return {
        "report_type": line[41:46].strip(),
        "date_str": date_str,
        "hour": hour,
        "temp_c": temp_c,
        "dew_c": dew_c,
        "wind_mps": wind_mps,
        "precip_mm": precip_mm,
        "precip_acc": precip_acc,
        "wx_wet": wx_wet,
        "cloud_pct": cloud_pct,
    }
