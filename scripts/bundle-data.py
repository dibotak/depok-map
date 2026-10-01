#!/usr/bin/env python3
"""
Bundle both administrative levels + meta into src/data.js as a single global.

Why a .js and not several .json fetches: it removes request round-trips before
the map can draw, and it keeps the app loadable from file:// for inspection.

Run build-data.py and build-districts.py first.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "public" / "data"
OUT = ROOT / "src" / "data.js"

kel = json.loads((DATA / "depok-kelurahan.json").read_text())
kec = json.loads((DATA / "depok-kecamatan.json").read_text())
meta = json.loads((DATA / "depok-meta.json").read_text())

payload = {
    "meta": meta,
    "features": kel["features"],       # 63 kelurahan
    "districts": kec["features"],      # 11 kecamatan
}

js = "window.DEPOK=" + json.dumps(payload, separators=(",", ":")) + ";\n"
OUT.write_text(js)

kb = len(js) / 1024
print(f"wrote {OUT.relative_to(ROOT)}  {kb:.0f} KB  "
      f"({len(kel['features'])} kelurahan, {len(kec['features'])} kecamatan)")

# The drill-down filters kelurahan by district_code, so a district with no
# children would open to an empty view. Catch that here, at build time.
codes = {d["properties"]["district_code"] for d in kec["features"]}
covered = {f["properties"]["district_code"] for f in kel["features"]}
missing = codes - covered
if missing:
    raise SystemExit(f"ERROR: kecamatan with no kelurahan: {sorted(missing)}")
orphan = covered - codes
if orphan:
    raise SystemExit(f"ERROR: kelurahan pointing at a missing kecamatan: {sorted(orphan)}")
print(f"hierarchy consistent: {len(codes)} kecamatan cover all {len(covered)} district codes")

# A district boundary must be DISSOLVED: one ring per disconnected piece, with no
# interior child borders. An undissolved district ships one MultiPolygon part per
# kelurahan, and MapLibre strokes every part's outline -- so `kec-line` redraws all
# 63 child borders at city view and `kec-fill` paints 63 shapes where there should
# be 11. build-districts.py used to do exactly that, and the result looked like a
# basemap problem rather than a build problem, because the stray hairlines land
# precisely where child boundaries belong.
#
# So assert the part count never exceeds the child count, and that a district with
# several children did not come out with one ring per child.
from shapely.geometry import shape  # noqa: E402

for d in kec["features"]:
    name = d["properties"]["district"]
    n_kids = sum(1 for f in kel["features"] if f["properties"]["district_code"] == d["properties"]["district_code"])
    g = shape(d["geometry"])
    n_rings = len(g.geoms) if g.geom_type == "MultiPolygon" else 1
    if n_rings > n_kids:
        raise SystemExit(
            f"ERROR: {name} has {n_rings} ring(s) for {n_kids} kelurahan -- "
            f"the district boundary is not dissolved (see build-districts.py)"
        )
    if g.geom_type not in ("Polygon", "MultiPolygon"):
        raise SystemExit(f"ERROR: {name} dissolved to {g.geom_type}")
    if not g.is_valid:
        raise SystemExit(f"ERROR: {name} is not a valid polygon")
print("all 11 kecamatan dissolved: interior child borders removed")