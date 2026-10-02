#!/usr/bin/env python3
"""

# Overpass/road cache lives under the repo-adjacent .cache dir, not /tmp: a /tmp
# wipe mid-build loses the fetch and the run dies with nothing on disk to resume
# from.
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"

Build the public-transport layer from OSM route relations, filtered to Kota Depok.

WHY OSM RELATIONS AND NOT A HAND-TYPED ROUTE LIST
--------------------------------------------------
The roads build infers geometry by matching Perda names to OSM highway ways. Bus
routes cannot be done that way: nobody has mapped "Trayek D04" as an OSM
highway, and matching it to a street would be a guess. But OSM *does* carry
`type=route` relations with full geometry for most of these services -- the KRL
line, the LRT Cibubur line, Biskita/Teman Bus K1, Mikrotrans D10A, the
Transjakarta Metrotrans and Royaltrans routes, and the angkot feeder routes.
That is surveyor's geometry, not a guess, so it is what gets drawn.

WHAT IS FILTERED, AND HOW
--------------------------
The bbox deliberately covers more than Depok (feeder routes start in Parung,
Cibubur, Tanjung Barat). So every relation is clipped against the actual
Kota Depok administrative boundary taken from `public/data/depok-kelurahan.json`
-- the same source the map already draws. A relation is kept only if its
geometry intersects the city. "Passes through Depok" is then a measured fact,
not an assertion.

Routes whose relation exists but whose geometry never reaches the city are
dropped with a printed note, rather than silently shown as serving Depok.

WHAT IS AND IS NOT CLAIMED ABOUT SERVICE STATUS
-----------------------------------------------
OSM records the *alignment*. It does NOT record whether a service is running
today: a route tagged in 2022 and deleted from the streets stays in OSM
forever. So this script never claims a route "operates in 2026" on OSM's word.

It emits `status` from an explicit, cited table in STATUS below, each entry
carrying the date and source it was verified from. Anything OSM knows that the
table has not been updated for is emitted with `status: "unverified"` and the
UI says so. An operator can therefore see both the geometry and how confident
we are about the service, instead of a single confident-looking line.

Sources are quoted per entry in `source` and `checked`, and the UI shows them.

Source geometry: OpenStreetMap via Overpass API (ODbL).
City boundary: HDX/BPS cod-ab-idn via JfrAziz/indonesia-district (CC BY-IGO).
"""
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Overpass/road cache lives under a repo-adjacent .cache dir, not /tmp: a /tmp
# wipe mid-build loses the fetch and the run dies with nothing to resume from.
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "transport.js"
BOUNDARY = ROOT / "public" / "data" / "depok-kelurahan.json"
CACHE = CACHE_DIR

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "depok-map/1.0 (public transport build)"

# Overpass order (south,west,north,east). Wider than Depok so feeder routes that
# start outside the city are still returned and can then be *tested* against the
# boundary rather than assumed away.
TILES = [
    (-6.48, 106.70, -6.39, 106.82),
    (-6.48, 106.82, -6.39, 106.94),
    (-6.39, 106.70, -6.30, 106.82),
    (-6.39, 106.82, -6.30, 106.94),
]

ROUTE_Q = """
[out:json][timeout:300];
relation["type"="route"]["route"~"^(bus|tram|train|light_rail)$"](%s);
out geom;
"""

# ---------------------------------------------------------------------------
# Service status, verified by hand from cited sources. NOT from OSM.
#
# `scope` drives the "dalam kota" split the UI shows:
#   "kota"    -> runs inside Kota Depok (the emphasis of this layer)
#   "lintas"  -> leaves the city; shown as a connecting service
# ---------------------------------------------------------------------------
# Keys are the `ref` values OSM actually carries, which are NOT the human
# service numbers you would say out loud: Biskita K1 is tagged ref="1" and
# Mikrotrans D10A is ref="10A".
STATUS = {
    "1": dict(
        scope="kota",
        status="beroperasi",
        note=(
            "Koridor tunggal bus kota: Terminal Depok (Margonda) ke Stasiun LRT "
            "Harjamukti via Jl. Tole Iskandar, ~34 km dan 44 halte. Pada Januari "
            "2026 nama layanannya menjadi Teman Bus dalam program integrasi "
            "nasional; sebelumnya Biskita Trans Depok. Dipakai gratis sebagai "
            "feeder ke LRT Harjamukti."
        ),
        fare=("Gratis pada 2026-03-15. Tarif berikutnya masih menunggu "
              "keputusan Kemhub — gratis setidaknya hingga April 2026."),
        source="berita.depok.go.id (Teman Bus Lebaran 2026); id.wikipedia.org Trans Depok",
        checked="2026-03-15",
    ),
    "10A": dict(
        scope="kota",
        status="beroperasi",
        note=(
            "Mikrotrans/angkot ber-AC Terminal Depok ke Terminal Jatijajar via "
            "kawasan Grand Depok City. Armada AC berhenti 2 Mei 2026 dan "
            "digantikan angkot listrik dengan trayek yang sama; Dishub mengecek "
            "kesiapan armada di karoseri pada 16 Juli 2026 dan menyatakan "
            "peluncuran tinggal dekat."
        ),
        fare="Rp7.000 umum / Rp3.000 pelajar",
        source="berita.depok.go.id (angkot listrik D10A, 16 Jul 2026)",
        checked="2026-07-16",
    ),
}


