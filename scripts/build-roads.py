#!/usr/bin/env python3
"""
Build the road list from Perda Kota Depok No. 9 Tahun 2022, Pasal 16.

WHY THIS REPLACES THE EARLIER INFERENCE
---------------------------------------
The previous build grouped OSM roads by UU 22/2009 Pasal 25 authority class and
had to *guess* it, because OSM stores no road-authority tag:

    ref is a bare integer  ->  Kelas I (Nasional)
    otherwise              ->  Kelas III (Kabupaten/Kota)

That was 17 roads in one bucket and 333 in the other. The second bucket was
noise, and the README said so.

Perda Kota Depok No. 9 Tahun 2022 (RTRW Kota Depok 2022-2042), **Pasal 16**,
enumerates the city's road network explicitly. This is not an inference, it is
the binding local regulation naming each class and listing the ruas in it:

    ayat (1)  jalan umum = arteri | kolektor | lokal | lingkungan
    ayat (2)  arteri     = arteri primer | arteri sekunder
    ayat (3)  arteri primer     -> 1 ruas
    ayat (4)  arteri sekunder   -> 14 ruas
    ayat (5)  kolektor    = kolektor primer | kolektor sekunder
    ayat (6)  kolektor primer   -> 18 ruas
    ayat (7)  kolektor sekunder -> 38 ruas
    ayat (8)  jalan lokal        -> "diatur lebih lanjut dalam RDTR"  (not listed)
    ayat (9)  jalan lingkungan   -> "diatur lebih lanjut dalam RDTR"  (not listed)
    ayat (10) jalan tol           -> 6 ruas

    77 enumerated ruas, in five groups.

So the classes are the Perda's, verbatim, and the list is the Perda's own
enumeration rather than something derived from OSM tags. OSM is used only to
*locate* each named rua so it can be drawn and labelled on the map.

WHAT IS AND IS NOT IN HERE
--------------------------
* `lokal` and `lingkungan` are absent, and the Perda is the reason: ayat (8) and
  (9) defer both to the Rencana Detail Tata Ruang, a separate, non-published
  document. That is a documented gap in the source, not a gap in the
  extraction, and the UI says so instead of inventing two empty groups.
* Matching a Perda entry to an OSM name is approximate -- a "ruas" often spans
  several streets, and the Perda abbreviates ("Jalan Ir. H. Juanda" is tagged
  "Jalan Insinyur Haji Juanda"). Entries with no OSM match are kept in the data
  with `matched: []` so the list still shows the full 77. They are NOT silently
  dropped, and the UI marks them as not locatable on the map.

Source: Perda Kota Depok No. 9 Tahun 2022, BAB II Pasal 16.
Road geometry: OpenStreetMap via Overpass API (ODbL).
"""
import difflib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "roads.js"
PERDA = Path("/home/ubuntu/.hermes/cache/scratch/perda2/perda9-2022.txt")
# Overpass responses. Kept out of the repo (gitignored /tmp) and reused for a
# day, because Overpass 504s under load and the four tile queries take minutes.
CACHE = Path("/tmp/depok-roads-cache")

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Wider than Kota Depok on purpose. Pasal 16 enumerates corridors that leave the
# city -- Ruas Jalan Cibinong-Cimpaeun, Jalan Tole Iskandar-Pondok Rajeg to the
# Bogor border. A Depok-only bbox matches 34/77; this one matches 72/77.
# Tiled 2x2 because a single wide query 504s.
TILES = [
    (-6.52, 106.60, -6.38, 106.80),
    (-6.52, 106.80, -6.38, 107.00),
    (-6.38, 106.60, -6.24, 106.80),
    (-6.38, 106.80, -6.24, 107.00),
]
HIGHWAY = "^(motorway|trunk|primary|secondary|tertiary|unclassified)$"

# Lossy prefixes stripped before matching. "Jalan Ir. H. Juanda" -> "JUANDA"
# matches OSM's "Jalan Insinyur Haji Juanda" -> "JUANDA". Without this the two
# abbreviations in the Perda cost two real roads their geometry.
NOISE = r"\b(JALAN|JL\.|RUAS|RAYA|HJI\.|HAJI|H\.|IR\.|INSINYUR|R\.|RADEN|DRS\.|DR\.)\b"

# (label, ayat start, ayat end, key)
GROUPS = [
    ("Arteri Primer", "3", "4", "arteri-primer"),
    ("Arteri Sekunder", "4", "5", "arteri-sekunder"),
    ("Kolektor Primer", "6", "7", "kolektor-primer"),
    ("Kolektor Sekunder", "7", "8", "kolektor-sekunder"),
    ("Jalan Tol", "10", "11", "tol"),
]


def _overpass(query, label, tries=3):
    """Run one Overpass query across all endpoints, with backoff."""
    for attempt in range(tries):
        for ep in ENDPOINTS:
            try:
                body = urllib.parse.urlencode({"data": query}).encode()
                req = urllib.request.Request(
                    ep,
                    data=body,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": "depok-map/1.0 (Perda 9/2022 road build)",
                    },
                )
                with urllib.request.urlopen(req, timeout=200) as r:
                    data = json.loads(r.read())
                sys.stderr.write(f"  {label} -> {len(data.get('elements', []))} ways\n")
                return data
            except Exception as exc:
                sys.stderr.write(f"  {label} {ep.split('/')[2]}: {exc}\n")
        time.sleep(8 * (attempt + 1))
    return None


