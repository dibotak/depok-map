/* depok-map — "which kelurahan/kecamatan was that place in?"
 *
 * Two administrative levels, drilled down one at a time:
 *
 *   city view        11 kecamatan outlines; click one to drill in
 *   kecamatan view   that kecamatan's kelurahan; click one to select
 *
 * Boundaries: HDX/BPS cod-ab-idn via JfrAziz/indonesia-district (CC BY-IGO).
 * Basemap:    OpenFreeMap (OpenMapTiles schema, OSM data). No API key.
 *
 * Everything is client-side. Each feature carries its parent's code
 * (village_code / district_code), so filtering children is a lookup and no
 * server or reverse-geocoder is involved.
 */
'use strict';

const DATA = window.DEPOK;
const KEL = DATA.features;
const KEC = DATA.districts;
const META = DATA.meta;

// Keyed by CODE, never by name.
//
// Names are not unique: "Curug" is a kelurahan in both Bojongsari and
// Cimanggis, and 7 names (Beji, Cilodong, Cinere, Cipayung, Limo, Pancoran
// Mas, Tapos) are a kelurahan AND a kecamatan of the same name. A Map keyed
// by name silently keeps only the last duplicate, so selecting the Curug in
// Bojongsari highlighted the Curug in Cimanggis. village_code and
// district_code are both unique across their level, so they are the only safe
// keys. `KEL_BY_NAME` is kept solely for search-result labels, and reads the
// full list so duplicates can be shown separately.
const KEL_BY_CODE = new Map(KEL.map(f => [f.properties.village_code, f]));
const KEL_BY_NAME = new Map();
for (const f of KEL) {
  const n = f.properties.village;
  if (!KEL_BY_NAME.has(n)) KEL_BY_NAME.set(n, []);
  KEL_BY_NAME.get(n).push(f);
}
const KEC_BY_NAME = new Map(KEC.map(f => [f.properties.district, f]));
const KEC_BY_CODE = new Map(KEC.map(f => [f.properties.district_code, f]));

/* ---------- basemap styles ---------- */
const STYLES = {
  light: 'https://tiles.openfreemap.org/styles/positron',
  dark: 'https://tiles.openfreemap.org/styles/dark',
};

/* ---------- state ---------- */
const state = {
  theme: localStorage.getItem('depok-theme')
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
  view: 'city',      // 'city' | 'kecamatan'
  district: null,    // district_code of the open kecamatan
  village: null,     // selected village name
  active: -1,        // keyboard cursor in the results list
};

/* ---------- palette (mirrors the tokens in app.css) ---------- */
const PALETTE = {
  light: {
    sel: '#1f6b48', selFill: '#1f6b48', peer: '#4f8f6d', peerFill: '#5c9a79',
    line: '#8a9a90', ink: '#2c3a31', halo: '#ffffff',
  },
  dark: {
    sel: '#6fd39b', selFill: '#6fd39b', peer: '#3f7f5c', peerFill: '#38745a',
    line: '#4a5a4e', ink: '#d8e6dc', halo: '#10130e',
  },
};

/* ---------- map ---------- */
const map = new maplibregl.Map({
  container: 'map',
  style: STYLES[state.theme],
  bounds: META.bounds,
  fitBoundsOptions: { padding: 40 },
  minZoom: 9,
  maxZoom: 17,
  attributionControl: false, // our own licence-complete line instead
  dragRotate: false,
  pitchWithRotate: false,
});

map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), 'top-right');
map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }), 'bottom-right');

const SRC_KEC = 'kecamatan';
const SRC_KEL = 'kelurahan';
let mapReady = false;

/**
 * Repaint for the current view.
 *
 * At city view the 11 kecamatan carry the whole visual — the kelurahan overlay
 * is hidden outright, because drawing 63 sub-areas over 11 parent areas reads
 * as noise rather than hierarchy. At kecamatan view the kecamatan outline
 * stays as a thin frame while its children carry the fill.
 */
/**
 * Suppress the basemap's own sub-area labels.
 *
 * OpenFreeMap's style carries an `label_village` layer that rendered 56
 * sub-area names at city zoom — OSM village/neighbourhood POIs, not our
 * administrative data. Visually they read as "here are the kelurahan" when
 * they are not, and they contradict the drill-down (a name may be labelled
 * where the map has no boundary for it). We own labelling at both levels, so
 * they are hidden while drilled out.
 *
 * The layer id is basemap-specific: hide whatever exists, don't assume a name.
 */
