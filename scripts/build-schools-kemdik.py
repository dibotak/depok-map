#!/usr/bin/env python3
"""
Build the secondary-school list from Kemendikdasmen's own register, then find
each school's point.

Why this exists
---------------
The OSM-only list in build-schools.py found 30 schools. The official register at
referensi.data.kemendikdasmen.go.id lists 244. That is not a small discrepancy
-- it is 87% of the city missing -- so the OSM name match was never a coverage
claim, only a convenience. This script replaces it with the official data and
geocodes each entry.

The official source
-------------------
One page per kecamatan (11 pages), each a server-rendered DataTable with columns:

    No | NPSN | Nama Satuan Pendidikan | Alamat | Kelurahan | Status

NPSN is the national ID and is the join key: unique across all 250 rows, and
stable across the two fetches (grand total on the province page vs per-kecamatan
sums). It is the only thing here worth trusting over a name, because names are
spelled inconsistently even within one kecamatan ("SMA AL-ISLAM", "SMAS AL
ISLAM", "MAS AL ISLAM").

Geocoding, three passes
-----------------------
Coordinates come only from a real feature. There is no fallback that invents a
point, because a wrong pin on a school map is worse than no pin.

1. NPSN. Some OSM features carry `ref` or `school:ref` = NPSN. An exact match is
   authoritative and needs no fuzzy scoring at all.
2. Overpass name search within the school's own kelurahan bbox, then
   point-in-polygon against our kelurahan polygons to confirm the candidate
   really sits in that village. A name match across the whole city would put a
   wrong pin on a city full of identically-named schools.
3. Reuse an OSM point already resolved for another official school with the same
   normalised name (multi-campus entries share one address).

Anything that survives all three without a point is kept in the payload with
`lon: null` and listed in the UI as "belum ada titik", because dropping a real
school to make the map look complete would be the exact failure this script
exists to fix.

Also worth knowing: 21 NEGERI and 229 SWASTA. Only 21 public secondary schools in
the whole city, so any list that shows mostly "Negeri" is wrong.
"""

import json
import pathlib
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "kemdik"

# kecamatan_code -> (name, slug) exactly as Kemendikdasmen numbers them. These
# match depok-kelurahan.json's district_code suffix, which is how a school gets
# its kecamatan without geocoding.
KECAMATAN = [
    ("026601", "Sawangan"),
    ("026602", "Pancoran Mas"),
    ("026603", "Sukmajaya"),
    ("026604", "Cimanggis"),
    ("026605", "Beji"),
    ("026606", "Limo"),
    ("026607", "Cipayung"),
    ("026608", "Cilodong"),
    ("026609", "Cinere"),
    ("026610", "Tapos"),
    ("026611", "Bojongsari"),
]

# Two agents on purpose. The Kemendikdasmen pages are served to browsers and
# want one; Overpass answers 406 Not Acceptable to anything that looks like a
# browser and wants a tool that names itself.
UA_BROWSER = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120 Safari/537.36")
UA_TOOL = "depok-map-school-builder/1.0 (OSM data for a civic map)"
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]


# ---------------------------------------------------------------- fetch

def http(url, data=None, timeout=90, ua=None):
    headers = {"User-Agent": ua or UA_BROWSER, "Accept-Language": "id,en,*"}
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html_unescape(s)).strip()


def html_unescape(s):
    import html as _h
    return _h.unescape(s)


def fetch_register(force=False):
    """The 11 per-kecamatan pages, cached on disk."""
    CACHE.mkdir(parents=True, exist_ok=True)
    pages = {}
    for code, name in KECAMATAN:
        p = CACHE / f"{code}.html"
        if force or not p.exists():
            url = (f"https://referensi.data.kemendikdasmen.go.id"
                   f"/pendidikan/dikmen/{code}/3")
            for attempt in range(3):
                try:
                    p.write_text(http(url))
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    print(f"    retry {name}: {e}", file=sys.stderr)
                    time.sleep(3 * (attempt + 1))
            time.sleep(0.6)
        pages[code] = p.read_text()
    return pages


