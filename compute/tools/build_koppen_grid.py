#!/usr/bin/env python3
"""One-time data prep: build data/koppen.png, the grid the Köppen climate
zones map overlay uses (js/koppen.js).

    pip install pillow          # if not already installed
    python3 tools/build_koppen_grid.py

Source: Beck, H.E., N.E. Zimmermann, T.R. McVicar, N. Vergopolan, A. Berg, E.F.
Wood (2018), "Present and future Köppen-Geiger climate classification maps at
1-km resolution", Scientific Data 5:180214, CC BY 4.0.
https://doi.org/10.1038/sdata.2018.214 -- figshare 10.6084/m9.figshare.6396959

Downloads Beck_KG_V1.zip (~71 MB, cached in compute/cache/koppen_src/ so a
re-run doesn't re-download), pulls out Beck_KG_V1_present_0p5.tif -- already a
720x360 grid at 0.5 degrees, one pixel per cell, pixel value = Köppen class
code (0 = ocean/no data, else 1-30; see js/koppen.js KOPPEN_CLASSES for what
each code means) -- and copies it byte-for-byte into data/koppen.png.

The one thing to get right: the source TIFF is a PALETTE image (mode "P")
whose pixel VALUES are the class codes, but its palette happens to hold
near-grayscale colors. Image.open(...).convert("L") would reinterpret through
that palette (RGB -> luminance) and destroy the class-code values -- this
copies the raw palette indices directly instead.
"""

import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image

COMPUTE_DIR = Path(__file__).resolve().parent.parent
WEATHER2_DIR = COMPUTE_DIR.parent

FIGSHARE_ARTICLE = "https://api.figshare.com/v2/articles/6396959"
ZIP_MEMBER = "Beck_KG_V1_present_0p5.tif"


def find_download_url():
    with urllib.request.urlopen(FIGSHARE_ARTICLE, timeout=60) as r:
        article = json.load(r)
    for f in article["files"]:
        if f["name"] == "Beck_KG_V1.zip":
            return f["download_url"], f["size"]
    sys.exit("Beck_KG_V1.zip not found in the figshare record (it may have been renamed/moved)")


def fetch_cached(url, size, dest):
    if dest.exists() and dest.stat().st_size == size:
        print(f"using cached {dest}")
        return
    part = dest.with_suffix(".part")
    print(f"downloading {url} ({size / 1e6:.0f} MB)...")
    with urllib.request.urlopen(url, timeout=120) as r, open(part, "wb") as out:
        while chunk := r.read(1 << 20):
            out.write(chunk)
    part.rename(dest)


def main():
    src_dir = COMPUTE_DIR / "cache" / "koppen_src"
    src_dir.mkdir(parents=True, exist_ok=True)
    zip_path = src_dir / "Beck_KG_V1.zip"

    url, size = find_download_url()
    fetch_cached(url, size, zip_path)

    with zipfile.ZipFile(zip_path) as z:
        with z.open(ZIP_MEMBER) as f:
            src = Image.open(io.BytesIO(f.read()))
            src.load()

    if src.mode != "P":
        sys.exit(f"expected a palette ('P') image, got {src.mode} -- the source file may have changed")

    # Copy the raw palette INDICES (the class codes), not an RGB conversion of them.
    grid = Image.new("L", src.size)
    grid.putdata(list(src.getdata()))

    out_path = WEATHER2_DIR / "data" / "koppen.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    grid.save(out_path, optimize=True)

    codes = set(grid.getdata())
    print(f"wrote {out_path} ({out_path.stat().st_size / 1e3:.0f} KB), "
          f"{grid.size[0]}x{grid.size[1]}, class codes present: {sorted(codes)}")


if __name__ == "__main__":
    main()
