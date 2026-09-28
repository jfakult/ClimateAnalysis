// Köppen-Geiger climate classification background layer.
//
// Data: data/koppen.png, a 720x360 grayscale image where pixel value = climate
// class code (0-30, 0 = ocean/no data), one pixel per 0.5x0.5 degree cell,
// covering the whole globe (row 0 = 90N, col 0 = 180W). It is a lossless
// re-encoding of Beck_KG_V1_present_0p5.tif from:
//   Beck, H.E., N.E. Zimmermann, T.R. McVicar, N. Vergopolan, A. Berg, E.F.
//   Wood (2018): "Present and future Köppen-Geiger climate classification
//   maps at 1-km resolution", Scientific Data 5:180214. CC BY 4.0.
//   https://doi.org/10.1038/sdata.2018.214 -- figshare 10.6084/m9.figshare.6396959
// (verified pixel-for-pixel identical to the source .tif; see compute/ notes)
//
// Classes are discrete, so this draws flat-colored blocks (nearest-neighbor
// sampling), never blended -- unlike the "smooth between stations" layer,
// blending two adjacent classes' colors would invent a meaningless color.

// [code]: [abbreviation, description, [r,g,b]] -- from Beck et al.'s legend.txt.
const KOPPEN_CLASSES = {
  1: ['Af', 'Tropical, rainforest', [0, 0, 255]],
  2: ['Am', 'Tropical, monsoon', [0, 120, 255]],
  3: ['Aw', 'Tropical, savannah', [70, 170, 250]],
  4: ['BWh', 'Arid, desert, hot', [255, 0, 0]],
  5: ['BWk', 'Arid, desert, cold', [255, 150, 150]],
  6: ['BSh', 'Arid, steppe, hot', [245, 165, 0]],
  7: ['BSk', 'Arid, steppe, cold', [255, 220, 100]],
  8: ['Csa', 'Temperate, dry summer, hot summer', [255, 255, 0]],
  9: ['Csb', 'Temperate, dry summer, warm summer', [200, 200, 0]],
  10: ['Csc', 'Temperate, dry summer, cold summer', [150, 150, 0]],
  11: ['Cwa', 'Temperate, dry winter, hot summer', [150, 255, 150]],
  12: ['Cwb', 'Temperate, dry winter, warm summer', [100, 200, 100]],
  13: ['Cwc', 'Temperate, dry winter, cold summer', [50, 150, 50]],
  14: ['Cfa', 'Temperate, no dry season, hot summer', [200, 255, 80]],
  15: ['Cfb', 'Temperate, no dry season, warm summer', [100, 255, 80]],
  16: ['Cfc', 'Temperate, no dry season, cold summer', [50, 200, 0]],
  17: ['Dsa', 'Cold, dry summer, hot summer', [255, 0, 255]],
  18: ['Dsb', 'Cold, dry summer, warm summer', [200, 0, 200]],
  19: ['Dsc', 'Cold, dry summer, cold summer', [150, 50, 150]],
  20: ['Dsd', 'Cold, dry summer, very cold winter', [150, 100, 150]],
  21: ['Dwa', 'Cold, dry winter, hot summer', [170, 175, 255]],
  22: ['Dwb', 'Cold, dry winter, warm summer', [90, 120, 220]],
  23: ['Dwc', 'Cold, dry winter, cold summer', [75, 80, 180]],
  24: ['Dwd', 'Cold, dry winter, very cold winter', [50, 0, 135]],
  25: ['Dfa', 'Cold, no dry season, hot summer', [0, 255, 255]],
  26: ['Dfb', 'Cold, no dry season, warm summer', [55, 200, 255]],
  27: ['Dfc', 'Cold, no dry season, cold summer', [0, 125, 125]],
  28: ['Dfd', 'Cold, no dry season, very cold winter', [0, 70, 95]],
  29: ['ET', 'Polar, tundra', [178, 178, 178]],
  30: ['EF', 'Polar, frost', [102, 102, 102]],
};

