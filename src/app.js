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
const KEL_BY_CODE = new Map(KEL.map(f => [f.properties.village_code, f, { key: 'school', panel: 'panel-school', tab: 'tab-school', note: null }]));
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
    // Transport highlight is a violet, outside both the green admin family and
    // the orange road highlight, so a selected route can't be mistaken for a
    // selected road or a selected kecamatan.
    trans: '#6d28d9',
    // Schools are teal: a fourth family, so a school pin can never be read as a
    // selected kecamatan (green), road (orange) or route (violet).
    school: '#0f766e',
  },
  dark: {
    sel: '#6fd39b', selFill: '#6fd39b', peer: '#3f7f5c', peerFill: '#38745a',
    line: '#4a5a4e', ink: '#d8e6dc', halo: '#10130e',
    road: '#fb923c',
    trans: '#a78bfa',
    school: '#2dd4bf',
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
const SRC_TRANS = 'rute';
/* Every intra-city trayek at once, for the "show all" toggle. A separate source
   from the selected route so the two can be shown together -- the overlay is
   the context, the selected route is drawn on top of it. */
const SRC_TRANS_ALL = 'rute-semua';
/* Secondary schools, in two sources for the same reason the transport routes
   are: the selected school's marker is the subject, the show-all layer is the
   context underneath it. */
const SRC_SCHOOL = 'sekolah-terpilih';
const SRC_SCHOOL_ALL = 'sekolah-semua';
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
  // The transport layers need the same treatment. Guarded per-layer because a
  // theme flip during the initial map load can run before addLayers() has
  // created all three, and setPaintProperty on a missing layer throws.
  for (const [id, prop, val] of [
    ['trans-sel', 'line-color', p.trans],
    ['trans-sel-halo', 'line-color', p.trans],
    ['trans-sel-label', 'text-halo-color', p.trans],
    ['trans-sel-label', 'text-color', p.halo],
    ['trans-all', 'line-color', p.trans],
    ['trans-all-halo', 'line-color', p.trans],
    ['trans-all-label', 'text-halo-color', p.trans],
    ['trans-all-label', 'text-color', p.halo],
    ['school-all', 'circle-color', p.school],
    ['school-all', 'circle-stroke-color', p.halo],
    ['school-sel', 'circle-color', p.school],
    ['school-sel', 'circle-stroke-color', p.halo],
    ['school-sel-label', 'text-color', p.school],
    ['school-sel-label', 'text-halo-color', p.halo],
  ]) {
    if (map.getLayer(id)) map.setPaintProperty(id, prop, val);
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
  // The selected public-transport route. Separate source from the road one for
  // the same reason: it must survive a district change and be clearable on its
  // own, and it is drawn in a colour no other layer uses.
  if (!map.getSource(SRC_TRANS)) {
    map.addSource(SRC_TRANS, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  }
  // The "show all intra-city trayek" overlay. Added before trans-sel so the
  // selected route paints on top of the network it belongs to.
  if (!map.getSource(SRC_TRANS_ALL)) {
    map.addSource(SRC_TRANS_ALL, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  }
  if (!map.getLayer('trans-all')) {
    map.addLayer({
      id: 'trans-all-halo', type: 'line', source: SRC_TRANS_ALL,
      filter: ['==', ['get', 'role'], 'line'],
      paint: {
        'line-color': PALETTE[state.theme].trans,
        'line-width': 8,
        'line-opacity': 0.16,
        'line-blur': 2,
      },
    });
    map.addLayer({
      id: 'trans-all', type: 'line', source: SRC_TRANS_ALL,
      filter: ['==', ['get', 'role'], 'line'],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': PALETTE[state.theme].trans,
        // Solid and thin, against the selected route's dashed and thick: the
        // overlay is context, not the subject.
        'line-width': 1.6,
        'line-opacity': 0.5,
      },
    });
    // Ref numbers on every trayek, so the network is readable without clicking
    // through all 14 of them.
    map.addLayer({
      id: 'trans-all-label', type: 'symbol', source: SRC_TRANS_ALL,
      filter: ['==', ['get', 'role'], 'label'],
      layout: {
        'text-field': ['get', 'label'],
        'text-size': 10.5,
        'text-font': ['Noto Sans Regular'],
        'text-anchor': 'center',
        'symbol-placement': 'point',
      },
      paint: {
        'text-color': PALETTE[state.theme].halo,
        'text-halo-color': PALETTE[state.theme].trans,
        'text-halo-width': 2,
        'text-halo-blur': 0.3,
      },
    });
  }
  // Secondary schools. Same two-source shape as the transport routes: a thin
  // context layer of every school, and the selected one on top of it.
  for (const id of [SRC_SCHOOL_ALL, SRC_SCHOOL]) {
    if (!map.getSource(id)) {
      map.addSource(id, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    }
  }
  if (!map.getLayer('school-all')) {
    map.addLayer({
      id: 'school-all', type: 'circle', source: SRC_SCHOOL_ALL,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'],
          11, 3, 13, 4.5, 16, 7],
        'circle-color': PALETTE[state.theme].school,
        'circle-opacity': 0.62,
        'circle-stroke-width': 1,
        'circle-stroke-color': PALETTE[state.theme].halo,
        'circle-stroke-opacity': 0.5,
      },
    });
  }
  if (!map.getLayer('school-sel')) {
    map.addLayer({
      id: 'school-sel', type: 'circle', source: SRC_SCHOOL,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'],
          11, 5, 13, 8, 16, 12],
        'circle-color': PALETTE[state.theme].school,
        'circle-stroke-width': 2.5,
        'circle-stroke-color': PALETTE[state.theme].halo,
      },
    });
    map.addLayer({
      id: 'school-sel-label', type: 'symbol', source: SRC_SCHOOL,
      layout: {
        'text-field': ['get', 'name'],
        'text-size': 12,
        'text-font': ['Noto Sans Regular'],
        'text-anchor': 'top',
        'text-offset': [0, 1.1],
        'text-max-width': 14,
        'text-allow-overlap': false,
      },
      paint: {
        'text-color': PALETTE[state.theme].school,
        'text-halo-color': PALETTE[state.theme].halo,
        'text-halo-width': 2,
      },
    });
  }
  if (!map.getLayer('trans-sel')) {
    map.addLayer({
      id: 'trans-sel-halo', type: 'line', source: SRC_TRANS,
      filter: ['==', ['get', 'role'], 'line'],
      paint: {
        'line-color': PALETTE[state.theme].trans,
        'line-width': 14,
        'line-opacity': 0.24,
        'line-blur': 4,
      },
    });
    map.addLayer({
      id: 'trans-sel', type: 'line', source: SRC_TRANS,
      filter: ['==', ['get', 'role'], 'line'],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': PALETTE[state.theme].trans,
        // A transit route is a recurring service, so it reads as a dashed line
        // rather than the solid road highlight.
        'line-dasharray': [2, 1.6],
        'line-width': 3.6,
        'line-opacity': 0.95,
      },
    });
    if (!map.getLayer('trans-sel-label')) {
      map.addLayer({
        id: 'trans-sel-label', type: 'symbol', source: SRC_TRANS,
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
          'text-halo-color': PALETTE[state.theme].trans,
          'text-halo-width': 2.6,
          'text-halo-blur': 0.4,
        },
      });
    }
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
  renderSchoolCard(null);  // area selection supersedes a school
  renderRouteCard(null);   // area selection supersedes a route
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
  renderSchoolCard(null);  // area selection supersedes a school
  renderRouteCard(null);   // area selection supersedes a route
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
  renderSchoolCard(null);  // area selection supersedes a school
  renderRouteCard(null);   // area selection supersedes a route
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
    const expanded = !document.body.classList.contains('card-collapsed');
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
  // Both selection banners carry their colour as an inline custom property set
  // at selection time, so a theme flip leaves them in the old theme's colour.
  // Recolor the property directly rather than re-rendering: renderRoadCard and
  // the transport equivalent both need the road/route lookup maps, which are
  // declared further down and would be in the TDZ here.
  if (!roadCard.hidden) roadCard.style.setProperty('--road-c', PALETTE[next].road);
  if (!transportCard.hidden) transportCard.style.setProperty('--road-c', PALETTE[next].trans);
  // Optional: absent meta must not abort the rest of the theme swap. It is last
  // precisely because it is the one line that can throw on a page that omits the
  // tag, and everything above it (including the banner recolour) is what matters.
  const meta = document.querySelector('meta[name=theme-color]');
  if (meta) {
    meta.content =
      getComputedStyle(document.documentElement).getPropertyValue('--theme-color').trim();
  }
  // Dark mode re-wraps the licence line to a different number of lines, so the
  // card's offset has to be re-derived once the new theme has painted.
  requestAnimationFrame(relayoutOverlays);
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
    // Same problem, same fix, for the two transport sources: setStyle() drops
    // both back to empty, so without this a selected route disappears on a
    // theme toggle and the show-all network silently switches itself off.
    if (selectedRoute) {
      const r = TRANSPORT.routes.find(x => x.key === selectedRoute);
      if (r && r.segments && r.segments.length) {
        map.getSource(SRC_TRANS).setData(transportFeatures(r));
      }
    }
    if (selectedSchool) {
      const sc = SCHOOLS.schools.find(x => x.key === selectedSchool);
      if (sc) {
        map.getSource(SRC_SCHOOL).setData(
          { type: 'FeatureCollection', features: [schoolFeature(sc)] });
      }
    }
    if (showAllSchools) {
      const ss = map.getSource(SRC_SCHOOL_ALL);
      if (ss) {
        ss.setData({
          type: 'FeatureCollection',
          features: SCHOOLS.schools.map(schoolFeature),
        });
      }
    }
    if (showAllAngkot) {
      const src = map.getSource(SRC_TRANS_ALL);
      if (src) {
        const feats = [];
        for (const r of kotaAngkotWithGeometry()) feats.push(...transportFeatures(r).features);
        src.setData({ type: 'FeatureCollection', features: feats });
      }
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
  const on = !document.body.classList.toggle('card-collapsed');
  btn.setAttribute('aria-label', on ? 'Perkecil info' : 'Perluas info');
  btn.title = on ? 'Perkecil info' : 'Perluas info';
  // The card changes height, so the road banner above it must re-measure.
  positionRoadCard();
  // Its height also used to drag the map's container down with it. syncMapSize()
  // is a no-op when the container is already viewport-sized.
  requestAnimationFrame(syncMapSize);
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

/* Transport DOM handles live here rather than in the transport section,
   because positionRoadCard() (above) needs the card element to stack whichever
   selection banner is showing. Declaring it further down would put it in the
   temporal dead zone at that point. */
const transportCard = document.getElementById('transport-card');
const transportCardRef = document.getElementById('transport-card-ref');
const transportCardName = document.getElementById('transport-card-name');
const transportNote = document.getElementById('transport-note');
const transportGroups = document.getElementById('transport-groups');

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
  // Positions whichever selection banner is showing. It used to return early
  // when roadCard was hidden, which was correct with one banner but not with
  // two: selecting a transport route left this a no-op and the banner rendered
  // at its unpositioned default, overlapping the answer card.
  const banners = [roadCard, transportCard].filter(b => b && !b.hidden);
  if (!banners.length) return;
  const cardEl = document.getElementById('card');
  const gap = 12;
  let offset = 0;
  if (cardEl && !cardEl.hidden) {
    offset = Math.max(0, window.innerHeight - cardEl.getBoundingClientRect().top) + gap;
  }
  for (const b of banners) b.style.setProperty('--card-h', `${offset}px`);
}