def parse_register(pages):
    rows = []
    for code, name in KECAMATAN:
        t = re.search(r"<table.*?</table>", pages[code], re.S)
        if not t:
            continue
        for tr in re.findall(r"<tr.*?</tr>", t.group(0), re.S)[1:]:
            c = [strip_html(x) for x in
                 re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
            if len(c) < 6 or not c[0].isdigit() or not c[1]:
                continue
            rows.append({
                "npsn": c[1].strip(),
                "name": c[2].strip(),
                "address": c[3].strip(),
                "village": c[4].strip(),
                "status": c[5].strip().upper(),
                "district_code": code,
                "district": name,
            })
    return rows


def name_variants(name):
    """The register abbreviates; OSM spells out. Nominatim matches on the
    literal string, so "SMAN 5 KOTA DEPOK" finds nothing while OSM has it as
    "SMA Negeri 5 Kota Depok". Each rewrite below corresponds to a spelling
    that actually occurs in one dataset and not the other."""
    up = name.strip()
    out = [up]
    v = re.sub(r"\bSMAN\b", "SMA Negeri", up, flags=re.I)
    if v != up:
        out.append(v)
    v2 = re.sub(r"\bSMKN\b", "SMK Negeri", up, flags=re.I)
    if v2 != up:
        out.append(v2)
    v3 = re.sub(r"\bMAS\b", "MA", up, flags=re.I)
    if v3 != up:
        out.append(v3)
    v4 = re.sub(r"\bSMAS\b", "SMA", up, flags=re.I)
    if v4 != up:
        out.append(v4)
    v5 = re.sub(r"\bSMKS\b", "SMK", up, flags=re.I)
    if v5 != up:
        out.append(v5)
    # and the reverse: OSM abbreviates sometimes
    v6 = re.sub(r"\bSMA NEGERI\b", "SMAN", up, flags=re.I)
    if v6 != up:
        out.append(v6)
    v7 = re.sub(r"\bSMK NEGERI\b", "SMKN", up, flags=re.I)
    if v7 != up:
        out.append(v7)
    return list(dict.fromkeys(out))


# Nominatim's published policy is at most 1 request per second, and it answers
# 429 when you exceed it. It does NOT say when the block lifts, so the backoff
# below is deliberately long: an earlier run fired ~1400 queries, got 429, and
# then spent minutes retrying into a wall while looking like it was working.
# 1.1s between calls plus a growing penalty on 429 stays inside the policy.
_NOM_WAIT = [1.1]


def nominatim(q, tries=3):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "jsonv2", "limit": 8,
         "countrycodes": "id", "addressdetails": 1})
    for t in range(tries):
        try:
            res = json.loads(http(url, timeout=30, ua=UA_TOOL))
            _NOM_WAIT[0] = 1.1          # recovered
            return res
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                # back off hard, and remember it: the next call waits too
                _NOM_WAIT[0] = min(_NOM_WAIT[0] * 2 + 4, 30)
                print(f"    nominatim {e.code}, waiting {_NOM_WAIT[0]:.0f}s", file=sys.stderr)
                time.sleep(_NOM_WAIT[0])
                continue
            return []
        except Exception:  # noqa: BLE001
            time.sleep(2 * (t + 1))
    return []


# ---------------------------------------------------------------- geometry

def depok_mask():
    """(rings, bbox) for point-in-polygon against our own kelurahan data."""
    gj = json.loads((ROOT / "public" / "data" / "depok-kelurahan.json").read_text())
    rings, xmin, ymin, xmax, ymax = [], 180.0, 90.0, -180.0, -90.0
    for f in gj["features"]:
        g = f["geometry"]

        def walk(x):
            nonlocal xmin, ymin, xmax, ymax
            if isinstance(x[0], (int, float)):
                xmin, xmax = min(xmin, x[0]), max(xmax, x[0])
                ymin, ymax = min(ymin, x[1]), max(ymax, x[1])
            else:
                for y in x:
                    walk(y)
        walk(g["coordinates"])
        polys = g["coordinates"] if g["type"] == "Polygon" else \
            [p for poly in g["coordinates"] for p in poly]
        rings.append((f["properties"]["village"], polys))
    return rings, (xmin, ymin, xmax, ymax)


def point_in_ring(lon, lat, ring):
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            xin = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < xin:
                inside = not inside
    return inside


def point_in_polygon(lon, lat, polys):
    return any(point_in_ring(lon, lat, poly) for poly in polys)


def village_of(lon, lat, rings):
    for name, polys in rings:
        if point_in_polygon(lon, lat, polys):
            return name
    return None


# ---------------------------------------------------------------- names

