#!/usr/bin/env python3
"""
Build the puskesmas list for depok-map -- v2, official-portal primary.

Primary source: the real facility list from
    https://dinkes.depok.go.id/User/portal_puskesmas
The Portal Puskesmas page is plain HTML reachable from outside Indonesia and
groups 37 puskesmas under their 11 kecamatan, each linking its own site
(pkm<name>.depok.go.id). That table is embedded below as PORTAL_ROWS; the
per-facility site URL is carried through to the app (card "Web" fact).

Why not everything comes from Dinkes directly
---------------------------------------------
The interactive facility maps (dsw.depok.go.id/html/puskesmas_lokasi, embedded
in the portal page) and every per-facility pkm*.depok.go.id site sit behind an
F5 WAF that rejects requests from outside Indonesia (curl with full browser
headers, headless Chrome, r.jina.ai all bounce). Coordinates therefore come
from two reachable geo sources, in priority order:

  1. OpenStreetMap -- pins plus the rich tags (address, phone, opening hours,
     the OSM object ref). Matched to portal rows by normalised name
     (case/spacing-insensitive, 'UPTD'/'Kelurahan' prefixes dropped).
  2. Google Maps -- for portal rows OSM does not cover, the official name
     ("UPTD Puskesmas <name> Depok") was searched on google.com/maps and the
     place coordinates read from the result URL; each result was checked to
     land inside the city bbox and title-match the facility. Cached in
     scripts/.cache/puskesmas-gmaps.json with the result title as provenance.
     Unlike OSM these are Google's points, not community-mapped ones -- the
     payload records pin_src so the app can say which is which.

The 24-hour flag comes from the Dinkes schedule published Mar 2025 (11 UPTDs,
one per kecamatan) -- every kecamatan has exactly one in OPEN24, which is a
cheap consistency check.

Geometry policy
---------------
No invented points: a wrong pin on a health facility is worse than a missing
pin. Every portal row should end up with a pin from (1) or (2); a row that
matches neither is emitted without a pin and flagged, never guessed at the
kelurahan centroid. Village comes from point-in-polygon against the repo's own
kelurahan polygons (public/data/depok-kelurahan.json) at app runtime; the
kecamatan comes from the portal itself, which is the authoritative grouping.
"""

import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = "/home/ubuntu/projects/depok-map"
BBOX = (-6.496, 106.705, -6.316, 106.900)  # south, west, north, east
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OSM_CACHE = f"{ROOT}/scripts/.cache/puskesmas-osm.json"
GMAPS_CACHE = f"{ROOT}/scripts/.cache/puskesmas-gmaps.json"
# Overpass is frequently overloaded (504/500 for hours at a stretch). Every
# successful fetch is cached here; when all endpoints fail the build falls
# back to the cache rather than dying, and says it did.

