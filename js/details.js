// "Data details" panel: the per-year breakdown behind a station's numbers.
// Opened from the popup's button; loads data/details/<station id>.json
// (written by compute/5_build_output.py) and explains, year by year, how much
// each measurement the station actually reported and whether the year counted.

const DETAIL_CRITERIA = [
  ['temp_c', 'Temp'],
  ['dew_c', 'Dew pt'],
  ['precip_mm', 'Rain'],
  ['cloud_pct', 'Cloud'],
  ['wind_mps', 'Wind'],
];
const DETAIL_CRITERION_NAMES = {
  temp_c: 'temperature', dew_c: 'dew point', precip_mm: 'precipitation', cloud_pct: 'cloud cover', wind_mps: 'wind',
};

function escapeHTML(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Values under the cutoff round DOWN, so 32.6% never reads as "33%" next to a "fail" mark.
function pctText(fraction, cutoff) {
  const rounded = cutoff !== undefined && fraction < cutoff ? Math.floor(fraction * 100) : Math.round(fraction * 100);
  return `${rounded}%`;
}

// A table cell whose background fills like a bar, colored by how trustworthy the amount is.
function coverageCell(value, minFraction, failing) {
  const level = value < minFraction ? 'lo' : value < 0.667 ? 'mid' : 'hi';
  const fill = Math.min(100, value * 100).toFixed(0);
  return `<td class="dp-cell dp-${level}${failing ? ' dp-fail' : ''}" style="--p:${fill}%">${pctText(value, minFraction)}</td>`;
}

function intervalText(step) {
  return step === 1 ? 'hourly' : `${step}-hourly`;
}

function detailRow(row, minFraction) {
  const failing = row.excluded && row.excluded.kind === 'coverage' ? row.excluded.criterion : null;
  const cells = DETAIL_CRITERIA.map(([key]) => coverageCell(row.coverage[key], minFraction, key === failing)).join('');
  const daysFailing = row.excluded && row.excluded.kind === 'days';
  const days = coverageCell(row.valid_days, minFraction, daysFailing);

  let result;
  if (row.excluded) {
    const e = row.excluded;
    const label = (DETAIL_CRITERIA.find(([key]) => key === e.criterion) || [])[1];
    const why = e.kind === 'coverage' ? `${label} data only ${pctText(e.value, minFraction)}` : `Days usable only ${pctText(e.value, minFraction)}`;
    result = `<td class="dp-result dp-excluded" colspan="5"><span class="dp-chip">Not counted</span> ${why}</td>`;
  } else {
    result = `<td class="dp-num">${row.score.toFixed(2)}</td><td class="dp-num">${row.perfect.toFixed(1)}</td><td class="dp-num">${row.indoor.toFixed(1)}</td>` +
      `<td class="dp-num">${row.gloom.toFixed(1)}</td><td class="dp-num">${row.pstreak.toFixed(1)}</td>`;
  }
  return `<tr class="${row.excluded ? 'dp-row-out' : 'dp-row-in'}">
    <th scope="row">${row.year}</th>
    <td class="dp-reports">${row.obs.toLocaleString()}<div class="dp-dim">${intervalText(row.step)}</div></td>
    ${cells}${days}${result}</tr>`;
}

// Plain-language observations about how far to trust the numbers.
function detailNotes(station, detail) {
  const rows = detail.years;
  const used = rows.filter((r) => !r.excluded);
  const notes = [];

  if (used.length === 0) return notes;
  if (used.length < rows.length) {
    notes.push(`${used.length} of ${rows.length} years met the data requirements; the other ${rows.length - used.length} are left out (see "Not counted").`);
  }
  if (used.length <= 2) {
    notes.push(`Only ${used.length} year${used.length === 1 ? '' : 's'} of data went in, so these figures are a rough guide, not a long-term average.`);
  }
  const steps = used.map((r) => r.step);
  const usual = steps.sort((a, b) => steps.filter((x) => x === a).length - steps.filter((x) => x === b).length).pop();
  if (usual > 1) {
    notes.push(`This station reports every ${usual} hours, so daily peaks are sampled coarsely: indoor days tend to read low and outside-day counts are approximate.`);
  }
  for (const [key] of DETAIL_CRITERIA) {
    const mean = used.reduce((sum, r) => sum + r.coverage[key], 0) / used.length;
    if (mean < 0.6) {
      notes.push(`${DETAIL_CRITERION_NAMES[key][0].toUpperCase()}${DETAIL_CRITERION_NAMES[key].slice(1)} is reported for only ${pctText(mean)} of daytime hours in the counted years, so only the hours that have it are scored.`);
    }
  }
  const meanDays = used.reduce((sum, r) => sum + r.valid_days, 0) / used.length;
  if (meanDays < 0.95) {
    notes.push(`On average ${pctText(meanDays)} of days were usable; outside and indoor day counts are scaled up to a full 365-day year.`);
  }
  return notes;
}


// ---- "Typical year" snapshot: 12 monthly values per measurement, as small charts ----

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// Temperature ramp (deg C): cold blue -> green -> yellow -> orange -> red.
const TEMP_RAMP = [[-20, [63, 81, 181]], [0, [66, 165, 245]], [12, [38, 198, 160]], [22, [253, 216, 53]], [30, [251, 140, 0]], [40, [229, 57, 53]]];

function tempColor(c) {
  if (c <= TEMP_RAMP[0][0]) return `rgb(${TEMP_RAMP[0][1].join(',')})`;
  for (let i = 1; i < TEMP_RAMP.length; i++) {
    const [t1, c1] = TEMP_RAMP[i];
    const [t0, c0] = TEMP_RAMP[i - 1];
    if (c <= t1) {
      const f = (c - t0) / (t1 - t0);
      return `rgb(${c0.map((v, k) => Math.round(v + (c1[k] - v) * f)).join(',')})`;
    }
  }
  return `rgb(${TEMP_RAMP[TEMP_RAMP.length - 1][1].join(',')})`;
}

// Each chart: values are the stored metric units; `show` converts for display.
const SNAPSHOT_CHARTS = [
  { key: 'temp', title: 'Daytime temperature', unit: '°F', kind: 'area', show: (c) => c * 9 / 5 + 32, tip: (c) => `${(c * 9 / 5 + 32).toFixed(0)}°F (${c.toFixed(0)}°C)`, gradient: true },
  { key: 'dew', title: 'Dew point', unit: '°F', kind: 'area', color: '#26a69a', show: (c) => c * 9 / 5 + 32, tip: (c) => `${(c * 9 / 5 + 32).toFixed(0)}°F (${c.toFixed(0)}°C)` },
  { key: 'cloud', title: 'Cloud cover', unit: '%', kind: 'bars', color: '#78909c', domain: [0, 100], show: (v) => v, tip: (v) => `${v.toFixed(0)}%` },
  { key: 'rain', title: 'Rainy daytime hours', unit: '%', kind: 'bars', color: '#2f80ed', show: (v) => v, tip: (v) => `${v.toFixed(1)}% of daytime hours` },
  { key: 'wind', title: 'Wind', unit: 'mph', kind: 'area', color: '#8e6bbf', floor: 0, show: (v) => v * 2.23694, tip: (v) => `${(v * 2.23694).toFixed(1)} mph` },
  { key: 'perfect', title: 'Outside days', unit: '% of days', kind: 'bars', color: '#43a047', show: (v) => v, tip: (v) => `${v.toFixed(0)}% of days` },
  { key: 'indoor', title: 'Indoor days', unit: '% of days', kind: 'bars', color: '#e5533d', show: (v) => v, tip: (v) => `${v.toFixed(0)}% of days` },
  { key: 'gloom', title: 'Gloomy days', unit: '% of days', kind: 'bars', color: '#5c6bc0', show: (v) => v, tip: (v) => `${v.toFixed(0)}% of days` },
];

// A "nice" tick step (1, 2, 5 x 10^k) giving about `ticks` divisions of `range`.
function niceStep(range, ticks) {
  const raw = (range || 1) / ticks;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
}

function snapshotChart(cfg, raw) {
  const vals = raw.map((v) => (v === null || v === undefined ? null : cfg.show(v)));
  const present = vals.filter((v) => v !== null);
  if (present.length === 0) return '';

  // Geometry (SVG units; the SVG scales to the card): Y-axis labels on the left, month letters below.
  const W = 300, H = 118, left = 36, right = 6, top = 9, bottom = 100;
  const plotW = W - left - right;
  const slot = plotW / 12;
  const dataMax = Math.max(...present);
  const dataMin = Math.min(...present);

  // Y scale: rounded to nice tick values so the axis reads cleanly.
  let lo, hi, step;
  if (cfg.domain) {
    [lo, hi] = cfg.domain;
    step = niceStep(hi - lo, 2);
  } else if (cfg.kind === 'bars' || cfg.floor !== undefined) {
    step = niceStep(Math.max(dataMax, 1), 3);
    lo = 0;
    hi = Math.ceil(dataMax / step) * step || step;
  } else {
    step = niceStep(dataMax - dataMin, 3);
    lo = Math.floor(dataMin / step) * step;
    hi = Math.ceil(dataMax / step) * step;
    if (hi === lo) hi = lo + step;
  }
  const y = (v) => bottom - ((v - lo) / (hi - lo || 1)) * (bottom - top);
  const cx = (i) => left + slot * (i + 0.5);
  const color = cfg.color || '#888';
  const decimals = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;

  const ticks = [];
  for (let t = lo; t <= hi + step * 1e-6; t += step) ticks.push(Math.round(t / step) * step);
  const grid = ticks.map((t) => `<line x1="${left}" x2="${W - right}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}" stroke="#dde1e6" stroke-width="0.8" ${t === lo ? '' : 'stroke-dasharray="2.5 2.5"'}/>` +
    `<text x="${left - 5}" y="${(y(t) + 3).toFixed(1)}" text-anchor="end" font-size="9.5" fill="#7b828b">${t.toFixed(decimals)}</text>`).join('');
  const monthLetters = MONTH_NAMES.map((m, i) => `<text x="${cx(i).toFixed(1)}" y="${H - 3}" text-anchor="middle" font-size="9" fill="#9aa1a9">${m[0]}</text>`).join('');

  let shapes = '';
  let defs = '';
  if (cfg.kind === 'bars') {
    shapes = vals.map((v, i) => (v === null ? '' :
      `<rect x="${(left + slot * i + 2).toFixed(1)}" y="${y(v).toFixed(1)}" width="${(slot - 4).toFixed(1)}" height="${Math.max(0.8, bottom - y(v)).toFixed(1)}" rx="2" fill="${color}" opacity="${v === dataMax ? 1 : 0.62}"/>`)).join('');
  } else {
    const pts = vals.map((v, i) => (v === null ? null : [cx(i), y(v)]));
    const runs = [];
    let cur = [];
    pts.forEach((p) => { if (p) cur.push(p); else if (cur.length) { runs.push(cur); cur = []; } });
    if (cur.length) runs.push(cur);
    let stroke = color;
    let fill = color;
    if (cfg.gradient) {
      // vertical gradient so the color follows the temperature itself
      const stops = [0, 0.25, 0.5, 0.75, 1].map((f) => {
        const shown = hi - (hi - lo) * f;                   // display units at this height
        const c = (shown - 32) * 5 / 9;
        return `<stop offset="${f * 100}%" stop-color="${tempColor(c)}"/>`;
      }).join('');
      defs = `<linearGradient id="dpg-${cfg.key}" gradientUnits="userSpaceOnUse" x1="0" y1="${top}" x2="0" y2="${bottom}">${stops}</linearGradient>`;
      stroke = fill = `url(#dpg-${cfg.key})`;
    }
    shapes = runs.map((run) => {
      const line = run.map(([x, yy], k) => `${k ? 'L' : 'M'}${x.toFixed(1)} ${yy.toFixed(1)}`).join(' ');
      const area = `${line} L${run[run.length - 1][0].toFixed(1)} ${bottom} L${run[0][0].toFixed(1)} ${bottom} Z`;
      const dots = run.length === 1 ? `<circle cx="${run[0][0].toFixed(1)}" cy="${run[0][1].toFixed(1)}" r="2.5" fill="${stroke}"/>` : '';
      return `<path d="${area}" fill="${fill}" opacity="0.28"/><path d="${line}" fill="none" stroke="${stroke}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>${dots}`;
    }).join('');
  }
  const hover = raw.map((v, i) => (v === null ? '' :
    `<rect x="${(left + slot * i).toFixed(1)}" y="0" width="${slot.toFixed(1)}" height="${bottom}" fill="transparent"><title>${MONTH_NAMES[i]}: ${cfg.tip(v)}</title></rect>`)).join('');

  const maxI = vals.indexOf(dataMax);
  const minI = vals.indexOf(dataMin);
  const fmt = (v) => (Math.abs(v) >= 100 || cfg.unit === '%' || cfg.unit === '°F' ? v.toFixed(0) : v.toFixed(1));
  const range = maxI === minI ? '' : `<span>Low: ${MONTH_NAMES[minI]} ${fmt(dataMin)}</span><span>High: ${MONTH_NAMES[maxI]} ${fmt(dataMax)}</span>`;
  return `<div class="dp-chart">
    <div class="dp-chart-title">${cfg.title} <span>${cfg.unit}</span></div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${cfg.title} by month"><defs>${defs}</defs>${grid}${shapes}${monthLetters}${hover}</svg>
    <div class="dp-chart-range">${range}</div>
  </div>`;
}

function renderSnapshot(monthly) {
  const charts = SNAPSHOT_CHARTS.map((cfg) => (monthly[cfg.key] ? snapshotChart(cfg, monthly[cfg.key]) : '')).join('');
  if (!charts.trim()) return '';
  return `<div class="dp-section-title">A typical year <span class="dp-section-sub">monthly, daytime hours in the counted years</span></div>
    <div class="dp-charts">${charts}</div>`;
}

function streakLine(label, longest, avgLength) {
  if (!longest) return `${label}: none of 3+ days in the counted years.`;
  return `${label}: longest ${longest} day${longest === 1 ? '' : 's'} in a row, averaging ${avgLength.toFixed(1)} days per streak.`;
}

function renderDetails(station, detail) {
  const min = detail.min_data_fraction;
  const notes = detailNotes(station, detail);
  const rows = detail.years.map((r) => detailRow(r, min)).join('');
  const chip = (label, value, unit) =>
    `<div class="dp-stat"><div class="dp-stat-value">${value}</div><div class="dp-stat-label">${label}${unit ? ` <span>${unit}</span>` : ''}</div></div>`;
  return `
    <div class="dp-head">
      <div>
        <div class="dp-name">${escapeHTML(station.name)}</div>
        <div class="dp-sub">${escapeHTML(station.country_name || station.country)} &middot; ${escapeHTML(station.id)}${station.elevation !== null && station.elevation !== undefined ? ` (elevation: ${station.elevation.toLocaleString()} m)` : ''}</div>
      </div>
      <button type="button" class="dp-close" aria-label="Close details">&times;</button>
    </div>
    <div class="dp-stats">
      ${chip('Score', station.avg_score.toFixed(2), '')}
      ${chip('Outside days', station.avg_perfect_days.toFixed(1), '/yr')}
      ${chip('Indoor days', station.avg_indoor_days.toFixed(1), '/yr')}
      ${chip('Gloomy days', station.avg_gloomy_days == null ? 'n/a' : station.avg_gloomy_days.toFixed(1), '/yr')}
      ${chip('Indoor or gloomy days', station.avg_bad_days == null ? 'n/a' : station.avg_bad_days.toFixed(1), '/yr')}
      ${chip('Outside − indoor', station.avg_net_days.toFixed(1), '/yr')}
      ${chip('Outside − indoor streaks', station.avg_perfect_minus_gloom.toFixed(1), '/yr')}
      ${chip('Outside / indoor ratio', station.perfect_indoor_ratio.toFixed(2), '')}
      ${chip('Outside streaks', station.avg_perfect_streaks.toFixed(1), '/yr')}
      ${chip('Indoor streaks', station.avg_gloom_streaks.toFixed(1), '/yr')}
      ${chip('Vegetation', station.avg_vegetation === null ? 'n/a' : station.avg_vegetation.toFixed(2), station.vegetation_source === 'rain' ? '(from rain)' : '')}
      ${chip('Greenness (NDVI)', station.avg_ndvi == null ? 'n/a' : station.avg_ndvi.toFixed(2), '')}
      ${chip('Lush months in a row', station.vegetation_lush_months == null ? 'n/a' : station.vegetation_lush_months, 'of 12')}
      ${chip('Annual rain (est.)', station.avg_annual_precip_mm === undefined ? 'n/a' : station.avg_annual_precip_mm.toLocaleString(), 'mm')}
      ${chip('Seasonal concentration', station.seasonal_concentration === null ? 'n/a' : station.seasonal_concentration.toFixed(2), '')}
    </div>
    <ul class="dp-notes dp-streaks">
      <li>${streakLine('Outside streaks (3+ outside days in a row)', detail.perfect_streak_longest_run, detail.perfect_streak_avg_length)}</li>
      <li>${streakLine('Indoor streaks (3+ days in a row that are indoor or gloomy)', detail.gloom_longest_run, detail.gloom_avg_length)}</li>
    </ul>
    ${notes.length ? `<ul class="dp-notes">${notes.map((n) => `<li>${n}</li>`).join('')}</ul>` : ''}
    ${detail.monthly ? renderSnapshot(detail.monthly) : ''}
    <div class="dp-section-title">Year by year</div>
    <div class="dp-tablewrap">
      <table class="dp-table">
        <thead>
          <tr>
            <th rowspan="2">Year</th><th rowspan="2">Reports</th>
            <th colspan="5" class="dp-group">Daytime measurements present</th>
            <th rowspan="2">Days usable</th>
            <th colspan="5" class="dp-group">Result for the year</th>
          </tr>
          <tr>
            ${DETAIL_CRITERIA.map(([, label]) => `<th>${label}</th>`).join('')}
            <th>Day quality</th><th>Outside</th><th>Indoor</th><th>Indoor str.</th><th>Outside str.</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="dp-legend">
      <span><i class="dp-key dp-hi"></i> plenty</span>
      <span><i class="dp-key dp-mid"></i> partial</span>
      <span><i class="dp-key dp-lo"></i> under ${pctText(min)}: the year is not counted</span>
    </div>
    <p class="dp-foot">Only daytime (about 7 am to 9 pm local solar time) is scored, so nights and night-time gaps have no effect. Percentages are the share of the daytime hours the station should have reported (given its usual interval) that actually contain that measurement. A year counts only if every measurement and its share of usable days clear ${pctText(min)}, and a station needs at least 3 counted years to appear on the map. A "gloomy" day is one overcast (over 80%) for 6 or more daytime hours; it can also be an indoor day, and an "indoor streak" is a run of days that are indoor or gloomy. The "str." columns are that year's weighted streak count (3 days = 1, 4–6 = 2, 7–9 = 3, ...); a day with too little data breaks a streak in progress. "Day quality" is that year's average hour-by-hour temp/dew/rain/cloud score (0-1) -- unrelated to the map's overall Score, which instead ranks this station's outside days, vegetation and indoor-or-gloomy days against every other station's.</p>`;
}

// Panel wiring: one shared panel element (#detail-panel) in index.html.
// currentDetailStationId lets app.js's popup-close handling tell whether a
// closing popup is the one this panel is currently showing (and so should
// close it too) or an unrelated popup (multiple can be open at once).
let currentDetailStationId = null;

function openStationDetails(station) {
  currentDetailStationId = station.id;
  const panel = document.getElementById('detail-panel');
  panel.hidden = false;
  panel.innerHTML = `<div class="dp-head"><div class="dp-name">${escapeHTML(station.name)}</div>
    <button type="button" class="dp-close" aria-label="Close details">&times;</button></div>
    <p class="dp-loading">Loading…</p>`;
  panel.querySelector('.dp-close').addEventListener('click', closeStationDetails);

  fetch(`data/details/${encodeURIComponent(station.id)}.json`)
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then((detail) => {
      panel.innerHTML = renderDetails(station, detail);
      panel.querySelector('.dp-close').addEventListener('click', closeStationDetails);
      panel.scrollTop = 0;
    })
    .catch((err) => {
      console.error('Failed to load station details', err);
      panel.querySelector('.dp-loading').textContent =
        'No detailed breakdown for this station yet. Re-run compute steps 4 and 5 to generate it.';
    });
}

function closeStationDetails() {
  currentDetailStationId = null;
  document.getElementById('detail-panel').hidden = true;
}
