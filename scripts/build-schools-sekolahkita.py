#!/usr/bin/env python3
"""Turn the Sekolah Kita cache into src/schools.js.

Input : .cache/sekolah/depok-all.json          (list, from fetch-sekolahkita.py)
        .cache/sekolah/detail-<id>.json         (per-school, with coordinates)
Output: src/schools.js

Coordinate handling
-------------------
The portal's `lintang`/`bujur` are official and are used as-is. They are not
perfect, though, and three checks decide whether a point is trustworthy enough
to put on a map:

1. **Zero is not a location.** 17 of the 242 schools come back as 0/0, which is
   the portal's way of saying it has no point. Treated as missing, so the
   school still appears in the list with its address but no pin.

2. **Latitude sign.** One school, SMAS IT AL-QUDWAH in Beji, is published as
   latitude +6.3856 -- valid for Indonesia but 1,400km from Depok. Negating it
   lands 106m inside the city, so this is a dropped minus sign in the source
   rather than an error in our data, and it is corrected rather than discarded.
   The test is the city polygon, not a latitude range: a range check cannot
   catch this, because +6.38 is a real latitude somewhere in Indonesia. What
   identifies it is that the point misses the city and its negation does not,
   which cannot be true of a genuine Depok school.

3. **Proximity to the city.** A school whose point falls more than 2km outside
   the city polygon is refused, because a pin in the wrong city is worse than no
   pin. Two schools sit 30m and 423m outside -- the village polygons in our
   boundary data are slightly coarse at the edge -- and both are kept. Anything
   further out is dropped as unusable.

Level and label come from `bentuk_pendidikan`, which the portal gives directly
("SMA", "SMK", "MA"), so unlike the previous scrape there is no need to guess a
level out of the school's name or to drop the ones that are ambiguous.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "sekolah"
DEPOK = json.loads((ROOT / "public" / "data" / "depok-kelurahan.json").read_text())

# How far outside the city polygon a point may sit and still be believed. Two
# schools are 30m and 423m out because our village polygons are coarse at the
# edge; anything past this is a different place, not a rounding artefact.
MAX_OFF_CITY_M = 2000.0
SECONDARY = ("SMA", "SMK", "MA")

LABELS = {
    ("SMA", "NEGERI"): "SMA Negeri",
    ("SMA", "SWASTA"): "SMA Swasta",
    ("SMK", "NEGERI"): "SMK Negeri",
    ("SMK", "SWASTA"): "SMK Swasta",
    ("MA", "NEGERI"): "Madrasah Aliyah Negeri",
    ("MA", "SWASTA"): "Madrasah Aliyah Swasta",
}


# --- point-in-polygon over our own kelurahan boundaries -----------------------

def _rings():
    """[(village_name, ring_of_[lon, lat])] from the boundary file.

    Features are MultiPolygon, so every part is collected: a school in the
    smaller part of a split village still resolves, rather than falling through
    to "outside the city" and losing its pin.
    """
    out = []
    for f in DEPOK["features"]:
        name = (f.get("properties") or {}).get("village", "")
        coords = (f.get("geometry") or {}).get("coordinates") or []
        for poly in coords:
            if poly:
                out.append((name, poly[0]))
    return out


RINGS = _rings()


def village_of(lon: float, lat: float) -> str | None:
    for name, ring in RINGS:
        inside = False
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
            if (y1 > lat) != (y2 > lat):
                if lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
                    inside = not inside
        if inside:
            return name
    return None


def metres_to_city(lon: float, lat: float) -> float:
    """Distance in metres from (lon, lat) to the nearest village boundary."""
    import math
    best = float("inf")
    kx = 111320 * math.cos(math.radians(lat))
    ky = 110540
    for _, ring in RINGS:
        n = len(ring)
        for i in range(n):
            ax, ay = ring[i][0], ring[i][1]
            bx, by = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((lon - ax) * dx + (lat - ay) * dy) / L2))
            d = math.hypot((lon - (ax + t * dx)) * kx, (lat - (ay + t * dy)) * ky)
            if d < best:
                best = d
    return best


def clean_point(sk: dict):
    """(lat, lon, note). note is None, 'sign', or a refusal reason."""
    lat, lon = sk.get("lintang"), sk.get("bujur")
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None, "tidak ada koordinat"
    if not (abs(lat) <= 90 and abs(lon) <= 180):
        return None, None, "koordinat di luar rentang"
    # Near-null-island values are the portal's other way of saying "no point".
    # MAS ULUMUL QUR'AN is published at 0.0009, -0.0013 -- about 11,900km from
    # Depok, and not a place anyone could have meant. Reporting "11,901km di luar
    # Kota Depok" on a card would be technically true and practically useless, so
    # it is treated as missing like the 0/0 rows.
    if (abs(lat) < 0.01 and abs(lon) < 0.01):
        return None, None, "belum ada koordinat di sumber"
    if lat == 0 and lon == 0:
        # Shown verbatim on the card, so it is written in Indonesian like the
        # rest of the UI rather than as an internal note.
        return None, None, "belum ada koordinat di sumber"

    # Check 2: a latitude whose sign is wrong.
    #
    # The test is the city polygon, not a latitude range: +6.3856 is a perfectly
    # valid latitude in Indonesia (it is in Sulawesi), so a range check happily
    # accepts it and the school is lost. What gives the typo away is that the
    # negated latitude lands inside Depok and the given one does not. That
    # combination cannot happen for a genuine school in this city, so flipping
    # is safe; if the negated point misses the city too, nothing is changed.
    if not village_of(lon, lat) and village_of(lon, -lat):
        return -lat, lon, "latitude sign corrected (source had no minus)"

    # Check 3: inside the city, or close enough to be an edge artefact.
    if not village_of(lon, lat):
        d = metres_to_city(lon, lat)
        if d > MAX_OFF_CITY_M:
            return None, None, f"koordinat {d / 1000:.0f} km di luar Kota Depok"
    return lat, lon, None


def norm_name(s: str) -> str:
    s = re.sub(r"\(.*?\)", " ", s.upper())
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def main() -> None:
    rows = json.loads((CACHE / "depok-all.json").read_text())
    rows = [r for r in rows if r.get("bentuk_pendidikan") in SECONDARY]
    print(f"secondary schools from the portal: {len(rows)}")

    out, stats, notes = [], Counter(), Counter()
    for row in rows:
        dp = CACHE / f"detail-{row['sekolah_id']}.json"
        sk = {}
        if dp.exists():
            body = json.loads(dp.read_text())
            arr = (body.get("data") or {}).get("sekolah") or []
            sk = arr[0] if arr else {}

        street = (row.get("alamat_jalan") or "").strip(" ,.")
        lat, lon, note = clean_point(sk)
        if note:
            notes[note.split("(")[0].split(" outside")[0].strip()] += 1
        placed = lat is not None
        stats["placed" if placed else "unplaced"] += 1

        jenjang = row.get("bentuk_pendidikan") or ""
        status = (row.get("status_sekolah") or "").upper()
        out.append({
            "key": row["sekolah_id"],
            "npsn": row.get("npsn") or "",
            "name": (row.get("nama") or "").strip(),
            "label": LABELS.get((jenjang, status), f"{jenjang} {status.title()}".strip()),
            "level": "MA" if jenjang == "MA" else "SMA/SMK",
            "jenjang": jenjang,
            "status": status,
            "operator": status.title(),
            # The portal's own alamat_jalan usually already ends in "RT.xx/RW.xx",
            # so appending the rt/rw columns would print them twice. Only add them
            # when the street line is missing them.
            "address": ", ".join(x for x in [
                street,
                ("" if re.search(r"(?i)\bRT\b", street) else
                 (f"RT {row['rt']} / RW {row['rw']}"
                  if row.get("rt") and row.get("rw") else "")),
                (row.get("kecamatan") or "").strip(),
                (row.get("kabupaten") or "").strip(),
                row.get("kode_pos") or "",
            ] if x),
            "street": street,
            "postcode": row.get("kode_pos") or "",
            # village comes from the coordinate when we have one; when we do not,
            # the official kecamatan is still worth showing, so the card keeps its
            # hierarchy instead of collapsing to a bare address.
            "village": village_of(lon, lat) if placed else "",
            "district": (row.get("kecamatan") or "").replace("Kec. ", "").strip(),
            "kelurahan": (row.get("nama_dusun") or "").strip(),
            "lon": lon,
            "lat": lat,
            "placed": placed,
            "match": "kemdikdasmen",
            "note": note,
        })

    src = (f"Kemendikdasmen Sekolah Kita (sekolah.data.kemendikdasmen.go.id) -- "
           f"{len(out)} sekolah with official NPSN, address, jenjang, status and "
           f"coordinates. {stats['placed']} have a coordinate from the portal; "
           f"{stats['unplaced']} are published without one and are listed "
           f"without a pin.")
    js_rows = sorted(out, key=lambda r: (r["label"], r["name"]))
    payload = {"source": src, "generated": "scripts/build-schools-sekolahkita.py",
               "schools": js_rows}
    (ROOT / "src" / "schools.js").write_text(
        "// Generated by scripts/build-schools-sekolahkita.py -- do not edit.\n"
        "window.DEPOK_SCHOOLS = " + json.dumps(payload, ensure_ascii=False, indent=1) + ";\n")

    print(f"\nplaced {stats['placed']} / {stats['unplaced']} without a point")
    print("labels:", dict(Counter(r["label"] for r in out)))
    print("unique NPSN:", len({r["npsn"] for r in out}), "of", len(out))
    if notes:
        print("coordinate notes:", dict(notes))
    print("wrote src/schools.js")


if __name__ == "__main__":
    main()