/** Publish the attribution's real height so the card can clear it.
 *
 *  The licence line wraps to two or three lines on a phone, so its height is a
 *  function of viewport width and the theme (dark mode re-wraps differently).
 *  A hardcoded bottom offset therefore either overlaps the text or leaves a
 *  gap. Measure it and expose it as --attrib-h; the CSS falls back to a
 *  constant for the first paint before this runs.
 */
function syncAttribInset() {
  const attrib = document.getElementById('attrib');
  if (!attrib) return;
  const h = Math.round(attrib.getBoundingClientRect().height);
  if (h > 0) document.documentElement.style.setProperty('--attrib-h', `${h}px`);
}

/**
 * Keep the map canvas the same size as its container, and the container the same
 * size as the viewport.
 *
 *  The container is now `position: fixed`, so CSS alone should hold it open. But
 *  MapLibre also runs its own resize tracking against a *detached* measurement
 *  in some engines, and a mobile browser's dynamic toolbar changes the visual
 *  viewport without firing `resize`. Either way the failure is identical and
 *  very visible: the canvas re-rasterises at a fraction of the real size and the
 *  map appears to vanish. Collapsing the answer card used to trigger exactly
 *  this, because every overlay is out of flow, so body's box -- and with it the
 *  absolutely-positioned container -- shrank with the card.
 *
 *  So: compare the container against the viewport, and correct it when it
 *  drifts. Cheap, idempotent, and it cannot fight a correct layout because it
 *  only writes when the size is actually wrong.
 */
