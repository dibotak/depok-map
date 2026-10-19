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
data the browser fetches, so pruning them is safe and leaves **~5.1 MB** to
publish (the schools payload alone is 1.7 MB of the total).

Cloudflare Pages has no ignore file for Git-based deploys, so pruning has to
happen in the build command. `rm -rf` is safe here because the whole repo is
re-checked-out on every deploy — nothing is lost that git can't restore.

`scripts/` is pruned, so `build-roads.py` and its Overpass dependency never
touch the Pages build — `src/roads.js` is committed, exactly like `src/data.js`.

A subdomain or apex domain both work. The asset paths are root-absolute
(`/src/app.js`), so mounting this under a *subpath* like `example.com/depok-map/`
would 404 every asset — use `map.dibotak.com`, not a subpath.

## What it does

- **Sidebar with nine collapsible sections**, opened by the **icon-only**
  button beside the search bar on the same row. The button carries no text —
  its `aria-label` ("Buka daftar wilayah dan jalan" / "Tutup daftar") is the
  only name, and it flips with state. Three are structural: *Wilayah* lists all
  11 kecamatan with their child counts — opening one appends its kelurahan
  underneath, and the current selection stays highlighted as you click through
  the map; *Jalan* lists the 77 official ruas grouped by class; *Transport*
  lists angkot routes grouped by trayek. Five are the point categories of the
  shared Atlas Spasial taxonomy: *Pendidikan* (the 2,871 schools from the
  ministry register in six families, plus the 36 OSM campuses), *Kesehatan &
  darurat* (37 puskesmas + 82 faskes + 18 OSM police posts and fire stations),
  *Tempat ibadah* (266 OSM places of worship), *Perdagangan* (245 OSM markets,
  shops and banks) and *Ruang terbuka* (124 OSM parks, sports facilities and
  cemeteries). The ninth, *Lainnya*, holds *Banjir* — the 8 curated
  flood-report points. Tapping the open section collapses it, so the map gets
  the column back.
  - **Accordion, not tabs.** A tab strip could not hold even the four labels it
    first shipped with: they overflowed the 316px sidebar (~346px for four),
    and `.side-tab { flex: 1 }` without `min-width: 0` could not shrink below
    its content, pushing the 40px theme button on top of the strip. The same
    arithmetic makes a strip impossible at nine. Stacked headers wrap by
    nature, collapsed sections sit as quiet headers between open ones, and
    `setTab()` is the single owner of the open state — exactly one panel is
    ever open.
  - **The panel owns the height.** `.side-head` is a fixed strip,
    `--side-head-h` tall (32px, or 48px once the touch controls grow the
    buttons), so the accordion content below is the sidebar height minus that
    strip. `.side-body` is `overflow: hidden` — the sidebar itself never
    scrolls; the open panel is `flex: 1` and its own `.side-scroll` does the
    scrolling. Only on a very short column (landscape phones, `max-height:
    560px`) can the nine collapsed headers alone exceed the sidebar, and just
    there the fallback `overflow-y: auto` comes back so no section is
    stranded.
  - **One engine, six panels.** The five category panels and *Lainnya* all
    render through one row model and one `renderPanel()` (see *One list
    engine* below), which is why a curated point and a register entry share
    their row markup, their search, their lazy groups and their highlight.
- **Every point answers a map click, in both directions.** A click on any
  category pin — school, puskesmas, faskes, police/fire station, place of
  worship, market, park, curated point or flood report — is the same action as
  clicking that row in the sidebar: the map flies to it, its card opens, and
  the sidebar switches to the matching section with the row highlighted and
  scrolled into view. The pin takes the whole click — the kecamatan/kelurahan
  underneath does not also react, because a pin plus an area in one click would
  fight over the card slot.
- **Zoomed out, points become counts.** Every show-all source clusters (the
  one selected marker never does), so a family with many points on screen
  merges into a count bubble instead of a hail of dots. Clicking a bubble
  zooms to the cluster's expansion zoom — the level where it breaks apart —
  and past zoom 14 the individual pins are always the whole picture. A bubble
  is an aggregate, not a place, so it gets the pointer cursor and takes the
  click before the pins and the polygons underneath; it never opens a card
  itself.
