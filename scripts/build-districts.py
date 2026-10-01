#!/usr/bin/env python3
"""
Fetch the 11 kecamatan (district) boundaries of Kota Depok.

The upstream repo stores each administrative level in its own file:
    id3276_kota_depok.geojson                <- 63 kelurahan (build-data.py)
    id3276031_cilodong.geojson               <- district-level, one per kecamatan

Using the district files directly is better than dissolving the kelurahan
ourselves: the upstream geometry is the authoritative published boundary, so
stitching 63 polygons back together would both risk gaps along shared edges and
invent a shape BPS never published.

The file names are discovered from the repo listing rather than hardcoded, so a
renamed kecamatan surfaces as a clear failure instead of a silently missing area.
"""
import json
import sys
import urllib.request
from pathlib import Path

API = "https://api.github.com/repos/JfrAziz/indonesia-district/contents"
RAW = "https://raw.githubusercontent.com/JfrAziz/indonesia-district/master"
DIR = "id32_jawa_barat/id3276_kota_depok"

OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "data"
KEEP = ["district", "district_code", "regency", "regency_code", "province", "province_code"]

PRECISION = 6


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": "depok-map/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read() if binary else json.loads(r.read())


def round_coords(c):
    if isinstance(c, (int, float)):
        return round(c, PRECISION)
    return [round_coords(x) for x in c]


def strip(coords):
    """Drop consecutive duplicate vertices created by rounding."""
    if not isinstance(coords, list) or not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        out = []
        for pt in coords:
            if not out or out[-1] != pt:
                out.append(pt)
        return out
    return [strip(x) for x in coords]


def geometry_out(g):
    if g["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": strip(round_coords(g["coordinates"]))}
    if g["type"] == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": strip(round_coords(g["coordinates"]))}
    raise SystemExit(f"unexpected geometry type {g['type']}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    listing = get(f"{API}/{DIR}")
    files = sorted(f["name"] for f in listing if f["type"] == "file" and f["name"].endswith(".geojson"))

    # The regency-level file is the 63-kelurahan file, not a kecamatan.
    district_files = [f for f in files if f != "id3276_kota_depok.geojson"]

    sys.stderr.write(f"found {len(district_files)} district files\n")
    if len(district_files) != 11:
        sys.stderr.write(f"WARNING: expected 11 kecamatan, found {len(district_files)}\n")

    features = []
    for name in district_files:
        url = f"{RAW}/{DIR}/{name}"
        try:
            fc = get(url)
        except Exception as e:
            # A single unavailable kecamatan must not sink the whole build.
            sys.stderr.write(f"  SKIP {name}: {e}\n")
            continue

        feats = fc.get("features", [])
        if not feats:
            sys.stderr.write(f"  SKIP {name}: no features\n")
            continue

        # One file = one kecamatan, but a boundary may be split into several
        # features; merge them by dissolving is wrong, so keep each part and let
        # the name carry the identity.
        for f in feats:
            p = f["properties"]
            out = {k: p.get(k) for k in KEEP}
            if not out.get("district") or not out.get("district_code"):
                sys.stderr.write(f"  SKIP {name}: missing district identity {p}\n")
                continue
            features.append({
                "type": "Feature",
                "properties": out,
                "geometry": geometry_out(f["geometry"]),
            })
        sys.stderr.write(f"  ok {name} ({len(feats)} part(s))\n")

    # Merge parts that share a district_code into one MultiPolygon feature, so
    # clicking any part selects the whole kecamatan.
    by_code = {}
    for f in features:
        by_code.setdefault(f["properties"]["district_code"], {"props": f["properties"], "parts": []})
        by_code[f["properties"]["district_code"]]["parts"].append(f["geometry"])

    merged = []
    for code, v in sorted(by_code.items(), key=lambda kv: kv[1]["props"]["district"]):
        if len(v["parts"]) == 1:
            geom = v["parts"][0]
        else:
            polys = []
            for g in v["parts"]:
                polys.extend(g["coordinates"] if g["type"] == "Polygon" else g["coordinates"])
            geom = {"type": "MultiPolygon", "coordinates": polys}
        merged.append({"type": "Feature", "properties": v["props"], "geometry": geom})

    payload = {"type": "FeatureCollection", "features": merged}
    out_file = OUT_DIR / "depok-kecamatan.json"
    out_file.write_text(json.dumps(payload, separators=(",", ":")))

    sys.stderr.write(
        f"wrote {len(merged)} kecamatan -> {out_file.name} "
        f"({out_file.stat().st_size / 1024:.0f} KB)\n"
    )
    for f in merged:
        sys.stderr.write(f"    {f['properties']['district']:16} {f['properties']['district_code']}\n")


if __name__ == "__main__":
    main()