function syncMapSize() {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const el = map.getContainer();
  const r = el.getBoundingClientRect();
  if (r.width === vw && r.height === vh) return;
  el.style.width = `${vw}px`;
  el.style.height = `${vh}px`;
  map.resize();
}

function setSidebar(open) {
  sideEl.hidden = !open;
  document.body.classList.toggle('side-open', open);
  if (open) {
    syncMapSize();
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
/* Re-measure the attribution first: its height feeds the card's bottom offset,
   and the road banner then stacks on whatever the card ended up doing. Order
   matters -- the banner reads the card's new top edge. */
function relayoutOverlays() {
  syncAttribInset();
  // The container is viewport-sized, so a rotate or a browser-UI collapse
  // leaves it at the old size until something corrects it. Without this the
  // canvas stays rasterised for the previous viewport.
  syncMapSize();
  positionRoadCard();
}
window.addEventListener('resize', relayoutOverlays);
window.addEventListener('orientationchange', relayoutOverlays);

/* Catch a container that drifts for any reason we did not anticipate: a mobile
   toolbar collapsing, a browser-UI resize that skips the `resize` event, or a
   future layout change that again lets a sibling influence the map's box. The
   observer fires on the element itself, so it covers cases the window-level
   listeners above never hear about. */
if (typeof ResizeObserver === 'function') {
  let pending = false;
  new ResizeObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; syncMapSize(); });
  }).observe(map.getContainer());
}

sideToggle.addEventListener('click', () => setSidebar(sideEl.hidden));
sideClose.addEventListener('click', () => setSidebar(false));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !sideEl.hidden) setSidebar(false);
});

/* Three tabs now, so this is driven by a name rather than a boolean. The earlier
 * two-tab version was `const road = which === 'road'` and toggled both tabs off
 * that one flag, which cannot express a third panel: 'area' and 'transport'
 * would both fall through to the same branch. */
const TABS = [
  { id: 'area', tab: 'tab-area', panel: 'panel-area' },
  { id: 'road', tab: 'tab-road', panel: 'panel-road' },
  { id: 'transport', tab: 'tab-transport', panel: 'panel-transport' },
  { id: 'school', tab: 'tab-school', panel: 'panel-school' },
];