const KoppenLayer = L.Layer.extend({
  options: {
    pane: 'koppen',
    zIndex: 250,
    cols: 720,
    rows: 360,
    cellDeg: 0.5,
    cellPx: 5,        // computation grid; drawn crisp (no smoothing) so class edges stay sharp
    opacity: 0.75,
    padding: 0.25,
  },

  initialize(options) {
    L.setOptions(this, options);
    this._grid = null; // Uint8ClampedArray, one class code per cell, row-major
    this._raf = null;
  },

  // url: data/koppen.png. Returns a promise that resolves once _grid is ready.
  load(url) {
    if (this._loading) return this._loading;
    this._loading = new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const { cols, rows } = this.options;
        const canvas = document.createElement('canvas');
        canvas.width = cols;
        canvas.height = rows;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
        const data = ctx.getImageData(0, 0, cols, rows).data;
        const grid = new Uint8ClampedArray(cols * rows);
        for (let i = 0; i < grid.length; i++) grid[i] = data[i * 4]; // grayscale: R=G=B=class code
        this._grid = grid;
        this.refresh();
        resolve();
      };
      img.onerror = () => reject(new Error(`Failed to load ${url}`));
      img.src = url;
    });
    return this._loading;
  },

  onAdd(map) {
    this._map = map;
    if (!map.getPane(this.options.pane)) {
      const pane = map.createPane(this.options.pane);
      pane.style.zIndex = this.options.zIndex;   // above tiles (200), below the smoothing layer (350) and dots (400)
      pane.style.pointerEvents = 'none';
    }
    this._canvas = L.DomUtil.create('canvas', 'leaflet-zoom-hide', map.getPane(this.options.pane));
    this._canvas.style.opacity = this.options.opacity;
    map.on('moveend zoomend resize', this.refresh, this);
    map.on('zoomstart', this._hide, this);
    this.refresh();
  },

  onRemove(map) {
    map.off('moveend zoomend resize', this.refresh, this);
    map.off('zoomstart', this._hide, this);
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    L.DomUtil.remove(this._canvas);
    this._canvas = null;
  },

  _hide() {
    if (this._canvas) this._canvas.style.visibility = 'hidden';
  },

  refresh() {
    if (!this._map || this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = null;
      this._redraw();
    });
  },

  // grid value -> CSS color, or null to leave the cell transparent. Subclasses
  // (js/ndvi.js) override this; here the value is a Köppen class code.
  _colorFor(code) {
    const cls = KOPPEN_CLASSES[code];
    if (!cls) return null;
    return `rgb(${cls[2][0]},${cls[2][1]},${cls[2][2]})`;
  },

  // (lat, lon) -> class code, or 0 if out of range/ocean/no data.
  classAt(lat, lon) {
    if (!this._grid || lat > 90 || lat < -90) return 0;
    const { cols, rows, cellDeg } = this.options;
    const col = Math.min(cols - 1, Math.max(0, Math.floor((lon + 180) / cellDeg)));
    const row = Math.min(rows - 1, Math.max(0, Math.floor((90 - lat) / cellDeg)));
    return this._grid[row * cols + col];
  },

  _redraw() {
    const map = this._map;
    if (!map || !this._canvas || !this._grid) return;
    const canvas = this._canvas;
    canvas.style.visibility = 'visible';

    const size = map.getSize();
    const pad = this.options.padding;
    const w = Math.ceil(size.x * (1 + 2 * pad));
    const h = Math.ceil(size.y * (1 + 2 * pad));
    const topLeft = map.containerPointToLayerPoint([-size.x * pad, -size.y * pad]).round();
    canvas.width = w;
    canvas.height = h;
    L.DomUtil.setPosition(canvas, topLeft);

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    ctx.imageSmoothingEnabled = false;

    const cell = this.options.cellPx;
    for (let py = 0; py < h; py += cell) {
      for (let px = 0; px < w; px += cell) {
        const ll = map.layerPointToLatLng(L.point(topLeft.x + px + cell / 2, topLeft.y + py + cell / 2));
        if (ll.lat > 90 || ll.lat < -90) continue;
        const lon = ((((ll.lng + 180) % 360) + 360) % 360) - 180;
        const code = this.classAt(ll.lat, lon);
        if (!code) continue; // 0 = ocean/no data: leave transparent
        const rgb = this._colorFor(code);
        if (!rgb) continue;
        ctx.fillStyle = rgb;
        ctx.fillRect(px, py, cell, cell);
      }
    }
  },
});