# ---------------------------------------------------------------------------
# Overpass
# ---------------------------------------------------------------------------
def overpass(query, label, tries=3):
    last = None
    for attempt in range(tries):
        for ep in ENDPOINTS:
            try:
                body = urllib.parse.urlencode({"data": query}).encode()
                req = urllib.request.Request(
                    ep, data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded",
                             "User-Agent": UA},
                )
                with urllib.request.urlopen(req, timeout=300) as r:
                    data = json.loads(r.read())
                sys.stderr.write("  %s -> %d\n" % (label, len(data.get("elements", []))))
                return data
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write("  %s %s: %s\n" % (label, ep.split("/")[2], exc))
                last = exc
        time.sleep(8 * (attempt + 1))
    sys.stderr.write("  %s GAVE UP: %s\n" % (label, last))
    return None


def fetch_routes():
    """Route relations with full geometry, tiled and cached."""
    CACHE.mkdir(parents=True, exist_ok=True)
    merged = {}
    for t in TILES:
        bbox = ",".join(str(v) for v in t)
        path = CACHE / ("routesgeom-%s.json" % bbox.replace(",", "_").replace(".", ""))
        if path.exists() and time.time() - path.stat().st_mtime < 86400:
            sys.stderr.write("  cached %s\n" % bbox)
            data = json.load(open(path))
        else:
            data = overpass(ROUTE_Q % bbox, "routes %s" % bbox)
            if data is None:
                continue
            json.dump(data, open(path, "w"))
        for e in data.get("elements", []):
            merged[e["id"]] = e
    return {"elements": list(merged.values())}


# ---------------------------------------------------------------------------
# City boundary, for the "does this actually reach Depok" test
# ---------------------------------------------------------------------------
def depok_bbox():
    """Bounding box of Kota Depok from the same boundary file the map draws."""
    feats = json.load(open(BOUNDARY))
    src = feats["features"] if isinstance(feats, dict) else feats
    xs, ys = [], []
    for f in src:
        for ring in _rings(f["geometry"]):
            for x, y in ring:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _rings(geom):
    t = geom["type"]
    if t == "Polygon":
        return geom["coordinates"]
    if t == "MultiPolygon":
        return [r for poly in geom["coordinates"] for r in poly]
    return []


def in_depok(lon, lat):
    return MINX <= lon <= MAXX and MINY <= lat <= MAXY


MINX, MINY, MAXX, MAXY = depok_bbox()


# ---------------------------------------------------------------------------
# Route -> features
# ---------------------------------------------------------------------------
def classify(tags):
    """Map OSM route tags onto our three display classes.

    This is a presentation choice, not a claim about the operator: 'krl' is any
    railway relation, 'lrt' any light rail, and bus routes split on whether the
    operator is the city's own service.
    """
    route = tags.get("route")
    net = (tags.get("network") or "")
    ref = (tags.get("ref") or "").strip()
    if route in ("train",):
        return "krl"
    if route in ("light_rail", "tram"):
        return "lrt"
    if net in ("Trans Depok",):
        return "bus-kota"
    if ref == "D10A":
        return "bus-kota"
    return "bus-lintas"


def segments_of(rel):
    """LineStrings for a route relation, in member order.

    Nodes and platform members are dropped: they carry no geometry and their
    lat/lon would otherwise break the LineString into one-point fragments.
    """
    segs = []
    for m in rel.get("members", []):
        if m.get("type") != "way":
            continue
        g = m.get("geometry") or []
        coords = [[p["lon"], p["lat"]] for p in g if "lon" in p and "lat" in p]
        # Drop duplicate consecutive points; they are common at member joins and
        # add nothing but bytes.
        clean = [coords[0]] if coords else []
        for c in coords[1:]:
            if c != clean[-1]:
                clean.append(c)
        if len(clean) >= 2:
            segs.append(clean)
    return segs