function setTab(which) {
  let active = null;
  for (const t of TABS) {
    const on = t.id === which;
    const tabEl = document.getElementById(t.tab);
    const panelEl = document.getElementById(t.panel);
    if (tabEl) {
      tabEl.classList.toggle('is-on', on);
      tabEl.setAttribute('aria-selected', String(on));
      if (on) active = tabEl;
    }
    if (panelEl) panelEl.hidden = !on;
  }
  // Four tabs do not fit beside the theme and close buttons on a phone, so the
  // strip scrolls. Bring the chosen tab into view, otherwise the last one can be
  // active while sitting off-screen.
  if (active && active.scrollIntoView) {
    active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
}
for (const t of TABS) {
  const el = document.getElementById(t.tab);
  if (el) el.addEventListener('click', () => setTab(t.id));
}

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

/* ---------- transport ---------- */
const TRANSPORT = window.DEPOK_TRANSPORT || { routes: [] };

/** Which route is selected, by `key`. */
let selectedRoute = null;
/** Which transport groups are expanded. 'kota' starts open because the whole
 *  point of this layer is the intra-city network. */
const transportOpen = new Set(['kota', 'bus-kota', 'rail']);


/** Line+label GeoJSON for one route.
 *
 *  Same shape as the roads one, for the same reason: MapLibre symbol layers
 *  cannot label a LineString, so the name goes on a Point at the midpoint of
 *  the longest segment. */
function transportFeatures(route) {
  const out = [];
  const segs = route.segments || [];
  let longest = -1;
  let mid = null;
  for (const s of segs) {
    if (!s || s.length < 2) continue;
    out.push({
      type: 'Feature',
      properties: { role: 'line' },
      geometry: { type: 'LineString', coordinates: s },
    });
    if (s.length > longest) {
      longest = s.length;
      mid = s[Math.floor(s.length / 2)];
    }
  }
  if (mid) {
    out.push({
      type: 'Feature',
      properties: { role: 'label', label: route.ref || route.name },
      geometry: { type: 'Point', coordinates: mid },
    });
  }
  return { type: 'FeatureCollection', features: out };
}

function transportBounds(route) {
  let minx = 180, miny = 90, maxx = -180, maxy = -90;
  for (const s of route.segments || []) {
    for (const [x, y] of s) {
      if (x < minx) minx = x;
      if (x > maxx) maxx = x;
      if (y < miny) miny = y;
      if (y > maxy) maxy = y;
    }
  }
  return minx <= maxx ? [[minx, miny], [maxx, maxy]] : null;
}

/** Draw the selected route, zoom to it, and show its detail card. */
function highlightRoute(key) {
  selectedRoute = key;
  if (!map.getSource(SRC_TRANS)) return;
  const route = TRANSPORT.routes.find(r => r.key === key);
  if (!route || !route.segments || !route.segments.length) {
    map.getSource(SRC_TRANS).setData({ type: 'FeatureCollection', features: [] });
    transportCard.hidden = true;
    renderRouteCard(null);
    // Hand the screen back to the area card, which is where the user was
    // before they picked a route.
    card.hidden = false;
    renderCard();
    relayoutOverlays();
    renderTransport();
    return;
  }
  map.getSource(SRC_TRANS).setData(transportFeatures(route));
  const b = transportBounds(route);
  if (b) {
    map.fitBounds(b, {
      padding: { top: 120, bottom: 220, left: 80, right: 80 },
      maxZoom: 15.5,
      duration: 800,
    });
  }
  // The detail card supersedes the small banner: it carries the same ref and
  // name plus the endpoints, fare and geometry confidence. Keep the banner as
  // the fallback for a route with no geometry, which cannot open the card.
  transportCardRef.textContent = route.ref || route.network || 'Rute';
  transportCardName.textContent = routeName(route);
  transportCard.style.setProperty('--road-c', PALETTE[state.theme].trans);
  transportCard.hidden = true;
  renderRouteCard(route);
  // The area card and the route card are both .card and would stack on top of
  // each other, so the area one steps aside for as long as a route is chosen.
  card.hidden = true;
  legend.hidden = true;
  positionRoadCard();
  relayoutOverlays();
  renderTransport();
}

function routeName(r) {
  const a = r.from, b = r.to;
  if (a && b) return `${a} – ${b}`;
  return r.name;
}

/* ---------- route detail card ---------- */
/* The area card shows a place's hierarchy (kelurahan -> kecamatan ->
   kabupaten -> provinsi). A route has the same shape of thing: where it starts,
   what it passes, where it ends. Reusing .card wholesale means it inherits the
   minimise button, the collapsed-body rule and the mobile layout for free. */
const tdEyebrow = document.getElementById('td-eyebrow');
const tdName = document.getElementById('td-name');
const tdLadder = document.getElementById('td-ladder');
const tdFacts = document.getElementById('td-facts');
const tdSource = document.getElementById('td-source');
const tdMin = document.getElementById('td-min');
const tdDetail = document.getElementById('trans-detail');
const sdDetail = document.getElementById('school-detail');
const sdEyebrow = document.getElementById('sd-eyebrow');
const sdName = document.getElementById('sd-name');
const sdLadder = document.getElementById('sd-ladder');
const sdFacts = document.getElementById('sd-facts');
const sdSource = document.getElementById('sd-source');
const sdMin = document.getElementById('sd-min');

function renderRouteCard(route) {
  if (!route) { tdDetail.hidden = true; return; }
  const segs = route.segments || [];
  tdDetail.hidden = false;

  tdEyebrow.textContent = route.scope === 'kota' ? 'Trayek dalam kota' : 'Rute transportasi';
  tdName.textContent = routeName(route);

  // Ladder: Dari / [via] / Ke, matching the area card's rung layout.
  tdLadder.innerHTML = '';
  const rungs = [];
  if (route.from) rungs.push(['Dari', route.from, true]);
  if (route.via) rungs.push(['Via', route.via, false]);
  if (route.to) rungs.push(['Ke', route.to, true]);
  for (const [lvl, val, isLeg] of rungs) {
    const row = document.createElement('div');
    row.className = 'rung';
    const dot = document.createElement('i');
    dot.className = 'dot';
    const l = document.createElement('span');
    l.className = 'lvl'; l.textContent = lvl;
    const v = document.createElement('span');
    v.className = isLeg ? 'nm leg' : 'nm';
    v.textContent = val;
    row.append(dot, l, v);
    tdLadder.append(row);
  }

  // Facts. Every field is optional; the angkot trayek have fare + estimated
  // geometry, the OSM relations have neither.
  const facts = [];
  if (route.fare) facts.push(['Tarif', esc(route.fare)]);
  if (route.busiest) facts.push(['Puncak', esc(route.busiest)]);
  if (route.network) facts.push(['Operator', esc(route.network)]);
  if (!segs.length) {
    facts.push(['Jalur', '<span class="trans-warn">Belum ada jalur digambar</span>']);
  } else if (route.geometry === 'estimated') {
    facts.push(['Jalur', '<span class="trans-warn">Estimasi</span> — bukan hasil survei']);
  } else {
    facts.push(['Jalur', 'Traced dari OpenStreetMap']);
  }
  if (route.status === 'unverified') {
    facts.push(['Status', '<span class="trans-warn">Belum diverifikasi untuk 2026</span>']);
  }
  if (route.checked) facts.push(['Data', esc(route.checked)]);

  tdFacts.className = 'kids trans-facts';
  tdFacts.innerHTML = facts
    .map(([k, v]) => `<div class="trans-fact"><dt>${k}</dt><dd>${v}</dd></div>`)
    .join('');

  tdSource.textContent = route.source || '';
  // The minimise button is rebuilt on every render, so re-apply the label from
  // the body state or it comes back saying "Perkecil" while collapsed.
  const expanded = !document.body.classList.contains('card-collapsed');
  tdMin.setAttribute('aria-label', expanded ? 'Perkecil info' : 'Perluas info');
  tdMin.title = expanded ? 'Perkecil info' : 'Perluas info';
}

tdMin.addEventListener('click', () => {
  document.body.classList.toggle('card-collapsed');
  relayoutOverlays();
});

/** The groups, in the order a rider would look for them: your own city first,
 *  then rail, then everything that leaves the city. */
function transportGroupsOf() {
  const routes = TRANSPORT.routes || [];
  const kota = routes.filter(r => r.scope === 'kota');
  return [
    { key: 'kota', label: 'Dalam kota', hint: 'Bus kota & angkot',
      items: kota },
    { key: 'bus-kota', label: 'Bus kota (terverifikasi)',
      hint: 'K1 / D10A',
      items: kota.filter(r => r.klass === 'bus-kota' && r.geometry === 'osm') },
    { key: 'rail', label: 'Kereta & LRT',
      hint: 'KRL Commuter · LRT Jabodeks',
      items: routes.filter(r => r.klass === 'krl' || r.klass === 'lrt') },
    { key: 'lintas', label: 'Lintas kota',
      hint: 'Feeder ke Jakarta/Bogor',
      items: routes.filter(r => r.scope !== 'kota' && r.klass === 'bus-lintas') },
  ].filter(g => g.items.length);
}

function renderTransport() {
  if (!TRANSPORT.routes || !TRANSPORT.routes.length) {
    transportNote.innerHTML = 'Data transportasi belum tersedia.';
    transportGroups.innerHTML = '';
    return;
  }
  const kotaRoutes = TRANSPORT.routes.filter(r => r.scope === 'kota');
  const kota = kotaRoutes.length;
  const unver = TRANSPORT.routes.filter(r => r.status === 'unverified').length;
  // Say how many of the intra-city routes can actually be drawn, so the "show
  // all" toggle does not imply a completeness the data does not have.
  const drawable = kotaRoutes.filter(r => r.segments && r.segments.length).length;
  transportNote.innerHTML =
    `Dari <b>OpenStreetMap</b> — ${TRANSPORT.routes.length} rute yang melintasi `
    + `Kota Depok, disaring batas kota. <b>${kota}</b> di dalam kota. `
    + (unver
      ? `${unver} rute berstatus <i>belum diverifikasi</i> untuk 2026: `
        + `OSM mencatat jalurnya, bukan apakah layanannya masih jalan.`
      : '')
    + (drawable < kota
      ? ` <b>${drawable}</b> dari ${kota} rute dalam kota punya jalur; sisanya `
        + `tercantum tapi belum bisa digambar.`
      : '')
    + ` Jalur angkot <i>diestimasi</i> dari jaringan jalan OSM, bukan hasil survei.`;

  let html = '';
  for (const g of transportGroupsOf()) {
    const open = transportOpen.has(g.key);
    html += `<div class="road-group${open ? ' is-open' : ''}" data-key="${esc(g.key)}">
      <button class="road-head" type="button" aria-expanded="${open}">
        <span class="road-chev">▶</span>
        <span class="road-title">${esc(g.label)}</span>
        <span class="road-basis">${esc(g.hint)}</span>
        <span class="road-count">${g.items.length}</span>
      </button>
      <div class="road-body">`;
    for (const r of g.items) {
      const isOn = selectedRoute === r.key;
      const noGeom = !r.segments || !r.segments.length;
      const unver = r.status === 'unverified';
      html += `<button class="road-row${isOn ? ' is-on' : ''}${noGeom ? ' is-dead' : ''}"
        type="button" data-key="${esc(r.key)}"${noGeom ? ' disabled' : ''}
        title="${esc(r.source || '')}">
        ${r.ref ? `<span class="rr-ref">${esc(r.ref)}</span>` : ''}
        <span class="rr-name">${esc(routeName(r))}</span>
        ${unver ? '<span class="rr-badge">belum verifikasi</span>' : ''}
        ${noGeom ? '<span class="rr-badge">tanpa jalur</span>' : ''}
        ${r.geometry === 'estimated' ? '<span class="rr-badge">estimasi</span>' : ''}
        ${r.fare ? `<span class="rr-fare">${esc(r.fare)}</span>` : ''}
      </button>`;
    }
    html += `</div></div>`;
  }
  if ((TRANSPORT.inactive_trayek || []).length) {
    html += `<details class="trans-dead">
      <summary>Trayek berhenti beroperasi (${TRANSPORT.inactive_trayek.length})</summary>
      <ul>` + TRANSPORT.inactive_trayek
        .map(t => `<li><b>${esc(t.ref)}</b> ${esc(t.name)}</li>`).join('') + `</ul>
    </details>`;
  }
  transportGroups.innerHTML = html;
}

/* Show every intra-city trayek at once.
 *
 *  Only routes with geometry can be drawn, so the toggle reports how many of
 *  the 14 it managed to include -- four (D05, D07, D10, D11) have endpoints
 *  that resolve to no OSM feature at all and are metadata-only. Saying
 *  "14 trayek" while drawing 10 would overstate what the map shows. */
const transAllBox = document.getElementById('trans-all');
let showAllAngkot = false;

function kotaAngkotWithGeometry() {
  return (TRANSPORT.routes || []).filter(
    r => r.scope === 'kota' && r.segments && r.segments.length);
}

function toggleAllAngkot(on) {
  showAllAngkot = !!on;
  if (!map.getSource(SRC_TRANS_ALL)) return;
  const src = map.getSource(SRC_TRANS_ALL);
  if (!showAllAngkot) {
    src.setData({ type: 'FeatureCollection', features: [] });
    return;
  }
  const routes = kotaAngkotWithGeometry();
  // One GeoJSON, many routes: reuse transportFeatures per route and merge, so
  // each keeps its own ref label.
  const features = [];
  for (const r of routes) features.push(...transportFeatures(r).features);
  src.setData({ type: 'FeatureCollection', features });
  // Frame the network the first time it is switched on, so it does not land
  // off-screen when the map happens to be zoomed into a street corner.
  let minx = 180, miny = 90, maxx = -180, maxy = -90;
  for (const f of features) {
    if (f.geometry.type !== 'LineString') continue;
    for (const [x, y] of f.geometry.coordinates) {
      if (x < minx) minx = x;
      if (x > maxx) maxx = x;
      if (y < miny) miny = y;
      if (y > maxy) maxy = y;
    }
  }
  if (minx <= maxx) {
    map.fitBounds([[minx, miny], [maxx, maxy]], {
      padding: { top: 110, bottom: 220, left: 70, right: 70 },
      maxZoom: 13.5,
      duration: 700,
    });
  }
}

/* ---------- secondary schools (SMA/SMK/MA) ---------- */
/* Same three pieces as the transport layer: a list, a selected marker with a
   detail card, and a show-all overlay. Kept separate rather than merged into
   one generic "points" feature because the two have different provenance --
   schools come from OSM places, routes come from OSM relations plus Dishub. */

const SCHOOLS = window.DEPOK_SCHOOLS || { schools: [], counts: {} };
const schoolAllBox = document.getElementById('school-all');
const schoolNote = document.getElementById('school-note');
const schoolGroups = document.getElementById('school-groups');
let selectedSchool = null;
let showAllSchools = false;

const SCHOOL_ORDER = ['SMA Negeri', 'SMA Swasta', 'SMK Negeri', 'SMK Swasta', 'Madrasah Aliyah'];

function schoolFeature(s) {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
    properties: {
      name: s.name, label: s.label, key: s.key,
      website: s.website || '', phone: s.phone || '',
    },
  };
}

