/* depok-map — which kelurahan/kecamatan was that place in?
 *
 * Boundaries: 63 kelurahan of Kota Depok (HDX/BPS cod-ab-idn, CC BY-IGO).
 * Basemap: OpenFreeMap (OpenMapTiles schema, OSM data). No API key.
 *
 * Everything runs client-side: the hierarchy is denormalised into every feature
 * (district_code / village_code), so drill-down is a filter and no server or
 * reverse-geocoder is involved.
 */
'use strict';

const DATA = window.DEPOK;
const FEATURES = DATA.features;
const META = DATA.meta;
const BY_NAME = new Map(FEATURES.map(f => [f.properties.village, f]));

/* ---------- basemap styles ---------- */
const STYLES = {
  light: 'https://tiles.openfreemap.org/styles/positron',
  dark: 'https://tiles.openfreemap.org/styles/dark',
};

/* ---------- state ---------- */
const state = {
  theme: localStorage.getItem('depok-theme')
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
  selected: null,   // village name
  active: -1,       // keyboard cursor in the results list
};

/* ---------- palette (matches app.css tokens) ---------- */
const PALETTE = {
  light: { sel: '#1f6b48', selFill: '#1f6b48', same: '#7aa892', line: '#8a9a90', idle: 'rgba(0,0,0,0)' },
  dark:  { sel: '#6fd39b', selFill: '#6fd39b', same: '#3d6b52', line: '#4a5a4e', idle: 'rgba(0,0,0,0)' },
};

/* ---------- map ---------- */
const map = new maplibregl.Map({
  container: 'map',
  style: STYLES[state.theme],
  bounds: META.bounds,
  fitBoundsOptions: { padding: 48 },
  minZoom: 9,
  maxZoom: 17,
  attributionControl: false, // we render our own, licence-complete line
  dragRotate: false,
  pitchWithRotate: false,
});

map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'top-right');
map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }), 'bottom-right');

const SRC = 'depok';
let mapReady = false;

function paint() {
  if (!mapReady) return;
  const p = PALETTE[state.theme];
  const sel = state.selected;
  const selDistrict = sel ? BY_NAME.get(sel).properties.district_code : null;

  // Build the expression by accumulating cases, so the conditional stays valid
  // when there is no selection (a ternary in the middle of an array literal
  // silently produces a hole).
  const isSel = ['==', ['get', 'village'], sel];
  const isSame = selDistrict ? ['==', ['get', 'district_code'], selDistrict] : null;

  map.setPaintProperty('kel-fill', 'fill-color',
    isSame ? ['case', isSel, p.selFill, isSame, p.same, p.idle] : p.idle);

  map.setPaintProperty('kel-fill', 'fill-opacity',
    isSame ? ['case', isSel, 0.55, isSame, 0.16, 0.04] : 0.04);

  map.setPaintProperty('kel-line', 'line-color',
    isSame ? ['case', isSel, p.sel, isSame, p.same, p.line] : p.line);

  map.setPaintProperty('kel-line', 'line-width',
    isSame ? ['case', isSel, 2.5, 1] : 1);
}

function addLayers() {
  // Guard both halves: addSource throws if the source exists, and a duplicate
  // layer id is equally fatal. Either one aborts the rest of addLayers().
  if (!map.getSource(SRC)) {
    map.addSource(SRC, { type: 'geojson', data: DATA, promoteId: 'village' });
  }
  if (!map.getLayer('kel-fill')) {
    map.addLayer({
      id: 'kel-fill', type: 'fill', source: SRC,
      paint: { 'fill-color': PALETTE[state.theme].idle, 'fill-opacity': 0.04 },
    });
  }
  if (!map.getLayer('kel-line')) {
    map.addLayer({
      id: 'kel-line', type: 'line', source: SRC,
      paint: { 'line-color': PALETTE[state.theme].line, 'line-width': 1, 'line-opacity': 0.8 },
    });
  }
  mapReady = true;
  paint();
}

map.on('load', addLayers);

map.on('click', 'kel-fill', e => {
  const f = e.features && e.features[0];
  if (f) select(f.properties.village);
});

