#!/usr/bin/env python3
"""Build src/schools.js -- secondary schools (SMA/SMK/MA) in Kota Depok.

Why a script and not the raw Overpass response:

1. OSM's `amenity=school` does not say what level a school is. `isced:level` is
   present on 10 of 899 features in this bbox, so level has to come from the
   name -- "SMA Negeri 3", "SMKN 1", "MA Al-Ijayah" are the reliable signal.

2. The raw query returns 899 features, of which only ~140 are secondary. The
   rest are driving schools (Primagama, Ganesha Operation), English courses
   (English First, TBI), universities and kindergartens, which all share the
   same amenity value.

3. Campuses are mapped as several building polygons under one name: "SMA
   Negeri 1 Kota Depok" comes back as 7 ways within 70m of each other. Left
   alone, the list would show the same school seven times.

4. The city clip needs the same boundary file the map draws, so a "SMA" whose
   polygon only clips a corner of Depok is not listed.

Level mapping (Indonesian):
  SMA  Sekolah Menengah Atas      -- general upper secondary, the usual "sma"
  SMK  Sekolah Menengah Kejuruan  -- vocational upper secondary, same level
  MA   Madrasah Aliyah            -- Islamic upper secondary, same level
  SMP/SD excluded; likewise universities, polytechnics and courses.

Classification is by name pattern and is therefore only as good as what
mappers typed. A school whose OSM name omits its level cannot be classified and
is dropped rather than guessed at; the counts of dropped features are reported
on stderr so the shortfall is visible instead of silent.

Usage:  python3 scripts/build-schools.py
Writes: src/schools.js  (window.DEPOK_SCHOOLS)
Caches: .cache/schools-raw.json, .cache/schools.json
"""

import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache"
OUT = ROOT / "src" / "schools.js"
BOUNDARY = ROOT / "public" / "data" / "depok-kelurahan.json"

UA = "depok-map/1.0 (public school survey)"
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Kota Depok administrative extent, from the same boundary file the map draws.
BBOX = "-6.461283,106.716747,-6.314414,106.918848"

# Two queries, unioned, because amenity=school alone is not a complete filter:
# a mapper who wrote "SMAN 3" or "SMKS Kesehatan Logos" and tagged the feature
# amenity=college, or left amenity off entirely, would be missed by the first
# branch alone. Overpass evaluates the union server-side, so this costs one
# request.
#
# Measured: the name branch currently returns 17 extra features, and all 17 fall
# outside the city clip (they carry addr:city=Bogor --Sawangan, Leuwiliang and
# the Bogor side of the border). So the count is unchanged at 30, but the query
# is not relying on every mapper having filled in amenity correctly.
#
# The SMAN/SMKS abbreviations are the reason the name branch matters: "SMAS" and
# "SMKS" are how private schools are commonly abbreviated, and neither is in the
# amenity value at all.
QUERY = (
    '[out:json][timeout:180];'
    '('
    '  nwr["amenity"~"^(school|college|university)$"](%(bbox)s);'
    '  nwr["name"~"(^|\\s)(SMAN|SMKN|SMAS|SMKS|SMA|SMK)(\\s|$|NEGERI)",i](%(bbox)s);'
    ');'
    'out center tags;' % {"bbox": BBOX}
)