/** Which village and district a school sits in, by point-in-polygon.
 *
 *  Schools are points, so unlike a route corridor they really do belong to one
 *  village -- this is exact, not an approximation. */
function placeForSchool(lon, lat) {
  for (const f of KEL) {
    const g = f.geometry;
    const polys = g.type === 'Polygon' ? [g.coordinates]
      : g.type === 'MultiPolygon' ? g.coordinates : [];
    for (const poly of polys) for (const ring of poly) {
      let inside = false;
      for (let i = 0, n = ring.length - 1; i < n; i++) {
        const [x1, y1] = ring[i], [x2, y2] = ring[i + 1];
        if ((y1 > lat) !== (y2 > lat) &&
            lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1) inside = !inside;
      }
      if (inside) return f.properties;
    }
  }
  return null;
}

function renderSchoolCard(s) {
  if (!s) { sdDetail.hidden = true; return; }
  sdDetail.hidden = false;
  sdEyebrow.textContent = s.label;
  sdName.textContent = s.name;

  // Ladder: village -> district -> city, the same hierarchy the area card shows.
  const place = placeForSchool(s.lon, s.lat);
  sdLadder.innerHTML = '';
  if (place) {
    for (const [lvl, val] of [['Kelurahan', place.village], ['Kecamatan', place.district]]) {
      if (!val) continue;
      const row = document.createElement('div');
      row.className = 'rung';
      row.innerHTML = '<i class="dot"></i><span class="lvl"></span><span class="nm leg"></span>';
      row.querySelector('.lvl').textContent = lvl;
      row.querySelector('.nm').textContent = val;
      sdLadder.append(row);
    }
  }
  // No village matched: every kept school is inside the city clip, so this only
  // happens on a boundary sliver. Say so rather than showing an empty ladder.
  if (!sdLadder.children.length) {
    sdLadder.innerHTML = '<div class="rung"><i class="dot"></i>'
      + '<span class="lvl">Wilayah</span><span class="nm leg">Batas Kota Depok</span></div>';
  }

  // At least three facts, so the card never reads as a title with nothing in
  // it. Every field is optional, so fall back through progressively more
  // specific and then to the least specific, rather than dropping the row.
  const facts = [];
  if (s.address) {
    facts.push(['Alamat', esc(s.address)]);
  } else if (s.street) {
    // Most of these 30 have no street tag at all, so the village the school
    // sits in is the honest address fallback -- and it is already computed for
    // the ladder, so it costs nothing.
    const where = placeForSchool(s.lon, s.lat);
    facts.push(['Alamat', esc(s.street + (where ? `, ${where.village}` : ''))]);
  } else {
    const where = placeForSchool(s.lon, s.lat);
    if (where) facts.push(['Alamat', esc(`${where.village}, ${where.district}`)]);
  }
  if (s.grades) facts.push(['Kelas', esc(s.grades).replace('-', '–')]);
  if (s.website) facts.push(['Web', esc(s.website)]);
  if (s.phone) facts.push(['Telp', esc(s.phone)]);
  if (s.operator) facts.push(['Pengelola', esc(s.operator)]);
  facts.push(['Koordinat', `${s.lat.toFixed(5)}, ${s.lon.toFixed(5)}`]);
  // Provenance last: this is what the card is resting on, and it is also the
  // least useful thing for someone choosing a school.
  facts.push(['Data', `OpenStreetMap <span class="code">${s.osm_type}/${s.osm_id}</span>`]);
  sdFacts.className = 'kids trans-facts';
  sdFacts.innerHTML = facts
    .map(([k, v]) => `<div class="trans-fact"><dt>${k}</dt><dd>${v}</dd></div>`).join('');

  sdSource.textContent = SCHOOLS.source || '';
  const expanded = !document.body.classList.contains('card-collapsed');
  sdMin.setAttribute('aria-label', expanded ? 'Perkecil info' : 'Perluas info');
  sdMin.title = expanded ? 'Perkecil info' : 'Perluas info';
}

