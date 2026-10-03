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
(874 KB of source JSON for the build scripts) and `scripts/`. Neither is
referenced by `index.html` or `app.js` — `src/data.js` is the only boundary
data the browser fetches, so pruning them is safe and leaves **~2.0 MB** to
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

- **Sidebar with four tabs**, opened by the **icon-only** button beside the
  search bar on the same row. The button carries no text — its `aria-label`
  ("Buka daftar wilayah dan jalan" / "Tutup daftar") is the only name, and it
  flips with state. *Wilayah* lists all 11 kecamatan with their child counts;
  opening one appends its kelurahan underneath, and the current selection stays
  highlighted as you click through the map. *Jalan* lists the 77 official ruas
  grouped by class, collapsible. *Transport* lists angkot routes grouped by
  trayek. *SMA/SMK* lists 30 secondary schools.
  - **The tab strip wraps.** `.side-head` is `flex-wrap: wrap` and `.side-tabs`
    is `flex: 1 1 100%`, so the four labels take the whole first line (~346px of
    a 316px sidebar's inner width, which is why they previously overflowed) and
    the theme and close buttons sit right-aligned on a second line. Before this,
    `.side-tab { flex: 1 }` with no `min-width: 0` could not shrink below its
    content, so the labels pushed the 40px theme button on top of the tab strip.
    The tab label *Transportasi* was also shortened to *Transport* (106px → 62px)
    for the same reason. `setTab()` calls `scrollIntoView` so the active tab is
    visible even if the strip does scroll on a narrower phone.
- **Search fills the row.** The top bar is two grid columns — the icon button,
  then the search stretching across everything left over — so the input takes
  ~80% of a phone's width and ~94% on desktop. It is no longer centred: with
  the label gone and the theme toggle moved into the sidebar there is nothing to
  balance, and centring would throw away the width it just gained.
- **One way to shrink the panel:** `✕` closes it, and `Esc` closes it. There is
  no panel minimise — see the answer card below.
- **The answer card has its own minimise.** The chevron in the card's title row
  collapses it to just the name, which is what actually gets the map back on a
  phone, where the card covers a third of the screen. The state lives on `<body>`
  as `card-collapsed`, so picking another area does not pop it open again
  mid-browse, and it survives drill-down and theme changes. Two things about the
  card's anchoring are load-bearing:
  - The card is `position: fixed`. As an absolute box it anchored to `<body>`,
    whose height is content-driven (every other element on the page is
    absolutely positioned), so collapsing the card dragged itself off the top of
    the screen.
  - The state class is named `card-collapsed`, **not** `card-min`. `.card-min` is
    the minimise *button's* class, and its rule is `position: absolute; top: 10px;
    right: 10px; width: 30px; height: 30px`. Putting `card-min` on `<body>` made
    body match the button's own rule and collapsed body into a 30×30 box at the
    top right. `#map` and `.card` are `position: fixed` so they survived
    untouched — which is why the map looked fine while every `position: absolute`
    overlay (`.topbar`, `.sidebar`, `.back`, `.legend`, `.attrib`,
    `.road-note-card`) squeezed into a 2px strip. **A class name used for state
    on `<body>` must never also be an element class in the stylesheet.**
- **Theme and GPS live in the sidebar.** The light/dark toggle sits in the
  sidebar header instead of inside the search box, where it ate ~44px of input
  width. "Cari lokasi saya" is a row under *Wilayah*, with its result next to it
  — it used to be a floating banner directly under the search bar that pushed
  the map down on a phone.
- **Both lists scroll independently.** Each panel is its own scroll container,
  so the 77 ruas and the 11+7 area rows are both fully reachable — including on
  a phone, where the panel is capped at 78dvh so a strip of map always shows
  underneath. The OSM source note sticks to the top of the road list.
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
- **Light/dark theme** swapping the whole basemap, remembered across visits. The
  toggle keeps the same icon-swap rules it had in the search box, and the
  current selection, zoom, and selected road all survive the style swap.
- **Deep links**: `#id3276031005` for a kelurahan, `#kec/id3276040` for a
  kecamatan. Both are shareable and survive a reload.

- **Mobile: nothing overlaps, everything is tappable.** The answer card clears
  the licence line by a *measured* amount — the attribution wraps to 53px at
  390px wide but only 39px at 430px, so its real height is published as
  `--attrib-h` by `syncAttribInset()` and re-derived on resize, rotate, and
  theme change. The card and road banner are hidden outright while the panel is
  open; at full width the panel covers the map anyway, so a card peeking out
  underneath it just looked broken. Every control a thumb has to hit is at least
  40px: the theme and close buttons, the search clear button, the card
  minimise, the GPS button, each list row, and MapLibre's own zoom/compass
  buttons (29px by default).

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
src/roads.js          generated — 77 ruas as window.DEPOK_ROADS (committed)
public/data/          generated — the boundaries as JSON, build input only
vendor/maplibre-gl.*  MapLibre GL JS 5.6 (vendored, no CDN at runtime)
scripts/              data pipeline
shots/                screenshots (gitignored)
.venv-build/          pip target for Shapely (gitignored)
```

## Repo layout

This repository is the **shipped app only** -- the files a visitor's browser
downloads, plus the scripts that generate them. It is public, so nothing that is
still being figured out belongs here.

```
index.html          the app
src/                app.js, styles, and the generated *.js payloads
public/data/        boundary GeoJSON
vendor/             MapLibre, vendored so there is no build step
scripts/            build + fetch scripts for the payloads in src/
```

Design notes, the offline test harness (`harness.html`), the canonical-data-model
experiment, and one-off endpoint probes live in a separate private checkout at
`~/projects/depok-map-lab`, alongside this one. The lab reads `src/*.js` from
here; nothing here reads from the lab.

## Secondary schools

`scripts/fetch-sekolahkita.py` + `scripts/build-schools-sekolahkita.py` ->
`src/schools.js`. **242 schools, 224 of them with official coordinates.**

### The API

`sekolah.data.kemendikdasmen.go.id` is the ministry's newer portal ("Sekolah
Kita"), an Angular SSG app with a public JSON API on the same origin and no
authentication:

```
POST /v1/sekolah-service/sekolah/cari-sekolah
     {page, size, keyword, kabupaten_kota, bentuk_pendidikan, status_sekolah}
GET  /v1/sekolah-service/sekolah/full-detail/{sekolah_id}
```

Two things that each cost a wrong turn first:

- `kabupaten_kota` wants the **name** ("Kota Depok"), not `kode_kabupaten`
  ("026600"). The wilayah reference endpoint returns both, and passing the code
  returns `total: 0` rather than an error -- a silent zero, not a rejection.
- `page` is **0-based**. Page 1 silently skips the first 100 rows.

### Why this replaced the scrape

The earlier pipeline scraped the DataTable pages on
`referensi.data.kemendikdasmen.go.id` for names and addresses, then had to guess
coordinates from OpenStreetMap. It could place only **21 of 250** schools,
because OSM has never mapped most of the city's private secondary schools --
there are 299 school features inside Depok in total, and only 36 whose names
read as secondary.

This portal publishes `lintang`/`bujur` per school. No geocoding, no string
matching, no false positives, and a real point for schools with no OSM presence
at all. Coordinate count went **21 -> 224**.

It is also more complete: 242 secondary schools versus the scrape's 250 rows
across a slightly different jenjang mix, and `bentuk_pendidikan` is given
directly, so level no longer has to be guessed from the name or dropped for
being ambiguous.

| | SMA Negeri | SMA Swasta | SMK Negeri | SMK Swasta | MA Swasta |
|---|---|---|---|---|---|
| count | 15 | 68 | 5 | 120 | 34 |

### Coordinates are official, but not perfect

Three checks decide whether a point is trusted:

1. **Zero is not a location.** 18 schools come back as `0/0`, the portal's way
   of saying it has no point. Kept in the list with their address, no pin.
2. **Latitude sign.** `SMAS IT AL-QUDWAH` (Beji) is published at latitude
   **+6.3856** -- valid for Indonesia, 1,400km from Depok. Negating it lands
   106m inside the city in Kemirimuka, so it is a dropped minus sign in the
   source and is corrected. The test is the *city polygon*, not a latitude
   range: a range check cannot catch this, because +6.38 is a real latitude
   somewhere in Indonesia. What gives it away is that the point misses the city
   and its negation does not.
3. **Proximity.** A point more than 2km outside the city is refused. Two
   schools sit 30m and 423m outside because our village polygons are coarse at
   the edge, and both are kept.

Near-null-island values (`|lat| < 0.01 and |lon| < 0.01`) count as missing.
`MAS ULUMUL QUR'AN` is published at `0.0009, -0.0013` -- about 11,900km away --
and "koordinat 11901 km di luar Kota Depok" on a card would be true and useless.

### Rate limiting

Nothing was throttled: 29 list requests and 242 detail requests in 134s, no
429s. The policy is deliberately conservative anyway -- 0.45s between detail
requests, 0.6s between pages, exponential backoff on 429/503 with the error
surfaced rather than swallowed, and a descriptive User-Agent.

Every response is cached to `.cache/sekolah/` by `sekolah_id`, so a rebuild
makes no network requests. That is what makes the output checkable: a warm
rebuild is ~1s and byte-identical, verified by rerunning and diffing.

### UI

`hasPoint()` gates every GeoJSON path, since 18 records have `lon: null` and
would otherwise produce invalid features and break `fitBounds`. Those rows get a
`no pin` badge and a card reading "belum ada koordinat di sumber". Cards carry
6+ facts: address, kelurahan, postcode, coordinates, source id, NPSN.

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
| Size | 854 KB raw / **214 KB gzipped** for both levels |

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
- **A state class on `<body>` must not collide with any element class.** This is
  the one that cost the most, because the symptom lies about its own cause: the
  map looked *fine* while the entire UI was destroyed. `<body>` was getting
  `card-min` — also the minimise button's class, whose rule pins an element to
  `position: absolute; top: 10px; right: 10px; width: 30px; height: 30px`. Body
  became a 30×30 box. Because `#map` and `.card` are `position: fixed`, they were
  immune, so the report was "map's fine, UI's so broken" — and the natural
  conclusion was that two things were wrong when only one was. Every
  `position: absolute` overlay resolved against the shrunken body instead
  (`.topbar` to x=1240, `.sidebar` to 2px tall, the road list to
  `clientHeight: 0`). State classes now use a distinct prefix (`card-collapsed`,
  `side-open`) and never reuse an element class name.
- **`position: absolute` on an overlay is a landmine when every sibling is out of
  flow.** `<body>` has `overflow: hidden` and no in-flow content, so its box is
  content-driven: *any* sibling height change re-anchors it and drags every
  absolute child with it. Overlays that must not move (`#map`, `.card`, `.back`,
  `.attrib`) are `position: fixed`, and `syncMapSize()` compares the map's
  container against `innerWidth/innerHeight` and calls `map.resize()` when they
  drift — belt and braces for the mobile-toolbar case where the visual viewport
  changes without a `resize` event. `relayoutOverlays()` calls it on every resize
  and rotate.
- **Diagnose by measuring `<body>`, not just the element you were looking at.**
  The whole class of bug above shows up as `document.body.getBoundingClientRect()`
  being the wrong size. If body is not the viewport, every absolutely-positioned
  descendant is suspect.
- Names from an external dataset go into the DOM via `textContent`, never
  `innerHTML`.