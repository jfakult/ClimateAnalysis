// Short helpers for the metric-info boxes: a green "good" value, a red "bad"
// value, and a bullet list of the parameters behind a metric.
const mvGood = (text) => `<span class="mv-good">${text}</span>`;
const mvBad = (text) => `<span class="mv-bad">${text}</span>`;
const miParams = (items) => `<ul class="mi-params">${items.map((i) => `<li>${i}</li>`).join('')}</ul>`;

const STREAK_WEIGHTING = `${mvGood('3 days')} = 1 &middot; ${mvGood('4–6')} = 2 &middot; ${mvGood('7–9')} = 3 &middot; ...`;

// The 4 per-hour factor ranges behind Outside Days (an hour
// only counts as "good" when every one of these holds) -- one place so the
// two descriptions can't drift out of sync with each other or with
// common/scoring.py's actual thresholds.
const SCORE_FACTOR_PARAMS = [
  `Temp: ideal ${mvGood('75°F')}, range 65–82°F`,
  `Dew point: ideal ${mvGood('50°F')}, range 30–60°F`,
  `Rain: ${mvGood('≤ 0.1 mm')}/hour`,
  `Cloud: ${mvGood('≤ 50%')}`,
];

// Metric definitions: which field, how to label it, and whether a higher
// raw value is "better" (score/outside days: yes; indoor_days: no, fewer
// is better, so its color/bar direction is inverted relative to the others).
// infoHTML is shown in full below the dropdown (see index.html #metric-info).
const METRICS = {
  avg_score: {
    icon: '⭐', label: 'Score', higherIsBetter: true, decimals: 2,
    infoHTML: '<span class="mi-summary">One overall number (0–1): how much outside weather, how green, and how few indoor-or-gloomy days, relative to every other station.</span>' +
      miParams([
        `${mvGood('+')} Outside days`,
        `${mvGood('+')} Vegetation, counted at half weight (×0.5)`,
        `${mvBad('&minus;')} Indoor <i>or</i> gloomy days`,
      ]) +
      '<span class="mi-foot">"Indoor or gloomy" is one count of days that are either (they overlap, so it is not the two totals added). Outside days and that count are rescaled 0-1 against every other station first; vegetation is already 0-1. They are summed with those signs, then the result is rescaled 0-1. It shifts if the station mix changes -- it\'s a ranking, not a fixed quantity like the others.</span>',
  },
  avg_perfect_days: {
    icon: '☀️', label: 'Outside Days / Year', higherIsBetter: true, decimals: 1,
    infoHTML: '<span class="mi-summary">Days per year with a run of great weather, daylight hours only.</span>' +
      miParams([`${mvGood('4+')} consecutive hours all meeting:`, ...SCORE_FACTOR_PARAMS]) +
      '<span class="mi-foot">2 consecutive reports counts too, at 3-hourly stations. Scaled up to a 365-day year when some days are missing.</span>',
  },
  avg_indoor_days: {
    icon: '🏠', label: 'Indoor Days / Year', higherIsBetter: false, decimals: 1,
    infoHTML: '<span class="mi-summary">Days per year that are unpleasant outdoors (any one condition triggers it).</span>' +
      miParams([
        `High temp ${mvBad('< 20°F')} or ${mvBad('> 95°F')}`,
        `Dew point ${mvBad('> 68°F')}`,
        `Wind ${mvBad('> 25 mph')}`,
        `Cloud ${mvBad('> 85%')} AND rain ${mvBad('> 0.2 in')}`,
      ]) +
      '<span class="mi-foot">Fewer is better, so the color scale is reversed.</span>',
  },
  avg_gloomy_days: {
    icon: '☁️', label: 'Gloomy Days / Year', higherIsBetter: false, decimals: 1,
    infoHTML: '<span class="mi-summary">Days per year that are mostly overcast (clouds only).</span>' +
      miParams([`Cloud ${mvBad('> 80%')} for ${mvBad('6+')} daytime hours`]) +
      '<span class="mi-foot">Fewer is better. Independent of Indoor Days: a day can be both, either or neither.</span>',
  },
  avg_net_days: {
    icon: '⚖️', label: 'Outside − Indoor Days', higherIsBetter: true, decimals: 1,
    infoHTML: '<span class="mi-summary">Outside days minus indoor days per year.</span>' +
      `<span class="mi-foot">${mvGood('High')} = many great days, few miserable ones. Rewards reliably good weather, not just occasional extremes.</span>`,
  },
  avg_perfect_minus_gloom: {
    icon: '🌦️', label: 'Outside Days − Indoor Streaks', higherIsBetter: true, decimals: 1,
    infoHTML: '<span class="mi-summary">Outside days minus indoor streaks per year.</span>' +
      `<span class="mi-foot">${mvGood('High')} = plenty of great days without long stuck-inside runs eating into them. Units don\'t actually match (days vs. weighted streak count) -- it\'s a rough balance, not a rate.</span>`,
  },
  perfect_indoor_ratio: {
    icon: '📊', label: 'Outside / Indoor Ratio', higherIsBetter: true, decimals: 2,
    infoHTML: '<span class="mi-summary">(outside &minus; indoor) / (outside + indoor), from &minus;1 to +1.</span>' +
      miParams([
        `${mvGood('+1')} = every classified day is an outside day`,
        `${mvBad('&minus;1')} = every classified day is an indoor day`,
        `0 = an even split, or neither ever happens`,
      ]) +
      '<span class="mi-foot">Fixes a blind spot in "Outside &minus; Indoor": 5 outside/5 indoor (a mild, unremarkable climate) and 150/150 (an extreme, polarized one) both net to 0 there, but read very differently as a ratio... except the ratio can\'t tell those two apart either (both are 0). Look at both together.</span>',
  },
  avg_perfect_streaks: {
    icon: '🔥', label: 'Outside Streaks / Year', higherIsBetter: true, decimals: 1,
    infoHTML: '<span class="mi-summary">Runs of 3+ outside days in a row, per year.</span>' +
      miParams([`Weighted per run: ${STREAK_WEIGHTING}`]) +
      '<span class="mi-foot">A data gap breaks a streak in progress. Longest streak & average length: "Data details".</span>',
  },
  avg_gloom_streaks: {
    icon: '🌧️', label: 'Indoor Streaks / Year', higherIsBetter: false, decimals: 1,
    infoHTML: '<span class="mi-summary">Runs of 3+ days in a row that you\'d spend indoors, per year.</span>' +
      miParams([
        `A day counts if it is an ${mvBad('indoor day')} <i>or</i> a ${mvBad('gloomy day')}`,
        `Weighted per run: ${STREAK_WEIGHTING}`,
      ]) +
      '<span class="mi-foot">Fewer is better. A data gap breaks a streak. Longest streak & average length: "Data details".</span>',
  },
  avg_vegetation: {
    icon: '🌿', label: 'Vegetation', higherIsBetter: true, decimals: 2,
    infoHTML: '<span class="mi-summary">How green the land is, and for how much of the year (0–1), from satellite.</span>' +
      miParams([
        `Average of the 12 monthly greenness values: ${mvGood('1')} = dense green (NDVI ${mvGood('≥ 0.70')}), ${mvBad('0')} = bare (${mvBad('≤ 0.10')})`,
        `A lush month = NDVI ${mvGood('≥ 0.45')}. One lush month in a dead year scores low`,
        `${mvGood('Seasonal relief')}: the penalty of a low average shrinks for ${mvGood('4–8')} lush months in a row, most at ${mvGood('6')}; none at ${mvBad('≤ 3')} or ${mvBad('≥ 9')}`,
        `Months colder than ${mvBad('~5°C')} (daytime average) count as dormant, since satellite winter values are filled in too green`,
        `No penalty for very wet places`,
      ]) +
      '<span class="mi-foot">NDVI = how much light plants reflect; monthly averages, 2015–2019, ~10 km. Irrigated farmland reads green. If a station can\'t be placed on the grid, it falls back to a rough annual-rain estimate. Toggle "Vegetation (satellite)" to see the map.</span>',
  },
  seasonal_concentration: {
    icon: '📅', label: 'Seasonal Concentration', higherIsBetter: false, decimals: 2,
    infoHTML: '<span class="mi-summary">Do outside days happen year-round, or mostly in one season?</span>' +
      miParams([
        `${mvGood('0')} = outside days spread evenly across all 12 months`,
        `${mvBad('1')} = they all fall in a single month`,
      ]) +
      '<span class="mi-foot">Coefficient of variation of each month\'s outside-day rate, normalized 0-1. Hidden (n/a) below 10 outside days/year -- too few to show a real seasonal pattern rather than noise. Colored as "lower is more reliably pleasant," but a sharp one-glorious-season place isn\'t wrong, just different -- worth checking "A typical year" in Data details either way.</span>',
  },
};