- **Area select has its own toggle**, the second round button in the topbar's
  left column, right under the sidebar button (on by default, remembered like
  the theme). Off, bare map clicks leave the polygons alone, so pins can be
  browsed without re-drilling the map; search results and *Cari lokasi saya*
  still select areas, because those say what they do before they are used.
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
- **The distance read-out is a zoom gesture, not chrome.** It appears on every
  zoom event and fades 5 s after the last one, so "how far is that?" is
  answerable mid-gesture without a permanent box at the bottom of the map
  (where the legend and the licence line already live). It draws a round
  distance — 10 m to 50 km — at exactly that many pixels, corrected for the
  centre latitude, so it stays honest at every zoom. It sits under the zoom
  buttons and is `aria-hidden`; the zoom buttons already announce the zoom to
  assistive tech.

- **Mobile: nothing overlaps, everything is tappable.** The answer card clears
  the licence line by a *measured* amount — the attribution wraps to 53px at
  390px wide but only 39px at 430px, so its real height is published as
  `--attrib-h` by `syncAttribInset()` and re-derived on resize, rotate, and
  theme change. The sidebar's and back button's top offset is measured the same
  way — `--topbar-left-h` from `syncTopbarInset()` — because the topbar's left
  column stacks the sidebar toggle and the area toggle and its height depends
  on the breakpoint's button sizes. The card and road banner are hidden outright
  while the panel is open; at full width the panel covers the map anyway, so a
  card peeking out underneath it just looked broken. Every control a thumb has
  to hit is at least 40px: the theme, close, and area-toggle buttons, the search
  clear button, the card minimise, the GPS button, each list row, and
  MapLibre's own zoom/compass buttons (29px by default).

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
src/transport.js      generated — angkot routes as window.DEPOK_TRANSPORT
src/schools.js        generated — 2,871 schools as window.DEPOK_SCHOOLS
src/puskesmas.js      generated — 37 Dinkes facilities as window.DEPOK_PUSKESMAS
src/faskes.js         generated — 82 OSM health facilities as window.DEPOK_FASKES
src/poi.js            generated — 689 OSM category points as window.DEPOK_POI
src/kurasi.js         generated — curated points as window.DEPOK_KURASI
src/flood.js          generated — 8 curated flood reports as window.DEPOK_FLOOD
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

## Schools: the ministry register, all levels

`scripts/fetch-sekolahkita.py` + `scripts/build-schools-sekolahkita.py` ->
`src/schools.js`. **2,871 schools, the portal's full Depok list across all 22
`bentuk_pendidikan`; 2,253 carry a pin** — 2,236 from the portal's own
coordinates plus 17 recovered by hand (below). 618 rows have no point: 536 the
portal publishes without one, 81 whose published coordinate fails the city
test, and one deliberately left unpinned rather than guessed onto the map.

The sidebar groups them into the six families the register itself implies:

| Family | n | jenjang folded in |
|---|---|---|
| PAUD/TK | 1,376 | KB, TK, RA, SPS, PAUDQ, TPA |
| SD/MI | 587 | SD, MI, SPK SD |
| SMP/MTs | 358 | SMP, MTs, SPK SMP, SPM Wustha |
| SMA/SMK/MA | 244 | SMA, SMK, MA, SPK SMA, SPM Ulya |
| SLB | 10 | SLB |
| Kursus & PKBM | 296 | Kursus, PKBM, SKB |

An unknown `bentuk_pendidikan` is a **hard error** in the build, not a skipped
row: a new level appearing on the portal must get a family, or it would vanish
from the map silently.

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
at all. Coordinate count went **21 -> 224** for secondary schools at this
stage, then on to **2,236** once the fetch was widened to every level, and
**2,253** after the leftover 17 were researched by hand (below).

It is also more complete than the earlier scrape: 2,871 schools versus the
scrape's 250 secondary rows, and `bentuk_pendidikan` is given directly, so
level no longer has to be guessed from the name or dropped for being ambiguous.

### Coordinates are official, but not perfect

Three checks decide whether a point is trusted:

1. **Zero is not a location.** 536 schools come back without any usable
   coordinate (or as `0/0`), the portal's way of saying it has no point. Kept
   in the list with their address, no pin.
2. **Latitude sign.** `SMAS IT AL-QUDWAH` (Beji) is published at latitude
   **+6.3856** -- valid for Indonesia, 1,400km from Depok. Negating it lands
   106m inside the city in Kemirimuka, so it is a dropped minus sign in the
   source and is corrected. The test is the *city polygon*, not a latitude
   range: a range check cannot catch this, because +6.38 is a real latitude
   somewhere in Indonesia. What gives it away is that the point misses the city
   and its negation does not. Nine rows have their sign corrected this way.
