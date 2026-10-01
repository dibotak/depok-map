#!/usr/bin/env python3
"""
Fetch the 11 kecamatan (district) boundaries of Kota Depok.

The upstream repo stores each administrative level in its own file:
    id3276_kota_depok.geojson                <- 63 kelurahan (build-data.py)
    id3276031_cilodong.geojson               <- district-level, one per kecamatan

The file names are discovered from the repo listing rather than hardcoded, so a
renamed kecamatan surfaces as a clear failure instead of a silently missing area.

DISSOLVING IS REQUIRED, NOT OPTIONAL
-------------------------------------
The upstream per-kecamatan file does NOT contain one district polygon. It contains
one feature per *kelurahan* inside that district, each carrying the district's
own name. `id3276031_cilodong.geojson` ships 5 features -- Cilodong, Jatimulya,
Kalibaru, Kalimulya, Sukamaju -- i.e. the five children, not the parent.

Collecting those parts into a MultiPolygon (the previous approach) therefore does
not produce a district boundary at all. MapLibre strokes every part's outline, so
the shared edges between children are drawn too: `kec-line` rendered all 63
kelurahan borders at city view, and `kec-fill` painted 63 shapes where there should
be 11. That reads as "the whole thing is subdivided" and looks like a basemap
problem, because the extra hairlines sit exactly where child boundaries would be.

So the children are unioned per district_code here. The result is one polygon per
kecamatan with the interior edges removed -- which is the published boundary the
app actually intends to draw. Overlaps are expected (children share edges, not
areas) and `unary_union` absorbs them.

This dissolves the upstream *district* file's parts. The 63 kelurahan polygons in
src/data.js are untouched and remain the authoritative drill-down geometry.
"""
import json
import sys
import urllib.request
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import unary_union

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


def geometry_out(geom):
    """Shapely geometry -> GeoJSON, rounded, with duplicate vertices dropped.

    Rounding must happen *after* the union: rounding first can nudge shared edges
    apart by a rounding error, and the union would then keep a sliver instead of
    absorbing the seam.
    """
    if geom.is_empty:
        raise ValueError("empty geometry")
    if isinstance(geom, Polygon):
        coords = [list(geom.exterior.coords)]
        for ring in geom.interiors:
            coords.append(list(ring.coords))
        return {"type": "Polygon", "coordinates": strip(round_coords(coords))}
    if isinstance(geom, MultiPolygon):
        return {
            "type": "MultiPolygon",
            "coordinates": strip(round_coords([list(p.exterior.coords) for p in geom.geoms])),
        }
    raise SystemExit(f"unexpected dissolved geometry type {geom.geom_type}")


def dissolve(parts):
    """Union the parts of one district into a single geometry.

    Children of a district share edges, so `unary_union` welds them and drops the
    interior boundaries. What remains is the district outline: one ring per
    disconnected piece, with no seams.
    """
    geoms = [shape(g) for g in parts]
    union = unary_union(geoms)

    # A district that dissolves to a bare line or point means the children did not
    # actually cover an area. Fail loudly rather than emit geometry that renders
    # as nothing and is indistinguishable from a styling bug.
    if union.geom_type not in ("Polygon", "MultiPolygon"):
        raise SystemExit(f"district dissolved to {union.geom_type}, expected an area")

    # Repair any self-touching the weld leaves behind, so MapLibre's even-odd
    # fill rule does not punch holes in the district.
    if not union.is_valid:
        sys.stderr.write("  repairing invalid dissolve\n")
        union = union.buffer(0)
        if union.geom_type == "GeometryCollection":
            polys = [g for g in union.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
            if not polys:
                raise SystemExit("dissolve produced no usable polygon")
            union = unary_union(polys)

    return union


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

        # One file = one kecamatan, but it ships one feature per *child*
        # kelurahan. Collect the raw geometries per district_code and dissolve
        # them below; keeping the parts would redraw every child border.
        for f in feats:
            p = f["properties"]
            out = {k: p.get(k) for k in KEEP}
            if not out.get("district") or not out.get("district_code"):
                sys.stderr.write(f"  SKIP {name}: missing district identity {p}\n")
                continue
            features.append({
                "type": "Feature",
                "properties": out,
                "geometry": f["geometry"],
            })
        sys.stderr.write(f"  ok {name} ({len(feats)} part(s))\n")

    # Dissolve per district_code. The result is one polygon per kecamatan with the
    # interior child borders removed -- see the module docstring for why this is
    # load-bearing rather than cosmetic.
    by_code = {}
    for f in features:
        by_code.setdefault(f["properties"]["district_code"], {"props": f["properties"], "parts": []})
        by_code[f["properties"]["district_code"]]["parts"].append(f["geometry"])

    merged = []
    for code, v in sorted(by_code.items(), key=lambda kv: kv[1]["props"]["district"]):
        n_parts = len(v["parts"])
        try:
            union = dissolve(v["parts"])
        except Exception as e:
            raise SystemExit(f"{v['props']['district']}: dissolve failed: {e}")
        geom = geometry_out(union)
        n_out = len(geom["coordinates"]) if geom["type"] == "MultiPolygon" else 1
        merged.append({"type": "Feature", "properties": v["props"], "geometry": geom})
        sys.stderr.write(
            f"  dissolved {v['props']['district']:16} {n_parts:>2} part(s) -> "
            f"{geom['type']} with {n_out} ring(s)\n"
        )

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