sdMin.addEventListener('click', () => {
  document.body.classList.toggle('card-collapsed');
  relayoutOverlays();
});

/** Draw the selected school, zoom to it, and show its card. */
function highlightSchool(key) {
  selectedSchool = key;
  if (!map.getSource(SRC_SCHOOL)) return;
  const s = SCHOOLS.schools.find(x => x.key === key);
  const src = map.getSource(SRC_SCHOOL);
  if (!s) {
    src.setData({ type: 'FeatureCollection', features: [] });
    renderSchoolCard(null);
    // Hand the screen back to the area card, the same way deselecting a route
    // does. Leaving it hidden would strand the user with nothing in the slot.
    card.hidden = false;
    renderCard();
    renderSchools();
    relayoutOverlays();
    return;
  }
  src.setData({ type: 'FeatureCollection', features: [schoolFeature(s)] });
  map.flyTo({ center: [s.lon, s.lat], zoom: Math.max(map.getZoom(), 15), duration: 700 });
  // Same stand-aside as a route: two .cards in one slot overlap.
  card.hidden = true;
  renderRouteCard(null);
  renderSchoolCard(s);
  renderSchools();
  relayoutOverlays();
}

/** Show every school at once. Points, so this is cheap -- no fitBounds guard
 *  beyond the obvious one, unlike the route overlay. */