def touches_depok(segs):
    return any(in_depok(c[0], c[1]) for s in segs for c in s)


def station_of(tags):
    """Clean a display name out of OSM's 'Name (kode); other' convention."""
    nm = tags.get("name") or ""
    nm = re.sub(r"\s*\(.*?\)\s*", " ", nm)
    nm = re.sub(r"\s*;.*$", "", nm)
    return nm.strip()


def build_angkot():
    """The intra-city angkot trayek, routed along real streets.

    OSM has no route relations for these, so geometry is synthesized by
    resolving each endpoint to a real OSM node and running Dijkstra over the
    drivable network. That is an estimate of the path, not a surveyed route,
    and the payload says so in `geometry_note`.
    """
    # Put this script's own directory on sys.path FIRST: build-transport.py is
    # run as `python3 scripts/build-transport.py`, so when a sibling module does
    # its own `import transport_routing`, the interpreter's sys.path[0] is
    # already scripts/ -- but only because we added it here, and only if this
    # line runs before the import below.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from transport_endpoints import ENDPOINTS, TRAYEK, TRAYEK_INACTIVE
        import transport_routing as TR
    except ImportError as e:
        sys.stderr.write("\nangkot skipped (deps missing: %s)\n" % e)
        return []

    nodes = load_endpoint_nodes()
    if not nodes:
        sys.stderr.write("\nangkot skipped (no endpoint nodes; run probe-endpoints.py)\n")
        return []

    # Second pass over street names, appended after the place/POI nodes so an
    # exact place match always wins over a street-name approximation.
    road_nodes = nodes_from_roads()
    if road_nodes:
        sys.stderr.write("\nroad-name fallback nodes: %d\n" % len(road_nodes))
    pool = nodes + road_nodes

    resolved, missing = {}, []
    for label, spec in ENDPOINTS.items():
        hit = find_node(pool, spec["match"], spec.get("what"))
        if hit:
            resolved[label] = hit
        else:
            missing.append(label)
    if missing:
        sys.stderr.write("\nangorat endpoints unresolved: %s\n" % ", ".join(missing))

    g = TR.build_graph()
    idx = TR.build_index(g)
    sys.stderr.write("  endpoint index: %d cells\n" % len(idx))

    out, unroutable = [], []
    for t in TRAYEK:
        # find_node returns a dict (name/lat/lon/tags), not a (lat, lon) tuple.
        # Indexing it positionally raises KeyError: 1 and was the crash that
        # aborted the first full build.
        a, b = resolved.get(t["a"]), resolved.get(t["b"])
        segs = []
        if a and b:
            try:
                path = TR.route(g, idx, (a["lon"], a["lat"]), (b["lon"], b["lat"]))
            except Exception as e:  # never let one trayek abort the build
                sys.stderr.write("  trayek %s routing error: %s\n" % (t["ref"], e))
                path = None
            if path:
                segs = [path]
        if not segs:
            unroutable.append(t["ref"])
        out.append(dict(
            key="angkot-" + t["ref"], name="Angkot %s" % t["ref"],
            ref=t["ref"], network="Kota Depok (mikrotrans/angkot)",
            klass="bus-kota", scope="kota", status="beroperasi",
            **{"from": t["a"], "to": t["b"]},
            note=("Angkot dalam kota. Jalur %s. Tarif dasar %s sesuai Perwali "
                  "52/2022 (indikatif; Dishub sedang menata ulang tarif)."
                  % ("%s via %s" % (t["a"], t["via"]) if t.get("via")
                     else "%s - %s" % (t["a"], t["b"]), t["fare"])),
            fare=t["fare"], busiest=t.get("busiest"),
            source=("Dishub Kota Depok via berita.depok.go.id (30 Sep 2024); "
                    "tarif Perwali 52/2022"),
            checked="2024-09-30",
            geometry="estimated",
            geometry_note=("Jalur diestimasi dengan perutean di atas jaringan jalan "
                           "OpenStreetMap, bukan hasil survei operator."),
            segments=segs,
        ))

    if unroutable:
        sys.stderr.write("\ntrayek emitted WITHOUT geometry (no route found): %s\n"
                         % ", ".join(unroutable))
    return out


