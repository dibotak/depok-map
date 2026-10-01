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