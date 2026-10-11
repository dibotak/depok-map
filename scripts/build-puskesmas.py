#!/usr/bin/env python3
"""
Build the puskesmas list for depok-map.

Why this exists
---------------
The user asked for "all puskesmas data across Depok". The official register
(dsw.depok.go.id, the Dinkes facility maps embedded in dinkes.depok.go.id) sits
behind an F5 WAF that rejects every request from outside Indonesia -- curl,
headless Chrome, r.jina.ai and opendata.jabarprov.go.id all bounce. This script
therefore builds the list from the two sources that ARE reachable, and the
sidebar note says exactly how the list was assembled:

  1. OpenStreetMap -- pins, addresses, phones. Everything named *Puskesmas*
     inside the city's own kelurahan polygons (public/data/depok-kelurahan.json,
     the same polygons the app maps). OSM covers the low kecamatan 11
     UPTDs only partially: 11 facilities carry a pin.
  2. Dinkes Kota Depok, via JPNN (Mar 2025) -- the UPTD mains that have no OSM
     pin are still real, so they are listed without a pin rather than dropped,
     exactly like the schools menu treats the register-only schools.

The 24-hour flag comes from the same Dinkes list (11 UPTDs open 24 jam in 2025);
opening_hours from OSM is carried too when present.

Geometry policy
---------------
Same as the schools builder: no fallback that invents a point. A wrong pin on a
health facility is worse than no pin. Village/district come from
point-in-polygon against the repo's own kelurahan polygons at runtime, so the
payload only needs to carry what OSM said.

Dedup policy
------------
OSM spells one facility several ways ("Puskesmas Panmas", "Puskesmas
Pancoranmas", "UPT Puskesmas Pancoranmas" are the same node cluster). Canonical
names are mapped below; same-canonical hits within ~200 m collapse to the
record with the richest tag set. Street names like "Jalan Puskesmas" are
dropped by name prefix, never by location.
"""

import json
import math
import sys
import urllib.parse
import urllib.request

ROOT = "/home/ubuntu/projects/depok-map"
BBOX = (-6.496, 106.705, -6.316, 106.900)  # south, west, north, east
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
CACHE = f"{ROOT}/scripts/.cache/puskesmas-osm.json"
# Overpass is frequently overloaded (504/500 for hours at a stretch). Every
# successful fetch is cached here; when all endpoints fail the build falls
# back to the cache rather than dying, and says it did.

# OSM name -> official-style name. Only entries OSM actually spells differently
# from the register; anything not listed here keeps its OSM name verbatim.
CANON = {
    "UPT Puskesmas Pancoranmas": "UPTD Puskesmas Pancoran Mas",
    "Puskesmas Panmas": "UPTD Puskesmas Pancoran Mas",
    "Puskesmas Pancoranmas": "UPTD Puskesmas Pancoran Mas",
    "Puskesmas Kecamatan Sawangan": "UPTD Puskesmas Sawangan",
    "Puskesmas Abadijaya Depok 2 Timur": "UPTD Puskesmas Abadijaya",
    "Puskesmas Cilodong": "UPTD Puskesmas Cilodong",
    "Puskesmas Sukmajaya": "UPTD Puskesmas Sukmajaya",
    "Puskesmas Beji": "UPTD Puskesmas Beji",
    "Puskesmas Tapos": "UPTD Puskesmas Tapos",
    "Puskesmas Sukatani": "UPTD Puskesmas Sukatani",
}

# kind: "UPTD" = kecamatan-level main facility; "kelurahan" / "pembantu" =
# satellite facilities. Where OSM does not say, the canonical name does:
# "Puskesmas Kelurahan X" is kelurahan; everything else defaults to UPTD.
# Villa Pertiwi is a puskesmas pembantu, not a kelurahan facility.
KIND_OVERRIDE = {
    "Puskesmas Villa Pertiwi": "pembantu",
}