# Ordered most-specific first: "SMA Negeri 3" must not be caught by the plain
# "SMA" rule, and "MA" must not swallow the "ALMA"/"NAMA" fragments that a bare
# substring match would hit.
LEVELS = [
    # SMAN/SMKN are the public forms; SMAS/SMKS are how many mappers abbreviate
    # the private ones ("SMAS Dwiwarna", "SMKS Kesehatan Logos"). Leaving those
    # two forms out dropped 17 real schools from the list. Negeri must be tried
    # before the bare abbreviation, or "SMA Negeri 3" matches as private.
    ("SMA", "negeri", r"\bSMA\s*NEGERI\b|\bSMAN\b|\bSMAN\s"),
    ("SMA", "swasta", r"\bSMA\b|\bSMAS\b"),
    ("SMK", "negeri", r"\bSMK\s*NEGERI\b|\bSMKN\b"),
    ("SMK", "swasta", r"\bSMK\b|\bSMKS\b"),
    ("MA", "swasta", r"\bMA\s+AL[IY]YAH\b|\bMADRASAH\s+ALIYAH\b|\bMTS?\.?\s+AL[IY]YAH\b"),
    # A bare "MA" would match any word containing those two letters, so require
    # it as a standalone token and require an Islamic marker nearby.
    ("MA", "swasta", r"\bMA\b(?=.*\b(ISLAM|ISTIQ[AO]?MAH|MUHAMMADIYAH|ISLAMIYAH)\b)"),
]

LEVEL_LABEL = {
    ("SMA", "negeri"): "SMA Negeri",
    ("SMA", "swasta"): "SMA Swasta",
    ("SMK", "negeri"): "SMK Negeri",
    ("SMK", "swasta"): "SMK Swasta",
    ("MA", "swasta"): "Madrasah Aliyah",
}


