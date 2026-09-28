// Vegetation (satellite greenness) background layer. Same machinery as the Koppen
// layer (js/koppen.js): a grayscale PNG grid drawn as flat cells, sampled by
// screen position.
//
// Data: data/ndvi.png, built by compute/tools/build_ndvi_grid.py -- 3600 x 1800 at
// 0.1 degree (row 0 = 90N, col 0 = 180W); pixel 0 = no data/ocean, else 1..255 =
// mean annual NDVI 0..1. Source: OpenGeoHub / OpenLandMap monthly NDVI from NOAA
// AVHRR + MODIS (CC BY 4.0), https://zenodo.org/records/4305975

// NDVI 0..1 -> color: bare tan -> pale green -> deep green (matches the Vegetation
// score ramp in compute/common/scoring.py: 0.10 = bare, 0.70 = dense).
const NDVI_STOPS = [
  [0.00, [222, 200, 160]],
  [0.10, [214, 190, 140]],
  [0.30, [198, 208, 120]],
  [0.50, [120, 180, 80]],
  [0.70, [40, 140, 50]],
  [0.90, [10, 95, 40]],
];

const NDVI_PALETTE = (() => {
  const table = new Array(256).fill(null);
  for (let v = 1; v < 256; v++) {
    const t = (v - 1) / 254;
    let i = 1;
    while (i < NDVI_STOPS.length - 1 && t > NDVI_STOPS[i][0]) i++;
    const [t0, c0] = NDVI_STOPS[i - 1];
    const [t1, c1] = NDVI_STOPS[i];
    const f = Math.max(0, Math.min(1, (t - t0) / (t1 - t0)));
    table[v] = `rgb(${c0.map((c, k) => Math.round(c + (c1[k] - c) * f)).join(',')})`;
  }
  return table;
})();

const NdviLayer = KoppenLayer.extend({
  options: {
    pane: 'ndvi',
    zIndex: 251,
    cols: 3600,
    rows: 1800,
    cellDeg: 0.1,
    cellPx: 4,
    opacity: 0.8,
  },

  _colorFor(code) {
    return NDVI_PALETTE[code];
  },
});