map.on('mousemove', 'kel-fill', () => {
  map.getCanvas().style.cursor = 'pointer';
});
map.on('mouseout', 'kel-fill', () => {
  map.getCanvas().style.cursor = '';
});

/* ---------- selection ---------- */
function select(name, opts = {}) {
  const f = BY_NAME.get(name);
  if (!f) return;
  state.selected = name;
  paint();

  const p = f.properties;
  const card = document.getElementById('card');
  card.className = 'card';
  card.innerHTML = `
    <div class="card-top">
      <div class="eyebrow">Kelurahan dipilih</div>
      <div class="card-name"></div>
    </div>
    <div class="ladder">
      <div class="rung"><i class="dot"></i><span class="lvl">Kecamatan</span><span class="nm"></span></div>
      <div class="rung"><i class="dot"></i><span class="lvl">Kabupaten</span><span class="nm"></span></div>
      <div class="rung"><i class="dot"></i><span class="lvl">Provinsi</span><span class="nm"></span></div>
    </div>
    <div class="card-foot">
      <span class="code"></span>
      <button class="copy" type="button">Salin kode</button>
    </div>`;
  // textContent, not innerHTML: names come from an external dataset.
  card.querySelector('.card-name').textContent = p.village;
  const nms = card.querySelectorAll('.rung .nm');
  nms[0].textContent = p.district;
  nms[1].textContent = p.regency;
  nms[2].textContent = p.province;
  card.querySelector('.code').textContent = p.village_code;

  const btn = card.querySelector('.copy');
  btn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(p.village_code);
      btn.textContent = 'Tersalin';
      btn.classList.add('done');
      setTimeout(() => { btn.textContent = 'Salin kode'; btn.classList.remove('done'); }, 1600);
    } catch {
      // Clipboard needs a secure context; fall back to a manual selection.
      const r = document.createRange();
      r.selectNode(card.querySelector('.code'));
      const sel = getSelection();
      sel.removeAllRanges(); sel.addRange(r);
      btn.textContent = 'Pilih manual';
      setTimeout(() => { btn.textContent = 'Salin kode'; }, 1600);
    }
  });

  document.getElementById('legend').hidden = false;

  if (opts.zoom !== false) {
    map.fitBounds(boundsOf(f), { padding: { top: 130, bottom: 220, left: 80, right: 80 }, maxZoom: 15.5, duration: 700 });
  }
  // Deep-link the selection so a found area can be shared or reloaded.
  history.replaceState(null, '', '#' + encodeURIComponent(p.village_code));
}

/**
 * Bounding box of a feature's geometry.
 *
 * Computed by walking coordinates directly. There is no maplibregl.LngLatBounds
 * convenience for a bare GeoJSON geometry, and fitBounds() on an empty box
 * silently leaves the camera where it was — which looks like "zoom is broken"
 * rather than an error.
 */
function boundsOf(f) {
  const b = new maplibregl.LngLatBounds();
  const walk = coords => {
    if (typeof coords[0] === 'number') b.extend(coords);
    else coords.forEach(walk);
  };
  walk(f.geometry.coordinates);
  return b;
}

/* ---------- search ---------- */
const q = document.getElementById('q');
const results = document.getElementById('results');
const clearBtn = document.getElementById('clear');

/**
 * Fuzzy match a single field. Lower is better; -1 means no match.
 * exact 0 < prefix 1 < substring 2 < subsequence 3.
 */
function score(hay, needle) {
  const h = hay.toLowerCase();
  if (h === needle) return 0;
  if (h.startsWith(needle)) return 1;
  if (h.includes(needle)) return 2;
  let j = 0;
  for (const ch of h) {
    if (ch === needle[j]) j++;
    if (j === needle.length) return 3;
  }
  return -1;
}

/**
 * Rank name matches ahead of everything else.
 *
 * Taking Math.min() across the name and district scores is wrong: for "jagak",
 * Jagakarsa scores 1 on its own name, but every kelurahan inside the Jagakarsa
 * district scores -1 on name and 1 on district, so min() promoted the siblings
 * and put Ciganjur above the actual prefix match. Order here is
 * name -> code -> district siblings (always last).
 */