3. **Proximity.** A point more than 2km outside the city is refused — 76 are,
   and their rows say how far the pin would have been (`koordinat 838 km di
   luar Kota Depok` is the largest cluster at 52: the portal carries another
   city's coordinate for the school). Five more are rejected for values outside
   the latitude/longitude range altogether. Two schools sit 30m and 423m
   outside because our village polygons are coarse at the edge, and both are
   kept.

Near-null-island values (`|lat| < 0.01 and |lon| < 0.01`) count as missing.
`MAS ULUMUL QUR'AN` is published at `0.0009, -0.0013` -- about 11,900km away --
and "koordinat 11901 km di luar Kota Depok" on a card would be true and useless.

### The 618 the register cannot place

536 schools have no coordinate in the portal, 76 sit outside the city, five
carry impossible values and one is parked on purpose. Of those, 17 were
resolved by hand and the outcome is checked in as a small override file rather
than patched into the build: `scripts/data/school-coordinates.json` maps NPSN
-> `{lat, lon, pin_src, source}`. The builder merges it over the portal rows,
and `pin_src` rides along into `src/schools.js` so the card can say where a pin
came from:

| `pin_src` | n | where the point comes from |
|---|---|---|
| `gmaps` | 15 | the school's Google Maps place entry |
| `situs` | 1 | a map link on the school's own site |
| `dapodik` | 1 | the ministry's Dapodik listing (same complex) |

No coordinate is invented: each `source` names the page the point was read
from, and the test is still the city polygon plus proximity, not trust in the
field. Seventeen of the eighteen researched points survived it.

The eighteenth — **SMK Negeri 5**, in Meruyung/Limo — is deliberately left
unpinned. Its building is not built yet (the fasos/fasum land in Cinere
Residence RW 05 came through in January 2026, per `berita.depok.go.id`, and
classes currently borrow space at SMAN 6 / SMKN 2), so any pin would be a guess
about a field. It keeps its row with a `no pin` badge, and the override file
carries the same note, so the gap reads as a gap rather than a bug.

The remaining **601 rows stay in the list without a pin** — with family, level,
address and NPSN, searchable and clickable, just not drawn. That is a deliberate
choice: their absence from the map is the register's gap, and the panel notes
say how many are missing.

### Rate limiting

Nothing was throttled: 2,871 detail requests in 1,568s (~0.55s each), no 429s
and no 503s. The policy is deliberately conservative anyway — 0.45s between
detail requests, 0.6s between pages, exponential backoff on 429/503 with the
error surfaced rather than swallowed, and a descriptive User-Agent.

Every response is cached to `.cache/sekolah/` by `sekolah_id`, so a rebuild
makes no network requests. That is what makes the output checkable: a warm
rebuild is ~10s and byte-identical, verified by rerunning and diffing.

```bash
python3 scripts/fetch-sekolahkita.py           # warm: cache only, no network
python3 scripts/build-schools-sekolahkita.py   # -> src/schools.js
```

### UI

`hasPin()` gates every GeoJSON path: 618 records have no usable point, and one
without would otherwise produce an invalid feature and break `fitBounds`. Those
rows get a `no pin` badge and a card that reads why the pin is missing. Cards
carry 6+ facts: address, kelurahan, postcode, coordinates, source id, NPSN,
family and jenjang.

## Health: the Dinkes register and its OSM complements

*Kesehatan & darurat* is one panel with three sources, kept apart by row badge
rather than by panel: the official register, the OSM health complement, and the
OSM emergency places (police, fire stations) that the category needs but no
health register covers.

**Puskesmas** (`scripts/build-puskesmas.py` -> `src/puskesmas.js`) is the
official register: 37 facilities grouped under 11 kecamatan, from
`dinkes.depok.go.id/User/portal_puskesmas`, embedded in the build as
`PORTAL_ROWS` with each facility's own site URL carried through. Coordinates
are matched, not invented: OpenStreetMap first (community pins plus
address/phone/hours tags), then Google Maps for the rows OSM misses, with
`pin_src` recorded so the card says which is which. A row that matches neither
geo source is emitted without a pin, never guessed at a kelurahan centroid. The
24-hour flag comes from the Dinkes schedule (Mar 2025) — exactly one UPTD per
kecamatan, a cheap consistency check on the parse.

The portal writes the kecamatan as "Sukmajaya" while the boundary layer (and
therefore every other payload) writes "Sukma Jaya"; the builder normalises it,
because the panel groups rows by that exact string and one kecamatan with two
spellings would read as two.

The WAF is worth knowing about, because it shapes the build: the interactive
facility map (`dsw.depok.go.id`) and every per-facility `pkm*.depok.go.id`
site reject clients outside Indonesia (browser headers, headless Chrome and
r.jina.ai all bounce). That is why the portal table is the source of truth and
only the coordinates come from elsewhere.

**Faskes** (`scripts/build-faskes.py` -> `src/faskes.js`) is the complement:
the rest of the city's health facilities as mapped in OpenStreetMap — 82 rows
of hospitals, clinics, doctors'/dentists' practices and pharmacies. Sources
tried first and rejected: `sirs.kemkes.go.id` (login wall), `data.depok.go.id`
(domain does not resolve as of Oct 2026), `depok.go.id` subdomains (F5 WAF).
OSM is ODbL, print-safe, and rebuildable at will from the Overpass API.

The build drops what would double or mislead: objects named
"Puskesmas"/"Pustu" *and* the exact OSM refs the puskesmas build used (one
facility must never be two pins), unnamed objects (a pin that cannot be
labelled is noise on a printed map), obviously wrong mapper names, and points
outside the city polygon (counted and printed, not swallowed). Same kind +
name within 120m collapses to the richer record — a building outline and its
POI node are one place. A hospital mistagged but named "Klinik ..." is listed
as a clinic; the name the owner uses beats a stray tag. Responses are cached to
`/tmp/depok-faskes-cache.json` for a day; `FASKES_REFRESH=1` forces a refetch.
The payload's `source` string repeats the caveat the sidebar note also carries:
completeness is OSM's, so a facility missing from this layer is *unmapped*, not
non-existent.

## The four OSM categories

`scripts/build-poi.py` -> `src/poi.js` builds the four point categories that no
local register covers, from one Overpass sweep of the city:

| Category | Panel | kinds | n |
|---|---|---|---|
| `tempat-ibadah` | Tempat ibadah | place_of_worship | 266 |
| `perdagangan` | Perdagangan | marketplace, perdagangan/shop, bank | 245 |
| `ruang-terbuka` | Ruang terbuka | park, fasilitas_olahraga, kuburan | 124 |
| `kesehatan-darurat` | Kesehatan & darurat | police, fire_station | 18 |
| `pendidikan` | Pendidikan | college, university | 36 |

The categories are the Atlas Spasial taxonomy — the same five slugs the
`ensiklopedia-kota` site uses (`pendidikan`, `kesehatan-darurat`,
`tempat-ibadah`, `perdagangan`, `ruang-terbuka`), so a curated point from the
studio lands in the same panel as its OSM neighbours.

The build keeps the same discipline as the faskes one: unnamed objects are
dropped (744 of them — a pin that cannot be labelled is noise on a printed
map), points outside the city polygon are dropped and counted (1,696, almost
all because the Overpass bbox reaches into the neighbouring cities), and the
same kind + name within 120m collapses to the richer record (18 collapsed).
Rows carry `religion`/`denomination`, `subkind` and a display `badge`, so a
`convenience` shop reads "Minimarket", a `mall` reads "Mal", a
`sports_centre` reads "Olahraga" and a `playground` "Taman bermain" — the row
and the card say what the point actually is, not what the tag is called.
Responses are cached to `/tmp/depok-poi-cache.json` for a day; `POI_REFRESH=1`
forces a refetch.

## One list engine, six panels

The five category panels and *Lainnya* are not six implementations. Every point
payload is flattened into one row model (`{key, name, badge, group, chips, hay,
panel, src, kind, data, kurasi}`) at boot, and a single `renderPanel()` builds
each panel's groups, rows, counts, search matching and empty state from it. The
engine is why a curated point and a 2,871-row register can share markup:

- **`src` is the pin family**, and it decides which map layer a row draws in
  and which accent its card takes: `school` (teal), `puskes`/`faskes` (rose,
  lime), `darurat`/`ibadah`/`dagang`/`ruang` (amber, violet, magenta, cyan),
  `flood` (blue), plus fuchsia for a curated row's card. The kesehatan panel
  holds three families at once, which is why the model carries a family per row
  rather than per panel.
- **Selection is one row, not one per layer.** `selectPoint(key)` owns the
  highlighted row, the selected marker (pushed into the family's selected
  source), the fly-to and the card. `highlightSchool()`/`highlightPuskes()`/
  `highlightFaskes()`/`highlightFlood()` remain as aliases of `selectPoint`
  because the deep-link paths already call them.
- **Groups open lazily where the list is huge.** Pendidikan renders its six
  family headers up front and materialises a family's rows (1,376 for PAUD/TK)
  only when that family is opened or searched; the other panels open with their
  rows already in the DOM.
- **Cards dispatch on family** (`CARD_OF`), and curated rows always go to the
  shared POI card whatever their kind: a register card (NPSN, Dinkes schedule)
  has nothing to say about a point that is not in a register.

The taxonomy itself is defined once per side and kept in sync by hand: the app
maps `category` -> panel (`PANEL_OF_CATEGORY`) and `kind` -> family
(`SRC_OF_KIND`), `scripts/curated.py` maps kind -> category for legacy files
(`KIND_CATEGORY`), and the studio offers the same five slugs. A kind with no
family mapping still gets its row and a pin in a fallback colour, so a new kind
is a colour mismatch, never a lost point.

## Flood reports (PetaBencana.id)

`scripts/build-flood.py` -> `src/flood.js`. A different kind of layer: real
flood reports for Kota Depok from [PetaBencana.id](https://petabencana.id), the
crowdsourced hazard platform run by the MIT Urban Risk Lab / Yayasan Peta
Bencana. Each pin is a report location carrying the original report text, the
date and — when the reporter gave one — the depth. Pins say "this was reported
here", not "we measured this", and the card repeats that. It is the only
occupant of the sidebar's *Lainnya* section: the five categories above all have
a register or an OSM layer behind them, and a crowd report is neither.

**The screening is the build.** Depok's feed is dominated by training material:
GRASP/BNPB simulation rounds (`#TES_Trainer ...`, "Latihan simulasi ..."), HKB
disaster days, campus classes (`UI_...`, `DUTA ...`) and the occasional joke
post. The archive endpoint carries no `is_training` flag, so every
Depok-tagged flood report — 217 of them, Apr 2023 – May 2026 — was screened by
hand and logged in `scripts/data/flood-history.json`: `review="keep"` for 8
real street-level reports, `review="drop"` with a per-record note for the other
209. The reason for each drop is a line of text, not a flag, because "why was
this excluded" is the question a reader will ask, and "it was a training post"
is an answer. Reports within 150m of each other share a pin. Nothing reaches
the map without a `keep`.

Coverage was verified by a full archive scan 2020-10-01 → 2026-10-17 (the
archive is Jakarta-only before ~2021 and holds no Depok flood report before Apr
2023). The default rebuild is offline and byte-reproducible from the checked-in
history; the opt-in fetch merges new reports as `review="pending"`, which the
build prints until a human classifies them:

```bash
python3 scripts/build-flood.py                  # offline, from the history file
FLOOD_FETCH=1 python3 scripts/build-flood.py    # + fetch new windows/live first
```

The archive API answers in ≤~217-day windows and the live endpoint in ≤7-day
windows, both behind a browser User-Agent; the scan and its retry ladder are
documented in the script. Licence: public data PetaBencana.id (MIT Urban Risk
Lab / Yayasan Peta Bencana), attributed in the footer.

## Regenerating the boundary data

```bash
python3 scripts/build-data.py       # 63 kelurahan  -> public/data/
python3 scripts/build-districts.py  # 11 kecamatan  -> public/data/
python3 scripts/bundle-data.py      # both          -> src/data.js
python3 scripts/build-roads.py      # 77 ruas       -> src/roads.js  (Overpass)
python3 scripts/build-faskes.py     # 82 faskes     -> src/faskes.js (Overpass)
python3 scripts/build-poi.py        # 689 titik     -> src/poi.js   (Overpass)
python3 scripts/build-flood.py      # 8 laporan     -> src/flood.js  (offline)
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

## Kurasi dari Studio Atlas

Everything in this repo is reproducible from its sources except **curation**:
corrections to a school's name, a clinic's kind, a road's class — and points
that exist in no scraped dataset. Those arrive as `scripts/data/curated.json`,
written by Studio Atlas (the `ensiklopedia-kota` repo, tab **Ekspor**) and
never edited by hand here. The contract lives in `scripts/curated.py`, and both
repos share it: the studio imports this module for its preview.

```json
{
 "version": 1,
 "pois": [
  {"id": "titik-1", "op": "add", "layer": "kurasi",
   "row": {"key": "titik-1", "name": "Taman …", "kind": "park", "label": "Taman",
           "category": "ruang-terbuka",
           "lat": -6.38, "lon": 106.72, "village": "Curug", "district": "Bojongsari"},
   "reason": "…", "source": "…"},
  {"id": "titik-2", "op": "set",    "layer": "faskes", "key": "node-…",
   "set": {"kind": "Rumah sakit"}, "reason": "…", "source": "…"},
  {"id": "titik-3", "op": "remove", "layer": "schools", "key": "…",
   "reason": "…", "source": "…"}
 ],
 "roads": [
  {"id": "jalan-1", "op": "set-class", "group": "arteri-primer", "n": "A1",
   "match": "Cilodong Raya", "class": "lokal", "reason": "…", "source": "…"}
 ]
}
```

Rules worth knowing before touching that file:

- **Additive and reversible.** Every patched row carries a `_kurasi`
  provenance block (previous values, or `_kurasi.removed` at the document
  level) and the payload header gains a marker line. Emptying the curation and
  re-applying restores every payload byte-for-byte — dropping an edit never
  leaves a fossil behind.
- **Curated points carry a category, not a layer.** `row.category` is one of
  the five shared slugs, chosen by the curator in the studio (it defaults to
  the kind's own category, `KIND_CATEGORY` in `scripts/curated.py`); the point
  is then listed in that category's panel with a `(kurasi)` tag, drawn in the
  category's pin family, and its card adds the reason and the source. There is
  no separate *Kurasi* section any more, and a row whose kind has no natural
  category (`library`, `townhall`, …) must pick one explicitly — a missing
  category is a validation error, not a silently dropped point.
- **Road classes are a badge override, never a re-grouping.** `set-class`
  rewrites the badge to `Lokal (kurasi)` and keeps `badge_perda` beside it; the
  77 ruas and the Perda's own grouping stay verbatim.
- **The studio resolves before writing.** An edit whose target has no
  counterpart here is *skipped and reported* at export time (atlas-only
  landmarks stay in the book); an *ambiguous* match blocks the export (422,
  nothing written). Once an entry is in this file it must resolve — a missing
  or ambiguous target at apply time is a hard error and no payload is written
  partially.

Apply (offline, idempotent):

```bash
python3 scripts/apply-curated.py            # rewrite the payloads where needed
python3 scripts/apply-curated.py --check    # validate targets, write nothing
```

`src/schools.js`, `src/puskesmas.js`, `src/faskes.js` and `src/roads.js` are
rewritten only when changed; `src/kurasi.js` is regenerated from the added
points on every run (empty curation → empty payload, so the script tag never
404s). Re-running with no curation change touches nothing, and the
`build-*.py` scripts call the same hook, so a full rebuild keeps curation
applied.

**Publishing is manual.** Studio Atlas writes the file and runs the apply; it
never commits or pushes. From here:

```bash
git add src/schools.js src/puskesmas.js src/faskes.js src/roads.js src/kurasi.js scripts/data/curated.json
git commit -m "kurasi: ekspor Studio Atlas (N titik, M ruas)"
git push origin main
```

Cloudflare Pages deploys on push. **Edition-loop warning:** after that deploy,
the site payloads Studio Atlas *fetches* already contain the applied curation.
Running `fetch` + `facts` again without reviewing the studio's curation list
can double-apply the same edit — the studio's Ekspor tab repeats this warning
at the point of export.

## Data

| | |
|---|---|
| Boundaries | 63 kelurahan, 11 kecamatan, Kota Depok, Jawa Barat |
| Source | HDX/BPS `cod-ab-idn` via `JfrAziz/indonesia-district` |
| Licence | **CC BY-IGO** — attribution required, commercial use fine |
| Vintage | 2020-04-01 |
| Size | 874 KB raw / **220 KB gzipped** for both levels |

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