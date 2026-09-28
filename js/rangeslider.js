// Dual-handle range slider that doubles as the map legend: the two handles pick
// the value range to show, and the color ramp is stretched between them (the
// map recolors markers relative to the chosen range, so the legend does too);
// the parts of the track outside the range are dimmed gray.
//
// Built from two stacked native <input type="range"> elements, so keyboard,
// touch and screen-reader behaviour come for free.
//
//   const slider = createRangeSlider(container, { onChange(lo, hi) {...} });
//   slider.configure({ min, max, decimals, gradient });  // new metric: resets to full range
//   slider.set(lo, hi)   // move the handles (clamped); does not call onChange
//   slider.get() -> [lo, hi]

function createRangeSlider(container, { onChange }) {
  container.innerHTML = `
    <div class="range">
      <div class="range-track">
        <div class="range-fill"></div>
        <div class="range-dim range-dim-lo"></div>
        <div class="range-dim range-dim-hi"></div>
      </div>
      <input type="range" class="range-input range-input-lo" aria-label="Minimum value">
      <input type="range" class="range-input range-input-hi" aria-label="Maximum value">
    </div>
    <div class="range-labels">
      <span class="range-label-lo"></span>
      <button type="button" class="range-reset" hidden>Reset</button>
      <span class="range-label-hi"></span>
    </div>`;

  const track = container.querySelector('.range-track');
  const fill = container.querySelector('.range-fill');
  const dimLo = container.querySelector('.range-dim-lo');
  const dimHi = container.querySelector('.range-dim-hi');
  const inLo = container.querySelector('.range-input-lo');
  const inHi = container.querySelector('.range-input-hi');
  const labelLo = container.querySelector('.range-label-lo');
  const labelHi = container.querySelector('.range-label-hi');
  const reset = container.querySelector('.range-reset');

  let min = 0;
  let max = 1;
  let decimals = 1;

  // Native range inputs round to their step, so a handle parked at an end can
  // read as a hair inside it; snap so "full range" really includes every value.
  function values() {
    const eps = (max - min) * 1e-6;
    let lo = parseFloat(inLo.value);
    let hi = parseFloat(inHi.value);
    if (lo - min < eps) lo = min;
    if (max - hi < eps) hi = max;
    return [lo, hi];
  }

  function render() {
    const [lo, hi] = values();
    const span = max - min || 1;
    fill.style.left = `${((lo - min) / span) * 100}%`;
    fill.style.width = `${((hi - lo) / span) * 100}%`;
    dimLo.style.width = `${((lo - min) / span) * 100}%`;
    dimHi.style.width = `${((max - hi) / span) * 100}%`;
    labelLo.textContent = lo.toFixed(decimals);
    labelHi.textContent = hi.toFixed(decimals);
    reset.hidden = lo <= min && hi >= max;
  }

  function changed(source) {
    let [lo, hi] = values();
    // handles may touch but never cross
    if (lo > hi) {
      if (source === inLo) inLo.value = hi; else inHi.value = lo;
      [lo, hi] = values();
    }
    render();
    onChange(lo, hi);
  }

  inLo.addEventListener('input', () => changed(inLo));
  inHi.addEventListener('input', () => changed(inHi));
  reset.addEventListener('click', () => {
    inLo.value = min;
    inHi.value = max;
    render();
    onChange(min, max);
  });

  return {
    configure(cfg) {
      min = cfg.min;
      max = cfg.max;
      decimals = cfg.decimals;
      fill.style.background = cfg.gradient;
      for (const input of [inLo, inHi]) {
        input.min = min;
        input.max = max;
        input.step = (max - min) / 1000;
      }
      inLo.value = min;
      inHi.value = max;
      render();
    },
    set(lo, hi) {
      inLo.value = Math.max(min, Math.min(max, lo));
      inHi.value = Math.max(min, Math.min(max, hi));
      render();
    },
    get: values,
  };
}