def norm(s):
    """Compare on the school's distinctive name, not its formatting."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    # level prefixes and honorifics vary but mean nothing for matching
    s = re.sub(r"\b(SMA|SMK|SMAN|SMKN|SMAS|SMKS|MA|MAS|MTS|SD|MI)\b", " ", s)
    s = re.sub(r"\b(NEGERI|NEGARA|SWASTA|PRIVAT)\b", " ", s)
    s = re.sub(r"\bKOTA DEPOK\b|\bDEPOK\b", " ", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def osm_candidates(name, bbox, tries=3):
    """Overpass name search inside one bbox."""
    q = ('[out:json][timeout:120];'
         f'nwr["name"~"{re.escape(name)}",i]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});'
         'out center tags;')
    body = urllib.parse.urlencode({"data": q}).encode()
    for ep in OVERPASS:
        try:
            return json.loads(http(ep, body, timeout=120, ua=UA_TOOL)).get("elements", [])
        except Exception:  # noqa: BLE001
            time.sleep(4)
    return []


def centroid(e):
    if e.get("type") == "node":
        return (e["lon"], e["lat"])
    c = e.get("center") or {}
    if "lon" in c:
        return (c["lon"], c["lat"])
    g = e.get("geometry")
    if g:
        xs = [p["lon"] for p in g]
        ys = [p["lat"] for p in g]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    return None


# ---------------------------------------------------------------- main

def main():
    force = "--force" in sys.argv
    print("fetching Kemendikdasmen register ...")
    rows = parse_register(fetch_register(force))
    print(f"  official schools: {len(rows)}")
    from collections import Counter
    print("  status:", dict(Counter(r["status"] for r in rows)))

    rings, _ = depok_mask()
    villages = {name: polys for name, polys in rings}

    # --- pass 1: OSM features carrying the NPSN as a ref. No fuzzy matching.
    print("\npass 1: matching on NPSN refs in OSM ...")
    q = ('[out:json][timeout:240];'
         'nwr["amenity"~"^(school|college)$"]'
         '(-6.461283,106.716747,-6.314414,106.918848);'
         'out center tags;')
    CACHE.mkdir(parents=True, exist_ok=True)
    fcache = CACHE / "osm-features.json"
    if fcache.exists() and not force:
        feats = json.loads(fcache.read_text())
        print("  (using cached OSM fetch; --force to refetch)")
    else:
        body = urllib.parse.urlencode({"data": q}).encode()
        feats = []
        for ep in OVERPASS:
            try:
                feats = json.loads(http(ep, body, timeout=240, ua=UA_TOOL)).get("elements", [])
                break
            except Exception as e:  # noqa: BLE001
                print(f"    {ep.split('/')[2]}: {e}", file=sys.stderr)
                time.sleep(5)
        fcache.write_text(json.dumps(feats))
    print(f"  OSM school features in bbox: {len(feats)}")

    by_npsn, by_name = {}, {}
    for e in feats:
        t = e.get("tags") or {}
        p = centroid(e)
        if not p:
            continue
        rec = {"lon": p[0], "lat": p[1],
               "osm_type": e["type"], "osm_id": e["id"],
               "osm_name": t.get("name", "")}
        for key in ("ref", "school:ref", "ref:school", "npsn", "NPSN",
                    "school_id", "SEKOLAHID"):
            v = t.get(key)
            if v and re.fullmatch(r"\d{8}", str(v).strip()):
                by_npsn.setdefault(str(v).strip(), rec)
        nn = norm(t.get("name", ""))
        if nn:
            by_name.setdefault(nn, []).append(rec)

    out, stats = [], Counter()
    for r in rows:
        rec = dict(r)
        rec["lon"] = rec["lat"] = None
        rec["village_verified"] = None
        hit = by_npsn.get(r["npsn"])
        if hit:
            rec.update(lon=hit["lon"], lat=hit["lat"], osm_type=hit["osm_type"],
                       osm_id=hit["osm_id"], match="npsn",
                       village_verified=village_of(hit["lon"], hit["lat"], rings))
            stats["npsn"] += 1
            out.append(rec)
            continue
        out.append(rec)
    print(f"  matched on NPSN: {stats['npsn']}")
    print("  (pass 2 reuses this same fetch -- no second Overpass round trip)")

    # --- pass 2: batched name search, per kecamatan.
    #
    # NOT one query per school. 243 sequential Overpass calls is ~40 minutes and
    # gets you rate-limited; 11 queries -- one per kecamatan, pulling every
    # school-ish feature in that bbox -- is under a minute, and matching happens
    # locally. Same result, because a school's own kecamatan is already known
    # from the register row it came from.
    print("\npass 2: batched Overpass fetch per kecamatan, matched locally ...")
    per_kec = {}
    for code, name in KECAMATAN:
        rows_k = [r for r in out if r["lon"] is None and r["district_code"] == code]
        if not rows_k:
            continue
        polys_all = [polys for vn, polys in rings
                     if vn in {r["village"] for r in rows_k}]
        xs = [p_[0] for polys in polys_all for poly in polys for p_ in poly]
        ys = [p_[1] for polys in polys_all for poly in polys for p_ in poly]
        # Overpass answers 400 for a bbox with long decimals; 6 places is about
        # 10cm, far finer than any school's footprint here.
        bbox = tuple(round(v, 6) for v in (min(xs), min(ys), max(xs), max(ys)))
        # already fetched in pass 1; filter locally instead of re-querying
        els = [e for e in feats if bbox[0] <= (centroid(e) or (0, 0))[0] <= bbox[2]
               and bbox[1] <= (centroid(e) or (0, 0))[1] <= bbox[3]]
        idx = {}
        for e in els:
            c = centroid(e)
            if not c:
                continue
            idx.setdefault(norm((e.get("tags") or {}).get("name", "")), []).append(
                {"lon": c[0], "lat": c[1], "osm_type": e["type"], "osm_id": e["id"],
                 "osm_name": (e.get("tags") or {}).get("name", "")})
        # second chance: any candidate at all in the claimed village, scored by
        # how much of the official name it shares
        allc = [c for v in idx.values() for c in v]
        for r in rows_k:
            vn = norm(r["name"])
            cands = list(idx.get(vn, []))
            # Second chance only when the kecamatan holds exactly one candidate
            # and its name is a clear substring of the official name ("MAS
            # ISLAMIYAH" vs "SMAS ISLAMIYAH SAWANGAN"). Anything looser than that
            # starts putting pins on the wrong school in a city with several
            # namesakes, so it is deliberately not attempted.
            if not cands and len(allc) == 1 and norm(allc[0]["osm_name"]) in vn:
                cands = allc
            good = None
            polys_v = villages.get(r["village"])
            for c in cands:
                if polys_v and not point_in_polygon(c["lon"], c["lat"], polys_v):
                    continue
                good = c
                break
            if good:
                r.update(lon=good["lon"], lat=good["lat"],
                         osm_type=good["osm_type"], osm_id=good["osm_id"],
                         match="name", village_verified=r["village"])
                stats["name"] += 1
            else:
                stats["unresolved"] += 1
        per_kec[name] = (len(rows_k), len(els))
        print(f"    {name:14} {len(rows_k):>3} schools | {len(els):>3} OSM features | "
              f"resolved {stats['name']} total")

    print(f"  matched on name: {stats['name']}")

    # --- pass 3: Nominatim, by name.
    #
    # A previous version ran this for every unresolved school and got almost
    # nothing, for a reason worth recording: Nominatim indexes the same OSM data
    # as pass 2, so if a school is not mapped there is no point for it to find.
    # The city has 299 school features in total, and only 36 distinct ones whose
    # names read as secondary. Against a register of 244, that is the ceiling.
    #
    # It is kept, and cached, because it is the one pass that can resolve a
    # school mapped under a name the register spells differently -- and it does
    # work when the school is actually there (SMAN 2 -> "SMAN 2 Depok, Abadijaya").
    # It runs once, at most 3 queries per school, with a 1.1s floor and an
    # exponential backoff on 429, and every result is cached on disk so a rerun
    # costs nothing. The earlier run was not rate-limit-safe: it fired ~1400
    # queries and spent minutes retrying into a 429.
    todo = [r for r in out if r["lon"] is None]
    stats["unresolved"] = 0   # pass 2 counted these too; pass 3 is the final say
    print(f"\npass 3: Nominatim by name for {len(todo)} schools "
          f"(max {len(todo) * 3} queries, cached) ...")
    ncache_path = CACHE / "nominatim.json"
    ncache = json.loads(ncache_path.read_text()) if ncache_path.exists() else {}
    done = 0
    for r in todo:
        street = re.sub(r"(?i)\b(jl|jl\.|jalan|no\.?|rt|rw)\b.*$", "",
                        r["address"]).strip(" ,.")
        queries = [f"{nv}, {r['village']}, {r['district']}, Depok"
                   for nv in name_variants(r["name"])[:2]]
        if street:
            queries += [f"{nv}, {street}, {r['village']}, Depok"
                        for nv in name_variants(r["name"])[:1]]
        for q in queries[:3]:
            if q in ncache:
                hits = ncache[q]
            else:
                hits = nominatim(q)
                ncache[q] = hits
            if not hits:
                time.sleep(_NOM_WAIT[0])
                continue
            for h in hits:
                try:
                    lon, lat = float(h["lon"]), float(h["lat"])
                except (KeyError, ValueError):
                    continue
                # Hard verification against the register's own kelurahan, so a
                # namesake in another district cannot win on string similarity.
                if village_of(lon, lat, rings) != r["village"]:
                    continue
                r.update(lon=lon, lat=lat, match="nominatim",
                         osm_type=h.get("osm_type") or None,
                         osm_id=h.get("osm_id") or None,
                         village_verified=r["village"])
                stats["nominatim"] += 1
                break
            if r["lon"] is not None:
                break
            time.sleep(_NOM_WAIT[0])
        if r["lon"] is None:
            stats["unresolved"] += 1
        done += 1
        if done % 25 == 0:
            ncache_path.write_text(json.dumps(ncache))
            print(f"    {done}/{len(todo)}  (nominatim {stats['nominatim']})")
    ncache_path.write_text(json.dumps(ncache))
    print(f"  matched on Nominatim: {stats['nominatim']}")
    print(f"  UNRESOLVED (kept in payload, no pin): {stats['unresolved']}")

    # --- which are SMA/SMK vs MA? The tab says SMA/SMK, so split it out.
    for r in out:
        n = norm(r["name"])
        r["level"] = "MA" if re.match(r"^(MAS|MTS|MA)\b", r["name"].upper()) else "SMA/SMK"

    # The register's page only ever says "SMA (Sederajat)" and "SMK (Sederajat)"
    # as column headings, not per row, so the level has to come from the name --
    # which is reliable here, unlike the OSM-only list: an official row is
    # always named for its level ("SMKS Kesehatan Logos"), never "Sekolah".
    def label_of(r):
        if r["level"] == "MA":
            return "Madrasah Aliyah Negeri" if r["status"] == "NEGERI" \
                else "Madrasah Aliyah Swasta"
        lvl = "SMK" if re.match(r"^SMK", r["name"].strip().upper()) else "SMA"
        return f"{lvl} {'Negeri' if r['status'] == 'NEGERI' else 'Swasta'}"

    js_rows = [{
        "key": r["npsn"],
        "npsn": r["npsn"], "name": r["name"], "address": r["address"],
        "village": r["village"], "district": r["district"],
        "status": r["status"].lower(),
        "label": label_of(r),
        "level": r["level"],
        "lon": r["lon"], "lat": r["lat"],
        "osm_type": r.get("osm_type"), "osm_id": r.get("osm_id"),
        "match": r.get("match"),
        "placed": r["lon"] is not None,
    } for r in out]
    matched = sum(1 for r in out if r["lon"] is not None)

    matched = sum(1 for r in js_rows if r["lon"] is not None)
    resolved = matched
    how = {}
    for r in out:
        how[r.get("match") or "none"] = how.get(r.get("match") or "none", 0) + 1
    src = ("Kemendikdasmen Data Referensi Pendidikan "
           "(referensi.data.kemendikdasmen.go.id) -- NPSN, nama, alamat, "
           "kelurahan, status resmi. Koordinat dari OpenStreetMap (ODbL): "
           f"{how.get('npsn', 0)} dari NPSN, {how.get('name', 0)} dari nama, "
           f"{how.get('nominatim', 0)} dari Nominatim; {len(js_rows) - matched} "
           "sekolah belum ada titik di OSM dan tetap terdaftar tanpa peta.")

    out_js = ("// Generated by scripts/build-schools-kemdik.py -- do not edit.\n"
              "window.DEPOK_SCHOOLS = " +
              json.dumps({"source": src, "schools": js_rows}, ensure_ascii=False,
                         indent=1) + ";\n")
    (ROOT / "src" / "schools.js").write_text(out_js)
    CACHE_OUT = ROOT / ".cache" / "kemdik" / "resolved.json"
    CACHE_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))

    print(f"\nwrote src/schools.js ({len(js_rows)} schools, {resolved} with a point)")
    print(f"  also cached {CACHE_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()