#!/usr/bin/env python3
"""Turn OpenStreetMap points of interest into src/poi.js.

WHAT THIS LAYER IS
------------------
The four thematic categories beside Pendidikan and Kesehatan & darurat in the
sidebar, in the same taxonomy the Atlas Spasial (ensiklopedia-kota) uses for
its map symbols:

  tempat-ibadah     place_of_worship
  perdagangan       marketplace, bank, shop (supermarket/mal/toko/minimarket)
  ruang-terbuka     park, sports_centre/stadium/playground, cemetery
  pendidikan        university, college (kampus)
  kesehatan-darurat police, fire_station (the darurat half; the health half is
                    the official puskesmas register plus the faskes layer)

The kind slugs are the atlas's own (place_of_worship, marketplace, park,
fasilitas_olahraga, kuburan, university, ...), so a curated point exported from
Studio Atlas and an OSM row here speak one language.

RULES
-----
* One row per OSM object (node/way/relation), keyed by its OSM ref. The ref is
  kept so any pin can be checked against the map data.
* Unnamed objects are dropped -- a pin that cannot be labelled is noise on a
  printed map.
* A point outside the city polygons is dropped (counted, not swallowed).
* Duplicates (same kind + name within 120m, e.g. a building outline and its
  POI node) collapse to the richer record.
* No coordinates are invented, ever.
* Schools are NOT here: the Pendidikan category's school rows come from the
  Kemendikdasmen register (scripts/build-schools-sekolahkita.py); only kampus
  (which the register does not cover) is added from OSM.

Output: src/poi.js  (window.DEPOK_POI)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPOK = json.loads((ROOT / "public" / "data" / "depok-kelurahan.json").read_text())
CACHE = Path("/tmp/depok-poi-cache.json")

# The five atlas categories in sidebar order. Used for reporting only; the
# per-tag mapping lives in TAGS below.
CATEGORY_ORDER = ("pendidikan", "kesehatan-darurat", "tempat-ibadah",
                  "perdagangan", "ruang-terbuka")

# The tag -> (category, kind, label) map. Labels are display names in the
# sidebar rows; the kind slug stays the atlas's, and `tag` keeps the raw OSM
# tag for the detail card.
TAGS = {
    ("amenity", "university"): ("pendidikan", "university", "Kampus"),
    ("amenity", "college"): ("pendidikan", "college", "Kampus"),
    ("amenity", "police"): ("kesehatan-darurat", "police", "Polisi"),
    ("amenity", "fire_station"): ("kesehatan-darurat", "fire_station", "Damkar"),
    ("amenity", "place_of_worship"): ("tempat-ibadah", "place_of_worship", "Tempat ibadah"),
    ("amenity", "marketplace"): ("perdagangan", "marketplace", "Pasar"),
    ("amenity", "bank"): ("perdagangan", "bank", "Bank"),
    ("shop", "supermarket"): ("perdagangan", "perdagangan", "Perdagangan"),
    ("shop", "mall"): ("perdagangan", "perdagangan", "Perdagangan"),
    ("shop", "department_store"): ("perdagangan", "perdagangan", "Perdagangan"),
    ("shop", "convenience"): ("perdagangan", "perdagangan", "Perdagangan"),
    ("leisure", "park"): ("ruang-terbuka", "park", "Taman"),
    ("leisure", "sports_centre"): ("ruang-terbuka", "fasilitas_olahraga", "Fasilitas olahraga"),
    ("leisure", "stadium"): ("ruang-terbuka", "fasilitas_olahraga", "Stadion"),
    ("leisure", "playground"): ("ruang-terbuka", "fasilitas_olahraga", "Taman bermain"),
    ("landuse", "cemetery"): ("ruang-terbuka", "kuburan", "Pemakaman"),
}

# House of worship labels read better per religion than as one generic word.
RELIGION_LABEL = {
    "muslim": "Masjid", "christian": "Gereja", "catholic": "Gereja",
    "hindu": "Pura", "buddhist": "Wihara", "confucian": "Klenteng",
    "jewish": "Sinagoga",
}
RELIGION_NAME = {
    "muslim": "Islam", "christian": "Kristen", "catholic": "Katolik",
    "hindu": "Hindu", "buddhist": "Buddha", "confucian": "Konghucu",
    "jewish": "Yahudi",
}

# Badges for the sidebar rows: shorter than the label where the label is a full
# phrase, and finer-grained where one kind covers several things (a minimarket
# and a mall are both "perdagangan" in the atlas taxonomy but should say what
# they are in a list).
SHOP_BADGE = {
    "supermarket": "Supermarket", "mall": "Mal",
    "department_store": "Serba ada", "convenience": "Minimarket",
}
LEISURE_BADGE = {
    "sports_centre": "Olahraga", "stadium": "Stadion",
    "playground": "Taman bermain",
}

# Overpass mirrors, tried in order. Same list as build-faskes.py: the first
# two answered reliably while overpass-api.de / kumi / private.coffee were
# returning 5xx (Oct 2026).
ENDPOINTS = (
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)


# --- polygon helpers, same logic as build-faskes.py / build-schools ---------

def _rings():
    out = []
    for f in DEPOK["features"]:
        name = (f.get("properties") or {}).get("village", "")
        coords = (f.get("geometry") or {}).get("coordinates") or []
        for poly in coords:
            if poly:
                out.append((name, poly[0]))
    return out


RINGS = _rings()
KEL_TO_KEC = {}
for f in DEPOK["features"]:
    p = f.get("properties") or {}
    KEL_TO_KEC[(p.get("village") or "").strip().lower()] = (
        p.get("district") or p.get("kecamatan") or "").strip()


def village_of(lon: float, lat: float) -> str | None:
    for name, ring in RINGS:
        inside = False
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
            if (y1 > lat) != (y2 > lat):
                if lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
                    inside = not inside
        if inside:
            return name
    return None


def bbox_of_depok(margin: float = 0.02):
    lons = [p[0] for _, ring in RINGS for p in ring]
    lats = [p[1] for _, ring in RINGS for p in ring]
    return (min(lats) - margin, min(lons) - margin,
            max(lats) + margin, max(lons) + margin)


# --- fetch ------------------------------------------------------------------

def _overpass(query: str, tries: int = 2):
    for attempt in range(tries):
        for ep in ENDPOINTS:
            try:
                body = urllib.parse.urlencode({"data": query}).encode()
                req = urllib.request.Request(
                    ep, data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded",
                             "User-Agent": "depok-map/1.0 (poi layer build)"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.loads(r.read())
                sys.stderr.write(f"  overpass via {ep.split('/')[2]}: "
                                 f"{len(data.get('elements', []))} elements\n")
                return data
            except Exception as exc:
                sys.stderr.write(f"  {ep.split('/')[2]}: {str(exc)[:80]}\n")
        time.sleep(6 * (attempt + 1))
    return None


def fetch_poi():
    """Raw Overpass elements, cached for a day; POI_REFRESH=1 forces."""
    if CACHE.exists() and os.environ.get("POI_REFRESH") != "1":
        age = time.time() - CACHE.stat().st_mtime
        if age < 86400:
            sys.stderr.write(f"using cached overpass result ({CACHE})\n")
            return json.loads(CACHE.read_text())

    s, w, n, e = bbox_of_depok()
    parts = "\n".join(
        f'  nwr["{k}"="{v}"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});'
        for (k, v) in TAGS)
    query = f"[out:json][timeout:120];\n(\n{parts}\n);\nout center tags;"
    data = _overpass(query)
    if data is None:
        if CACHE.exists():
            sys.stderr.write("all mirrors failed; reusing stale cache\n")
            return json.loads(CACHE.read_text())
        sys.exit("overpass unreachable and no cache to fall back on")
    CACHE.write_text(json.dumps(data))
    sys.stderr.write(f"  cached raw result -> {CACHE}\n")
    return data


# --- build ------------------------------------------------------------------

def norm(s) -> str:
    return (s or "").strip()


def kind_row(tags: dict):
    """(category, kind, label, tag_str) for the first matching filter."""
    for (k, v), (cat, kind, label) in TAGS.items():
        if norm(tags.get(k)) == v:
            return cat, kind, label, f"{k}={v}"
    return None


def main() -> None:
    data = fetch_poi()

    kept, stats, skipped_out = [], Counter(), 0
    seen = set()
    for el in data.get("elements", []):
        t = el.get("type")
        eid = f"{t}/{el.get('id')}"
        if eid in seen:
            continue
        seen.add(eid)
        tags = el.get("tags") or {}
        hit = kind_row(tags)
        if hit is None:
            continue
        category, kind, label, tag_str = hit

        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            stats["tanpa koordinat"] += 1
            continue

        name = norm(tags.get("name"))
        if not name:
            stats["tanpa nama (dibuang)"] += 1
            continue

        religion = norm(tags.get("religion")).lower()
        if kind == "place_of_worship" and religion in RELIGION_LABEL:
            label = RELIGION_LABEL[religion]

        village = village_of(lon, lat)
        if not village:
            skipped_out += 1
            continue

        street = norm(tags.get("addr:street"))
        number = norm(tags.get("addr:housenumber"))
        address = " ".join(x for x in [street, number] if x)
        # `tag` + `subkind` keep the raw OSM tag for the detail card; `kind`
        # stays the atlas slug so curation and the atlas speak one language.
        if kind == "fasilitas_olahraga":
            subkind = norm(tags.get("leisure"))
        elif kind == "perdagangan":
            subkind = norm(tags.get("shop"))
        elif kind == "kuburan":
            subkind = "cemetery"
        else:
            subkind = ""
        if kind == "perdagangan":
            badge = SHOP_BADGE.get(subkind, "Toko")
        elif kind == "fasilitas_olahraga":
            badge = LEISURE_BADGE.get(subkind, label)
        else:
            badge = label
        row = {
            "key": eid.replace("/", "-"),
            "osm": eid,
            "name": name,
            "kind": kind,
            "label": label,
            "badge": badge,
            "category": category,
            "tag": tag_str,
            "subkind": subkind,
            "religion": religion if kind == "place_of_worship" else "",
            "religion_name": RELIGION_NAME.get(religion, "") if kind == "place_of_worship" else "",
            "denomination": norm(tags.get("denomination")) if kind == "place_of_worship" else "",
            "operator": norm(tags.get("operator")),
            "address": address,
            "phone": norm(tags.get("phone") or tags.get("contact:phone")),
            "opening_hours": norm(tags.get("opening_hours")),
            "website": norm(tags.get("website") or tags.get("contact:website")),
            "lat": round(float(lat), 7),
            "lon": round(float(lon), 7),
            "village": village,
            "district": KEL_TO_KEC.get(village.strip().lower(), ""),
        }
        kept.append(row)

    # Collapse node/way duplicates of the same place (same kind + name within
    # 120m). Keep the richer record: the one carrying hours, a phone or an
    # address is the better POI.
    import math

    def _dist_m(a, b):
        kx = 111320 * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
        return math.hypot((a["lat"] - b["lat"]) * 110540, (a["lon"] - b["lon"]) * kx)

    groups = {}
    for r in kept:
        groups.setdefault((r["kind"], r["name"].casefold()), []).append(r)
    rows, merged = [], 0
    for rs in groups.values():
        if len(rs) == 1:
            rows.append(rs[0])
            continue
        rs.sort(key=lambda r: sum(1 for v in r.values() if v), reverse=True)
        for r in rs:
            if any(_dist_m(r, k) <= 120 for k in rows
                   if k["kind"] == r["kind"] and k["name"].casefold() == r["name"].casefold()):
                merged += 1
                continue
            rows.append(r)
    kept = rows
    stats["duplikat digabung"] = merged

    by_kind, by_cat = Counter(), Counter()
    for r in kept:
        by_kind[r["kind"]] += 1
        by_cat[r["category"]] += 1

    kept.sort(key=lambda r: (r["category"], r["kind"], r["name"] or "zzz", r["key"]))
    src = ("OpenStreetMap via Overpass API (ODbL) -- titik tema kategori "
           "pendidikan (kampus), kesehatan & darurat (polisi, damkar), tempat "
           "ibadah, perdagangan (pasar, bank, toko), dan ruang terbuka (taman, "
           "fasilitas olahraga, pemakaman). Sekolah tidak ada di sini: baris "
           "sekolah datang dari register Kemendikdasmen. Objek tanpa nama atau "
           "di luar poligon Kota Depok dibuang.")
    payload = {
        "source": src,
        "generated": "scripts/build-poi.py",
        "counts": {"total": len(kept),
                   "by_kind": dict(by_kind),
                   "by_category": dict(by_cat)},
        "list": kept,
    }
    (ROOT / "src" / "poi.js").write_text(
        "// Generated by scripts/build-poi.py -- do not edit.\n"
        "window.DEPOK_POI = " + json.dumps(payload, ensure_ascii=False, indent=1) + ";\n",
        encoding="utf-8")

    print(f"kept {len(kept)} poi")
    for cat in CATEGORY_ORDER:
        print(f"  {cat:<18} {by_cat.get(cat, 0)}")
    for k, v in sorted(by_kind.items()):
        print(f"    {k:<20} {v}")
    if stats:
        print("skipped:", dict(stats))
    if skipped_out:
        print(f"  {skipped_out} di luar poligon Kota Depok")
    print("wrote src/poi.js")


if __name__ == "__main__":
    main()
