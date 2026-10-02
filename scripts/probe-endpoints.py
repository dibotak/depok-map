"""Geocode the intra-city angkot endpoints from OSM place/station nodes.


# Overpass/road cache lives under the repo-adjacent .cache dir, not /tmp: a /tmp
# wipe mid-build loses the fetch and the run dies with nothing on disk to resume
# from.
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"

The 14 operating trayek have official endpoints (Dishub, via berita.depok.go.id
30 Sep 2024) but NO OSM route geometry -- angkot paths are not mapped as
relations. So for those we build geometry by matching endpoint nodes and
snapping to the nearest named road, rather than inventing coordinates.

Run: prints a table of candidate node matches for review.
"""
import json
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

# Overpass/road cache lives under a repo-adjacent .cache dir, not /tmp: a /tmp
# wipe mid-build loses the fetch and the run dies with nothing to resume from.
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"

BBOX = "-6.48,106.70,-6.30,106.94"
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = "depok-map/1.0 (transport endpoint geocode)"

Q = """
[out:json][timeout:180];
(
  node["railway"~"^(station|halt)$"](%s);
  node["public_transport"="station"](%s);
  node["amenity"="bus_station"](%s);
  node["highway"="bus_stop"]["name"](%s);
  node["place"~"^(suburb|neighbourhood|quarter|hamlet|village)$"]["name"](%s);
  node["landuse"="industrial"]["name"](%s);
);
out center;
""" % (BBOX, BBOX, BBOX, BBOX, BBOX, BBOX)


def main():
    body = urllib.parse.urlencode({"data": Q}).encode()
    data = None
    for ep in ENDPOINTS:
        try:
            req = urllib.request.Request(
                ep, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.loads(r.read())
            print("fetched from", ep, len(data["elements"]))
            break
        except Exception as e:
            print("fail", ep, e)
            time.sleep(5)
    if not data:
        sys.exit(1)
    json.dump(data, open(str(CACHE_DIR / "endpoints.json"), "w"))

    def clean(n):
        import re
        n = re.sub(r"\s*\(.*?\)\s*", " ", n)
        return re.sub(r"\s*;.*$", "", n).strip()

    for e in sorted(data["elements"], key=lambda x: x["tags"].get("name", "")):
        t = e["tags"]
        n = clean(t.get("name", ""))
        # `out center` puts coords under `center` for nodes; be tolerant of both
        # shapes rather than assuming one.
        c = e.get("center") or e
        if not n or "lat" not in c or "lon" not in c:
            continue
        kind = (t.get("railway") or t.get("public_transport") or
                t.get("amenity") or t.get("highway") or t.get("place") or
                t.get("landuse"))
        print("%-34s %-16s %.5f,%.5f" % (n[:34], kind, c["lat"], c["lon"]))


if __name__ == "__main__":
    main()