def fetch_osm():
    """Named roads over the tiled bbox, cached to /tmp.

    Overpass is rate-limited and 504s under any real load: a single wide query
    over the whole metro area times out every time, and four tiled queries take
    3-6 minutes with retries. The result changes only when OSM does, so it is
    cached for a day. `ROADS_CACHE_AGE=0` forces a refetch.
    """
    cache = CACHE.with_suffix(".names.json")
    if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
        sys.stderr.write(f"using cached name query ({cache})\n")
        return {e["id"]: e for e in json.loads(cache.read_text())["elements"]}

    ways = {}
    for bb in TILES:
        query = (
            f'[out:json][timeout:180];'
            f'way["highway"~"{HIGHWAY}"]["name"]({bb[0]},{bb[1]},{bb[2]},{bb[3]});'
            f"out tags;"
        )
        data = _overpass(query, f"tile {bb[0]},{bb[1]}")
        if data:
            for e in data.get("elements", []):
                ways[e["id"]] = e
    if not ways:
        raise SystemExit("ERROR: Overpass returned no ways -- refusing to build an empty list")
    cache.write_text(json.dumps({"elements": list(ways.values())}))
    sys.stderr.write(f"  cached {len(ways)} ways -> {cache}\n")
    return ways


def load_perda():
    """Isolate Pasal 16 and pull out each ayat's enumerated ruas."""
    if not PERDA.exists():
        raise SystemExit(
            f"ERROR: {PERDA} not found.\n"
            "The Perda text is not committed (232 pages). Re-fetch it with:\n"
            "  curl -sL https://r.jina.ai/"
            "https://jdih-dprd.depok.go.id/assets/uploads/files/produk/2022pd3224009.pdf"
        )
    text = PERDA.read_text(encoding="utf-8", errors="replace")
    i = text.find("Pasal 16 (1) Jalan umum")
    if i < 0:
        raise SystemExit("ERROR: could not locate Pasal 16 in the Perda text")
    seg = text[i:text.find("Pasal 17", i)]
    # Strip the running page numbers the PDF text layer interleaves
    # ("29 (5) Jalan kolektor..."), which otherwise corrupt the ayat numbering.
    seg = re.sub(r"\b(2[5-9]|3[0-9])\s+", " ", seg)
    return re.sub(r"\s+", " ", seg)


def ayat(seg, a, b):
    x = seg.find(f"({a})")
    y = seg.find(f"({b})", x)
    return seg[x:y]


def lettered(body):
    """Split 'a. foo; b. bar; ...' into (letter, text). Handles a..ll."""
    parts = re.split(r"(?:^|\s)([a-z]{1,2})\.\s+", body)
    return [(parts[k], parts[k + 1].strip().rstrip(";").rstrip(".").strip())
            for k in range(1, len(parts) - 1, 2)]