const BASEMAP_LABEL_LAYERS = ['label_village', 'place_labels', 'place_subdivision'];

function setForeignLabels(on) {
  for (const id of BASEMAP_LABEL_LAYERS) {
    if (map.getLayer(id)) {
      map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
    }
  }
}

function paint() {
  if (!mapReady) return;
  const p = PALETTE[state.theme];

  if (state.view === 'city') {
    map.setPaintProperty('kec-fill', 'fill-color', p.peerFill);
    map.setPaintProperty('kec-fill', 'fill-opacity', 0.12);
    map.setPaintProperty('kec-line', 'line-color', p.line);
    map.setPaintProperty('kec-line', 'line-width', 1.4);
    map.setPaintProperty('kec-label', 'text-color', p.sel);
    map.setPaintProperty('kec-label', 'text-halo-color', p.halo);
    map.setLayoutProperty('kel-fill', 'visibility', 'none');
    map.setLayoutProperty('kel-line', 'visibility', 'none');
    map.setLayoutProperty('kel-label', 'visibility', 'none');
    setForeignLabels(false);
    return;
  }

  setForeignLabels(false);
  map.setLayoutProperty('kel-fill', 'visibility', 'visible');
  map.setLayoutProperty('kel-line', 'visibility', 'visible');

  // Only the open kecamatan's children get labels — 63 names at once would be
  // unreadable and would fight the basemap for attention.
  map.setFilter('kel-label', ['==', ['get', 'district_code'], state.district]);
  map.setLayoutProperty('kel-label', 'visibility', 'visible');
  map.setPaintProperty('kel-label', 'text-color', p.ink);
  map.setPaintProperty('kel-label', 'text-halo-color', p.halo);

  // Compare on village_code, not village: two kelurahan share the name
  // "Curug", so a name comparison would highlight both at once.
  const isSel = ['==', ['get', 'village_code'], state.village];
  const isOpen = ['==', ['get', 'district_code'], state.district];

  // Selected kelurahan solid, the rest of the open kecamatan lightly tinted,
  // and everything outside it faint — so the open kecamatan reads as one unit.
  map.setPaintProperty('kel-fill', 'fill-color', p.selFill);
  map.setPaintProperty('kel-fill', 'fill-opacity',
    ['case', isSel, 0.62, isOpen, 0.16, 0.04]);
  map.setPaintProperty('kel-line', 'line-color',
    ['case', isSel, p.sel, p.line]);
  map.setPaintProperty('kel-line', 'line-width',
    ['case', isSel, 2.5, isOpen, 1.2, 0.8]);

  map.setPaintProperty('kec-fill', 'fill-color', p.selFill);
  map.setPaintProperty('kec-fill', 'fill-opacity', 0.03);
  map.setPaintProperty('kec-line', 'line-color', p.sel);
  map.setPaintProperty('kec-line', 'line-width', 1.8);
  // At kecamatan view the open district's own label would sit on top of its
  // children's names, so it fades out.
  map.setLayoutProperty('kec-label', 'visibility', 'none');
}

