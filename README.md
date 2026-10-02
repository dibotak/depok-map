# depok-map

An interactive map of **Kota Depok** — drill from the city down through its 11
kecamatan to the 63 kelurahan inside them. Search a place name or click the map
to get the full administrative hierarchy and BPS code.

## Run it

No build step and no dependencies to install. Any static server works:

```bash
python3 -m http.server 4400
# http://localhost:4400
```

A server is required — the app loads its geometry via `<script src>`, which
`file://` blocks.

## Deploying to Cloudflare Pages

Connect the repo in the Pages dashboard with:

| Setting | Value |
|---|---|
| Framework preset | None |
| Build command | `rm -rf public scripts` |
| Build output directory | `/` |

The build command prunes the two things the site never loads: `public/data/`
(1.1 MB of source JSON for the build scripts) and `scripts/`. Neither is
referenced by `index.html` or `app.js` — `src/data.js` is the only data the
browser actually fetches, so pruning them is safe and leaves **~2.1 MB** to
publish.

Cloudflare Pages has no ignore file for Git-based deploys, so pruning has to
happen in the build command. `rm -rf` is safe here because the whole repo is
re-checked-out on every deploy — nothing is lost that git can't restore.

`scripts/` is pruned, so `build-roads.py` and its Overpass dependency never
touch the Pages build — `src/roads.js` is committed, exactly like `src/data.js`.

A subdomain or apex domain both work. The asset paths are root-absolute
(`/src/app.js`), so mounting this under a *subpath* like `example.com/depok-map/`
would 404 every asset — use `map.dibotak.com`, not a subpath.

## What it does

- **Sidebar with two tabs.** *Wilayah* lists all 11 kecamatan with their child
  counts; opening one appends its kelurahan underneath, and the current
  selection stays highlighted as you click through the map. *Jalan* lists 350
  named roads grouped by class, collapsible. Collapsible panel, `Esc` to close.
- **Drill down one level at a time.** City view shows the 11 kecamatan
  boundaries, each labelled, and no kelurahan at all. Click one to zoom in: that
  kecamatan's kelurahan appear and are labelled, while the other 10 stay plain
  polygons — so you keep the city's shape around you without 55 irrelevant
  hairlines. Click a kelurahan to select it, or click one of the other
  kecamatan to jump straight to it. The card lists the open kecamatan's
  kelurahan as chips, which is the reliable way to pick one on a phone.
- **Back walks up one level.** From a kelurahan it reads `Ke Limo` and returns to
  Limo with its children still drawn; from a kecamatan it reads `Semua
  kecamatan` and returns to the city view.
- **Search** any kecamatan or kelurahan. Fuzzy matching (exact → prefix →
  substring → subsequence), so `sgm`, `sukam`, or the full `id3276031005` all
  work. Results are ranked name → code → district siblings, so `cimang` puts
  Kecamatan Cimanggis first, then its children.
- **Answer card** always shows the full chain — Kelurahan › Kecamatan ›
  Kabupaten › Provinsi — plus the BPS code with a copy button.
- **"Cari lokasi saya"** uses your GPS position to tell you which area you're
  standing in, at whichever level is open.
- **Light/dark theme** swapping the whole basemap, remembered across visits.
- **Deep links**: `#id3276031005` for a kelurahan, `#kec/id3276040` for a
  kecamatan. Both are shareable and survive a reload.

At each level the map shows only that level's detail — 63 kelurahan lines drawn
over 11 kecamatan outlines at city zoom reads as noise, not hierarchy. The
kelurahan overlay is genuinely hidden, not just dimmed, and at kecamatan view the
kelurahan layers are *filtered* to the open district rather than dimmed: the
other 10 kecamatan's children at 4% opacity were still ~55 visible hairlines.
Any `setFilter` a view applies must be cleared by the view that replaces it —
`paint()` resets `kec-label` to `null` at city view, or the last-opened
kecamatan stays silently unlabelled.

