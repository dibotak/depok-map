#!/usr/bin/env python3
"""
Fetch Depok's named arterial roads and group them by UU 22/2009 Pasal 25 class.

WHAT THIS IS, AND WHAT IT IS NOT
---------------------------------
UU 22/2009 Pasal 25 classifies Indonesian roads by *administrative authority*,
not by engineering function:

    Kelas I     Jalan Nasional      -_state roads
    Kelas II    Jalan Provinsi       -province
    Kelas III   Jalan Kabupaten/Kota -city/regency   <- Depok sits here
    Kelas IV    Jalan Desa          -village
    Kelas V     Jalan Lingkungan    -neighbourhood

OSM carries **no authority tag**. There is no `road_authority`, no
`jalan_nasional`, nothing in the schema that says who maintains a road. So this
cannot be a lookup -- any static mapping would be a guess dressed as data.

The one signal that IS reliable is `ref`. Indonesian national routes carry a bare
integer route number (2, 12, 17 here), and a numbered national route is Jalan
Nasional by definition. So:

    ref is a bare integer  ->  Kelas I  (Nasional)   [inferred, high confidence]
    otherwise              ->  Kelas III (Kabupaten/Kota)  [inferred, default]

Kelas II, IV and V are not emitted. They are not "empty" -- nothing here proves
their absence, we simply cannot distinguish a 省-province road from a city road
without the tag. Publishing them as empty would imply we looked and found none,
which would be false. The sidebar omits them and says why.

Perda Kota Depok No. 9 Tahun 2022 (RTRW 2022-2042) sets the spatial plan and
names Jalan Kolektor among its classes, but the Perda is a planning instrument:
it does not tag OSM ways, and its classification is not machine-readable from the
street data. It is cited as context in the UI, not used to assign classes.

Source: OpenStreetMap via Overpass API, ODbL. Attribution is on the map already.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "roads.js"

# Overpass endpoints, tried in order. The main one 504s on large area queries,
# which is why the query is a bbox rather than an area filter.
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Kota Depok bounding box (S,W,N,E). Overpass bbox order is south,west,north,east.
BBOX = "-6.42,106.68,-6.32,106.90"

# Only routes with an administrative identity. `unclassified`/`residential` are
# the local street grid -- hundreds of them, unnamed or trivially named, and they
# are exactly what the user is not asking to toggle between.
HIGHWAY = "^(motorway|trunk|primary|secondary|tertiary)$"

# OSM `class` -> Indonesian engineering function. Distinct from the UU authority
# class above, and shown per road so the two are not confused.
OSM_CLASS_LABEL = {
    "motorway": "Tol",
    "trunk": "Arteri",
    "primary": "Arteri",
    "secondary": "Kolektor",
    "tertiary": "Kolektor",
}

CLASS_ORDER = ["I", "III"]

CLASS_META = {
    "I": {
        "kelas": "I",
        "name": "Jalan Nasional",
        "basis": "ref bernomor (jalur nasional)",
        "confidence": "high",
    },
    "III": {
        "kelas": "III",
        "name": "Jalan Kabupaten/Kota",
        "basis": "tanpa ref; default kotamadya",
        "confidence": "low",
    },
}


def fetch(query, tries=3):
    """POST an Overpass query, trying each endpoint then backing off on 504."""
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(tries):
        for ep in ENDPOINTS:
            try:
                req = urllib.request.Request(
                    ep,
                    data=body,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": "depok-map/1.0 (road class build)",
                    },
                )
                with urllib.request.urlopen(req, timeout=180) as r:
                    payload = r.read()
                sys.stderr.write(f"  ok {ep.split('/')[2]} ({len(payload)} bytes)\n")
                return json.loads(payload)
            except Exception as e:
                last = e
                sys.stderr.write(f"  fail {ep.split('/')[2]}: {e}\n")
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"all Overpass endpoints failed: {last}")


def infer_kelas(ref):
    """UU 22/2009 Pasal 25 class, inferred from the national route number.

    Indonesian national routes are numbered with a bare integer. Anything else
    (a district code like 22.01.729, or empty) is not a national route.
    """
    ref = (ref or "").strip()
    return "I" if ref.isdigit() else "III"


def main():
    query = (
        f'[out:json][timeout:150];'
        f'way["highway"~"{HIGHWAY}"]({BBOX});'
        f"out tags;"
    )
    sys.stderr.write(f"querying Overpass bbox={BBOX}\n")
    data = fetch(query)

    # Collapse the 1012 ways into distinct roads. OSM splits one street into many
    # ways, so counting ways would triple-count every corridor.
    roads = {}
    for el in data.get("elements", []):
        t = el.get("tags", {})
        name = (t.get("name") or "").strip()
        if not name:
            continue  # an unnamed arterial has nothing to list or search for
        ref = (t.get("ref") or "").strip()
        osm_class = t.get("highway", "")
        key = (name, ref)
        r = roads.setdefault(
            key,
            {
                "name": name,
                "ref": ref,
                "osm_class": osm_class,
                "osm_label": OSM_CLASS_LABEL.get(osm_class, osm_class),
                "kelas": infer_kelas(ref),
                "ways": 0,
            },
        )
        r["ways"] += 1
        # A road can be tagged trunk on one way and primary on another. Keep the
        # most significant class seen so the badge is stable.
        rank = {"motorway": 0, "trunk": 1, "primary": 2, "secondary": 3, "tertiary": 4}
        if rank.get(osm_class, 9) < rank.get(r["osm_class"], 9):
            r["osm_class"] = osm_class
            r["osm_label"] = OSM_CLASS_LABEL.get(osm_class, osm_class)

    by_kelas = defaultdict(list)
    for r in roads.values():
        by_kelas[r["kelas"]].append(r)

    payload = {
        "source": "OpenStreetMap via Overpass API (ODbL)",
        "statute": "UU No. 22 Tahun 2009 Pasal 25",
        "spatial_plan": "Perda Kota Depok No. 9 Tahun 2022 (RTRW 2022-2042)",
        "inferred": True,
        "disclaimer": (
            "Kelas jalan diinferensikan dari atribut OSM (ref), bukan tag resmi. "
            "OSM tidak menyimpan kewenangan pengelola jalan. Kelas II, IV, V "
            "tidak ditampilkan karena tidak dapat dibedakan dari data jalan."
        ),
        "classes": [
            {
                **CLASS_META[k],
                "count": len(by_kelas.get(k, [])),
                "roads": sorted(by_kelas.get(k, []), key=lambda r: r["name"]),
            }
            for k in CLASS_ORDER
        ],
    }

    js = "window.DEPOK_ROADS=" + json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + ";\n"
    OUT.write_text(js)

    sys.stderr.write(f"\nwrote {OUT.relative_to(ROOT)}  {len(js) / 1024:.0f} KB\n")
    for c in payload["classes"]:
        sys.stderr.write(f"  Kelas {c['kelas']:4} {c['name']:22} {c['count']:>4} roads\n")
    sys.stderr.write(f"  total {len(roads)} distinct named roads\n")

    if not any(c["count"] for c in payload["classes"]):
        raise SystemExit("ERROR: no roads resolved -- refusing to ship an empty list")


if __name__ == "__main__":
    main()
