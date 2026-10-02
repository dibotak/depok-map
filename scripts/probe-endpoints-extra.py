#!/usr/bin/env python3
"""Fetch OSM places/transport features for named angkot endpoints that
probe-endpoints.py missed.

The general probe returned 1,111 named elements but none matched 8 of the 14
documented trayek endpoints (Rawa Denok, Leuwinanggung, Parung, Bojong Gede,
Palsigunung, Studio Alam, Desa Tengah, Pitara). Several of these are pasar or
terminal points rather than administrative places, so a place-centric probe
misses them. This runs a name-substring query per endpoint across a wider set of
tag combinations and caches whatever it finds.

Run after probe-endpoints.py; build-transport.py merges the result.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "transport"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BBOX = "-6.48,106.70,-6.30,106.94"
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = "depok-map/1.0 (public transport endpoint probe)"

# Endpoint label -> substrings worth trying in OSM `name`. The dishub pages use
# colloquial names ("Pasar Rawa Denok") that rarely match the OSM feature
# exactly, so each gets several plausible forms.
WANTED = {
    "Rawa Denok": ["rawa denok", "denok"],
    "Leuwinanggung": ["leuwinanggung", "leuw in anggung", "leuwin"],
    "Parung": ["parung"],
    "Bojong Gede": ["bojong gede", "bojonggede", "bojong gede"],
    "Palsigunung": ["palsigunung", "palsi"],
    "Studio Alam": ["studio alam", "studioalam"],
    "Desa Tengah": ["desa tengah", "desatengah"],
    "Pitara": ["pitara", "pita rara", "pita rara", "terminal pitara"],
}

TAGS = [
    "name", "place", "shop", "amenity", "public_transport", "highway",
    "railway", "landuse", "building", "office", "leisure",
]


def query(q):
    body = urllib.parse.urlencode({"data": q}).encode()
    for attempt, ep in enumerate(MIRRORS):
        try:
            req = urllib.request.Request(ep, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            sys.stderr.write("  %s: %s\n" % (ep.split("/")[2], e))
            time.sleep(5 * (attempt + 1))
    return None


def main():
    tag_filter = "|".join("k:%s" % t for t in TAGS)
    found, misses = {}, []

    for label, subs in WANTED.items():
        name_re = "|".join(re.escape(s) for s in subs)
        q = """[out:json][timeout:180];
(
  nwr["name"~"(%s)",i](%s);
);
out center tags;""" % (name_re, BBOX)
        sys.stderr.write("\n%s ...\n" % label)
        data = query(q)
        if not data:
            misses.append(label)
            continue
        els = data.get("elements", [])
        hits = []
        for e in els:
            c = e.get("center") or e
            if "lat" in c and "lon" in c:
                hits.append(dict(
                    name=e.get("tags", {}).get("name"),
                    lat=c["lat"], lon=c["lon"],
                    kind=e.get("type"), tags=e.get("tags", {}),
                ))
        # Prefer the most specific name: an exact match beats a prefix stub.
        hits.sort(key=lambda h: (len(h["name"] or ""), h["name"] or ""))
        if hits:
            found[label] = hits[:8]
            sys.stderr.write("  %d hit(s): %s\n"
                             % (len(hits), ", ".join(h["name"] for h in hits[:4])))
        else:
            misses.append(label)
            sys.stderr.write("  no hit\n")

    out = CACHE_DIR / "endpoints-extra.json"
    json.dump({"found": found, "missing": misses}, open(out, "w"))
    sys.stderr.write("\nwrote %s: %d resolved, %d still missing (%s)\n"
                     % (out, len(found), len(misses), ", ".join(misses) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())