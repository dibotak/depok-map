"""Fetch full geometry for the candidate route relations, one tiled query.


# Overpass/road cache lives under the repo-adjacent .cache dir, not /tmp: a /tmp
# wipe mid-build loses the fetch and the run dies with nothing on disk to resume
# from.
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"

`out tags` gave us the 111 candidate relations; `out geom` is the expensive
part. Fetch it once, cached, and cache the result as the input the real build
script consumes.
"""
import json
from pathlib import Path
import os
import time
import urllib.parse
import urllib.request

# Overpass/road cache lives under a repo-adjacent .cache dir, not /tmp: a /tmp
# wipe mid-build loses the fetch and the run dies with nothing to resume from.
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"

TILES = [
    (-6.48, 106.70, -6.39, 106.82),
    (-6.48, 106.82, -6.39, 106.94),
    (-6.39, 106.70, -6.30, 106.82),
    (-6.39, 106.82, -6.30, 106.94),
]
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "depok-map/1.0 (public transport route build)"
CACHE = CACHE_DIR

Q = """
[out:json][timeout:300];
relation["type"="route"]["route"~"^(bus|tram|train|light_rail)$"](%s);
out geom;
"""


def fetch(bbox):
    path = os.path.join(CACHE, "routesgeom-%s.json" % bbox.replace(",", "_").replace(".", ""))
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400:
        print("cached", bbox, flush=True)
        return json.load(open(path))
    body = urllib.parse.urlencode({"data": Q % bbox}).encode()
    for attempt in range(3):
        for ep in ENDPOINTS:
            try:
                print("GET", bbox, ep, flush=True)
                req = urllib.request.Request(
                    ep, data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded",
                             "User-Agent": UA})
                with urllib.request.urlopen(req, timeout=300) as r:
                    data = json.load(r)
                n = len(data.get("elements", []))
                print("  -> %d relations" % n, flush=True)
                json.dump(data, open(path, "w"))
                return data
            except Exception as e:
                print("  fail:", e, flush=True)
        time.sleep(10 * (attempt + 1))
    return None


def main():
    merged = {}
    for t in TILES:
        bbox = ",".join(str(v) for v in t)
        d = fetch(bbox)
        if not d:
            continue
        for e in d.get("elements", []):
            merged[e["id"]] = e
    out = {"elements": list(merged.values())}
    json.dump(out, open(os.path.join(CACHE, "routesgeom-merged.json"), "w"))
    print("MERGED", len(out["elements"]))
    withgeom = sum(1 for e in out["elements"] if e.get("members"))
    print("with members", withgeom)


if __name__ == "__main__":
    main()