function rank(qRaw) {
  const needle = qRaw.trim().toLowerCase();
  if (!needle) return [];
  const out = [];
  for (const f of FEATURES) {
    const p = f.properties;
    const nameScore = score(p.village, needle);
    const districtScore = score(p.district, needle);
    const codeHit = String(p.village_code).toLowerCase().includes(needle);
    let s;
    if (nameScore >= 0) s = nameScore;
    else if (codeHit) s = 1.5;
    else if (districtScore >= 0) s = 4 + districtScore;
    else continue;
    out.push({ f, s });
  }
  return out.sort((a, b) => a.s - b.s || a.f.properties.village.localeCompare(b.f.properties.village)).slice(0, 8);
}

function highlight(text, needle) {
  const i = text.toLowerCase().indexOf(needle);
  if (i < 0 || needle.length < 2) return text;
  const frag = document.createDocumentFragment();
  frag.append(text.slice(0, i));
  const mark = document.createElement('mark');
  mark.textContent = text.slice(i, i + needle.length);
  frag.append(mark, text.slice(i + needle.length));
  return frag;
}

function renderResults(qRaw) {
  const hits = rank(qRaw);
  state.active = hits.length ? 0 : -1;
  results.innerHTML = '';

  if (!qRaw.trim()) { hideResults(); return; }
  if (!hits.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = `Tidak ditemukan “${qRaw.trim()}”`;
    results.append(li);
  } else {
    const needle = qRaw.trim().toLowerCase();
    hits.forEach(({ f }, i) => {
      const p = f.properties;
      const li = document.createElement('li');
      li.role = 'option';
      li.id = 'res-' + i;
      li.setAttribute('aria-selected', i === 0 ? 'true' : 'false');
      li.dataset.name = p.village;

      const name = document.createElement('span');
      name.className = 'r-name';
      name.append(highlight(p.village, needle));

      const meta = document.createElement('span');
      meta.className = 'r-meta';
      meta.textContent = p.district;

      li.append(name, meta);
      li.addEventListener('click', () => choose(f));
      li.addEventListener('pointerenter', () => setActive(i));
      results.append(li);
    });
  }
  results.hidden = false;
  q.setAttribute('aria-expanded', 'true');
}

function setActive(i) {
  const items = [...results.querySelectorAll('li[role="option"]')];
  if (!items.length) return;
  items.forEach(el => el.setAttribute('aria-selected', 'false'));
  state.active = Math.max(0, Math.min(i, items.length - 1));
  const el = items[state.active];
  el.setAttribute('aria-selected', 'true');
  el.scrollIntoView({ block: 'nearest' });
  q.setAttribute('aria-activedescendant', el.id);
}

function choose(f) {
  select(f.properties.village);
  hideResults();
  q.blur();
}

function hideResults() {
  results.hidden = true;
  results.innerHTML = '';
  q.setAttribute('aria-expanded', 'false');
  q.removeAttribute('aria-activedescendant');
  state.active = -1;
}

q.addEventListener('input', () => {
  clearBtn.hidden = !q.value;
  renderResults(q.value);
});

q.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    const items = [...results.querySelectorAll('li[role="option"]')];
    if (!items.length) return;
    e.preventDefault();
    setActive(state.active + (e.key === 'ArrowDown' ? 1 : -1));
  } else if (e.key === 'Enter') {
    const items = [...results.querySelectorAll('li[role="option"]')];
    const active = items[state.active];
    if (active) {
      e.preventDefault();
      choose(BY_NAME.get(active.dataset.name));
    }
  } else if (e.key === 'Escape') {
    if (!results.hidden) { hideResults(); }
    else { q.value = ''; clearBtn.hidden = true; }
  }
});

clearBtn.addEventListener('click', () => {
  q.value = '';
  clearBtn.hidden = true;
  hideResults();
  q.focus();
});

document.addEventListener('click', e => {
  if (!e.target.closest('.search')) hideResults();
});

