"""Route angkot paths along the real street network.


# Overpass/road cache lives under the repo-adjacent .cache dir, not /tmp: a /tmp
# wipe mid-build loses the fetch and the run dies with nothing on disk to resume
# from.
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"

WHY THIS EXISTS
---------------
The 14 operating intra-city trayek (Dishub Depok, 30 Sep 2024) have official
endpoints but no OSM route relation. Drawing them as straight lines between
endpoints would be a lie: it would suggest a shortcut across fields or a river
where no such road exists. So we fetch the actual drivable street graph around
each pair of endpoints and run Dijkstra over it.

The result is an approximation, and the UI says so: it is "estimated path along
mapped streets", not a surveyed route. Speed on the target network is set per
class so a motorway is preferred over a residential alley, matching how an
angkot actually drives.

Requires: networkx (pip install networkx)
Run: build-transport.py calls route_angkot(); this module is importable/testable
on its own.
"""
import json
from pathlib import Path
import math
import sys
import time
import urllib.parse
import urllib.request

# Overpass/road cache lives under a repo-adjacent .cache dir, not /tmp: a /tmp
# wipe mid-build loses the fetch and the run dies with nothing to resume from.
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "depok-map/1.0 (angkot routing)"

BBOX = "-6.48,106.70,-6.30,106.94"

# Drivable ways. `service` and footway-ish classes are excluded: an angkot does
# not drive down a footway or a parking aisle.
DRIVE_QUERY = """
[out:json][timeout:300];
way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link)$"](%s);
out geom;
""" % BBOX

# Per-class speed in km/h. Used only for weighting, which is what biases the
# path toward roads an angkot would plausibly use.
SPEED = {
    "motorway": 60, "trunk": 55, "primary": 45, "secondary": 40,
    "tertiary": 35, "unclassified": 30, "residential": 25,
    "living_street": 15, "service": 12,
    "motorway_link": 30, "trunk_link": 25, "primary_link": 25,
    "secondary_link": 22, "tertiary_link": 20,
}


def overpass(query, tries=3):
    body = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(tries):
        for ep in ENDPOINTS:
            try:
                req = urllib.request.Request(
                    ep, data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded",
                             "User-Agent": UA})
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.loads(r.read())
            except Exception as e:
                sys.stderr.write("  routing %s: %s\n" % (ep.split("/")[2], e))
                time.sleep(5 * (attempt + 1))
    return None


def build_graph():
    """Weighted graph of the drivable network. Cached to disk."""
    try:
        import networkx as nx
    except ImportError:
        sys.exit("networkx is required: pip install networkx")

    cache = str(CACHE_DIR / "roads.json")
    try:
        import os
        if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 86400:
            data = json.load(open(cache))
        else:
            data = overpass(DRIVE_QUERY)
            if data is None:
                sys.exit("could not fetch the road network")
            json.dump(data, open(cache, "w"))
    except OSError:
        data = overpass(DRIVE_QUERY)

    g = nx.Graph()
    for w in data["elements"]:
        geom = w.get("geometry") or []
        if len(geom) < 2:
            continue
        hw = w["tags"].get("highway")
        # A oneway tagged `yes` allows only the given direction; `no`/absent is
        # bidirectional. -1 means reverse only. Treating everything as
        # bidirectional would be wrong for oneways but is far less bad than
        # routing against one, so reverse-only is honoured and plain oneway is
        # approximated as bidirectional with a small penalty.
        oneway = w["tags"].get("oneway")
        pts = [(p["lon"], p["lat"]) for p in geom]
        cost_k = 1.0 if oneway in (None, "no", "0", "false") else 2.0
        for a, b in zip(pts, pts[1:]):
            km = math.hypot((b[0] - a[0]) * math.cos(math.radians(a[1])),
                            b[1] - a[1]) * 111.32
            if km <= 0:
                continue
            v = (km / SPEED.get(hw, 25)) * 60.0 * cost_k  # minutes
            if g.has_edge(a, b):
                if v < g[a][b]["weight"]:
                    g[a][b]["weight"] = v
                    g[a][b]["highway"] = hw
            else:
                g.add_edge(a, b, weight=v, highway=hw)
    sys.stderr.write("  road graph: %d nodes, %d edges\n" % (g.number_of_nodes(), g.number_of_edges()))
    return g


def build_index(g):
    """Spatial grid over graph nodes, for O(1)-ish nearest-node lookup.

    The previous implementation scanned the whole connected component per call:
    573k nodes x 14 trayek, plus `nx.node_connected_component` is itself O(V+E)
    each time. This indexes once and the router reuses it.
    """
    idx = {}
    for n in g.nodes():
        try:
            lon, lat = n[0], n[1]
        except (TypeError, IndexError, KeyError):
            continue  # skip any non-coordinate node id that slipped into the graph
        idx.setdefault((round(lat, 3), round(lon, 3)), n)
    return idx


def nearest_node(g, idx, lon, lat, max_m=1500):
    """Snap a point to the nearest graph node, within a sane radius.

    Walks outward through the grid by increasing cell size rather than scanning
    every node, and always returns a node that is actually in `g` -- a stale or
    synthetic id here makes networkx raise NodeNotFound deep inside
    shortest_path, which is a confusing way to learn that snapping failed.
    """
    latc = math.cos(math.radians(lat))
    best, best_d = None, None
    # ~1500 m is about 0.014 deg of latitude; search a few cells either side.
    for cells in range(1, 6):
        found = False
        for dlat in range(-cells, cells + 1):
            for dlon in range(-cells, cells + 1):
                for key in ((round(lat + dlat * 0.01, 3),
                             round(lon + dlon * 0.01, 3)),):
                    n = idx.get(key)
                    if n is None or not g.has_node(n):
                        continue
                    found = True
                    d = math.hypot((n[0] - lon) * latc, n[1] - lat) * 111320
                    if d <= max_m and (best_d is None or d < best_d):
                        best, best_d = n, d
        # Stop once this ring found anything: nearest is guaranteed inside it.
        if found and best_d is not None and best_d <= cells * 0.01 * 111320:
            break
    return best


def route(g, idx, a, b):
    """Dijkstra path between two (lon, lat) points, as a coordinate list.

    Returns None for any failure rather than raising: one unroutable trayek
    must not abort the whole build.
    """
    import networkx as nx
    try:
        na, nb = nearest_node(g, idx, *a), nearest_node(g, idx, *b)
        if na is None or nb is None:
            return None
        path = nx.shortest_path(g, na, nb, weight="weight")
        return [[n[0], n[1]] for n in path]
    except (nx.NetworkXNoPath, nx.NodeNotFound, nx.NetworkXError,
            KeyError, TypeError, IndexError, ValueError) as e:
        sys.stderr.write("  route failed (%s -> %s): %s\n" % (a, b, e))
        return None
