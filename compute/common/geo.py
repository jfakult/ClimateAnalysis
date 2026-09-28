"""Station coordinates -> local solar time and daylight, used to find each
station's daytime.

ISD timestamps are UTC. "Daytime" starts from local solar time: UTC hour +
longitude/15, rounded to a whole hour. Solar rather than civil time on
purpose -- no time-zone or daylight-saving tables, and it tracks when the sun
is actually up. On top of that, scoring only looks at hours that are BOTH
within the civil awake-hours window (common/scoring.py DAY_START_HOUR/
DAY_END_HOUR) AND actually past sunrise / before sunset that day (see
daylight_bounds below) -- a "perfect" reading at 6am in a Norwegian winter,
while it's still dark out, isn't a perfect day for anyone actually outside.
"""

import csv
import math
from pathlib import Path

ISD_HISTORY_PATH = Path(__file__).resolve().parent.parent / "isd-history.csv"

_coords = None


def utc_offset_for_lon(lon):
    """Whole-hour offset from UTC to local solar time."""
    return max(-12, min(12, int(round(lon / 15.0))))


def load_coordinates():
    """{station_id: (lat, lon)} from isd-history.csv (cached per process)."""
    global _coords
    if _coords is None:
        _coords = {}
        with open(ISD_HISTORY_PATH, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    _coords[f"{row['USAF']}-{row['WBAN']}"] = (float(row["LAT"]), float(row["LON"]))
                except ValueError:
                    continue
    return _coords


def utc_offset_for_station(station_id):
    """UTC -> local solar offset for a station; 0 if its longitude is unknown."""
    coord = load_coordinates().get(station_id)
    return 0 if coord is None else utc_offset_for_lon(coord[1])


def latitude_for_station(station_id):
    """Station latitude in degrees, or None if unknown (daylight_bounds then
    falls back to the fixed civil window -- see its docstring)."""
    coord = load_coordinates().get(station_id)
    return None if coord is None else coord[0]


# Sun's declination swings +-23.44 degrees (Earth's axial tilt) over the year;
# day 81 (~March 22) approximates the spring equinox, where it crosses zero.
EARTH_TILT_DEG = 23.44
EQUINOX_DAY_OF_YEAR = 81


def daylight_bounds(lat, day_of_year, day_start, day_end):
    """Local-solar-hour (day_start, day_end) narrowed to the hours that are
    both in that fixed civil window AND actually between sunrise and sunset
    on this day, at this latitude -- a standard sunrise/sunset approximation
    (ignores the equation of time and refraction, consistent with the whole-
    -hour-rounded "local solar time" this whole pipeline already uses).
    Sunrise rounds up and sunset rounds down, so only hours entirely inside
    daylight count (a hint of dawn/dusk in an hour's first/last minutes isn't
    enough). lat=None (unknown) returns (day_start, day_end) unchanged.

    Near the poles the sun can stay up all day (returns (day_start, day_end)
    unchanged) or never rise (returns an empty window, end <= start) -- both
    fall out of the same formula with no special-casing needed."""
    if lat is None:
        return day_start, day_end
    decl = math.radians(EARTH_TILT_DEG) * math.sin(2 * math.pi / 365.0 * (day_of_year - EQUINOX_DAY_OF_YEAR))
    cos_h = -math.tan(math.radians(lat)) * math.tan(decl)
    if cos_h <= -1.0:
        half_day = 12.0    # polar day: sun never sets
    elif cos_h >= 1.0:
        half_day = 0.0     # polar night: sun never rises
    else:
        half_day = math.degrees(math.acos(cos_h)) / 15.0
    sunrise, sunset = 12.0 - half_day, 12.0 + half_day
    return max(day_start, math.ceil(sunrise)), min(day_end, math.floor(sunset))