function addLayers() {
  // Guard each half: addSource throws if the source exists, and a duplicate
  // layer id is equally fatal. Either one aborts the rest of addLayers().
  if (!map.getSource(SRC_KEC)) {
    map.addSource(SRC_KEC, { type: 'geojson', data: { type: 'FeatureCollection', features: KEC } });
  }
  if (!map.getLayer('kec-fill')) {
    map.addLayer({
      id: 'kec-fill', type: 'fill', source: SRC_KEC,
      paint: { 'fill-color': PALETTE[state.theme].peerFill, 'fill-opacity': 0.12 },
    });
  }
  if (!map.getLayer('kec-line')) {
    map.addLayer({
      id: 'kec-line', type: 'line', source: SRC_KEC,
      paint: { 'line-color': PALETTE[state.theme].line, 'line-width': 1.4, 'line-opacity': 0.9 },
    });
  }
  // Label the 11 kecamatan at city view. Without this the map shows outlines
  // but no names, so the reader has to click each one to learn what it is.
  if (!map.getLayer('kec-label')) {
    map.addLayer({
      id: 'kec-label', type: 'symbol', source: SRC_KEC,
      layout: {
        'text-field': ['get', 'district'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 10, 11, 13, 14],
        'text-font': ['Noto Sans Bold'],
        'text-anchor': 'center',
        'text-allow-overlap': false,
        'text-optional': true,
      },
      paint: {
        'text-color': PALETTE[state.theme].sel,
        'text-halo-color': PALETTE[state.theme].halo,
        'text-halo-width': 1.6,
        'text-halo-blur': 0.6,
      },
    });
  }

  if (!map.getSource(SRC_KEL)) {
    // Must be an explicit FeatureCollection. DATA also carries `meta` and
    // `districts`, and passing that object straight through leaves `type`
    // undefined — the source then loads but resolves zero features, so the
    // layer renders nothing at all.
    map.addSource(SRC_KEL, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: KEL },
      promoteId: 'village',
    });
  }
  if (!map.getLayer('kel-fill')) {
    map.addLayer({
      id: 'kel-fill', type: 'fill', source: SRC_KEL,
      paint: { 'fill-color': PALETTE[state.theme].selFill, 'fill-opacity': 0.04 },
    });
  }
  if (!map.getLayer('kel-line')) {
    map.addLayer({
      id: 'kel-line', type: 'line', source: SRC_KEL,
      paint: { 'line-color': PALETTE[state.theme].line, 'line-width': 1, 'line-opacity': 0.8 },
    });
  }
  // Label the open kecamatan's kelurahan. 63 names at once would be unreadable
  // and would compete with the basemap, so this only appears once drilled in.
  if (!map.getLayer('kel-label')) {
    map.addLayer({
      id: 'kel-label', type: 'symbol', source: SRC_KEL,
      filter: ['==', ['get', 'district_code'], ''],  // replaced by paint()
      layout: {
        'text-field': ['get', 'village'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 11, 10, 14, 13],
        'text-font': ['Noto Sans Regular'],
        'text-anchor': 'center',
        'text-allow-overlap': false,
        'text-optional': true,
      },
      paint: {
        'text-color': PALETTE[state.theme].ink,
        'text-halo-color': PALETTE[state.theme].halo,
        'text-halo-width': 1.6,
        'text-halo-blur': 0.6,
      },
    });
  }
  mapReady = true;
  paint();
}

map.on('load', addLayers);

/* Click routing by level: a kecamatan click drills in, a kelurahan click
   selects. Guards keep a click from re-firing the other level's handler. */
map.on('click', 'kec-fill', e => {
  if (state.view !== 'city') return;
  const f = e.features && e.features[0];
  if (f) openDistrict(f.properties.district_code);
});

map.on('click', 'kel-fill', e => {
  if (state.view !== 'kecamatan') return;
  const f = e.features && e.features[0];
  if (f) selectVillage(f.properties.village_code);
});