/* ---------- theme ---------- */
const themeBtn = document.getElementById('theme');

function applyTheme(next) {
  state.theme = next;
  document.documentElement.dataset.theme = next;
  localStorage.setItem('depok-theme', next);
  document.querySelector('meta[name=theme-color]').content =
    getComputedStyle(document.documentElement).getPropertyValue('--theme-color').trim();
  map.setStyle(STYLES[next]);
}

/**
 * setStyle() discards every custom source and layer, so the boundary overlay
 * must be re-created.
 *
 * Instrumented findings on this app (MapLibre 5.6):
 *  - `load` does NOT re-fire on a setStyle() swap.
 *  - `styledata` fires exactly ONCE, while isStyleLoaded() is still false.
 *  - `idle` fires once the style and tiles settle, but only if the map is left
 *    alone; swapping again before it settles drops the event entirely. That is
 *    why a rapid light->dark->light sequence lost the overlay on the dark leg.
 *
 * So: hook the events AND poll until the style is loaded. The poll is the part
 * that actually makes this reliable; the listeners just make it fast.
 */
function restoreLayers() {
  mapReady = false;

  const tryAdd = () => {
    // Do NOT guard on "layer already exists". setStyle() does not tear down
    // custom layers synchronously — they survive into the next tick — so a
    // `if (map.getLayer(...)) return` guard bails out before the swap has even
    // started and the overlay is never restored. Wait for the style to report
    // loaded, then add if absent.
    if (!map.isStyleLoaded()) return;
    if (!map.getLayer('kel-fill')) addLayers();
    if (state.selected) select(state.selected, { zoom: false });
    stop();
  };

  function stop() {
    map.off('idle', tryAdd);
    map.off('styledata', tryAdd);
    clearInterval(poll);
  }

  map.on('idle', tryAdd);
  map.on('styledata', tryAdd);

  const poll = setInterval(tryAdd, 200);
  // Hard ceiling so a failed swap cannot leave a timer running forever.
  setTimeout(stop, 15000);
}

themeBtn.addEventListener('click', () => {
  applyTheme(state.theme === 'light' ? 'dark' : 'light');
  restoreLayers();
});

document.documentElement.dataset.theme = state.theme;

/* ---------- geolocation: "which kelurahan am I in?" ---------- */
const geoNote = document.getElementById('geo-note');
const geoText = document.getElementById('geo-text');

if ('geolocation' in navigator) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = 'Cari';
  btn.addEventListener('click', locate);
  geoNote.append(btn);
  geoNote.hidden = false;
  geoText.innerHTML = 'Ingin tahu kelurahannya di mana? <b>Cari lokasi saya</b>';

  navigator.geolocation.getCurrentPosition(
    pos => showGeo(pos),
    () => { geoNote.hidden = true; },
    { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 }
  );
}

function locate() {
  navigator.geolocation.getCurrentPosition(
    pos => showGeo(pos),
    () => { geoText.textContent = 'Lokasi tidak bisa diakses — pastikan izin lokasi aktif.'; },
    { enableHighAccuracy: true, timeout: 12000 }
  );
}

function showGeo(pos) {
  const pt = [pos.coords.longitude, pos.coords.latitude];
  const hits = map.queryRenderedFeatures(pt, { layers: ['kel-fill'] });
  if (!hits.length) {
    geoText.innerHTML = 'Lokasi ini <b>di luar Kota Depok</b>.';
    return;
  }
  const p = hits[0].properties;
  geoText.innerHTML = `Anda berada di <b>Kelurahan ${esc(p.village)}</b>, Kec. ${esc(p.district)}.`;
  select(p.village, { zoom: false });
}

function esc(s) {
  const d = document.createElement('span');
  d.textContent = s;
  return d.innerHTML;
}

/* ---------- boot ---------- */
const fromHash = decodeURIComponent(location.hash.slice(1));
if (fromHash) {
  const f = FEATURES.find(x => x.properties.village_code === fromHash || x.properties.village === fromHash);
  if (f) select(f.properties.village);
}

window.__depok = { map, state, select, rank, FEATURES, applyTheme };