## Layout

```
index.html            markup + attribution
src/app.js            map, drill-down, search, theme, geolocation
src/app.css           theme tokens (light/dark) + all styling
src/data.js           generated — 74 features as window.DEPOK
public/data/          generated — the same data as JSON
vendor/maplibre-gl.*  MapLibre GL JS 5.6 (vendored, no CDN at runtime)
scripts/              data pipeline
shots/                screenshots (gitignored)
```

## Regenerating the boundary data

```bash
python3 scripts/build-data.py       # 63 kelurahan  -> public/data/
python3 scripts/build-districts.py  # 11 kecamatan  -> public/data/
python3 scripts/bundle-data.py      # both          -> src/data.js
python3 scripts/build-roads.py      # 77 ruas      -> src/roads.js  (Overpass)
```

The first three pull from
[JfrAziz/indonesia-district](https://github.com/JfrAziz/indonesia-district)
(the split of HDX `cod-ab-idn`), round coordinates to 6 decimals, drop
consecutive duplicate vertices, and **fail loudly** if any feature is missing
its name or code — a nameless feature would render as an unclickable blank
polygon and read as a broken map rather than a data bug.

`build-districts.py` **dissolves** the 63 kelurahan per `district_code` with
`shapely.ops.unary_union`. This is not cosmetic. The upstream per-kecamatan file
contains one feature per *child kelurahan*, each carrying the district's own
name, so collecting its parts without unioning produces a MultiPolygon that
MapLibre strokes part-by-part — drawing all 63 child borders at city view. That
read as a foreign-geometry bug and got misattributed to the basemap twice.
Coordinates are rounded **after** the union, because rounding first separates
shared edges by a rounding error and the union keeps the sliver instead of
absorbing the seam.

`bundle-data.py` then verifies the two levels agree — every `district_code` used
by a kelurahan must exist in the kecamatan set, and each district must be a
valid single-ring geometry — or the build fails. An undissolved build cannot
ship.

## Roads come from the city's own planning law

The road list is **Perda Kota Depok No. 9 Tahun 2022** (RTRW Kota Depok
2022–2042), **Pasal 16**, read straight out of the enacted text. That is the
binding local regulation and it enumerates the network explicitly:

| Pasal 16 ayat | Class | Ruas |
|---|---|---|
| (3) | Arteri Primer | 1 |
| (4) | Arteri Sekunder | 14 |
| (6) | Kolektor Primer | 18 |
| (7) | Kolektor Sekunder | 38 |
| (10) | Jalan Tol | 6 |
| | **Total** | **77** |

The class names, the grouping, and the names in each row are the Perda's own
text — nothing is derived from OSM tags. Ayat (1)–(2) define the hierarchy
(`jalan umum` = arteri / kolektor / lokal / lingkungan; arteri = primer /
sekunder) and are why the groups are nested the way they are.

**Jalan lokal and jalan lingkungan are deliberately absent.** Ayat (8) and (9)
defer both to the Rencana Detail Tata Ruang, a separate document that is not
published. So this is a gap in the *source*, and the UI says so rather than
inventing two empty groups or quietly dropping them.

### OSM is used only to draw, never to classify

The Perda gives a name; OpenStreetMap gives it coordinates. That's the whole
division of labour, and it matters:

- **Matching is approximate.** A "ruas" often spans several streets —
  `Ruas Jalan Merawan – Jalan Cinere Raya – Jalan Limo Raya – Jalan Meruyung
  Raya` is four OSM ways. And the Perda abbreviates where OSM spells out:
  `Jalan Ir. H. Juanda` ↔ `Jalan Insinyur Haji Juanda`. The build normalises
  prefixes and honorifics before matching.
- **The bbox is wider than Depok on purpose.** Pasal 16 enumerates corridors
  that leave the city (`Cibinong-Cimpaeun`, `Tole Iskandar-Pondok Rajeg` to the
  Bogor border). A Depok-only bbox matches 34/77; the wide one matches 72/77.
- **3 of 77 have no OSM counterpart** (`Ruas Jalan Cibinong-Cimpaeun`, `Jalan
  Akses Kota Kembang Raya`, `Jalan Sukatani Permai`). They stay in the list at
  full size, marked *not locatable*, and are non-interactive rather than
  silently absent — a regulation that lists 77 roads should show 77 rows.

The previous build used UU 22/2009 Pasal 25 (authority classes: Nasional /
Provinsi / Kabupaten-Kota / Desa / Lingkungan). That was abandoned because OSM
records no road-maintenance authority, so 333 of the 350 rows were a guess
disguised as a class. The Perda gives real, citable classes.

### Regenerating

```bash
python3 scripts/build-roads.py   # 77 ruas -> src/roads.js
```

Overpass is rate-limited and 504s under load — a single wide query times out
every time, and the tiled queries take 3–6 minutes with retries — so responses
are cached to `/tmp/depok-roads-cache.*` for a day. Re-running is instant from
cache; delete the files to force a refetch.

`scripts/` is pruned on Cloudflare Pages, so `src/roads.js` is committed — same
as `src/data.js`. The build needs no Python packages beyond the standard
library (the district scripts need Shapely; the road script does not).

## Data

| | |
|---|---|
| Boundaries | 63 kelurahan, 11 kecamatan, Kota Depok, Jawa Barat |
| Source | HDX/BPS `cod-ab-idn` via `JfrAziz/indonesia-district` |
| Licence | **CC BY-IGO** — attribution required, commercial use fine |
| Vintage | 2020-04-01 |
| Size | 1098 KB raw / **264 KB gzipped** for both levels |

The hierarchy is denormalised into every feature (`district_code`,
`village_code`), so drilling down is a client-side lookup — no server and no
reverse-geocoder. The two levels were checked against each other: total area
ratio 1.0000, identical bounding boxes.

**The 2020 vintage is worth stating plainly**: Indonesian administrative units have
been renamed, split, and merged since. Treat results as "as of 2020".

## Basemap

[OpenFreeMap](https://openfreemap.org) — free, **no API key, no rate limit, no
account**. Vector tiles in the OpenMapTiles schema.

- light: `/styles/positron`
- dark: `/styles/dark`

Map data © OpenStreetMap contributors (ODbL). Boundaries are CC BY-IGO, so the
two are attributed separately in the footer.

## Notes for anyone extending this

- **`setStyle()` drops custom sources and layers.** `restoreLayers()` in `app.js`
  re-adds them. Do **not** guard it with `if (map.getLayer(...)) return` —
  `setStyle` doesn't tear layers down synchronously, so that guard bails before
  the swap starts and the overlay silently disappears. `load` does not re-fire,
  `styledata` fires once while the style is still loading, and `idle` can be lost
  if you toggle again quickly — so hook all of them *and* poll
  `map.isStyleLoaded()`.
- **GeoJSON sources need an explicit `FeatureCollection`.** Passing the bundle
  object (`{meta, features, districts}`) leaves `type` undefined; the source
  reports as loaded, `isSourceLoaded()` returns true, and it still renders zero
  features. Silent failure — check `querySourceFeatures()` count, not just layer
  existence.
- **Search ranking must be best-name-first.** Taking `Math.min()` across the name
  and district scores ranks a *district* match above an exact *name* match:
  searching `jagak` put Ciganjur above Jagakarsa. Order is name → code → district
  siblings (always last).
- **Measure responsive layout, don't eyeball it.** The back button overlapped the
  centred search box below 1025px and the summary line again between 721–1024px;
  both were found by comparing `getBoundingClientRect()` pairs, not by looking.
- Names from an external dataset go into the DOM via `textContent`, never
  `innerHTML`.