def overpass(query):
    body = urllib.parse.urlencode({"data": query}).encode()
    for attempt, ep in enumerate(MIRRORS * 2):
        try:
            req = urllib.request.Request(ep, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            sys.stderr.write("  %s: %s\n" % (ep.split("/")[2], e))
            time.sleep(5 * (attempt + 1))
    raise SystemExit("all Overpass mirrors failed")


def fetch():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "schools-raw.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 7 * 86400:
        sys.stderr.write("using cached %s\n" % cache)
        return json.load(open(cache))
    sys.stderr.write("querying Overpass ...\n")
    data = overpass(QUERY)
    json.dump(data, open(cache, "w"))
    return data


def point_of(el):
    c = el.get("center") or el
    if "lat" in c and "lon" in c:
        return (c["lat"], c["lon"])
    return None


# --- city boundary, for clipping -------------------------------------------
def depok_mask():
    """Per-polygon ring list + bbox, for a cheap point-in-city test."""
    if not BOUNDARY.exists():
        raise SystemExit("missing %s -- regenerate the boundary first" % BOUNDARY)
    g = json.load(open(BOUNDARY))
    rings = []
    xs, ys = [], []
    for f in g["features"]:
        geom = f.get("geometry") or {}
        polys = [geom["coordinates"]] if geom.get("type") == "Polygon" \
            else geom.get("coordinates") or []
        for poly in polys:
            for ring in poly:
                if len(ring) < 4:
                    continue
                rings.append(ring)
                for x, y in ring:
                    xs.append(x)
                    ys.append(y)
    return rings, (min(xs), min(ys), max(xs), max(ys))


def in_city(lon, lat, rings, bbox):
    if not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
        return False
    inside = False
    for ring in rings:
        n = len(ring)
        for i in range(n - 1):
            x1, y1 = ring[i]
            x2, y2 = ring[i + 1]
            if (y1 > lat) != (y2 > lat):
                xin = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
                if lon < xin:
                    inside = not inside
    return inside


def haversine(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def classify(name, type_idn=None):
    """Level and sector for one feature.

    `school:type_idn` wins over the name when present. A name like "SMP Al
    Muhtadin/SMA Muhamadiyah" lists several levels and matches whichever pattern
    comes first, which is how an SMP-typed campus ended up filed as an SMA; the
    explicit tag is the mapper's considered answer. Only exact single-level tags
    are trusted -- "sd, smp, sma" is a combined campus and stays name-matched,
    since it genuinely offers all three.
    """
    if type_idn:
        tid = type_idn.strip().lower()
        if tid in ("sma", "sman"):
            return "SMA", None
        if tid in ("smk", "smkn"):
            return "SMK", None
        if re.search(r"\bma\b", tid):
            return "MA", "swasta"
        # A single non-secondary level means this is not a secondary school at
        # all, whatever the name says. A multi-level tag ("sd, smp, sma") is a
        # combined campus that genuinely offers secondary, so it falls through
        # to the name instead.
        if re.fullmatch(r"sd|smp|paud|tk|tb", tid):
            return None, None
        if re.fullmatch(r"(sd|smp|paud|tk|tb)(\s*,\s*(sd|smp|paud|tk|tb))+", tid) \
                and not re.search(r"sma|smk|ma\b", tid):
            return None, None

    up = (name or "").upper()
    for level, sector, pat in LEVELS:
        if re.search(pat, up):
            return level, sector
    return None, None


def tidy(tags):
    t = (tags or {}).get("name", "").strip()
    # Collapse the doubled spaces and separator variants mappers leave behind
    # ("SMA  NEGERI 1 ", "SMA Negeri 1 Depok", "S M A ...").
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .-–—")


def canonical(name):
    """Collapse naming variants of the same school.

    "SMA Negeri 1 Depok" and "SMA Negeri 1 Kota Depok" are one school, typed two
    ways, and dedupe() only matches on exact name so it would list them as two
    rows. The variants that actually differ are: the "Kota"/"Kab." prefix, an
    "SMAN"/"SMKN" abbreviation, and spacing around the level word. Only those
    are removed -- enough to merge one school's aliases without merging two
    genuinely different schools that happen to share a number.
    """
    t = re.sub(r"\s+", " ", name).upper().strip(" .-")
    t = re.sub(r"\b(NEGERI|NEGARA)\b", "NEGERI", t)
    t = re.sub(r"^SMA\s+NEGERI", "SMA NEGERI", t)
    t = re.sub(r"^SMK\s+NEGERI", "SMK NEGERI", t)
    t = re.sub(r"^SMA\s*K\s+N", "SMAN ", t)     # "SMA N 5 Depok" -> "SMAN 5 Depok"
    t = re.sub(r"^SMK\s*N\s+", "SMKN ", t)      # "SMK N 1"        -> "SMKN 1"
    # "Kota Depok" / "Depok" at the end are the same city.
    t = re.sub(r"\bKOTA\s+DEPOK\b", "DEPOK", t)
    t = re.sub(r"\bKAB\.?\s+DEPOK\b", "DEPOK", t)
    return t.strip()


def dedupe(items):
    """Collapse the several building polygons that make up one campus.

    Keyed on the canonical name, then on distance: two features within 400m are
    the same school mapped twice. 400m rather than something tighter because a
    large campus spans more than that, and a genuine collision between two
    different secondary schools in a 60 km2 city is not plausible.
    """
    kept, dropped = [], 0
    for it in sorted(items, key=lambda x: (x["canon"], x["lat"], x["lon"])):
        hit = None
        for k in kept:
            if k["canon"] == it["canon"] and \
                    haversine((k["lat"], k["lon"]), (it["lat"], it["lon"])) <= 400:
                hit = k
                break
        if hit:
            dropped += 1
            # Prefer the richer tag set, and the fuller display name: "SMA Negeri
            # 1 Kota Depok" is a better label than "SMA Negeri 1 Depok" even
            # though the latter is what the canonical key was built from.
            if it.get("website") and not hit.get("website"):
                hit["website"] = it["website"]
            if it.get("phone") and not hit.get("phone"):
                hit["phone"] = it["phone"]
            for f in ("address", "street", "postcode", "jenjang", "type_idn",
                      "grades", "operator"):
                if it.get(f) and not hit.get(f):
                    hit[f] = it[f]
            if len(it["osm_tags"]) > len(hit["osm_tags"]):
                hit["osm_tags"] = it["osm_tags"]
                hit["name"] = it["name"]
            continue
        kept.append(it)
    return kept, dropped


def main():
    data = fetch()
    els = data.get("elements", [])
    sys.stderr.write("features returned: %d\n" % len(els))

    rings, bbox = depok_mask()

    out, skipped_level, skipped_city, skipped_noname = [], 0, 0, 0
    for e in els:
        tags = e.get("tags") or {}
        name = tidy(tags)
        if not name:
            skipped_noname += 1
            continue
        level, sector = classify(name, tags.get("school:type_idn"))
        if not level:
            skipped_level += 1
            continue
        # classify() can return a level with no sector when school:type_idn
        # decided it; an SMA tag says nothing about public or private.
        if sector is None:
            sector = "negeri" if re.search(r"\bNEGERI\b|\bSMAN\b|\bSMKN\b", name.upper()) \
                else "swasta"
        pt = point_of(e)
        if not pt:
            continue
        lon, lat = pt[1], pt[0]
        if not in_city(lon, lat, rings, bbox):
            skipped_city += 1
            continue
        out.append(dict(
            key="sekolah-%s-%s" % (e["type"], e["id"]),
            osm_type=e["type"],
            osm_id=e["id"],
            name=name,
            canon=canonical(name),
            level=level,
            sector=sector,
            label=LEVEL_LABEL[(level, sector)],
            lat=round(lat, 6),
            lon=round(lon, 6),
            website=tags.get("website") or tags.get("contact:website") or None,
            phone=tags.get("phone") or tags.get("contact:phone") or None,
            # addr:full is the one address tag most Indonesian school mappers
            # fill in (85 of the ~93 that carry any address), and it is the
            # third fact the detail card needs. addr:street alone appears on 8.
            address=tags.get("addr:full") or None,
            street=tags.get("addr:street") or None,
            postcode=tags.get("addr:postcode") or None,
            # Indonesian mappers use jenjang (1-6) and school:type_idn ("sma",
            # "smp") where they bother; they are present on only a few of these
            # 31, but where present they are authoritative and outrank the name.
            jenjang=tags.get("jenjang") or None,
            type_idn=tags.get("school:type_idn") or None,
            grades=tags.get("grades") or None,
            operator=tags.get("operator") or None,
            isced=tags.get("isced:level") or None,
            operator_type=tags.get("operator:type") or None,
            osm_tags=tags,
        ))

    sys.stderr.write(
        "classified: %d | dropped: %d not a secondary school, %d outside the city, "
        "%d unnamed\n" % (len(out), skipped_level, skipped_city, skipped_noname))

    kept, dropped_dupes = dedupe(out)
    sys.stderr.write("campus duplicates collapsed: %d -> %d schools\n"
                     % (dropped_dupes, len(kept)))

    kept.sort(key=lambda x: (x["level"], x["sector"], x["name"]))

    payload = dict(
        source="OpenStreetMap via Overpass (ODbL); batas kota dari HDX/BPS (CC BY-IGO)",
        built=time.strftime("%Y-%m-%d"),
        note=("Tingkat sekolah dibaca dari nama di OpenStreetMap, bukan dari "
              "isced:level (hanya 10 dari %d fitur yang mengisinya). Sekolah "
              "yang namanya tidak menyebut tingkatnya tidak bisa diklasifikasi "
              "dan tidak ditampilkan." % len(els)),
        counts={label: sum(1 for k in kept if k["label"] == label)
                for label in sorted({k["label"] for k in kept})},
        schools=[{k: v for k, v in s.items() if k not in ("osm_tags", "canon")}
                 for s in kept],
    )

    OUT.write_text(
        "/* GENERATED by scripts/build-schools.py -- do not edit.\n"
        "   Secondary schools (SMA/SMK/MA) inside Kota Depok.\n"
        "   %s */\n"
        "window.DEPOK_SCHOOLS=%s;\n"
        % (payload["source"], json.dumps(payload, ensure_ascii=False))
    )
    sys.stderr.write("wrote %s (%d bytes)\n" % (OUT, OUT.stat().st_size))
    for label, n in payload["counts"].items():
        sys.stderr.write("  %-18s %d\n" % (label, n))


if __name__ == "__main__":
    main()