for (const layer of ['kec-fill', 'kel-fill']) {
  map.on('mousemove', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseout', layer, () => { map.getCanvas().style.cursor = ''; });
}

/* ---------- bounds ---------- */
/**
 * Bounding box of a GeoJSON geometry.
 *
 * Computed by walking coordinates directly. fitBounds() on an empty box
 * silently leaves the camera where it was, which looks like "zoom is broken"
 * rather than an error.
 */
function boundsOfCoords(coords) {
  const b = new maplibregl.LngLatBounds();
  const walk = c => (typeof c[0] === 'number' ? b.extend(c) : c.forEach(walk));
  walk(coords);
  return b;
}
const boundsOf = f => boundsOfCoords(f.geometry.coordinates);

/* ---------- navigation ---------- */
const VIEW_PADDING = { top: 110, bottom: 230, left: 70, right: 70 };

function goCity(opts = {}) {
  state.view = 'city';
  state.district = null;
  state.village = null;
  paint();
  renderCard();
  renderBack();
  if (opts.zoom !== false) {
    map.fitBounds(boundsOfCoords([META.bounds]), {
      padding: { top: 100, bottom: 200, left: 56, right: 56 }, duration: 700,
    });
  }
  history.replaceState(null, '', location.pathname + location.search);
}

function openDistrict(code, opts = {}) {
  const kec = KEC_BY_CODE.get(code);
  if (!kec) return;
  state.view = 'kecamatan';
  state.district = code;
  state.village = null;
  paint();
  renderCard(kec);
  renderBack();
  if (opts.zoom !== false) {
    map.fitBounds(boundsOf(kec), { padding: VIEW_PADDING, maxZoom: 14.5, duration: 700 });
  }
  history.replaceState(null, '', '#kec/' + encodeURIComponent(code));
}

/** Selects a kelurahan by village_code. Names are ambiguous (see KEL_BY_CODE). */
function selectVillage(code, opts = {}) {
  const f = KEL_BY_CODE.get(code);
  if (!f) return;
  state.village = code;
  // Selecting a kelurahan from search must also open its kecamatan, otherwise
  // the highlight would land on a hidden layer.
  state.district = f.properties.district_code;
  state.view = 'kecamatan';
  paint();
  renderCard();
  renderBack();
  if (opts.zoom !== false) {
    map.fitBounds(boundsOf(f), {
      padding: { top: 120, bottom: 250, left: 70, right: 70 }, maxZoom: 15.5, duration: 700,
    });
  }
  history.replaceState(null, '', '#' + encodeURIComponent(f.properties.village_code));
}

function selectDistrict(name, opts = {}) {
  const f = KEC_BY_NAME.get(name);
  if (f) openDistrict(f.properties.district_code, opts);
}

/* ---------- card ---------- */
const card = document.getElementById('card');
const legend = document.getElementById('legend');

/**
 * Names come from an external dataset, so every one of them goes into the DOM
 * via textContent — never innerHTML.
 */
function renderCard(kecOverride) {
  legend.hidden = state.view === 'city';

  if (state.view === 'city') {
    card.className = 'card';
    card.innerHTML = `
      <div class="card-top">
        <div class="eyebrow">Kota Depok</div>
        <div class="card-name"></div>
      </div>
      <div class="ladder">
        <div class="rung"><i class="dot"></i><span class="lvl">Provinsi</span><span class="nm prov"></span></div>
        <div class="rung"><i class="dot"></i><span class="lvl">Wilayah</span><span class="nm scope"></span></div>
      </div>
      <div class="card-foot">
        <span class="hint">Klik kecamatan di peta untuk masuk</span>
      </div>`;
    card.querySelector('.card-name').textContent = META.regency;
    card.querySelector('.prov').textContent = META.province;
    card.querySelector('.scope').textContent =
      `${META.district_count} kecamatan · ${META.village_count} kelurahan`;
    return;
  }

  const kec = kecOverride || KEC_BY_CODE.get(state.district);
  if (!kec) { goCity(); return; }
  const kp = kec.properties;

  const kids = KEL.filter(f => f.properties.district_code === kp.district_code);
  const sel = state.village ? KEL_BY_CODE.get(state.village) : null;
  const sp = sel ? sel.properties : null;

  card.className = 'card';
  card.innerHTML = `
    <div class="card-top">
      <div class="eyebrow"></div>
      <div class="card-name"></div>
    </div>
    <div class="ladder"></div>
    <div class="card-foot">
      <span class="code"></span>
      <button class="copy" type="button"></button>
    </div>`;

  card.querySelector('.eyebrow').textContent = sp ? 'Kelurahan dipilih' : 'Kecamatan';
  card.querySelector('.card-name').textContent = sp ? sp.village : kp.district;

  const ladder = card.querySelector('.ladder');
  const rungs = [['Kecamatan', kp.district], ['Kabupaten', kp.regency], ['Provinsi', kp.province]];
  if (sp) rungs.unshift(['Kelurahan', sp.village]);
  for (const [lvl, val] of rungs) {
    const row = document.createElement('div');
    row.className = 'rung';
    const dot = document.createElement('i'); dot.className = 'dot';
    const l = document.createElement('span'); l.className = 'lvl'; l.textContent = lvl;
    const v = document.createElement('span'); v.className = 'nm'; v.textContent = val;
    row.append(dot, l, v);
    ladder.append(row);
  }

  const codeEl = card.querySelector('.code');
  const btn = card.querySelector('.copy');

  if (sp) {
    codeEl.textContent = sp.village_code;
    btn.textContent = 'Salin kode';
    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(sp.village_code);
        btn.textContent = 'Tersalin';
        btn.classList.add('done');
        setTimeout(() => { btn.textContent = 'Salin kode'; btn.classList.remove('done'); }, 1600);
      } catch {
        // Clipboard needs a secure context; fall back to a manual selection.
        const r = document.createRange();
        r.selectNode(codeEl);
        const s = getSelection();
        s.removeAllRanges(); s.addRange(r);
        btn.textContent = 'Pilih manual';
        setTimeout(() => { btn.textContent = 'Salin kode'; }, 1600);
      }
    });
  } else {
    codeEl.textContent = kp.district_code;
    btn.textContent = `Lihat ${kids.length} kelurahan`;
    btn.addEventListener('click', () => {
      const input = document.getElementById('q');
      input.value = kp.district;
      input.dispatchEvent(new Event('input'));
      input.focus();
    });
  }
}

