#!/usr/bin/env python3
"""
Bundle the GeoJSON + meta into src/data.js as a single global.

Why a .js and not two .json fetches: it removes a request round-trip before the
map can draw, and it keeps the app loadable from file:// for quick inspection.
Run build-data.py first.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "public" / "data"
OUT = ROOT / "src" / "data.js"

fc = json.loads((DATA / "depok-kelurahan.json").read_text())
meta = json.loads((DATA / "depok-meta.json").read_text())

payload = {"meta": meta, "features": fc["features"]}
js = "window.DEPOK=" + json.dumps(payload, separators=(",", ":")) + ";\n"

OUT.write_text(js)
print(f"wrote {OUT.relative_to(ROOT)}  {len(js) / 1024:.0f} KB  "
      f"({len(fc['features'])} features)")
