#!/usr/bin/env python3
"""Turn OpenStreetMap health facilities into src/faskes.js.

WHAT THIS LAYER IS
------------------
The map already has an official puskesmas layer (scripts/build-puskesmas.py,
Dinkes Kota Depok register). This layer is the *complement*: the rest of the
city's health facilities as mapped in OpenStreetMap -- hospitals, clinics,
doctors' and dentists' practices, and pharmacies. It is deliberately casual:
ODbL data, print-safe, rebuilt from the Overpass API at any time.

Sources that were tried and failed before settling on OSM:
  * sirs.kemkes.go.id (SIRS) -- login wall, not scrapable.
  * data.depok.go.id open data portal -- domain does not resolve (Oct 2026).
  * depok.go.id subdomains -- F5 WAF blocks non-browser clients.
So OSM it is, with the caveat recorded on the layer's source string.

RULES
-----
* One row per OSM object (node/way/relation), keyed by its OSM ref. The ref is
  kept so any pin can be checked against the map data.
* Objects whose name says "Puskesmas" (or the "Puskemas" typo) or "Pustu" are
  dropped: they belong to the official Dinkes layer, and showing them twice
  would double the pins. So are the exact OSM refs the puskesmas build used
  for its own pins.
* Unnamed objects are dropped -- a pin that cannot be labelled is noise on a
  printed map. So are objects whose name is plainly not a health facility
  ("School", "Sekolah"): those are mapper errors on the amenity tag.
* A hospital mistagged in OSM but named "Klinik ..." is listed as a clinic;
  the name the owner uses beats a stray tag. The amenity tag stays in the row
  for transparency.
* Duplicates (same kind + name within 120m, e.g. a building outline and its
  POI node) collapse to the richer record. Chain outlets (separate Kimia
  Farma stores) are farther apart and all stay.
* A point outside the city polygons is dropped (a clinic 500m past the border
  is another city's clinic); the count is printed, not silently swallowed.
* No coordinates are invented, ever. If OSM does not know a facility, it is
  not here.

Output: src/faskes.js  (window.DEPOK_FASKES)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPOK = json.loads((ROOT / "public" / "data" / "depok-kelurahan.json").read_text())
CACHE = Path("/tmp/depok-faskes-cache.json")

AMENITIES = ("hospital", "clinic", "doctors", "dentist", "pharmacy")
KINDS = {
    "hospital": "Rumah sakit",
    "clinic": "Klinik",
    "doctors": "Praktik dokter",
    "dentist": "Dokter gigi",
    "pharmacy": "Apotek",
}

# Overpass mirrors, tried in order. The first two answered reliably while
# overpass-api.de / kumi / private.coffee were returning 5xx (Oct 2026).
ENDPOINTS = (
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)


# --- polygon helpers, same logic as build-schools-sekolahkita.py ------------

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
                             "User-Agent": "depok-map/1.0 (faskes layer build)"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.loads(r.read())
                sys.stderr.write(f"  overpass via {ep.split('/')[2]}: "
                                 f"{len(data.get('elements', []))} elements\n")
                return data
            except Exception as exc:
                sys.stderr.write(f"  {ep.split('/')[2]}: {str(exc)[:80]}\n")
        time.sleep(6 * (attempt + 1))
    return None


def fetch_faskes():
    """Raw Overpass elements, cached for a day; FASKES_REFRESH=1 forces."""
    if CACHE.exists() and os.environ.get("FASKES_REFRESH") != "1":
        age = time.time() - CACHE.stat().st_mtime
        if age < 86400:
            sys.stderr.write(f"using cached overpass result ({CACHE})\n")
            return json.loads(CACHE.read_text())

    s, w, n, e = bbox_of_depok()
    parts = "\n".join(
        f'  nwr["amenity"="{a}"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});' for a in AMENITIES)
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

def puskes_refs_and_names():
    raw = (ROOT / "src" / "puskesmas.js").read_text()
    body = json.loads(re.search(r"window\.DEPOK_PUSKESMAS\s*=\s*(\{.*\});?\s*$",
                                raw, re.S).group(1))
    refs = {r["osm"] for r in body["list"] if r.get("osm")}
    return refs, body["list"]


def norm(s) -> str:
    s = (s or "").strip()
    return s


def main() -> None:
    data = fetch_faskes()
    puskes_osm, puskes_list = puskes_refs_and_names()
    puskes_names = {norm(r.get("name")).lower() for r in puskes_list}

    kept, stats, by_kind, skipped_out = [], Counter(), Counter(), 0
    seen = set()
    for el in data.get("elements", []):
        t = el.get("type")
        eid = f"{t}/{el.get('id')}"
        if eid in seen:
            continue
        seen.add(eid)
        tags = el.get("tags") or {}
        amenity = tags.get("amenity")
        if amenity not in KINDS:
            continue

        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            stats["tanpa koordinat"] += 1
            continue

        name = norm(tags.get("name"))
        low = name.lower()
        # The official register keeps its own layer; do not pin a second time.
        # "Puskemas" is a common OSM typo for the same institution.
        if eid in puskes_osm or re.search(r"puskemas|puskesmas|pustu", low) or \
                (low and low in puskes_names):
            stats["puskesmas (dilewati)"] += 1
            continue
        # A pin nobody can label (or a "School" tagged as a hospital) is noise
        # on a printed map; both are mapper gaps, printed as counts.
        if not name:
            stats["tanpa nama (dibuang)"] += 1
            continue
        if re.match(r"^(school|sekolah)$", low):
            stats["nama bukan faskes (dibuang)"] += 1
            continue

        kind = KINDS[amenity]
        # A name that says "Klinik ..." beats a stray tag (hospital, doctors,
        # pharmacy). OSM tags each of those variants somewhere in the city.
        if low.startswith("klinik") and kind != "Klinik":
            stats["nama 'Klinik...' -> Klinik"] += 1
            kind = "Klinik"

        village = village_of(lon, lat)
        if not village:
            skipped_out += 1
            continue

        street = norm(tags.get("addr:street"))
        number = norm(tags.get("addr:housenumber"))
        address = " ".join(x for x in [street, number] if x)
        row = {
            "key": eid.replace("/", "-"),
            "osm": eid,
            "name": name,
            "kind": kind,
            "amenity": amenity,
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

    # Collapse node/way duplicates of the same facility (same kind + name
    # within 120m). Keep the richer record: the one carrying hours, a phone or
    # an address is the better POI.
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
    for r in kept:
        by_kind[r["kind"]] += 1

    kept.sort(key=lambda r: (r["kind"], r["name"] or "zzz", r["key"]))
    src = ("OpenStreetMap via Overpass API (ODbL) -- fasilitas kesehatan di luar "
           "daftar puskesmas Dinkes: rumah sakit, klinik, praktik dokter/dokter "
           "gigi, dan apotek. Objek bernama Puskesmas/Pustu serta pin yang sudah "
           "dipakai lapisan puskesmas tidak diulang. Objek tanpa koordinat atau "
           "di luar poligon Kota Depok dibuang.")
    payload = {
        "source": src,
        "generated": "scripts/build-faskes.py",
        "counts": {"total": len(kept), "by_kind": dict(by_kind)},
        "list": kept,
    }
    # Kurasi Studio Atlas (scripts/data/curated.json) ditumpuk di sini, supaya
    # rebuild penuh lewat Overpass tidak menghapus suntingan kurator.
    import curated
    payload, cerrs, mark, _rep = curated.hook("faskes", payload, root=ROOT)
    if cerrs:
        for e in cerrs:
            sys.stderr.write(f"  !! {e}\n")
        sys.exit("kurasi faskes tidak bisa diterapkan — sinkronkan lewat Studio "
                 "Atlas (tab Ekspor) lalu jalankan ulang")
    (ROOT / "src" / "faskes.js").write_text(
        curated.encode("faskes", payload, mark), encoding="utf-8")

    print(f"kept {len(kept)} faskes")
    for k, v in sorted(by_kind.items()):
        print(f"  {k:<16} {v}")
    if stats:
        print("skipped:", dict(stats))
    if skipped_out:
        print(f"  {skipped_out} di luar poligon Kota Depok")
    print("wrote src/faskes.js")


if __name__ == "__main__":
    main()