# The 11 UPTDs the Dinkes Kota Depok schedule (via JPNN, March 2025) lists as
# open 24 hours. Used both to flag open24 and to add the register-only mains.
OPEN24 = ["Cilodong", "Sukmajaya", "Cimanggis", "Tapos", "Bojongsari", "Cinere",
          "Beji", "Pancoran Mas", "Ratu Jaya", "Kedaung", "Limo"]

# Register-only UPTDs (no OSM pin found): official name -> kecamatan.
# Ratu Jaya sits in Cilodong, Kedaung in Pancoran Mas; the rest are kecamatan
# mains named after their kecamatan.
NAMES_ONLY = {
    "UPTD Puskesmas Cimanggis": "Cimanggis",
    "UPTD Puskesmas Bojongsari": "Bojongsari",
    "UPTD Puskesmas Cinere": "Cinere",
    "UPTD Puskesmas Limo": "Limo",
    "UPTD Puskesmas Ratu Jaya": "Cilodong",
    "UPTD Puskesmas Kedaung": "Pancoran Mas",
}

SOURCE_NOTE = ("Dari OpenStreetMap untuk pin dan alamat; UPTD tanpa pin dan "
               "daftar layanan 24 jam dari Dinkes Kota Depok (via JPNN, Maret 2025).")


def fetch_overpass():
    q = ('[out:json][timeout:70];'
         f'nwr["name"~"Puskesmas",i]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});'
         'out center;')
    for url in OVERPASS:
        try:
            req = urllib.request.Request(
                url, data=urllib.parse.urlencode({"data": q}).encode(),
                headers={"User-Agent": "depok-map-builder/1.0 (github.com/dibotak/depok-map)"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.load(r)
            import os
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE, "w") as f:
                json.dump(data, f)
            print(f"  fetched fresh from {url}")
            return data
        except Exception as e:
            print(f"  {url} failed: {e}", file=sys.stderr)
    try:
        print(f"  all endpoints failed; using cache {CACHE}", file=sys.stderr)
        return json.load(open(CACHE))
    except Exception as e:
        raise SystemExit(f"all Overpass endpoints failed and no cache: {e}")


def load_polygons():
    """Return one MultiPolygon-style coordinates list per kelurahan:
    [polygon][ring][lon, lat]. Polygon features are wrapped so every entry
    has the same shape."""
    gj = json.load(open(f"{ROOT}/public/data/depok-kelurahan.json"))
    out = []
    for f in gj["features"]:
        g = f["geometry"]
        if g["type"] == "MultiPolygon":
            out.append(g["coordinates"])
        elif g["type"] == "Polygon":
            out.append([g["coordinates"]])
    return out


def inside(lon, lat, polys):
    """Even-odd point-in-polygon over the load_polygons() shape:
    polys[i] = [polygon][ring][point]. Rings are closed (first point == last)
    in this dataset, so len(ring)-1 segments cover every edge."""
    for coords in polys:        # one kelurahan: list of polygons
        for poly in coords:     # one polygon: list of rings
            for ring in poly:   # one ring: list of [lon, lat]
                hit = False
                for i in range(len(ring) - 1):
                    x1, y1 = ring[i][0], ring[i][1]
                    x2, y2 = ring[i + 1][0], ring[i + 1][1]
                    if (y1 > lat) != (y2 > lat) and \
                            lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
                        hit = not hit
                if hit:
                    return True
    return False


def metres(lon1, lat1, lon2, lat2):
    dy = (lat2 - lat1) * 111320.0
    dx = (lon2 - lon1) * 111320.0 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)


