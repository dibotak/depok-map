# depok-map

An interactive map of **Kota Depok**'s 63 kelurahan across 11 kecamatan — search a
place name or click the map to get its full administrative hierarchy and BPS code.

## Run it

No build step and no dependencies to install. Any static server works:

```bash
python3 -m http.server 4400
# http://localhost:4400
```

A server is required — the app loads its geometry via `<script src>`, which
`file://` blocks.

## What it does

- **Search** any kelurahan or kecamatan. Fuzzy matching (exact → prefix → substring
  → subsequence), so `sgm`, `sukam`, or the full `id3276031005` all work.
- **Click any boundary** on the map to select it.
- **Answer card** always shows the full chain —
  Kecamatan › Kabupaten › Provinsi — plus the BPS code with a copy button.
- **"Cari lokasi saya"** uses your GPS position to tell you which kelurahan you're
  standing in.
- **Light/dark theme** swapping the whole basemap, remembered across visits.
- **Deep links**: selecting a kelurahan writes its code to the URL hash, so
  `#id3276031005` is shareable and survives a reload.

## Layout

```
index.html            markup + attribution
src/app.js            map, search, theme, geolocation
src/app.css           theme tokens (light/dark) + all styling
src/data.js           generated — 63 features as window.DEPOK
public/data/          generated — the same data as JSON
vendor/maplibre-gl.*  MapLibre GL JS 5.6 (vendored, no CDN at runtime)
scripts/              data pipeline
shots/                screenshots
```

## Regenerating the boundary data

```bash
python3 scripts/build-data.py    # fetch + trim upstream GeoJSON -> public/data/
python3 scripts/bundle-data.py   # -> src/data.js
```

`build-data.py` pulls from
[JfrAziz/indonesia-district](https://github.com/JfrAziz/indonesia-district)
(the split of HDX `cod-ab-idn`), rounds coordinates to 6 decimals, drops
consecutive duplicate vertices, and **fails loudly** if any feature is missing
`village`, `district`, or `village_code` — a nameless feature would render as an
unclickable blank polygon and read as a broken map rather than a data bug.

## Data

| | |
|---|---|
| Boundaries | 63 kelurahan, 11 kecamatan, Kota Depok, Jawa Barat |
| Source | HDX/BPS `cod-ab-idn` via `JfrAziz/indonesia-district` |
| Licence | **CC BY-IGO** — attribution required, commercial use fine |
| Vintage | 2020-04-01 |
| Size | 556 KB raw / **136 KB gzipped** for the whole city |

The hierarchy is denormalised into every feature (`district_code`, `village_code`),
so drill-down is a client-side filter — there is no server and no reverse-geocoder.

**The 2020 vintage is worth stating plainly**: Indonesian administrative units have
been renamed, split, and merged since. Treat results as "as of 2020".

## Basemap

[OpenFreeMap](https://openfreemap.org) — free, **no API key, no rate limit, no
account**. Vector tiles in the OpenMapTiles schema.

- light: `/styles/positron`
- dark: `/styles/dark`

Map data © OpenStreetMap contributors (ODbL).

## Notes for anyone extending this

- **`setStyle()` drops custom sources and layers.** `restoreLayers()` in `app.js`
  re-adds them. Do **not** guard it with `if (map.getLayer(...)) return` —
  `setStyle` doesn't tear layers down synchronously, so that guard bails before
  the swap starts and the overlay silently disappears. Wait for
  `map.isStyleLoaded()`.
- **Search ranking must be best-name-first.** Taking `Math.min()` across the name
  and district scores ranks a *district* match above an exact *name* match:
  searching `jagak` put Ciganjur above Jagakarsa. Order is name → code → district
  siblings (always last).
- Names from an external dataset go into the DOM via `textContent`, never
  `innerHTML`.
