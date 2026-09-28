// "Smooth between stations": a color surface estimated from the stations,
// drawn under the dots and clipped to land.
//
// Method: inverse-distance weighting (Shepard). Each pixel cell takes the
// weighted average of its nearest stations (weight 1 / (distance^2 + s^2), the
// small softening term s stops a bullseye around every station). Cells fade
// out with distance to the nearest station so sparse regions stay blank
// instead of inventing values. It is a visual estimate only: mountains,
// coastlines and lakes are not modeled.
//
// The surface is computed on a coarse grid (cellPx), upscaled with smoothing,
// then masked with land polygons (Natural Earth via world-atlas).

const InterpolationLayer = L.Layer.extend({
  options: {
    pane: 'interpolation',
    cellPx: 8,          // computation grid; upscaled smoothly
    maxRadiusKm: 300,   // no color farther than this from any station
    neighbors: 8,       // stations averaged per cell
    softenKm: 15,
    opacity: 0.7,
    padding: 0.25,      // canvas extends this fraction beyond the view on each side
  },

  initialize(options) {
    L.setOptions(this, options);
    this._range = [-Infinity, Infinity];
    this._land = null;
    this._lut = null;
    this._raf = null;
    const k = this.options.neighbors;
    this._kd2 = new Float64Array(k);
    this._kj = new Int32Array(k);
  },

  // stations: [{lat, lon}, ...]. Builds a 2-degree bucket index.
  setStations(stations) {
    const n = stations.length;
    this._n = n;
    this._lat = new Float64Array(n);
    this._lon = new Float64Array(n);
    this._buckets = new Map();
    for (let i = 0; i < n; i++) {
      this._lat[i] = stations[i].lat;
      this._lon[i] = stations[i].lon;
      const key = this._bucketKey(stations[i].lat, stations[i].lon);
      const list = this._buckets.get(key);
      if (list) list.push(i); else this._buckets.set(key, [i]);
    }
    this._values = null;
  },

  // values: one number per station; ratioOf(value) -> 0..1 color position.
  setMetric(values, ratioOf) {
    this._values = Float64Array.from(values);
    this._lut = new Array(256);
    for (let i = 0; i < 256; i++) this._lut[i] = null;
    this._ratioOf = ratioOf;
    this.refresh();
  },

  setRange(lo, hi) {
    this._range = [lo, hi];
    this.refresh();
  },

  // GeoJSON Feature/FeatureCollection of land polygons.
  setLand(geojson) {
    const rings = [];
    const addPolygon = (polygon) => {
      for (const ring of polygon) {
        const pts = new Float32Array(ring.length * 2);
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        for (let i = 0; i < ring.length; i++) {
          const [x, y] = ring[i];
          pts[2 * i] = x;
          pts[2 * i + 1] = y;
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
        rings.push({ pts, minX, maxX, minY, maxY });
      }
    };
    const addGeometry = (g) => {
      if (!g) return;
      if (g.type === 'Polygon') addPolygon(g.coordinates);
      else if (g.type === 'MultiPolygon') g.coordinates.forEach(addPolygon);
      else if (g.type === 'GeometryCollection') g.geometries.forEach(addGeometry);
    };
    if (geojson.type === 'FeatureCollection') geojson.features.forEach((f) => addGeometry(f.geometry));
    else if (geojson.type === 'Feature') addGeometry(geojson.geometry);
    else addGeometry(geojson);
    this._land = rings;
    this.refresh();
  },

  onAdd(map) {
    this._map = map;
    if (!map.getPane(this.options.pane)) {
      const pane = map.createPane(this.options.pane);
      pane.style.zIndex = 350;      // above tiles (200), below the station dots (400)
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

  // ---- spatial lookup ----

  _bucketKey(lat, lon) {
    const B = 2;
    const iy = Math.min(90, Math.max(0, Math.floor((lat + 90) / B)));
    const ix = Math.min(179, Math.max(0, Math.floor((lon + 180) / B)));
    return iy * 180 + ix;
  },

  // Sets this._outValue / this._outDist; false if no station within range.
  _sample(lat, lon) {
    const B = 2;
    const KM = 111.195;
    const { maxRadiusKm: R, neighbors: K, softenKm } = this.options;
    const R2 = R * R;
    const cosLat = Math.max(0.05, Math.cos((lat * Math.PI) / 180));
    const dLat = R / KM;
    const dLon = Math.min(180, R / (KM * cosLat));
    const iy0 = Math.max(0, Math.floor((lat - dLat + 90) / B));
    const iy1 = Math.min(90, Math.floor((lat + dLat + 90) / B));
    const ix0 = Math.floor((lon - dLon + 180) / B);
    const ix1 = Math.floor((lon + dLon + 180) / B);
    const cols = ix1 - ix0 + 1 >= 180 ? 180 : ix1 - ix0 + 1;

    const kd2 = this._kd2;
    const kj = this._kj;
    let found = 0;
    for (let iy = iy0; iy <= iy1; iy++) {
      for (let c = 0; c < cols; c++) {
        const ix = (((ix0 + c) % 180) + 180) % 180;
        const list = this._buckets.get(iy * 180 + ix);
        if (!list) continue;
        for (let m = 0; m < list.length; m++) {
          const j = list[m];
          let dlon = this._lon[j] - lon;
          if (dlon > 180) dlon -= 360; else if (dlon < -180) dlon += 360;
          const dx = dlon * cosLat * KM;
          const dy = (this._lat[j] - lat) * KM;
          const d2 = dx * dx + dy * dy;
          if (d2 > R2) continue;
          // keep the K nearest, sorted ascending
          let pos = found < K ? found : K;
          if (found >= K && d2 >= kd2[K - 1]) continue;
          if (found < K) found++;
          while (pos > 0 && kd2[pos - 1] > d2) {
            if (pos < K) { kd2[pos] = kd2[pos - 1]; kj[pos] = kj[pos - 1]; }
            pos--;
          }
          kd2[pos] = d2;
          kj[pos] = j;
        }
      }
    }
    if (found === 0) return false;

    const s2 = softenKm * softenKm;
    let sw = 0;
    let sv = 0;
    for (let i = 0; i < found; i++) {
      const w = 1 / (kd2[i] + s2);
      sw += w;
      sv += w * this._values[kj[i]];
    }
    this._outValue = sv / sw;
    this._outDist = Math.sqrt(kd2[0]);
    return true;
  },

  // ---- drawing ----

  _redraw() {
    const map = this._map;
    if (!map || !this._canvas) return;
    const canvas = this._canvas;
    canvas.style.visibility = 'visible';
    if (!this._values || !this._land) {
      canvas.width = canvas.height = 0; // nothing until we have data and a coastline
      return;
    }

    const size = map.getSize();
    const pad = this.options.padding;
    const w = Math.ceil(size.x * (1 + 2 * pad));
    const h = Math.ceil(size.y * (1 + 2 * pad));
    const topLeft = map.containerPointToLayerPoint([-size.x * pad, -size.y * pad]).round();
    canvas.width = w;
    canvas.height = h;
    L.DomUtil.setPosition(canvas, topLeft);

    const cell = this.options.cellPx;
    const cw = Math.ceil(w / cell);
    const ch = Math.ceil(h / cell);
    const small = document.createElement('canvas');
    small.width = cw;
    small.height = ch;
    const sctx = small.getContext('2d');
    const img = sctx.createImageData(cw, ch);
    const data = img.data;
    const [lo, hi] = this._range;
    const R = this.options.maxRadiusKm;

    for (let cy = 0; cy < ch; cy++) {
      for (let cx = 0; cx < cw; cx++) {
        const ll = map.layerPointToLatLng(L.point(topLeft.x + (cx + 0.5) * cell, topLeft.y + (cy + 0.5) * cell));
        if (ll.lat > 90 || ll.lat < -90) continue;
        const lon = ((((ll.lng + 180) % 360) + 360) % 360) - 180;
        if (!this._sample(ll.lat, lon)) continue;
        const v = this._outValue;
        if (v < lo || v > hi) continue; // outside the range filter
        const fade = this._outDist <= R * 0.5 ? 1 : Math.max(0, 1 - (this._outDist - R * 0.5) / (R * 0.5));
        const bin = Math.max(0, Math.min(255, Math.round(this._ratioOf(v) * 255)));
        let rgb = this._lut[bin];
        if (!rgb) rgb = this._lut[bin] = ratioToRGB(bin / 255);
        const o = (cy * cw + cx) * 4;
        data[o] = rgb[0];
        data[o + 1] = rgb[1];
        data[o + 2] = rgb[2];
        data[o + 3] = Math.round(255 * fade);
      }
    }
    sctx.putImageData(img, 0, 0);

    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(small, 0, 0, cw * cell, ch * cell);

    // Keep only what is over land.
    ctx.globalCompositeOperation = 'destination-in';
    ctx.fillStyle = '#000';
    ctx.beginPath();
    this._traceLand(ctx, topLeft, w, h);
    ctx.fill('evenodd');
    ctx.globalCompositeOperation = 'source-over';
  },

  _traceLand(ctx, topLeft, w, h) {
    const map = this._map;
    const scale = 256 * Math.pow(2, map.getZoom());
    const ox = map.getPixelOrigin().x + topLeft.x;
    const oy = map.getPixelOrigin().y + topLeft.y;
    const project = (lng, lat, shift) => {
      const clamped = Math.max(-85.0511, Math.min(85.0511, lat));
      const x = ((lng + shift + 180) / 360) * scale - ox;
      const y = (0.5 - Math.log(Math.tan(Math.PI / 4 + (clamped * Math.PI) / 360)) / (2 * Math.PI)) * scale - oy;
      return [x, y];
    };
    for (const shift of [-360, 0, 360]) {
      for (const ring of this._land) {
        // skip rings entirely outside the canvas
        const [x0, y0] = project(ring.minX, ring.maxY, shift);
        const [x1, y1] = project(ring.maxX, ring.minY, shift);
        if (x1 < 0 || x0 > w || y1 < 0 || y0 > h) continue;
        const pts = ring.pts;
        for (let i = 0; i < pts.length; i += 2) {
          const [x, y] = project(pts[i], pts[i + 1], shift);
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.closePath();
      }
    }
  },
});