/* ---------- back button ---------- */
const backBtn = document.getElementById('back');

function renderBack() {
  backBtn.hidden = state.view === 'city';
  if (state.view !== 'city') backBtn.querySelector('span').textContent = 'Semua kecamatan';
}

/* ---------- search ---------- */
const q = document.getElementById('q');
const results = document.getElementById('results');
const clearBtn = document.getElementById('clear');

/**
 * Fuzzy match one field. Lower is better; -1 means no match.
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
 * and put Ciganjur above the actual prefix match.
 *
 * Order here: name -> code -> district sibling (always last). A kecamatan
 * result only outranks a kelurahan when the kecamatan name is the better
 * match on its own name; otherwise the specific kelurahan wins.
 */
function rank(raw) {
  const needle = raw.trim().toLowerCase();
  if (!needle) return [];

  const out = [];

  // Kecamatan, by name or code. These are the entry point at city view.
  for (const f of KEC) {
    const p = f.properties;
    const ns = score(p.district, needle);
    let s;
    if (ns >= 0) s = ns;
    else if (String(p.district_code).toLowerCase().includes(needle)) s = 1.5;
    else continue;
    out.push({ kind: 'kec', f, s, label: p.district });
  }

  // Kelurahan, by name or code, falling back to their kecamatan's children.
  for (const f of KEL) {
    const p = f.properties;
    const ns = score(p.village, needle);
    let s;
    if (ns >= 0) s = ns;
    else if (String(p.village_code).toLowerCase().includes(needle)) s = 1.5;
    else {
      const ds = score(p.district, needle);
      if (ds < 0) continue;
      s = 6 + ds;
    }
    out.push({ kind: 'kel', f, s, label: p.village });
  }

  // District name is the final tiebreak so that two same-named kelurahan (the
  // two "Curug" entries) always come back in a stable, readable order rather
  // than whatever order the sort happened to leave them in.
  const districtOf = h => h.f.properties.district;
  return out
    .sort((a, b) => a.s - b.s
      || (a.kind === b.kind ? 0 : a.kind === 'kec' ? -1 : 1)
      || districtOf(a).localeCompare(districtOf(b))
      || a.label.localeCompare(b.label))
    .slice(0, 8);
}

function highlight(text, needle) {
  const i = text.toLowerCase().indexOf(needle);
  if (i < 0 || needle.length < 2) return document.createTextNode(text);
  const frag = document.createDocumentFragment();
  frag.append(text.slice(0, i));
  const mark = document.createElement('mark');
  mark.textContent = text.slice(i, i + needle.length);
  frag.append(mark, text.slice(i + needle.length));
  return frag;
}

