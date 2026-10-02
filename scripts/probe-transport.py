"""Probe Overpass for what public-transport geometry actually exists in Depok.


# Overpass/road cache lives under the repo-adjacent .cache dir, not /tmp: a /tmp
# wipe mid-build loses the fetch and the run dies with nothing on disk to resume
# from.
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"

Run before writing build-transport.py so the build targets real relations
instead of assumptions. Prints a summary and caches the raw responses.
"""
import json
from pathlib import Path
import os
import sys
import time
import urllib.parse
import urllib.request

# Overpass/road cache lives under a repo-adjacent .cache dir, not /tmp: a /tmp
# wipe mid-build loses the fetch and the run dies with nothing to resume from.
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"

# Overpass bbox order is (south,west,north,east). Generous margin around the
# city's real extent (106.717..106.919 lon, -6.461..-6.314 lat) so feeder
# routes that start just outside the city still appear.
BBOX = "-6.48,106.70,-6.30,106.94"
# Tiles as (south,west,north,east), Overpass order.
TILES = [
    (-6.48, 106.70, -6.39, 106.82),
    (-6.48, 106.82, -6.39, 106.94),
    (-6.39, 106.70, -6.30, 106.82),
    (-6.39, 106.82, -6.30, 106.94),
]
TAGGED_TILES = {"routes"}  # only the relation query needs tiling
CACHE = CACHE_DIR
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "depok-map/1.0 (public transport route build)"

QUERIES = {
    # Route relations of any public-transport kind. Tiled 2x2 because a single
    # metro-wide relation query 504s on every mirror.
    "routes": """
[out:json][timeout:180];
(
  relation["type"="route"]["route"~"^(bus|tram|train|light_rail)$"](%(bbox)s);
);
out tags;""",
    # Named railway stations / halts / tram stops.
    "stations": """
[out:json][timeout:180];
(
  node["railway"="station"]["name"](%(bbox)s);
  node["railway"="halt"]["name"](%(bbox)s);
  node["public_transport"="stop_position"]["name"](%(bbox)s);
  node["highway"="bus_stop"]["name"](%(bbox)s);
);
out tags center;""",
    # The KRL Bogor line alignment as a fallback for the rail layer.
    "rail": """
[out:json][timeout:180];
(
  way["railway"="rail"](%(bbox)s);
  way["railway"~"^(light_rail|tram|subway)$"](%(bbox)s);
);
out geom;""",
}


def fetch(name, q):
    """Run one query, tiled when needed, merging and de-duplicating the results."""
    if name in TAGGED_TILES:
        merged = {}
        for t in TILES:
            data = fetch_one(name, q, ",".join(str(v) for v in t))
            if data is None:
                continue
            for e in data.get("elements", []):
                merged[(e["type"], e["id"])] = e
        if not merged:
            return None
        out = {"elements": list(merged.values())}
        os.makedirs(CACHE, exist_ok=True)
        json.dump(out, open(os.path.join(CACHE, name + ".json"), "w"))
        return out
    return fetch_one(name, q, BBOX)


def fetch_one(name, q, bbox):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "%s-%s.json" % (name, bbox.replace(",", "_").replace(".", "")))
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400:
        print("cached", name, bbox, flush=True)
        return json.load(open(path))
    body = urllib.parse.urlencode({"data": q % {"bbox": bbox}}).encode()
    last = None
    for ep in ENDPOINTS:
        try:
            print("GET", name, bbox, ep, flush=True)
            req = urllib.request.Request(
                ep, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "User-Agent": UA},
            )
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.load(r)
            json.dump(data, open(path, "w"))
            return data
        except Exception as e:  # noqa: BLE001 - report and try the next mirror
            print("  fail:", e, flush=True)
            last = e
        time.sleep(5)
    print("  GIVING UP on", name, bbox, "->", last, flush=True)
    return None


def main():
    summary = {}
    for name, q in QUERIES.items():
        data = fetch(name, q)
        if data is None:
            print("\n=== %s: NO DATA (all mirrors failed) ===" % name)
            summary[name] = 0
            continue
        els = data.get("elements", [])
        if name == "routes":
            print("\n=== ROUTE RELATIONS (%d) ===" % len(els))
            for e in els:
                t = e.get("tags", {})
                print("  %-12s %-10s %s" % (e["id"], t.get("route"), t.get("name")))
                if t.get("refs") or t.get("network"):
                    print("        network=%s refs=%s" % (t.get("network"), t.get("refs")))
            summary[name] = len(els)
        elif name == "stations":
            print("\n=== STOPS (%d) ===" % len(els))
            by = {}
            for e in els:
                t = e.get("tags", {})
                by.setdefault(t.get("railway") or t.get("public_transport")
                             or t.get("highway"), []).append(t.get("name"))
            for k, v in sorted(by.items()):
                print("  %-18s %d" % (k, len(v)))
                for n in sorted(x for x in v if x)[:60]:
                    print("      -", n)
            summary[name] = len(els)
        else:
            print("\n=== RAIL WAYS (%d) ===" % len(els))
            summary[name] = len(els)

    print("\nSUMMARY", json.dumps(summary))


if __name__ == "__main__":
    sys.exit(main())