function toggleAllSchools(on) {
  showAllSchools = !!on;
  if (!map.getSource(SRC_SCHOOL_ALL)) return;
  const src = map.getSource(SRC_SCHOOL_ALL);
  if (!showAllSchools) {
    src.setData({ type: 'FeatureCollection', features: [] });
    return;
  }
  const features = SCHOOLS.schools.map(schoolFeature);
  src.setData({ type: 'FeatureCollection', features });
  const lons = SCHOOLS.schools.map(s => s.lon), lats = SCHOOLS.schools.map(s => s.lat);
  if (lons.length) {
    map.fitBounds([[Math.min(...lons), Math.min(...lats)],
      [Math.max(...lons), Math.max(...lats)]], {
      padding: { top: 110, bottom: 220, left: 70, right: 70 },
      maxZoom: 13, duration: 700,
    });
  }
}
schoolAllBox.addEventListener('change', () => toggleAllSchools(schoolAllBox.checked));

function renderSchools() {
  if (!schoolGroups) return;
  const byLabel = new Map();
  for (const s of SCHOOLS.schools) {
    if (!byLabel.has(s.label)) byLabel.set(s.label, []);
    byLabel.get(s.label).push(s);
  }
  const labels = SCHOOL_ORDER.filter(l => byLabel.has(l));
  // Any level the payload gained that ORDER does not know about still gets
  // listed, rather than silently vanishing from the sidebar.
  for (const l of byLabel.keys()) if (!labels.includes(l)) labels.push(l);

  schoolGroups.innerHTML = labels.map(l => {
    const list = byLabel.get(l).sort((a, b) => a.name.localeCompare(b.name, 'id'));
    return `<div class="tg" data-open="1">
      <button class="tg-head" type="button" aria-expanded="true">
        <svg class="tg-chev" width="11" height="11" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="3" stroke-linecap="round"
             stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
        <span class="tg-t">${esc(l)}</span><span class="tg-n">${list.length}</span>
      </button>
      <div class="tg-body">${list.map(s => `
        <button class="road-row${s.key === selectedSchool ? ' is-sel' : ''}" type="button"
                data-skey="${s.key}">
          <span class="rr-badge">${esc(s.label.replace(/ (Negeri|Swasta)$/, ''))}</span>
          <span class="rr-name">${esc(s.name)}</span>
        </button>`).join('')}
      </div></div>`;
  }).join('');

  // Counts and provenance. The note says plainly how the list was built and
  // what it leaves out, because a name-matched list is not an official register
  // and someone using it to pick a school needs to know that.
  const n = SCHOOLS.schools.length;
  schoolNote.innerHTML = `<b>${n}</b> sekolah menengah atas &amp; kejuruan di Kota Depok. `
    + `Tingkat dibaca dari nama di OpenStreetMap, bukan dari daftar resmi, `
    + `jadi sekolah yang namanya tidak menyebut tingkatnya tidak ikut di sini.`;
}