function renderResults(raw) {
  const hits = rank(raw);
  state.active = hits.length ? 0 : -1;
  results.innerHTML = '';

  if (!raw.trim()) { hideResults(); return; }
  if (!hits.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = `Tidak ditemukan “${raw.trim()}”`;
    results.append(li);
  } else {
    const needle = raw.trim().toLowerCase();
    hits.forEach((h, i) => {
      const p = h.f.properties;
      const isKec = h.kind === 'kec';
      const li = document.createElement('li');
      li.setAttribute('role', 'option');
      li.id = 'res-' + i;
      li.setAttribute('aria-selected', i === 0 ? 'true' : 'false');
      if (isKec) li.classList.add('is-district');

      const name = document.createElement('span');
      name.className = 'r-name';
      name.append(highlight(h.label, needle));

      const meta = document.createElement('span');
      meta.className = 'r-meta';
      meta.textContent = isKec ? 'kecamatan' : p.district;

      // "Curug" exists in both Bojongsari and Cimanggis. Without the
      // kecamatan in the row, both results read as identical and the user
      // cannot tell which one they are about to open.
      const dup = !isKec && (KEL_BY_NAME.get(h.label) || []).length > 1;
      if (dup) li.classList.add('is-dup');

      li.append(name, meta);
      li.addEventListener('click', () => choose(h));
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

function choose(h) {
  if (h.kind === 'kec') selectDistrict(h.f.properties.district);
  else selectVillage(h.f.properties.village_code);
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
    if (!results.querySelectorAll('li[role="option"]').length) return;
    e.preventDefault();
    setActive(state.active + (e.key === 'ArrowDown' ? 1 : -1));
  } else if (e.key === 'Enter') {
    const items = [...results.querySelectorAll('li[role="option"]')];
    if (items[state.active]) {
      e.preventDefault();
      // Re-rank rather than reading data off the DOM: the list is rendered
      // from the same ranking, but this stays correct if rendering ever
      // filters or reorders.
      const h = rank(q.value)[state.active];
      if (h) choose(h);
    }
  } else if (e.key === 'Escape') {
    if (!results.hidden) hideResults();
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

backBtn.addEventListener('click', () => goCity());

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
 * setStyle() discards every custom source and layer, so both levels must be
 * re-created.
 *
 * Instrumented findings on this app (MapLibre 5.6):
 *  - `load` does NOT re-fire on a setStyle() swap.
 *  - `styledata` fires exactly ONCE, while isStyleLoaded() is still false.
 *  - `idle` fires only if the map is left alone; swapping again before it
 *    settles drops the event entirely — that is why a fast toggle sequence
 *    lost the overlay.
 *
 * So hook the events AND poll until the style is loaded. Do NOT guard this with
 * `if (map.getLayer(...)) return`: setStyle() does not tear layers down
 * synchronously — they survive into the next tick — so that guard bails out
 * before the swap has even started and the overlay silently disappears.
 */
function restoreLayers() {
  mapReady = false;

  const tryAdd = () => {
    if (!map.isStyleLoaded()) return;
    if (!map.getLayer('kec-fill')) addLayers();
    else paint();
    renderCard();  // re-render without moving the camera
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
  setTimeout(stop, 15000); // hard ceiling; a failed swap must not leak a timer
}

themeBtn.addEventListener('click', () => {
  applyTheme(state.theme === 'light' ? 'dark' : 'light');
  restoreLayers();
});

/* ---------- geolocation: "which area am I in?" ---------- */
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
  const drilled = state.view === 'kecamatan';

  // Check the visible level first, then fall back: a point near a shared
  // kelurahan/kecamatan edge can miss the child polygon while still sitting
  // inside the parent, and a dead end there would be a silent failure.
  const hits = map.queryRenderedFeatures(pt, { layers: [drilled ? 'kel-fill' : 'kec-fill'] });
  if (hits.length) {
    const p = hits[0].properties;
    if (drilled) {
      geoText.innerHTML = `Anda berada di <b>Kelurahan ${esc(p.village)}</b>, Kec. ${esc(p.district)}.`;
      selectVillage(p.village_code, { zoom: false });
    } else {
      geoText.innerHTML = `Anda berada di <b>Kecamatan ${esc(p.district)}</b>.`;
      openDistrict(p.district_code, { zoom: false });
    }
    return;
  }

  if (drilled) {
    const kec = map.queryRenderedFeatures(pt, { layers: ['kec-fill'] });
    if (kec.length) {
      const p = kec[0].properties;
      geoText.innerHTML = `Di dalam <b>Kecamatan ${esc(p.district)}</b>.`;
      selectDistrict(p.district, { zoom: false });
      return;
    }
  }
  geoText.innerHTML = 'Lokasi ini <b>di luar Kota Depok</b>.';
}

function esc(s) {
  const d = document.createElement('span');
  d.textContent = s;
  return d.innerHTML;
}

/* ---------- boot ---------- */
document.documentElement.dataset.theme = state.theme;
renderCard();
renderBack();

const hash = decodeURIComponent(location.hash.slice(1));
if (hash) {
  if (hash.startsWith('kec/')) {
    openDistrict(hash.slice(4), { zoom: false });
  } else if (KEL_BY_CODE.has(hash)) {
    selectVillage(hash, { zoom: false });
  } else {
    // Tolerate a name in the hash for hand-typed links, but only when it is
    // unambiguous — guessing between two "Curug" entries would silently open
    // the wrong one.
    const matches = KEL_BY_NAME.get(hash) || [];
    if (matches.length === 1) selectVillage(matches[0].properties.village_code, { zoom: false });
  }
}

window.__depok = {
  map, state, KEL, KEC, rank, goCity, openDistrict, selectVillage, selectDistrict, applyTheme,
};