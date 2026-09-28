(function () {
  const canvasRenderer = L.canvas();
  const map = L.map('map', { preferCanvas: true, worldCopyJump: true }).setView([20, 0], 2);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  let fullStats = {};  // metricKey -> {min, max} of the WHOLE dataset; only used to size the slider
  let colorStats = {}; // metricKey -> {min, max} currently used to color markers/smoothing -- narrows
                        // as the filter narrows, so the ramp always spans the values in view
  const markers = []; // [{marker, station}]
  let currentMetric = 'avg_score';   // matches the first option in the dropdown
  let allStations = [];
  const stationsById = {};
  let filterRange = [-Infinity, Infinity]; // value range for currentMetric (see range slider)
  const savedRanges = {};                  // metricKey -> [lo, hi], so each metric keeps its own filter
  let interpolation = null;                // InterpolationLayer, created once stations load
  let landRequested = false;
  const openPopupMarkers = [];             // markers with a currently-open popup, oldest first
  const MAX_OPEN_POPUPS = 2;               // opening a 3rd closes the oldest of the current 2 (FIFO)

  // A metric value can be null for a handful of stations (see colormap.js's
  // computeStats comment); treat that as "no data" rather than letting it
  // coerce to 0 in the ratio math below.
  function styleFor(station, metricKey) {
    const v = station[metricKey];
    if (v === null || v === undefined) {
      return { color: '#333', weight: 1, radius: 6, fillColor: '#999', fillOpacity: 0.5 };
    }
    const cfg = METRICS[metricKey];
    const ratio = goodnessRatio(v, colorStats[metricKey], cfg.higherIsBetter);
    return { color: '#333', weight: 1, radius: 6, fillColor: ratioToColor(ratio), fillOpacity: 0.85 };
  }

  function metricRowHTML(station, metricKey) {
    const cfg = METRICS[metricKey];
    const v = station[metricKey];
    if (v === null || v === undefined) {
      return `
        <div class="metric-row">
          <div class="metric-label">${cfg.icon} ${cfg.label}</div>
          <div class="metric-bar-track"></div>
          <div class="metric-value">n/a</div>
        </div>`;
    }
    // Popups always use the full-dataset range, not the filter-narrowed one the
    // markers use, so a value's color means the same thing however the slider is set.
    const ratio = goodnessRatio(v, fullStats[metricKey], cfg.higherIsBetter);
    const color = ratioToColor(ratio);
    const textColor = ratioToTextColor(ratio);
    const value = v.toFixed(cfg.decimals);
    return `
      <div class="metric-row">
        <div class="metric-label">${cfg.icon} ${cfg.label}</div>
        <div class="metric-bar-track">
          <div class="metric-bar-fill" style="width:${(ratio * 100).toFixed(0)}%; background:${color};"></div>
        </div>
        <div class="metric-value" style="color:${textColor}; font-weight:600;">${value}</div>
      </div>`;
  }

  function cadenceLabel(step) {
    return step === 1 ? 'Hourly' : `${step}-hour`;
  }

  // Older stations.json files (before cadence/sample stats existed) lack these fields.
  function dataSummary(station) {
    let text = `${station.years_included} yrs of data`;
    if (station.samples !== undefined) {
      text = `${cadenceLabel(station.step)} data points spanning ${station.years_included} yrs (${station.samples.toLocaleString()} samples)`;
    }
    if (station.coverage !== undefined) {
      text += ` &middot; ${(station.coverage * 100).toFixed(0)}% of days usable`;
    }
    return text;
  }

  // Elevation isn't in every manifest row; only shown when known.
  function elevationText(station) {
    if (station.elevation === null || station.elevation === undefined) return '';
    return ` (elevation: ${station.elevation.toLocaleString()} m)`;
  }

  function popupHTML(station) {
    return `
      <div class="station-popup">
        <div class="station-name">${station.name}</div>
        <div class="station-sub">${station.country_name || station.country} &middot; ${station.id}${elevationText(station)}</div>
        <div class="station-sub">${dataSummary(station)}</div>
        ${metricRowHTML(station, 'avg_score')}
        ${metricRowHTML(station, 'avg_perfect_days')}
        ${metricRowHTML(station, 'avg_indoor_days')}
        ${metricRowHTML(station, 'avg_gloomy_days')}
        ${metricRowHTML(station, 'avg_net_days')}
        ${metricRowHTML(station, 'avg_perfect_minus_gloom')}
        ${metricRowHTML(station, 'perfect_indoor_ratio')}
        ${metricRowHTML(station, 'avg_perfect_streaks')}
        ${metricRowHTML(station, 'avg_gloom_streaks')}
        ${metricRowHTML(station, 'avg_vegetation')}
        ${metricRowHTML(station, 'seasonal_concentration')}
        <button type="button" class="detail-btn">&#9432; Data details</button>
      </div>`;
  }

  // Left end = lowest raw value, so for "fewer is better" metrics the ramp runs green -> red.
  function rampGradient(higherIsBetter) {
    const stops = [0, 0.25, 0.5, 0.75, 1].map((p) => `${ratioToColor(higherIsBetter ? p : 1 - p)} ${p * 100}%`);
    return `linear-gradient(to right, ${stops.join(', ')})`;
  }

  // Recolor every marker (and the smoothing layer, whose ratioOf callback
  // re-reads colorStats live) for the current metric, then repaint.
  function rescaleColors() {
    colorStats[currentMetric] = filterRange[0] === filterRange[1]
      ? { min: filterRange[0] - 1, max: filterRange[1] + 1 }
      : { min: filterRange[0], max: filterRange[1] };
    for (const { marker, station } of markers) {
      marker.setStyle(styleFor(station, currentMetric));
    }
    if (interpolation) interpolation.refresh();
  }

  const slider = createRangeSlider(document.getElementById('range-slider'), {
    onChange(lo, hi) {
      filterRange = [lo, hi];
      savedRanges[currentMetric] = filterRange;
      rescaleColors();
      applyFilter();
    },
  });

  function renderLegend(metricKey) {
    const cfg = METRICS[metricKey];
    const s = fullStats[metricKey];
    document.getElementById('legend-title').textContent = cfg.label;
    slider.configure({ min: s.min, max: s.max, decimals: cfg.decimals, gradient: rampGradient(cfg.higherIsBetter) });
    if (savedRanges[metricKey]) slider.set(savedRanges[metricKey][0], savedRanges[metricKey][1]);
    filterRange = slider.get();
  }

  // Show only stations whose current-metric value is inside the chosen range.
  // A null value (no data for this metric) is never filtered out by the
  // range slider -- there's nothing to compare, and it's already drawn gray
  // rather than colored, so it can't be mistaken for a real in-range value.
  function applyFilter() {
    let visible = 0;
    for (const { marker, station } of markers) {
      const v = station[currentMetric];
      if (v === null || v === undefined || (v >= filterRange[0] && v <= filterRange[1])) {
        if (!map.hasLayer(marker)) marker.addTo(map);
        visible++;
      } else if (map.hasLayer(marker)) {
        marker.remove();
      }
    }
    document.getElementById('station-count').textContent =
      visible === allStations.length ? `${visible} stations` : `${visible} of ${allStations.length} stations`;
    if (interpolation) interpolation.setRange(filterRange[0], filterRange[1]);
  }

  function applyMetric(metricKey) {
    currentMetric = metricKey;
    renderLegend(metricKey); // restores this metric's saved range filter (full range if none)
    document.getElementById('metric-info').innerHTML = METRICS[metricKey].infoHTML;
    if (interpolation) {
      const cfg = METRICS[metricKey];
      // A null value (no data) has no business influencing a distance-weighted
      // average of its neighbors, so it stands in as the metric's own mean --
      // effectively invisible to the smoothing rather than dragging it down.
      const mean = (fullStats[metricKey].min + fullStats[metricKey].max) / 2;
      interpolation.setMetric(
        allStations.map((st) => (st[metricKey] === null || st[metricKey] === undefined ? mean : st[metricKey])),
        (v) => goodnessRatio(v, colorStats[metricKey], cfg.higherIsBetter)
      );
    }
    rescaleColors(); // colors always relative to the (possibly saved-narrowed) current filter
    applyFilter();
  }

  // ---- "Smooth between stations" ----

  function loadLand() {
    if (landRequested) return;
    landRequested = true;
    const status = document.getElementById('smooth-status');
    status.textContent = 'Loading coastline…';
    fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/land-50m.json')
      .then((r) => r.json())
      .then((topology) => {
        interpolation.setLand(topojson.feature(topology, topology.objects.land));
        status.textContent = '';
      })
      .catch((err) => {
        console.error('Failed to load coastline', err);
        landRequested = false;
        status.textContent = 'Could not load coastline data';
      });
  }

  document.getElementById('smooth-toggle').addEventListener('change', (e) => {
    if (!interpolation) return;
    if (e.target.checked) {
      interpolation.addTo(map);
      loadLand();
    } else {
      interpolation.remove();
    }
  });

  fetch('data/stations.json')
    .then((r) => r.json())
    .then((stations) => {
      for (const station of stations) {
        // Older stations.json files predate these fields.
        if (station.avg_net_days === undefined) {
          station.avg_net_days = station.avg_perfect_days - station.avg_indoor_days;
        }
        if (station.avg_gloom_streaks === undefined) station.avg_gloom_streaks = 0;
        if (station.avg_perfect_streaks === undefined) station.avg_perfect_streaks = 0;
        if (station.avg_perfect_minus_gloom === undefined) {
          station.avg_perfect_minus_gloom = station.avg_perfect_days - station.avg_gloom_streaks;
        }
        if (station.perfect_indoor_ratio === undefined) {
          const denom = station.avg_perfect_days + station.avg_indoor_days;
          station.perfect_indoor_ratio = denom ? (station.avg_perfect_days - station.avg_indoor_days) / denom : 0;
        }
        if (station.avg_vegetation === undefined) station.avg_vegetation = null;
        if (station.avg_gloomy_days === undefined) station.avg_gloomy_days = null;
        if (station.seasonal_concentration === undefined) station.seasonal_concentration = null;
        if (station.elevation === undefined) station.elevation = null;
      }
      allStations = stations;
      for (const st of stations) stationsById[st.id] = st;
      for (const key of Object.keys(METRICS)) {
        fullStats[key] = computeStats(stations, key);
        colorStats[key] = fullStats[key]; // narrowed later, per metric, as the filter changes
      }
      for (const station of stations) {
        const marker = L.circleMarker(
          [station.lat, station.lon],
          Object.assign({ renderer: canvasRenderer }, styleFor(station, currentMetric))
        );
        marker._stationId = station.id;
        // autoClose: false lets up to MAX_OPEN_POPUPS stay open at once (see
        // the popupopen handler below); closeOnClick (left at its default,
        // true) still closes each one individually on a background click,
        // which is what makes clicking off the map close all of them.
        marker.bindPopup(() => popupHTML(station), { autoClose: false });
        marker.addTo(map);
        markers.push({ marker, station });
      }
      interpolation = new InterpolationLayer();
      interpolation.setStations(stations);
      applyMetric(currentMetric);
    })
    .catch((err) => {
      console.error('Failed to load station data', err);
      document.getElementById('station-count').textContent = 'Failed to load station data';
    });

  document.getElementById('metric-info').innerHTML = METRICS[currentMetric].infoHTML;

  // The description box is shown by default; the -/+ button just hides the
  // text (not the dropdown), for anyone who wants the panel smaller.
  document.getElementById('metric-info-toggle').addEventListener('click', (e) => {
    const box = document.getElementById('metric-info-box');
    const collapsed = box.classList.toggle('collapsed');
    e.target.textContent = collapsed ? '+' : '−';
    e.target.title = collapsed ? 'Show description' : 'Hide description';
    e.target.setAttribute('aria-label', e.target.title);
  });

  // ---- Köppen climate zones and satellite vegetation backgrounds ----
  // Both are static grids drawn as canvas layers; each is fetched on first use.

  function wireGridOverlay({ toggleId, statusId, layer, url, loadingText, failText, attribution }) {
    let requested = false;
    const status = document.getElementById(statusId);
    document.getElementById(toggleId).addEventListener('change', (e) => {
      if (e.target.checked) {
        layer.addTo(map);
        if (!requested) {
          requested = true;
          status.textContent = loadingText;
          layer.load(url)
            .then(() => { status.textContent = ''; })
            .catch((err) => {
              console.error(failText, err);
              requested = false;
              status.textContent = failText;
            });
        }
        map.attributionControl.addAttribution(attribution);
      } else {
        layer.remove();
        map.attributionControl.removeAttribution(attribution);
      }
    });
  }

  wireGridOverlay({
    toggleId: 'koppen-toggle', statusId: 'koppen-status', layer: new KoppenLayer(), url: 'data/koppen.png',
    loadingText: 'Loading climate data…', failText: 'Could not load climate data',
    attribution: 'Köppen climate: <a href="https://doi.org/10.1038/sdata.2018.214" target="_blank" rel="noopener">Beck et al. 2018</a> (CC BY 4.0)',
  });
  wireGridOverlay({
    toggleId: 'ndvi-toggle', statusId: 'ndvi-status', layer: new NdviLayer(), url: 'data/ndvi.png',
    loadingText: 'Loading vegetation data…', failText: 'Could not load vegetation data',
    attribution: 'Vegetation (NDVI): <a href="https://zenodo.org/records/4305975" target="_blank" rel="noopener">OpenGeoHub</a> (CC BY 4.0)',
  });

  // Popup content is rebuilt from a string on every open, so wire its button here.
  // Also enforces the MAX_OPEN_POPUPS cap: opening one more than that closes
  // the oldest currently-open popup (its own popupclose handler below then
  // drops it from openPopupMarkers).
  map.on('popupopen', (e) => {
    const btn = e.popup.getElement().querySelector('.detail-btn');
    const station = stationsById[e.popup._source && e.popup._source._stationId];
    if (btn && station) btn.addEventListener('click', () => openStationDetails(station));

    const marker = e.popup._source;
    if (marker) {
      const i = openPopupMarkers.indexOf(marker);
      if (i !== -1) openPopupMarkers.splice(i, 1);
      openPopupMarkers.push(marker);
      while (openPopupMarkers.length > MAX_OPEN_POPUPS) openPopupMarkers.shift().closePopup();
    }
  });

  const detailPanel = document.getElementById('detail-panel');
  L.DomEvent.disableClickPropagation(detailPanel);
  L.DomEvent.disableScrollPropagation(detailPanel);

  // The details panel goes away when the popup it's FOR closes (whether by
  // the user closing it directly or by the FIFO cap above evicting it), on a
  // click anywhere outside both popups and the panel, or on Escape. Other
  // popups closing doesn't touch it -- with two popups possibly open, only
  // the one the panel is actually showing should affect it.
  map.on('popupclose', (e) => {
    const marker = e.popup._source;
    if (marker) {
      const i = openPopupMarkers.indexOf(marker);
      if (i !== -1) openPopupMarkers.splice(i, 1);
      if (marker._stationId === currentDetailStationId) closeStationDetails();
    }
  });
  document.addEventListener('click', (e) => {
    if (detailPanel.hidden || detailPanel.contains(e.target) || e.target.closest('.leaflet-popup')) return;
    closeStationDetails();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeStationDetails();
  });

  document.getElementById('metric-select').addEventListener('change', (e) => {
    applyMetric(e.target.value);
  });

  document.getElementById('settings-toggle').addEventListener('click', () => {
    document.getElementById('settings-panel').classList.toggle('collapsed');
  });

  // ---- About modal ----

  const aboutBackdrop = document.getElementById('about-backdrop');
  function openAbout() { aboutBackdrop.hidden = false; }
  function closeAbout() { aboutBackdrop.hidden = true; }
  document.getElementById('about-toggle').addEventListener('click', openAbout);
  document.getElementById('about-close').addEventListener('click', closeAbout);
  aboutBackdrop.addEventListener('click', (e) => {
    if (e.target === aboutBackdrop) closeAbout();   // click on the dimmed backdrop, not the card itself
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !aboutBackdrop.hidden) closeAbout();
  });
})();