def main():
    data = fetch_overpass()
    els = data.get("elements", [])
    polys = load_polygons()

    hits = []
    for e in els:
        t = e.get("tags", {})
        name = (t.get("name") or "").strip()
        if not name or "puskesmas" not in name.lower():
            continue
        if name.lower().startswith("jalan"):
            continue  # street named after a puskesmas, not the facility
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None or lon is None or not inside(lon, lat, polys):
            continue
        hits.append({
            "osm_type": e["type"], "osm_id": e["id"],
            "name": name, "lon": lon, "lat": lat, "tags": t,
        })
    print(f"osm hits inside city polygons: {len(hits)}")

    # Collapse duplicates: same canonical name within 200 m -> richest record.
    def canon(h):
        return CANON.get(h["name"], h["name"])

    def richness(h):
        t = h["tags"]
        return sum(1 for k in ("addr:street", "addr:housenumber", "addr:place",
                               "phone", "contact:phone", "opening_hours",
                               "website", "contact:website",
                               "addr:postcode") if t.get(k)) \
            + (2 if h["osm_type"] == "node" else 0)

    kept = []
    for h in sorted(hits, key=richness, reverse=True):
        c = canon(h)
        dup = next((k for k in kept if canon(k) == c
                    and metres(k["lon"], k["lat"], h["lon"], h["lat"]) < 200), None)
        if dup is None:
            kept.append(h)
        elif richness(h) > richness(dup):
            kept[kept.index(dup)] = h
        else:
            print(f"  dup dropped: {h['name']} ({h['osm_type']}/{h['osm_id']})")

    records = []
    for h in kept:
        t = h["tags"]
        name = canon(h)
        phone = t.get("phone") or t.get("contact:phone") or ""
        website = t.get("website") or t.get("contact:website") or ""
        addr_bits = [x for x in [t.get("addr:housenumber", ""), t.get("addr:street", ""),
                                 t.get("addr:place", "")] if x]
        records.append({
            "key": str(h["osm_id"]),
            "name": name,
            "kind": KIND_OVERRIDE.get(name, "kelurahan" if "Kelurahan" in name else "UPTD"),
            "address": ", ".join(addr_bits),
            "phone": phone,
            "website": website,
            "opening_hours": t.get("opening_hours", ""),
            "open24": any(k.upper() in name.upper() for k in OPEN24)
                      or t.get("opening_hours", "").lower() in ("24/7",),
            "lon": h["lon"], "lat": h["lat"],
            "osm": f"{h['osm_type']}/{h['osm_id']}",
            "note": "",
        })

    # Register-only mains: listed, pinned nowhere, district known from the
    # official structure. A facility without a pin is still a facility.
    for name, district in NAMES_ONLY.items():
        if any(r["name"] == name for r in records):
            continue
        records.append({
            "key": f"reg-{name.split()[-1].lower()}",
            "name": name, "kind": "UPTD", "address": "", "phone": "",
            "website": "", "opening_hours": "",
            "open24": name.split("Puskesmas ")[-1] in OPEN24,
            "lon": None, "lat": None, "osm": "", "note": "",
            "district": district,
        })

    records.sort(key=lambda r: r["name"].lower())
    placed = sum(1 for r in records if r["lon"] is not None)
    payload = {
        "source": SOURCE_NOTE,
        "generated": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {"total": len(records), "placed": placed,
                   "open24": sum(1 for r in records if r["open24"])},
        "list": records,
    }

    out = f"{ROOT}/src/puskesmas.js"
    with open(out, "w") as f:
        f.write("/* Generated by scripts/build-puskesmas.py -- do not edit by hand.\n"
                "   Rebuild with: python3 scripts/build-puskesmas.py */\n"
                "window.DEPOK_PUSKESMAS = ")
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write(";\n")
    print(f"records: {len(records)} (placed {placed}, open24 "
          f"{payload['counts']['open24']}) -> {out}")
    for r in records:
        pin = f"{r['lon']:.5f},{r['lat']:.5f}" if r["lon"] is not None else "-- no pin"
        print(f"  {r['name'][:44]:46} {r['kind']:9} {pin} 24h={r['open24']}")


if __name__ == "__main__":
    main()