if (schoolGroups) {
  schoolGroups.addEventListener('click', e => {
    const head = e.target.closest('.tg-head');
    if (head) {
      const g = head.closest('.tg');
      g.dataset.open = g.dataset.open === '1' ? '0' : '1';
      return;
    }
    const row = e.target.closest('[data-skey]');
    if (!row) return;
    const key = row.dataset.skey;
    if (key === selectedSchool) {
      highlightSchool(null);          // tapping the selection clears it
    } else {
      highlightSchool(key);
    }
    if (window.matchMedia('(max-width: 760px)').matches) setSidebar(false);
  });
}

/* ---------- secondary schools ---------- */
transAllBox.addEventListener('change', () => toggleAllAngkot(transAllBox.checked));

/* Transport list interaction. Separate handlers from the road ones because the
   two lists share class names but not behaviour, and a combined query would
   make each of them guard against the other's rows. Placed after renderTransport
   so transportGroups is already initialised — attaching a listener to a const
   before its declaration throws on the TDZ. */
transportGroups.addEventListener('click', e => {
  const head = e.target.closest('.road-head');
  if (head) {
    const g = head.closest('.road-group');
    const key = g.dataset.key;
    if (transportOpen.has(key)) transportOpen.delete(key);
    else transportOpen.add(key);
    renderTransport();
    return;
  }
  const row = e.target.closest('.road-row');
  if (row && !row.disabled) {
    const k = row.dataset.key;
    highlightRoute(selectedRoute === k ? null : k);
    if (window.matchMedia('(max-width: 760px)').matches) setSidebar(false);
  }
});
document.getElementById('transport-card-x')
  .addEventListener('click', () => highlightRoute(null));

/* ---------- boot ---------- */
document.documentElement.dataset.theme = state.theme;
renderCard();
renderBack();
renderAreaList();
renderRoads();
renderTransport();
renderSchools();
sideEl.hidden = true;
/* Measure the attribution before the first overlay placement, so the card does
   not paint on top of the licence line and then jump. */
relayoutOverlays();
syncMapSize();

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