def load_endpoint_nodes():
    """Named OSM features usable as angkot endpoints.

    Merges the general probe (probe-endpoints.py) with the targeted per-name
    probe (probe-endpoints-extra.py). The second exists because the general probe
    is place-centric and misses market and terminal points like Pasar Rawa Denok.
    """
    nodes = []
    for fn in ("endpoints.json", "endpoints-extra.json"):
        p = Path(str(CACHE_DIR / fn))
        if not p.exists():
            continue
        data = json.load(open(p))
        if fn.endswith("extra.json"):
            # Shape is {"found": {label: [ {...}, ... ]}} rather than a raw
            # Overpass response, so walk it explicitly.
            for label, hits in (data.get("found") or {}).items():
                for h in hits:
                    nodes.append(dict(name=station_of(h.get("tags", {}) or {"name": h["name"]}),
                                      lat=h["lat"], lon=h["lon"], tags=h.get("tags", {})))
            continue
        for e in data.get("elements", []):
            c = e.get("center") or e
            if "lat" in c and "lon" in c and e.get("tags", {}).get("name"):
                nodes.append(dict(name=station_of(e["tags"]), lat=c["lat"], lon=c["lon"],
                                  tags=e["tags"]))
    return nodes


def nodes_from_roads():
    """Named street ways from the cached road network, usable as endpoint hints.

    Fallback source for endpoints the place-centric probes miss. Rawa Denok, for
    example, has no OSM place or POI at all, but the roads that serve it are
    mapped -- so matching on street names recovers a plausible position without
    inventing a coordinate. Cheap and offline: it reads roads.json, which
    build_graph() already fetches and caches.
    """
    p = Path(str(CACHE_DIR / "roads.json"))
    if not p.exists():
        return []
    out = []
    try:
        data = json.load(open(p))
    except (ValueError, OSError):
        return []
    for w in data.get("elements", []):
        tags = w.get("tags") or {}
        nm = tags.get("name")
        geom = w.get("geometry") or []
        if not nm or len(geom) < 2:
            continue
        # Midpoint of the way: a street name identifies an area better than
        # either end of it, and an endpoint is somewhere along it.
        mid = geom[len(geom) // 2]
        # Constrain to the city: several of these roads continue well outside
        # Kota Depok ("Jalan Raya Bojonggede—Kemang" runs south past the border),
        # and an angkot endpoint is by definition inside the city.
        if not in_depok(mid["lon"], mid["lat"]):
            continue
        out.append(dict(name=station_of(tags), lat=mid["lat"], lon=mid["lon"],
                        tags=dict(tags, _waypoint="1")))
    return out


def find_node(nodes, match, want=None):
    """Resolve an endpoint label to an OSM node.

    `match` may be a string or a list of alternative spellings. That matters:
    the dishub pages name endpoints colloquially ("Pasar Parung", "Bojonggede")
    while OSM calls the same place "Parung" or "Bojong Gede", so a single-string
    match silently fails even though the place is right there in the cache.

    Each alias is tried exact, then prefix, then substring; the best candidate
    across all aliases wins. Never guesses between ties -- ties are broken only
    by distance to the city centre.
    """
    aliases = [match] if isinstance(match, str) else list(match)
    for a in aliases:
        a = (a or "").strip().lower()
        if not a:
            continue
        # exact
        cands = [n for n in nodes if n["name"].lower() == a]
        if not cands:
            cands = [n for n in nodes if n["name"].lower().startswith(a)]
        if not cands:
            cands = [n for n in nodes if a in n["name"].lower()]
        if not cands:
            continue
        if want:
            pref = [n for n in cands
                    if (n["tags"].get("railway") or n["tags"].get("public_transport")
                        or n["tags"].get("amenity") or n["tags"].get("highway")
                        or n["tags"].get("place"))]
            if pref:
                cands = pref
        def d(n):
            return math.hypot((n["lon"] - 106.8227) * math.cos(math.radians(-6.39)),
                              n["lat"] + 6.39)
        return min(cands, key=d)
    return None


def build():
    rels = fetch_routes()
    sys.stderr.write("\nrelations: %d\n" % len(rels.get("elements", [])))
    seen = {}   # dedupe the A->B / B->A pair into one service
    dropped = []

    for rel in rels.get("elements", []):
        tags = rel.get("tags", {})
        segs = segments_of(rel)
        if not segs:
            continue

        name = tags.get("name") or ""
        ref = (tags.get("ref") or "").strip()
        klass = classify(tags)

        # Dedupe on (route, ref).
        #
        # OSM maps virtually every service twice: once as A->B and once as B->A.
        # Those pairs share route+ref and differ only in the arrow, so collapsing
        # on (route, ref) merges them into the single service a rider thinks of.
        # Checking this against the real data: every collision under this key is
        # an A/B pair, with one exception -- railway ref="B" appears as both
        # "Jakarta Kota -> Nambo" and "Jakarta Kota -> Bogor". Those are the
        # same track (the Bogor line runs Nambo-Bogor), so collapsing them is
        # also correct.
        #
        # The Parung feeder angkot share numbers ("27 BSD", "27 Babakan"), but
        # OSM keeps the destination inside `ref`, so those stay distinct.
        frm = station_of({"name": tags.get("from") or ""})
        to = station_of({"name": tags.get("to") or ""})
        key = (tags.get("route"), ref)

        if key in seen:
            # keep whichever copy has more geometry
            if sum(len(s) for s in segs) > sum(len(s) for s in seen[key]["segments"]):
                seen[key]["segments"] = segs
            continue

        if not touches_depok(segs):
            dropped.append((ref or name, klass))
            continue

        seen[key] = dict(
            key=ref or (frm + " - " + to) or name,
            name=name,
            ref=ref,
            network=tags.get("network") or "",
            klass=klass,
            from_=frm,
            to=to,
            segments=segs,
        )

    # Classify into kota vs lintas after dedupe.
    routes = []
    for r in seen.values():
        # Look status up by ref (OSM's "1" for Biskita K1, "10A" for D10A).
        # Fall back to the endpoint key for services with no ref.
        st = STATUS.get(r["ref"]) or STATUS.get(r["key"])
        if st is None:
            st = dict(scope="lintas", status="unverified",
                      note=("Rute ada di OpenStreetMap, tetapi status layanannya "
                            "belum diverifikasi untuk 2026."),
                      fare="", source="OpenStreetMap (geometri saja)",
                      checked=None)
        r["scope"] = st["scope"]
        r["status"] = st["status"]
        r["note"] = st["note"]
        r["fare"] = st.get("fare", "")
        r["source"] = st["source"]
        r["checked"] = st.get("checked")
        routes.append(r)

    routes.sort(key=lambda r: (r["klass"] != "bus-kota", r["scope"] != "kota", r["name"]))

    angkot = build_angkot()
    routes.extend(angkot)
    routes.sort(key=lambda r: (r["scope"] != "kota", r["klass"] != "bus-kota", r["name"]))

    try:
        from transport_endpoints import TRAYEK_INACTIVE
    except ImportError:
        TRAYEK_INACTIVE = []

    if dropped:
        sys.stderr.write("\ndropped (never enters Kota Depok):\n")
        for k, kl in sorted(set(dropped)):
            sys.stderr.write("  %-46s %s\n" % (k[:46], kl))

    n_kota = sum(1 for r in routes if r["scope"] == "kota")
    n_lintas = sum(1 for r in routes if r["scope"] == "lintas")
    n_geom = sum(1 for r in routes if r["segments"])
    sys.stderr.write("\nkept: %d (%d dalam kota, %d lintas), %d with geometry\n"
                     % (len(routes), n_kota, n_lintas, n_geom))

    payload = dict(
        source="OpenStreetMap via Overpass (ODbL); batas kota dari HDX/BPS (CC BY-IGO)",
        built=time.strftime("%Y-%m-%d"),
        inactive_trayek=[dict(ref=t["ref"], name=t["name"]) for t in TRAYEK_INACTIVE],
        routes=[dict(
            key=r["key"], name=r["name"], ref=r["ref"], network=r["network"],
            klass=r["klass"], scope=r["scope"], status=r["status"],
            note=r["note"], fare=r["fare"], source=r["source"], checked=r["checked"],
            geometry=r.get("geometry", "osm"),
            geometry_note=r.get("geometry_note"),
            busiest=r.get("busiest"),
            **{"from": r.get("from_"), "to": r.get("to")},
            segments=r["segments"],
        ) for r in routes],
    )

    OUT.write_text(
        "/* GENERATED by scripts/build-transport.py — do not edit.\n"
        "   Public transport routes that intersect Kota Depok.\n"
        "   %s */\n"
        "window.DEPOK_TRANSPORT=%s;\n" % (payload["source"], json.dumps(payload, ensure_ascii=False))
    )
    sys.stderr.write("wrote %s (%d bytes)\n" % (OUT, OUT.stat().st_size))


if __name__ == "__main__":
    build()
