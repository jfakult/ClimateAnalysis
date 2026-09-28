"""Station -> satellite greenness by month (NDVI), from the grids built by
tools/build_ndvi_grid.py (compute/cache/ndvi_monthly_0p1.u8: 12 calendar-month grids
of 3600 x 1800 bytes at 0.1 degrees, row 0 = 90N, col 0 = 180W; byte 0 = no
data/ocean, else 1..255 = NDVI 0..1).

Plain bytes on purpose, so step 5 needs no imaging/array libraries.
"""

from pathlib import Path

GRID_PATH = Path(__file__).resolve().parent.parent / "cache" / "ndvi_monthly_0p1.u8"
COLS, ROWS, CELL_DEG = 3600, 1800, 0.1   # keep in sync with tools/build_ndvi_grid.py and js/ndvi.js
MONTH_BYTES = COLS * ROWS

_grid = None


def load():
    """The grid bytes, or None if tools/build_ndvi_grid.py hasn't been run."""
    global _grid
    if _grid is None:
        if not GRID_PATH.exists():
            return None
        _grid = GRID_PATH.read_bytes()
        if len(_grid) != 12 * MONTH_BYTES:
            raise RuntimeError(f"{GRID_PATH} is {len(_grid)} bytes, expected {12 * MONTH_BYTES}; rebuild it")
    return _grid


def monthly_ndvi_at(lat, lon, max_ring=3):
    """[Jan..Dec] NDVI (0-1) at a point: its own 0.1 degree cell, or, if that cell is
    no-data (a coastal/island station whose cell is mostly sea), the mean of the
    valid cells in the nearest ring that has any (up to max_ring cells away).
    None if the grid isn't built or nothing valid is nearby."""
    grid = load()
    if grid is None:
        return None
    row = min(ROWS - 1, max(0, int((90.0 - lat) / CELL_DEG)))
    col = int((lon + 180.0) / CELL_DEG) % COLS
    for ring in range(max_ring + 1):
        cells = []
        for r in range(max(0, row - ring), min(ROWS - 1, row + ring) + 1):
            for c in range(col - ring, col + ring + 1):
                if ring and max(abs(r - row), abs(c - col)) != ring:
                    continue   # only the new outer ring
                offset = r * COLS + (c % COLS)
                if grid[offset]:   # land is the same in every month (gaps were filled at build time)
                    cells.append(offset)
        if cells:
            return [(sum(grid[m * MONTH_BYTES + o] for o in cells) / len(cells) - 1) / 254.0 for m in range(12)]
    return None
