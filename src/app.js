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
    // Road highlight is deliberately outside the green admin family, so a
    // selected road never reads as a selected kecamatan.
    road: '#c2410c',
  },
  dark: {
    sel: '#6fd39b', selFill: '#6fd39b', peer: '#3f7f5c', peerFill: '#38745a',
    line: '#4a5a4e', ink: '#d8e6dc', halo: '#10130e',
    road: '#fb923c',
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
const SRC_ROAD = 'jalan';
let mapReady = false;

/**
 * Repaint for the current view.
 *
 * One level of detail is on screen at a time:
 *   city      11 kecamatan outlines, no kelurahan at all
 *   kecamatan the open one's kelurahan; every other kecamatan stays a plain
 *             polygon, so you keep the city's shape as context
 *   kelurahan one child solid, its siblings tinted
 *
 * The rule is a filter on the kelurahan layers, not an opacity ramp: drawing
 * the other 10 kecamatan's children at 4% opacity still produced ~55 hairlines
 * across the map, which reads as noise rather than as "not this one".
 */

/**
 * Suppress the basemap's own sub-area geometry and labels.
 *
 * Two separate leaks, both fixed at city and district level:
 *
 * 1. Labels. OpenFreeMap's `label_village` layer rendered 56 OSM village and
 *    neighbourhood names at city zoom. They read as "these are the kelurahan"
 *    when they are not, and they contradicted the drill-down.
 *
 * 2. The basemap's own administrative boundary lines. `boundary_3` draws OSM
 *    admin_level 3-6, which in Indonesia stops at *kecamatan* — it does not
 *    include kelurahan (level 7). So these are a second, independent set of
 *    district edges: coarser, from OSM, and not the 2020 upstream geometry this
 *    app publishes. Where the two disagree you see doubled edges and slivers.
 *    Hidden alongside the village labels. We own geometry and labelling.
 *
 * NOTE: these layers were originally blamed for the subdivided look at city
 * view. They were not the main cause — `kec-line` was drawing all 63 kelurahan
 * borders because the district geometry was never dissolved. See
 * scripts/build-districts.py and the assertion in scripts/bundle-data.py.
 * Hiding the basemap layers is still correct (foreign admin geometry we do not
 * publish), but it was never sufficient.
 */
const BASEMAP_LABEL_LAYERS = ['label_village', 'place_labels', 'place_subdivision'];
const BASEMAP_ADMIN_LAYERS = ['boundary_3', 'boundary_2', 'boundary_4', 'boundary'];

function setForeignLayers(on) {
  for (const id of BASEMAP_LABEL_LAYERS.concat(BASEMAP_ADMIN_LAYERS)) {
    if (map.getLayer(id)) {
      map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
    }
  }
}

function paint() {
  if (!mapReady) return;
  const p = PALETTE[state.theme];

  // Road highlight first, and unconditionally. Both branches of this function
  // return early, so anything added after them would only be themed on district
  // changes and go stale on a theme toggle.
  if (map.getLayer('road-sel')) {
    map.setPaintProperty('road-sel', 'line-color', p.road);
    map.setPaintProperty('road-sel-halo', 'line-color', p.road);
    map.setPaintProperty('road-sel-label', 'text-halo-color', p.road);
    map.setPaintProperty('road-sel-label', 'text-color', p.halo);
  }

  if (state.view === 'city') {
    map.setPaintProperty('kec-fill', 'fill-color', p.peerFill);
    map.setPaintProperty('kec-fill', 'fill-opacity', 0.12);
    map.setPaintProperty('kec-line', 'line-color', p.line);
    map.setPaintProperty('kec-line', 'line-width', 1.4);
    map.setPaintProperty('kec-label', 'text-color', p.sel);
    map.setPaintProperty('kec-label', 'text-halo-color', p.halo);
    // Clear the drill-down label filter. Left in place it keeps the previously
    // open kecamatan unlabelled at city view — invisible, and only explicable
    // by reading the filter back out of the layer.
    map.setFilter('kec-label', null);
    map.setLayoutProperty('kel-fill', 'visibility', 'none');
    map.setLayoutProperty('kel-line', 'visibility', 'none');
    map.setLayoutProperty('kel-label', 'visibility', 'none');
    setForeignLayers(false);
    return;
  }

  setForeignLayers(false);

  // Only the open kecamatan's children exist on screen. Filtering the source
  // down (rather than dimming it) is what keeps the other 10 kecamatan reading
  // as plain areas — see the note on paint().
  const kidsOnly = ['==', ['get', 'district_code'], state.district];
  for (const id of ['kel-fill', 'kel-line', 'kel-label']) {
    map.setFilter(id, kidsOnly);
    map.setLayoutProperty(id, 'visibility', 'visible');
  }
  map.setPaintProperty('kel-label', 'text-color', p.ink);
  map.setPaintProperty('kel-label', 'text-halo-color', p.halo);

  // Compare on village_code, not village: two kelurahan share the name
  // "Curug", so a name comparison would highlight both at once.
  const isSel = ['==', ['get', 'village_code'], state.village];

  // Selected kelurahan solid, the rest of the open kecamatan lightly tinted.
  // Every feature passing the filter is inside the open kecamatan, so the
  // old "isOpen" branch is now unconditional.
  map.setPaintProperty('kel-fill', 'fill-color', p.selFill);
  map.setPaintProperty('kel-fill', 'fill-opacity', ['case', isSel, 0.62, 0.16]);
  map.setPaintProperty('kel-line', 'line-color', ['case', isSel, p.sel, p.line]);
  map.setPaintProperty('kel-line', 'line-width', ['case', isSel, 2.5, 1.2]);

  // The open kecamatan drops to a hairline so its children carry the fill;
  // the other 10 keep the city-level treatment, so the city's shape and the
  // open one's position inside it both stay readable.
  map.setPaintProperty('kec-fill', 'fill-color', p.peerFill);
  map.setPaintProperty('kec-fill', 'fill-opacity', 0.12);
  map.setPaintProperty('kec-line', 'line-color', p.line);
  map.setPaintProperty('kec-line', 'line-width', 1.4);

  // Label every kecamatan EXCEPT the open one — its own name would sit on top
  // of its children's. The others are useful: they are the map's "you are here"
  // at this level, and they are what a click on them acts on.
  map.setFilter('kec-label', ['!=', ['get', 'district_code'], state.district]);
  map.setLayoutProperty('kec-label', 'visibility', 'visible');
  map.setPaintProperty('kec-label', 'text-color', p.peer);
  map.setPaintProperty('kec-label', 'text-halo-color', p.halo);
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
  // The selected road (Perda 9/2022 Pasal 16). Separate from the admin layers so
  // it survives a district change, and drawn in a colour no admin layer uses.
  if (!map.getSource(SRC_ROAD)) {
    map.addSource(SRC_ROAD, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  }
  if (!map.getLayer('road-sel')) {
    map.addLayer({
      id: 'road-sel', type: 'line', source: SRC_ROAD,
      filter: ['==', ['get', 'role'], 'line'],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': PALETTE[state.theme].road,
        'line-width': 4.5,
        'line-opacity': 0.95,
      },
    });
    // A wider translucent pass underneath reads as a glow, so the road stays
    // findable against both the light and the dark basemap.
    map.addLayer({
      id: 'road-sel-halo', type: 'line', source: SRC_ROAD,
      filter: ['==', ['get', 'role'], 'line'],
      paint: {
        'line-color': PALETTE[state.theme].road,
        'line-width': 11,
        'line-opacity': 0.22,
        'line-blur': 3,
      },
    });
    // Name the selected road ON the map, at the middle of its longest segment.
    // Labelling the whole geometry would collide with itself on a long corridor.
    if (!map.getLayer('road-sel-label')) {
      map.addLayer({
        id: 'road-sel-label', type: 'symbol', source: SRC_ROAD,
        filter: ['==', ['get', 'role'], 'label'],
        layout: {
          'text-field': ['get', 'label'],
          'text-size': 12,
          'text-font': ['Noto Sans Regular'],
          'text-anchor': 'center',
          'symbol-placement': 'point',
        },
        paint: {
          'text-color': PALETTE[state.theme].halo,
          'text-halo-color': PALETTE[state.theme].road,
          'text-halo-width': 2.6,
          'text-halo-blur': 0.4,
        },
      });
    }
  }
  mapReady = true;
  paint();
}

map.on('load', addLayers);

/* Click routing: a kecamatan click opens it, a kelurahan click selects it.
   Both levels are clickable at once once drilled in — the open kecamatan's
   children sit on top of their parent, so a click inside it reaches both
   handlers. The parent's handler ignores its own district_code and lets the
   kelurahan handler win; a click on one of the other 10 switches to it
   directly, which is the same gesture as at city view. */
map.on('click', 'kec-fill', e => {
  const f = e.features && e.features[0];
  if (!f) return;
  if (f.properties.district_code === state.district) return;
  openDistrict(f.properties.district_code);
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
  positionRoadCard();
  renderBack();
  renderAreaList();
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
  renderAreaList();
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
  positionRoadCard();
  renderBack();
  renderAreaList();
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

  // renderCard() rebuilds the card from scratch, so the minimise button is a
  // brand-new element each time and would come back with the default label even
  // while the card is collapsed. Re-apply the label from the body state.
  const syncCardMin = () => {
    const b = card.querySelector('.card-min');
    if (!b) return;
    const expanded = !document.body.classList.contains('card-min');
    b.setAttribute('aria-label', expanded ? 'Perkecil info' : 'Perluas info');
    b.title = expanded ? 'Perkecil info' : 'Perluas info';
  };

  if (state.view === 'city') {
    card.className = 'card';
    card.innerHTML = `
      <div class="card-top">
        <div class="eyebrow">Kota Depok</div>
        <div class="card-name"></div>
      <button class="card-min" type="button" aria-label="Perkecil info" title="Perkecil info">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M6 9l6 6 6-6"/>
        </svg>
      </button>
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
    syncCardMin();
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
      <button class="card-min" type="button" aria-label="Perkecil info" title="Perkecil info">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M6 9l6 6 6-6"/>
        </svg>
      </button>
    </div>
    <div class="ladder"></div>
    <div class="kids"></div>
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

  // The open kecamatan's kelurahan as a chip row. The map draws them, but on a
  // phone several of Limo's 8 fit side by side at most — the chips are the
  // reliable way to reach one by name, and they double as the legend for the
  // selected chip. Hidden once a kelurahan is chosen: the card is then about
  // that one, and the map already highlights its siblings.
  const kidsEl = card.querySelector('.kids');
  if (!sp) {
    kidsEl.hidden = false;
    for (const f of kids.slice().sort((a, b) =>
      a.properties.village.localeCompare(b.properties.village))) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip';
      chip.textContent = f.properties.village;
      chip.addEventListener('click', () => selectVillage(f.properties.village_code));
      kidsEl.append(chip);
    }
  } else {
    kidsEl.hidden = true;
  }

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
    // No button here: the chip row above already lists this kecamatan's
    // kelurahan by name. The old "Lihat N kelurahan" button just refilled the
    // search box with the district name to reach the same list.
    codeEl.textContent = kp.district_code;
  }
  syncCardMin();
}

/* ---------- back button ---------- */
const backBtn = document.getElementById('back');

/**
 * Back walks one step up the hierarchy, so its label names the level it
 * returns to rather than always saying "all kecamatan": from a kelurahan it
 * reads "Ke Limo", from a kecamatan "Semua kecamatan". A single fixed label
 * would tell you nothing about where you are going.
 */
function renderBack() {
  backBtn.hidden = state.view === 'city';
  if (state.view === 'city') return;
  const label = backBtn.querySelector('span');
  if (state.village) {
    const kec = KEC_BY_CODE.get(state.district);
    label.textContent = kec ? `Ke ${kec.properties.district}` : 'Semua kecamatan';
  } else {
    label.textContent = 'Semua kecamatan';
  }
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

/**
 * One step up the hierarchy: a selected kelurahan returns to its kecamatan
 * (keeping the camera where it is — the parent is still on screen, so
 * re-fitting would only undo the user's zoom), a kecamatan returns to city.
 */
backBtn.addEventListener('click', () => {
  if (state.view === 'kecamatan' && state.village) {
    const kec = KEC_BY_CODE.get(state.district);
    if (kec) openDistrict(kec.properties.district_code, { zoom: false });
    else goCity();
    return;
  }
  goCity();
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
 * So hook the events AND poll until the overlay is back. Three traps, all hit
 * on this app:
 *
 *  1. Do NOT branch on `map.getLayer('kec-fill')` to decide whether to
 *     re-create. setStyle() does not tear layers down synchronously — they
 *     survive into the next tick — so that check passes while the overlay is
 *     still the *old* one, and the swap silently never happens.
 *
 *  2. Do NOT route the "already there" case to paint(). restoreLayers() sets
 *     mapReady = false up front, and paint() bails on `if (!mapReady)`, so
 *     paint() would be a no-op — and the stop() that follows would cancel the
 *     poll, leaving no way back. The symptom was a blank overlay after a
 *     theme toggle that never recovered.
 *
 *  3. Do NOT gate on map.isStyleLoaded(). It is not a reliable "the swap is
 *     done" signal: on a throttled or backgrounded tab it can stay false long
 *     after the style is usable, and then nothing is ever re-added. addLayers()
 *     is idempotent, so it is safe to simply call it — and it is the only
 *     thing that can tell us whether the overlay exists.
 */
function restoreLayers() {
  mapReady = false;

  const tryAdd = () => {
    // addLayers() must not abort on a style that is still settling, or the
    // poll would stop before it ever succeeds.
    try {
      addLayers();
    } catch (e) {
      return; // not ready yet; the poll tries again
    }
    renderCard();  // re-render without moving the camera
    positionRoadCard();
    // setStyle() drops the road source back to empty, so a selected road would
    // silently vanish on a theme toggle. Re-push the same selection -- without
    // re-fitting the camera, which would undo the user's own panning.
    if (selectedRoad) {
      // findRoad() returns {road, label}; roadFeatures() needs the road itself.
      const hit = findRoad(selectedRoad);
      if (hit.road) map.getSource(SRC_ROAD).setData(roadFeatures(hit.road));
    }
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
// Lives in the sidebar's Wilayah panel, not in a floating banner: the banner
// sat directly under the search box and pushed the map down on a phone.
const locateWrap = document.getElementById('side-locate');
const locateBtn = document.getElementById('locate-btn');
const geoText = document.getElementById('locate-text');

if ('geolocation' in navigator) {
  locateWrap.hidden = false;
  locateBtn.addEventListener('click', locate);
}

function locate() {
  // Reflect the in-flight state on the button; a silent 12s wait looks broken.
  locateBtn.disabled = true;
  locateBtn.textContent = 'Mencari…';
  const done = () => { locateBtn.disabled = false; locateBtn.textContent = 'Cari lokasi saya'; };
  navigator.geolocation.getCurrentPosition(
    pos => { done(); showGeo(pos); },
    () => { done(); geoText.textContent = 'Lokasi tidak bisa diakses — pastikan izin lokasi aktif.'; },
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
      geoText.textContent = `Anda berada di Kelurahan ${p.village}, Kec. ${p.district}.`;
      selectVillage(p.village_code, { zoom: false });
    } else {
      geoText.textContent = `Anda berada di Kecamatan ${p.district}.`;
      openDistrict(p.district_code, { zoom: false });
    }
    return;
  }

  if (drilled) {
    const kec = map.queryRenderedFeatures(pt, { layers: ['kec-fill'] });
    if (kec.length) {
      const p = kec[0].properties;
      geoText.textContent = `Di dalam Kecamatan ${p.district}.`;
      selectDistrict(p.district, { zoom: false });
      return;
    }
  }
  geoText.textContent = 'Lokasi ini di luar Kota Depok.';
}

function esc(s) {
  const d = document.createElement('span');
  d.textContent = s;
  return d.innerHTML;
}

/* ---------- minimise the answer card ---------- */
// Collapses the card to its title row so the map underneath is visible. State
// lives on <body> so it survives the re-render that renderCard() does on every
// selection change -- otherwise picking another area would pop the card open
// again mid-browse.
card.addEventListener('click', e => {
  const btn = e.target.closest('.card-min');
  if (!btn) return;
  const on = !document.body.classList.toggle('card-min');
  btn.setAttribute('aria-label', on ? 'Perkecil info' : 'Perluas info');
  btn.title = on ? 'Perkecil info' : 'Perluas info';
  // The card changes height, so the road banner above it must re-measure.
  positionRoadCard();
});

/* ---------- sidebar ---------- */
const sideEl = document.getElementById('sidebar');
const sideToggle = document.getElementById('side-toggle');
const sideClose = document.getElementById('side-close');
const tabArea = document.getElementById('tab-area');
const tabRoad = document.getElementById('tab-road');
const panelArea = document.getElementById('panel-area');
const panelRoad = document.getElementById('panel-road');
const areaList = document.getElementById('area-list');
const roadGroups = document.getElementById('road-groups');
const roadScroll = document.getElementById('road-scroll');
const roadNote = document.getElementById('road-note');
const roadCard = document.getElementById('road-card');
const roadCardKlas = document.getElementById('road-card-klas');
const roadCardName = document.getElementById('road-card-name');

/** Show the selected road's Perda class + name in a persistent banner, and keep
 *  it stacked above the answer card rather than on top of it. */
function renderRoadCard(road, label) {
  if (!road) {
    roadCard.hidden = true;
    return;
  }
  roadCardKlas.textContent = label;
  roadCardName.textContent = road.name;
  roadCard.style.setProperty('--road-c', PALETTE[state.theme].road);
  roadCard.hidden = false;
  positionRoadCard();
}

/** Stack the road banner directly above the answer card.
 *
 *  The gap is derived from the answer card's measured *top edge*, not its
 *  height: the answer card is positioned from the top of the viewport, so on a
 *  short phone screen its height and the space below it differ wildly. Offsetting
 *  by height put the banner straight through the card at 430x932. Measuring the
 *  top edge and subtracting from the viewport height is correct at any size.
 *
 *  Re-measured on resize and whenever the answer card re-renders, since both
 *  change what it should sit on top of.
 */
function positionRoadCard() {
  if (roadCard.hidden) return;
  const cardEl = document.getElementById('card');
  const gap = 12;
  let offset = 0;
  if (cardEl && !cardEl.hidden) {
    offset = Math.max(0, window.innerHeight - cardEl.getBoundingClientRect().top) + gap;
  }
  roadCard.style.setProperty('--card-h', `${offset}px`);
}

function setSidebar(open) {
  sideEl.hidden = !open;
  document.body.classList.toggle('side-open', open);
  if (open) {
    map.resize();
    // Focus the panel so keyboard users land inside it rather than behind it.
    const first = sideEl.querySelector('.side-tab.is-on');
    if (first) first.focus({ preventScroll: true });
  }
  syncToggle();
}

/** The toggle opens the panel when closed and closes it when open. Icon only,
 *  so there is no label to keep in sync -- only the accessible name. */
function syncToggle() {
  const open = !sideEl.hidden;
  const label = open ? 'Tutup daftar' : 'Buka daftar wilayah dan jalan';
  sideToggle.setAttribute('aria-label', label);
  sideToggle.title = label;
  sideToggle.setAttribute('aria-expanded', String(open));
}

document.getElementById('road-card-x').addEventListener('click', () => highlightRoad(null));

// The banner's offset depends on the answer card's position, which moves on
// rotate and on viewport changes.
window.addEventListener('resize', positionRoadCard);
window.addEventListener('orientationchange', positionRoadCard);

sideToggle.addEventListener('click', () => setSidebar(sideEl.hidden));
sideClose.addEventListener('click', () => setSidebar(false));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !sideEl.hidden) setSidebar(false);
});

function setTab(which) {
  const road = which === 'road';
  tabArea.classList.toggle('is-on', !road);
  tabRoad.classList.toggle('is-on', road);
  tabArea.setAttribute('aria-selected', String(!road));
  tabRoad.setAttribute('aria-selected', String(road));
  panelArea.hidden = road;
  panelRoad.hidden = !road;
}
tabArea.addEventListener('click', () => setTab('area'));
tabRoad.addEventListener('click', () => setTab('road'));

/** Area list: 11 kecamatan, then their kelurahan once one is open. */
function renderAreaList() {
  const kids = state.district
    ? KEL.filter(f => f.properties.district_code === state.district)
        .sort((a, b) => a.properties.village.localeCompare(b.properties.village, 'id'))
    : [];
  const openKec = state.district ? KEC_BY_CODE.get(state.district) : null;

  let html = '';
  html += `<div class="side-group">11 Kecamatan</div>`;
  for (const f of KEC.slice().sort((a, b) =>
    a.properties.district.localeCompare(b.properties.district, 'id'))) {
    const code = f.properties.district_code;
    const n = KEL.filter(k => k.properties.district_code === code).length;
    html += `<button class="side-row${code === state.district ? ' is-on' : ''}" type="button"
      data-code="${esc(code)}" data-kind="kec">
      <span class="sr-name">${esc(f.properties.district)}</span>
      <span class="sr-meta">${n}</span></button>`;
  }

  if (openKec) {
    html += `<div class="side-group">Kelurahan · ${esc(openKec.properties.district)}</div>`;
    if (!kids.length) {
      html += `<p class="road-empty">Tidak ada kelurahan.</p>`;
    }
    for (const f of kids) {
      const code = f.properties.village_code;
      html += `<button class="side-row${code === state.village ? ' is-on' : ''}" type="button"
        data-code="${esc(code)}" data-kind="kel">
        <span class="sr-name">${esc(f.properties.village)}</span></button>`;
    }
  }

  // Preserve scroll across a re-render. innerHTML replacement resets
  // scrollTop to 0, so on a phone tapping a kecamatan threw the list back to the
  // top and the row you just pressed moved under your thumb.
  const keepScroll = areaList.scrollTop;
  areaList.innerHTML = html;
  areaList.scrollTop = keepScroll;
  for (const el of areaList.querySelectorAll('.side-row')) {
    el.addEventListener('click', () => {
      if (el.dataset.kind === 'kec') openDistrict(el.dataset.code);
      else selectVillage(el.dataset.code);
      // Below 760px the panel is full-width and covers the card, which holds the
      // GPS button and the copyable BPS codes. Close it so the result is visible.
      if (window.matchMedia('(max-width: 760px)').matches) setSidebar(false);
    });
  }
}

/* ---------- roads ---------- */
const ROADS = window.DEPOK_ROADS || { classes: [] };

/** Which road is selected, by `key` (class key + ruas index). */
let selectedRoad = null;
/** Which road classes are expanded in the list. */
const roadOpen = new Set();

/** Build the GeoJSON for one ruas: its line segments, plus one labelled point
 *  at the midpoint of its longest segment.
 *
 *  The label is a separate Point feature rather than a symbol layer reading the
 *  line, because MapLibre symbol layers need point (or polygon) geometry —
 *  labelling a LineString with symbol-placement:'point' silently renders
 *  nothing. Labelling every segment would overlap on a corridor that crosses
 *  the city, so only the longest gets a name. */
function roadFeatures(road) {
  const out = [];
  const segs = road.segments || [];
  let longest = -1;
  let mid = null;
  segs.forEach((s, i) => {
    out.push({
      type: 'Feature',
      properties: { role: 'line', name: s.class },
      geometry: { type: 'LineString', coordinates: s.coords },
    });
    if (s.coords.length > longest) {
      longest = s.coords.length;
      const m = s.coords[Math.floor(s.coords.length / 2)];
      mid = m;
    }
  });
  if (mid) {
    out.push({
      type: 'Feature',
      properties: { role: 'label', label: road.name },
      geometry: { type: 'Point', coordinates: mid },
    });
  }
  return { type: 'FeatureCollection', features: out };
}

function roadBounds(road) {
  let minx = 180, miny = 90, maxx = -180, maxy = -90;
  for (const s of road.segments || []) {
    for (const [x, y] of s.coords) {
      if (x < minx) minx = x;
      if (x > maxx) maxx = x;
      if (y < miny) miny = y;
      if (y > maxy) maxy = y;
    }
  }
  if (minx > maxx) return null;
  return [[minx, miny], [maxx, maxy]];
}

/** Find a ruas by `classKey::n`, with its class label. */
function findRoad(key) {
  for (const c of ROADS.classes) {
    for (const r of c.roads) {
      if (`${c.key}::${r.n}` === key) return { road: r, label: c.label };
    }
  }
  return { road: null, label: '' };
}

/** Draw the selected road and zoom to it. */
function highlightRoad(key) {
  selectedRoad = key;
  // SRC_ROAD, not 'road-sel': that is the layer id. getSource() on a layer name
  // returns undefined, so the guard below used to bail out on every click and
  // the selection silently did nothing.
  if (!map.getSource(SRC_ROAD)) return;
  let road = null;
  let label = '';
  if (key) {
    const hit = findRoad(key);
    road = hit.road;
    label = hit.label;
  }
  if (!road || !road.segments || !road.segments.length) {
    map.getSource(SRC_ROAD).setData({ type: 'FeatureCollection', features: [] });
    renderRoadCard(null);
    renderRoads();  // clears the .is-on row left behind by the X button
    return;
  }
  map.getSource(SRC_ROAD).setData(roadFeatures(road));
  const b = roadBounds(road);
  if (b) {
    map.fitBounds(b, {
      padding: { top: 120, bottom: 220, left: 80, right: 80 },
      maxZoom: 15.5,
      duration: 800,
    });
  }
  renderRoadCard(road, label);
  renderRoads();
}

function renderRoads() {
  if (!ROADS.classes.length) {
    roadGroups.innerHTML = `<p class="road-empty">Data jalan tidak tersedia.</p>`;
    return;
  }
  const unloc = ROADS.total - ROADS.located;
  roadNote.innerHTML =
    `Dari <b>${esc(ROADS.source)}</b> — ${ROADS.total} ruas. `
    + `Jalan lokal &amp; lingkungan tidak dimunculkan: ayat (8) dan (9) `
    + `mendelegasikannya ke Rencana Detail Tata Ruang yang belum dipublikasikan.`
    + (unloc ? ` ${unloc} ruas tidak ada di OSM, jadi tidak bisa ditampilkan di peta.` : '');

  let html = '';
  for (const c of ROADS.classes) {
    const key = `k${c.key}`;
    const open = roadOpen.has(c.key);
    html += `<div class="road-group${open ? ' is-open' : ''}" data-key="${esc(c.key)}">
      <button class="road-head" type="button" aria-expanded="${open}">
        <span class="road-chev">▶</span>
        <span class="road-title">${esc(c.label)}</span>
        <span class="road-basis">${esc(c.pasal)}</span>
        <span class="road-count">${c.count}</span>
      </button>
      <div class="road-body">`;
    for (const r of c.roads) {
      const k = `${c.key}::${r.n}`;
      const isOn = selectedRoad === k;
      const dead = !r.segments || !r.segments.length;
      html += `<button class="road-row${isOn ? ' is-on' : ''}${dead ? ' is-dead' : ''}" type="button"
        data-key="${esc(k)}" data-name="${esc(r.name)}"
        aria-pressed="${isOn}"${dead ? ' disabled title="Ruas ini tidak ada di OSM, jadi tidak bisa digambar di peta"' : ''}>
        <span class="rr-name">${esc(r.name)}</span>
        <span class="rr-badge">${esc(r.badge)}</span>
      </button>`;
    }
    html += `</div></div>`;
  }
  // Preserve scroll across the re-render: replacing innerHTML resets scrollTop
  // to 0, which threw the list back to the top on every selection change.
  // roadScroll is the scroller; roadGroups is only its content.
  const keepScroll = roadScroll.scrollTop;
  roadGroups.innerHTML = html;
  roadScroll.scrollTop = keepScroll;

  for (const head of roadGroups.querySelectorAll('.road-head')) {
    head.addEventListener('click', () => {
      const g = head.closest('.road-group');
      const key = g.dataset.key;
      const nowOpen = !g.classList.contains('is-open');
      g.classList.toggle('is-open', nowOpen);
      head.setAttribute('aria-expanded', String(nowOpen));
      if (nowOpen) roadOpen.add(key); else roadOpen.delete(key);
    });
  }
  for (const row of roadGroups.querySelectorAll('.road-row')) {
    row.addEventListener('click', () => {
      // A ruas with no geometry cannot be drawn. Ignore the click instead of
      // running highlightRoad(null), which would wipe out an unrelated road the
      // user already had selected.
      if (row.classList.contains('is-dead')) return;
      const k = row.dataset.key;
      // Clicking the active road clears it, so there is always a way back.
      highlightRoad(selectedRoad === k ? null : k);
      if (window.matchMedia('(max-width: 760px)').matches) setSidebar(false);
    });
  }
}

/* ---------- boot ---------- */
document.documentElement.dataset.theme = state.theme;
renderCard();
renderBack();
renderAreaList();
renderRoads();
sideEl.hidden = true;

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
  setSidebar, setTab, renderAreaList, renderRoads, ROADS,
};