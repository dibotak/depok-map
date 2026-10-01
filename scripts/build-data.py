#!/usr/bin/env python3
"""
Build the static data files for depok-map from the upstream boundary GeoJSON.

Source: JfrAziz/indonesia-district (split of HDX cod-ab-idn), CC BY-IGO.
Only the properties the UI actually shows are kept — the upstream objects carry
country_code/valid_on/source/etc. that no consumer reads, and every retained key
is repeated in all 63 features.

Coordinates are rounded to 6 decimals (~11 cm at the equator), which is far below
the precision a 1:50.000 web map can resolve and removes ~20% of the payload.
"""
import json
import sys
import urllib.request
from pathlib import Path

SRC = (
    "https://raw.githubusercontent.com/JfrAziz/indonesia-district/master/"
    "id32_jawa_barat/id3276_kota_depok/id3276_kota_depok.geojson"
)
OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "data"

KEEP = [
    "village",
    "village_code",
    "district",
    "district_code",
    "regency",
    "regency_code",
    "province",
    "province_code",
]

PRECISION = 6


def round_coords(coords):
    """Recursively round a [lon, lat] nest to PRECISION decimals."""
    if isinstance(coords, (int, float)):
        return round(coords, PRECISION)
    if isinstance(coords, list):
        return [round_coords(c) for c in coords]
    return coords


def strip(coords):
    """
    Drop consecutive duplicate vertices.

    Rounding to 6 decimals can collapse two adjacent points onto each other, and
    a GeoJSON ring with zero-length segments renders as a visible seam in some
    renderers. Compare on the rounded values so we test what is actually written.
    """
    if not isinstance(coords, list) or not coords:
        return coords
    head = coords[0]
    # Polygon ring: list of positions.
    if isinstance(head, (int, float)):
        out = []
        for pt in coords:
            if not out or out[-1] != pt:
                out.append(pt)
        return out
    # Polygon / MultiPolygon: nest one level deeper.
    return [strip(c) for c in coords]


def geometry_out(geom):
    if geom["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": strip(round_coords(geom["coordinates"]))}
    if geom["type"] == "MultiPolygon":
        return {"type": "MultiPolygon", "coordinates": strip(round_coords(geom["coordinates"]))}
    raise SystemExit(f"unexpected geometry type: {geom['type']}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    sys.stderr.write(f"fetching {SRC}\n")
    req = urllib.request.Request(SRC, headers={"User-Agent": "depok-map/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        fc = json.load(r)

    features = []
    for f in fc["features"]:
        props = f["properties"]
        out = {k: props.get(k) for k in KEEP}
        # Guard against a silent rename upstream: a feature with no name would
        # render as an unclickable blank polygon and read as a broken map.
        missing = [k for k in ("village", "district", "village_code") if not out.get(k)]
        if missing:
            raise SystemExit(f"feature missing {missing}: {props}")
        features.append(
            {
                "type": "Feature",
                "properties": out,
                "geometry": geometry_out(f["geometry"]),
            }
        )

    features.sort(key=lambda f: f["properties"]["village"])

    districts = sorted({f["properties"]["district"] for f in features})
    lons = []
    lats = []

    def walk(c):
        if isinstance(c[0], (int, float)):
            lons.append(c[0])
            lats.append(c[1])
        else:
            for x in c:
                walk(x)

    for f in features:
        walk(f["geometry"]["coordinates"])

    meta = {
        "regency": features[0]["properties"]["regency"],
        "province": features[0]["properties"]["province"],
        "village_count": len(features),
        "district_count": len(districts),
        "districts": districts,
        "bounds": [min(lons), min(lats), max(lons), max(lats)],
        "source": "HDX/BPS cod-ab-idn via JfrAziz/indonesia-district",
        "license": "CC BY-IGO",
        "vintage": "2020-04-01",
    }

    payload = {"type": "FeatureCollection", "features": features}
    (OUT_DIR / "depok-kelurahan.json").write_text(json.dumps(payload, separators=(",", ":")))
    (OUT_DIR / "depok-meta.json").write_text(json.dumps(meta, indent=2))

    kb = lambda p: p.stat().st_size / 1024
    sys.stderr.write(
        f"wrote {len(features)} kelurahan / {len(districts)} kecamatan\n"
        f"  depok-kelurahan.json {kb(OUT_DIR / 'depok-kelurahan.json'):.0f} KB\n"
        f"  depok-meta.json      {kb(OUT_DIR / 'depok-meta.json'):.1f} KB\n"
        f"  bounds {meta['bounds']}\n"
    )


if __name__ == "__main__":
    main()