# The official table from https://dinkes.depok.go.id/User/portal_puskesmas
# (scraped 2026-10-12): 37 puskesmas in 11 kecamatan, each with its own site.
# Rows: (kecamatan, facility name, site subdomain).
PORTAL_ROWS = [
    ("Beji", "Beji", "pkmbeji"),
    ("Beji", "Depok Utara", "pkmdepokutara"),
    ("Beji", "Kemiri Muka", "pkmkemirimuka"),
    ("Beji", "Tanah Baru", "pkmtanahbaru"),
    ("Pancoran Mas", "Depok Jaya", "pkmdepokjaya"),
    ("Pancoran Mas", "Mampang", "pkmmampang"),
    ("Pancoran Mas", "Pancoran Mas", "pkmpancoranmas"),
    ("Pancoran Mas", "Rangkapan Jaya Baru", "pkmrangkapanjayabaru"),
    ("Cipayung", "Cipayung", "pkmcipayung"),
    ("Cipayung", "Ratu Jaya", "pkmratujaya"),
    ("Sukmajaya", "Abadijaya", "pkmabadijaya"),
    ("Sukmajaya", "Bhaktijaya", "pkmbhaktijaya"),
    ("Sukmajaya", "Sukmajaya", "pkmsukmajaya"),
    ("Cilodong", "Cilodong", "pkmcilodong"),
    ("Cilodong", "Kalimulya", "pkmkalimulya"),
    ("Cilodong", "Villa Pertiwi", "pkmvillapertiwi"),
    ("Limo", "Limo", "pkmlimo"),
    ("Cinere", "Cinere", "pkmcinere"),
    ("Cimanggis", "Cimanggis", "pkmcimanggis"),
    ("Cimanggis", "Cisalak Pasar", "pkmcisalakpasar"),
    ("Cimanggis", "Harjamukti", "pkmharjamukti"),
    ("Cimanggis", "Mekarsari", "pkmmekarsari"),
    ("Cimanggis", "Pasir Gunung Selatan", "pkmpasirgunungselatan"),
    ("Cimanggis", "Tugu", "pkmtugu"),
    ("Tapos", "Cilangkap", "pkmcilangkap"),
    ("Tapos", "Cimpaeun", "pkmcimpaeun"),
    ("Tapos", "Jati Jajar", "pkmjatijajar"),
    ("Tapos", "Sukamaju Baru", "pkmsukamajubaru"),
    ("Tapos", "Sukatani", "pkmsukatani"),
    ("Tapos", "Tapos", "pkmtapos"),
    ("Sawangan", "Cinangka", "pkmcinangka"),
    ("Sawangan", "Kedaung", "pkmkedaung"),
    ("Sawangan", "Pasir Putih", "pkmpasirputih"),
    ("Sawangan", "Pengasinan", "pkmpengasinan"),
    ("Sawangan", "Sawangan", "pkmsawangan"),
    ("Bojongsari", "Bojongsari", "pkmbojongsari"),
    ("Bojongsari", "Duren Seribu", "pkmdurenseribu"),
]

# Dinkes 24-jam schedule (Mar 2025, via JPNN): 11 UPTDs, one per kecamatan.
OPEN24 = [
    "Beji", "Bojongsari", "Cilodong", "Cimanggis", "Cinere", "Kedaung",
    "Limo", "Pancoran Mas", "Ratu Jaya", "Sukmajaya", "Tapos",
]

# OSM spellings that must be canonicalised before name matching. Only entries
# OSM actually spells differently from the portal; anything else matches via
# the spacing-insensitive norm().
CANON = {
    "UPT Puskesmas Pancoranmas": "UPTD Puskesmas Pancoran Mas",
    "Puskesmas Panmas": "UPTD Puskesmas Pancoran Mas",
    "Puskesmas Pancoranmas": "UPTD Puskesmas Pancoran Mas",
    "Puskesmas Kecamatan Sawangan": "UPTD Puskesmas Sawangan",
    "Puskesmas Abadijaya Depok 2 Timur": "UPTD Puskesmas Abadijaya",
}

# kind: "UPTD" = kecamatan-level main facility; "kelurahan" / "pembantu" =
# satellite facilities. Where OSM does not say, the name does:
# "Puskesmas Kelurahan X" is kelurahan; everything else defaults to UPTD.
# Villa Pertiwi is a puskesmas pembantu, not a kelurahan facility.
KIND_OVERRIDE = {
    "Puskesmas Villa Pertiwi": "pembantu",
}

SOURCE = ("Daftar resmi portal Puskesmas Dinkes Kota Depok (37 puskesmas, "
          "11 kecamatan). Pin dari OpenStreetMap dan Google Maps; jadwal 24 jam "
          "dari Dinkes (Maret 2025).")


def norm(name):
    """Match key: lowercase, alnum only, UPTD/Kelurahan/Puskesmas words gone."""
    words = CANON.get(name, name).lower().replace("-", " ").split()
    keep = [w for w in words if w not in ("uptd", "kelurahan", "puskesmas")]
    return "".join(keep)


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