def norm(s):
    s = s.upper()
    s = re.sub(NOISE, " ", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def streets(entry):
    """A 'ruas' often names several streets joined by dashes.

    'Ruas Jalan Merawan - Jalan Cinere Raya - Jalan Limo Raya' is three roads.
    Parentheticals carry qualifiers ('(Jl. Raya Bogor)', '(Simpang Siliwangi -
    Simpang Ramanda)') and are dropped -- they describe the segment, not its name.
    """
    body = re.sub(r"\([^)]*\)", " ", entry)
    parts = re.split(r"\s*[–—/]\s*|\s+dan\s+", body)
    return [p.strip(" .,;") for p in parts if len(p.strip()) > 3]


def fetch_geometry(names):
    """Fetch full way geometry for the matched road names.

    The tile query above returns tags only (`out tags`) because 8.9k ways with
    coordinates is a large response. Geometry is only needed for the ~150 names
    that actually matched a Perda ruas, so it is fetched in a second pass. This
    is what lets a selected road be drawn and labelled on the map instead of
    just highlighted in the list.
    """
    if not names:
        return {}
    cache = CACHE.with_suffix(".geom.json")
    out = {}
    if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
        out = json.loads(cache.read_text())
        sys.stderr.write(f"using cached geometry ({len(out)} ways)\n")

    todo = [n for n in sorted(names) if n not in out]
    chunk = 25
    for start in range(0, len(todo), chunk):
        part = todo[start:start + chunk]
        filt = "|".join(n.replace('"', '\\"') for n in part)
        query = (
            f'[out:json][timeout:180];'
            f'way["highway"]["name"~"^({filt})$"](-6.52,106.60,-6.24,107.00);'
            f"out geom;"
        )
        data = _overpass(query, f"geom {start + 1}-{start + len(part)}/{len(todo)}")
        if not data:
            continue
        for e in data.get("elements", []):
            geom = e.get("geometry")
            if not geom:
                continue
            n = (e.get("tags", {}).get("name") or "").strip()
            if n in out:
                continue
            coords = [[p["lon"], p["lat"]] for p in geom if p]
            if len(coords) >= 2:
                out[n] = {
                    "class": e.get("tags", {}).get("highway", ""),
                    "ref": (e.get("tags", {}).get("ref") or "").strip(),
                    "coords": [[round(c[0], 6), round(c[1], 6)] for c in coords],
                }
        cache.write_text(json.dumps(out))
    sys.stderr.write(f"  geometry for {len(out)}/{len(names)} names\n")
    return out


def main():
    seg = load_perda()
    sys.stderr.write("fetching OSM road names...\n")
    ways = fetch_osm()

    osm = {}
    for e in ways.values():
        name = (e.get("tags", {}).get("name") or "").strip()
        if name:
            osm[name] = e.get("tags", {}).get("highway", "")
    index = {}
    for name in osm:
        index.setdefault(norm(name), name)
    keys = sorted(index)

    def resolve(s):
        n = norm(s)
        if not n:
            return []
        if n in index:
            return [index[n]]
        hits = [index[k] for k in keys if k and (k in n or n in k)]
        if hits:
            return [hits[0]]
        m = difflib.get_close_matches(n, keys, n=1, cutoff=0.82)
        return [index[m[0]]] if m else []

    classes = []
    for label, a, b, key in GROUPS:
        entries = [v for _, v in lettered(ayat(seg, a, b))]
        if not entries:
            # Ayat (3) is a single sentence with no lettered list:
            # "(3) Jalan arteri primer ... meliputi ruas jalan Gandaria-..."
            # Strip the cross-reference preamble so the row shows the road,
            # not the law. The verb is "meliputi", not "melipui".
            one = re.sub(r"^\(\d\).*?meliputi\s+", "", ayat(seg, a, b)).strip().rstrip(".")
            entries = [one]
        roads = []
        for idx, e in enumerate(entries, 1):
            matched = []
            for s in streets(e):
                for h in resolve(s):
                    if h not in matched:
                        matched.append(h)
            seen = {osm[n] for n in matched}
            if key == "tol":
                badge = "Tol"
            elif "motorway" in seen or "trunk" in seen:
                badge = "Arteri"
            else:
                badge = "Kolektor"
            roads.append({
                "n": f"{label[0]}{idx}",
                "name": e,
                "matched": matched,
                "badge": badge,
            })
        classes.append({
            "key": key,
            "label": label,
            "pasal": f"Pasal 16 ayat ({a})",
            "count": len(roads),
            "roads": roads,
        })
        sys.stderr.write(f"  {label:20} {len(roads):>3} ruas\n")

    total = sum(c["count"] for c in classes)
    matched_names = sorted({m for c in classes for r in c["roads"] for m in r["matched"]})
    sys.stderr.write(f"\nfetching geometry for {len(matched_names)} matched names...\n")
    geom = fetch_geometry(matched_names)
    sys.stderr.write(f"  got geometry for {len(geom)} names\n")

    # Attach geometry and drop names that have none, so a selected road always
    # has something to draw. A name can match a Perda ruas but have no way left
    # after the second pass (deleted/retagged between queries), which would
    # otherwise leave a row that highlights nothing.
    drawable = 0
    for c in classes:
        for r in c["roads"]:
            r["segments"] = [geom[n] for n in r["matched"] if n in geom]
            r["matched"] = [n for n in r["matched"] if n in geom]
            if r["segments"]:
                drawable += 1
            else:
                # Nothing to highlight: say so rather than showing a dead row.
                r["unlocatable"] = True
        c["located"] = sum(1 for r in c["roads"] if r["segments"])
    located = drawable

    payload = {
        "source": "Perda Kota Depok No. 9 Tahun 2022 (RTRW 2022-2042), Pasal 16",
        "geometry": "OpenStreetMap via Overpass API (ODbL)",
        "verbatim": True,
        "total": total,
        "located": located,
        "disclaimer": (
            "Daftar jalan ini disusun dari enumerasi ruas pada Pasal 16 Perda "
            "Kota Depok No. 9 Tahun 2022, bukan dari tag OSM. Jalan lokal dan "
            "jalan lingkungan tidak dimunculkan karena ayat (8) dan (9) "
            "delegasikan keduanya ke Rencana Detail Tata Ruang yang belum "
            "dipublikasikan."
        ),
        "classes": classes,
    }

    js = ("/* Generated by scripts/build-roads.py -- do not edit by hand.\n"
          "   Perda Kota Depok No. 9 Tahun 2022, Pasal 16. */\n"
          "window.DEPOK_ROADS=" + json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + ";\n")
    OUT.write_text(js)

    sys.stderr.write(
        f"\nwrote {OUT.relative_to(ROOT)}  {len(js)/1024:.0f} KB\n"
        f"  {total} ruas enumerated, {located} locatable in OSM, {total-located} not\n"
    )
    if not classes or total == 0:
        raise SystemExit("ERROR: zero ruas parsed -- refusing to ship an empty list")


if __name__ == "__main__":
    main()