// Min/max are computed from whatever stations are actually loaded, not a
// hardcoded scale, so colors/bars always reflect the real observed range.
// A metric can be null for a handful of stations (e.g. seasonal_concentration
// needs several distinct calendar months of data) -- those are skipped here
// and rendered as "n/a" rather than dragging the scale toward 0.
function computeStats(stations, key) {
  let min = Infinity;
  let max = -Infinity;
  for (const s of stations) {
    const v = s[key];
    if (v === null || v === undefined) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (min === max) {
    min -= 1;
    max += 1;
  }
  return { min, max };
}

// 0 = worst observed value for this metric, 1 = best -- same meaning for
// every metric regardless of whether "best" is high or low raw value.
function goodnessRatio(value, stats, higherIsBetter) {
  let ratio = (value - stats.min) / (stats.max - stats.min);
  if (!higherIsBetter) ratio = 1 - ratio;
  return Math.max(0, Math.min(1, ratio));
}

// red -> yellow -> blue-leaning teal, explicit user choice: pulling the good
// end away from pure green and toward blue widens the perceptual range (the
// yellow -> green stretch of the old ramp read as one narrow "fine" band)
// and, incidentally, helps red-green colorblind accessibility.
const RAMP_STOPS = [
  { at: 0.0, rgb: [217, 48, 37] },
  { at: 0.5, rgb: [251, 188, 5] },
  { at: 1.0, rgb: [0, 137, 190] },
];

function ratioToRGB(ratio) {
  ratio = Math.max(0, Math.min(1, ratio));
  let lo = RAMP_STOPS[0];
  let hi = RAMP_STOPS[RAMP_STOPS.length - 1];
  for (let i = 0; i < RAMP_STOPS.length - 1; i++) {
    if (ratio >= RAMP_STOPS[i].at && ratio <= RAMP_STOPS[i + 1].at) {
      lo = RAMP_STOPS[i];
      hi = RAMP_STOPS[i + 1];
      break;
    }
  }
  const span = hi.at - lo.at || 1;
  const t = (ratio - lo.at) / span;
  return lo.rgb.map((c, i) => Math.round(c + (hi.rgb[i] - c) * t));
}

function ratioToColor(ratio) {
  const rgb = ratioToRGB(ratio);
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

// Same ramp, darkened ~30%: a bar/marker fill can use the bright color
// directly, but that same color as small TEXT on a white background (the
// popup's metric values) reads poorly at the yellow midpoint -- darkening
// keeps the red/yellow/blue signal legible at text size.
function ratioToTextColor(ratio) {
  const rgb = ratioToRGB(ratio).map((c) => Math.round(c * 0.7));
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}