def district_of(lon, lat, polys, feats):
    """Kecamatan label for a point, from the kelurahan polygons (used only for
    OSM-only extras the portal does not list)."""
    for coords, f in zip(polys, feats):
        for poly in coords:
            for ring in poly:
                hit = False
                for i in range(len(ring) - 1):
                    x1, y1 = ring[i][0], ring[i][1]
                    x2, y2 = ring[i + 1][0], ring[i + 1][1]
                    if (y1 > lat) != (y2 > lat) and \
                            lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
                        hit = not hit
                if hit:
                    return f["properties"]["district"]
    return ""


def fetch_overpass():
    q = ('[out:json][timeout:70];'
         f'nwr["name"~"Puskesmas",i]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});'
         'out center;')
    import os
    for url in OVERPASS:
        try:
            req = urllib.request.Request(
                url, data=urllib.parse.urlencode({"data": q}).encode(),
                headers={"User-Agent": "depok-map-builder/1.0 (github.com/dibotak/depok-map)"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.load(r)
            os.makedirs(os.path.dirname(OSM_CACHE), exist_ok=True)
            with open(OSM_CACHE, "w") as f:
                json.dump(data, f)
            print(f"  overpass fresh from {url}")
            return data
        except Exception as e:
            print(f"  {url} failed: {e}", file=sys.stderr)
    try:
        print(f"  all endpoints failed; using cache {OSM_CACHE}", file=sys.stderr)
        return json.load(open(OSM_CACHE))
    except Exception as e:
        raise SystemExit(f"all Overpass endpoints failed and no cache: {e}")


def osm_pins():
    """(norm_key -> {lat, lon, osm, tags}) from the Overpass result, keeping
    only nodes/ways that actually sit inside the city's kelurahan polygons.
    Same-canonical-name hits collapse to the richest-tagged record."""
    data = fetch_overpass()
    polys = load_polygons()
    pins = {}

    def richness(t):
        return sum(1 for k in ("addr:street", "addr:housenumber", "addr:place",
                               "phone", "contact:phone", "opening_hours",
                               "website", "contact:website") if t.get(k))

    for e in data.get("elements", []):
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
        oid = f"{'node' if e['type'] == 'node' else 'way'}/{e['id']}"
        key = norm(name)
        if key in pins and richness(t) <= richness(pins[key]["tags"]):
            continue
        pins[key] = {"lat": lat, "lon": lon, "osm": oid, "tags": t}
    return pins


def gmaps_pins():
    """(norm_key -> {lat, lon, title}) from the cached Google Maps lookups.
    The cache is produced by searching google.com/maps for each portal row the
    OSM pass does not cover; see the module docstring for the method."""
    try:
        data = json.load(open(GMAPS_CACHE))
    except FileNotFoundError:
        print("  no gmaps cache -- rows without OSM pins will have no pin",
              file=sys.stderr)
        return {}
    out = {}
    for name, v in data.items():
        if v.get("ok") and v.get("lat") and v.get("lon"):
            out[norm(name)] = {"lat": float(v["lat"]), "lon": float(v["lon"]),
                               "title": v.get("title", "")}
    return out


def base_record(key, name, kind, kec, sub):
    return {
        "key": key, "name": name, "kind": kind, "district": kec,
        "address": "", "phone": "", "opening_hours": "",
        "website": f"https://{sub}.depok.go.id/" if sub else "",
        "lat": None, "lon": None, "osm": "", "pin_src": None,
        "open24": key in {norm(k) for k in OPEN24},
    }


def main():
    polys = load_polygons()
    gj = json.load(open(f"{ROOT}/public/data/depok-kelurahan.json"))
    feats = gj["features"]
    pins = osm_pins()
    gmaps = gmaps_pins()
    records = []
    for kec, name, sub in PORTAL_ROWS:
        key = norm(name)
        osm = pins.pop(key, None)
        rec = base_record(key, name, "UPTD", kec, sub)
        if osm:
            t = osm["tags"]
            oname = CANON.get(t.get("name", name), t.get("name", name))
            rec["kind"] = (KIND_OVERRIDE.get(oname)
                           or ("kelurahan" if "Kelurahan" in oname else "UPTD"))
            rec["name"] = (oname if oname.startswith("UPTD") else "UPTD " + oname) \
                if rec["kind"] == "UPTD" else oname
            rec["address"] = ", ".join([x for x in [t.get("addr:housenumber", ""),
                                                    t.get("addr:street", ""),
                                                    t.get("addr:place", "")] if x])
            rec["phone"] = t.get("phone") or t.get("contact:phone") or ""
            rec["opening_hours"] = t.get("opening_hours", "")
            rec["website"] = t.get("website") or t.get("contact:website") \
                or f"https://{sub}.depok.go.id/"
            rec["lat"], rec["lon"], rec["osm"] = osm["lat"], osm["lon"], osm["osm"]
            rec["pin_src"] = "osm"
        elif key in gmaps:
            gm = gmaps[key]
            rec["name"] = f"UPTD Puskesmas {name}"
            rec["lat"], rec["lon"], rec["pin_src"] = gm["lat"], gm["lon"], "gmaps"
        else:
            print(f"  !! portal row without any pin: {kec} / {name}", file=sys.stderr)
        records.append(rec)

    # OSM pins that matched no portal row: real facilities the portal does not
    # list (pembantu branches). Keep them, district from point-in-polygon.
    for key, osm in sorted(pins.items()):
        t = osm["tags"]
        oname = t.get("name", key)
        rec = base_record(key, oname, KIND_OVERRIDE.get(oname, "pembantu"), "", "")
        rec["address"] = ", ".join([x for x in [t.get("addr:housenumber", ""),
                                                t.get("addr:street", ""),
                                                t.get("addr:place", "")] if x])
        rec["phone"] = t.get("phone") or t.get("contact:phone") or ""
        rec["opening_hours"] = t.get("opening_hours", "")
        rec["website"] = t.get("website") or t.get("contact:website") or ""
        rec["lat"], rec["lon"], rec["osm"] = osm["lat"], osm["lon"], osm["osm"]
        rec["pin_src"] = "osm"
        rec["district"] = district_of(osm["lon"], osm["lat"], polys, feats)
        records.append(rec)
        print(f"  + OSM-only facility kept: {oname} ({rec['district']})",
              file=sys.stderr)

    records.sort(key=lambda p: (p["district"] or "zzz", p["name"]))
    placed = sum(1 for r in records if r["pin_src"])
    payload = {
        "source": SOURCE,
        "generated": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {"total": len(records), "placed": placed,
                   "open24": sum(1 for r in records if r["open24"])},
        "list": records,
    }

    # Kurasi Studio Atlas (scripts/data/curated.json) ditumpuk di sini.
    import curated
    payload, cerrs, mark, _rep = curated.hook("puskesmas", payload, root=Path(ROOT))
    if cerrs:
        for e in cerrs:
            print(f"  !! {e}", file=sys.stderr)
        raise SystemExit("kurasi puskesmas tidak bisa diterapkan — sinkronkan lewat "
                         "Studio Atlas (tab Ekspor) lalu jalankan ulang")

    out = f"{ROOT}/src/puskesmas.js"
    with open(out, "w", encoding="utf-8") as f:
        f.write(curated.encode("puskesmas", payload, mark))
    osm_n = sum(1 for r in payload["list"] if r["pin_src"] == "osm")
    gm_n = sum(1 for r in payload["list"] if r["pin_src"] == "gmaps")
    none_n = len(payload["list"]) - osm_n - gm_n
    print(f"records: {len(payload['list'])} (osm {osm_n}, gmaps {gm_n}, no pin {none_n}, "
          f"open24 {payload['counts']['open24']}) -> {out}")
    for r in payload["list"]:
        pin = f"{r['lon']:.5f},{r['lat']:.5f} ({r['pin_src']})" if r["pin_src"] else "-- no pin"
        print(f"  {r['name'][:44]:46} {r['kind']:9} {pin} 24h={r['open24']}")


if __name__ == "__main__":
    main()