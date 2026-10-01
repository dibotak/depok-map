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

A subdomain or apex domain both work. The asset paths are root-absolute
(`/src/app.js`), so mounting this under a *subpath* like `example.com/depok-map/`
would 404 every asset — use `map.dibotak.com`, not a subpath.

## What it does

- **Drill down one level at a time.** City view highlights the whole city and
  shows all 11 kecamatan boundaries, each labelled. Click one to zoom in and see
  every kelurahan inside it, labelled with its name. Click a kelurahan to
  select it. `Semua kecamatan` goes back.
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
kelurahan overlay is genuinely hidden, not just dimmed.

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
```

All three pull from
[JfrAziz/indonesia-district](https://github.com/JfrAziz/indonesia-district)
(the split of HDX `cod-ab-idn`), round coordinates to 6 decimals, drop
consecutive duplicate vertices, and **fail loudly** if any feature is missing
its name or code — a nameless feature would render as an unclickable blank
polygon and read as a broken map rather than a data bug.

`build-districts.py` fetches the 11 published kecamatan polygons rather than
dissolving the 63 kelurahan. A dissolve leaves slivers along shared borders and
invents a boundary BPS never published. `bundle-data.py` then verifies the two
levels agree — every `district_code` used by a kelurahan must exist in the
kecamatan set, or the build fails.

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