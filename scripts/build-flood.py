#!/usr/bin/env python3
"""Turn the curated PetaBencana flood history into src/flood.js.

WHAT THIS LAYER IS
------------------
A record of real flood reports for Kota Depok from PetaBencana.id, the
crowdsourced hazard platform run by the MIT Urban Risk Lab / Yayasan Peta
Bencana. Each pin is a report location (through the platform's own geocoding),
carrying the original report text, date and -- when the reporter gave one --
the water depth. It is a *reports* layer, not a field survey: pins say "this
was reported here", not "we measured this".

WHY THE HISTORY FILE EXISTS
---------------------------
Depok's PetaBencana feed is dominated by training material: GRASP/BNPB
simulation rounds ("#TES_Trainer ...", "Latihan simulasi ..."), HKB disaster
days, campus classes ("UI_...", "DUTA ..."), plus the occasional joke post.
`data.petabencana.id/reports/archive` carries no is_training flag, so every
Depok-tagged flood report (217 of them, Apr 2023 - May 2026) was screened by
hand, one by one. scripts/data/flood-history.json is that screening log:
review="keep" for the 8 real reports, review="drop" with a note for the rest.
New reports fetched later start as review="pending" and are printed by this
script until a human classifies them; only "keep" reaches the map.

THE 7-DAY CEILING
-----------------
api.petabencana.id/reports only answers for the last 604800 seconds, so live
drift is fetched from there, while older material comes from the archive
(data.petabencana.id/reports/archive), whose windows max out around 217 days.
Verified Oct 2026 by full scan 2020-10-01 .. 2026-10-17; the archive is
Jakarta-only before ~2021 (ID-JK), and no Depok flood report exists before
Apr 2023.

USAGE
-----
    python3 scripts/build-flood.py              # offline rebuild from history
    FLOOD_FETCH=1 python3 scripts/build-flood.py  # + pull new windows/live,
                                                  # merge as 'pending', rebuild

Output: src/flood.js  (window.DEPOK_FLOOD)
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPOK = json.loads((ROOT / "public" / "data" / "depok-kelurahan.json").read_text())
HISTORY = ROOT / "scripts" / "data" / "flood-history.json"
CACHE = Path("/tmp/depok-flood-archive-cache")
LIVE_CACHE = Path("/tmp/depok-flood-live-cache.json")
ARCHIVE = "https://data.petabencana.id/reports/archive"
LIVE = "https://api.petabencana.id/reports"
UA = "depok-map/1.0 (flood layer build)"

CLUSTER_M = 150.0          # reports within this distance form one pin
WINDOW_LADDER = (210, 100, 30, 7)   # archive window size, shrinks on HTTP 500
WIB = timezone(timedelta(hours=7))

# Text the maintainers of this layer have seen mark simulation rounds. Only
# used for NEW records; the curated history file is not re-screened by it.
MARKER = re.compile(
    r"tes|test|trainer|simulas|latihan|pelatihan|hkb|duta|\bprb\b|fikom|"
    r"universitas indonesia|\bui\b|\bmrt\b|kejora|welcome|retest", re.I)


# --- polygon helpers, same logic as build-schools-sekolahkita.py ------------

def _rings():
    out = []
    for f in DEPOK["features"]:
        name = (f.get("properties") or {}).get("village", "")
        coords = (f.get("geometry") or {}).get("coordinates") or []
        for poly in coords:
            if poly:
                out.append((name, poly[0]))
    return out


RINGS = _rings()
KEL_TO_KEC = {}
for f in DEPOK["features"]:
    p = f.get("properties") or {}
    KEL_TO_KEC[(p.get("village") or "").strip().lower()] = (
        p.get("district") or p.get("kecamatan") or "").strip()


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


def dist_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    kx = 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat1 - lat2) * 110540, (lon1 - lon2) * kx)


def wib_date(s: str) -> str:
    try:
        return (datetime.fromisoformat(s.replace("Z", "+00:00"))
                .astimezone(WIB).date().isoformat())
    except Exception:
        return (s or "")[:10]


# --- fetching (only with FLOOD_FETCH=1) -------------------------------------

def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read())


def depok_flood_features(result) -> list[dict]:
    """Keep only flood reports tagged as Kota Depok (or inside its polygons)."""
    out = []
    for f in (result or {}).get("features") or []:
        p = f.get("properties") or {}
        tags = p.get("tags") or {}
        if p.get("disaster_type") != "flood":
            continue
        g = f.get("geometry") or {}
        c = g.get("coordinates") or [None, None]
        lon, lat = c[0], c[1]
        if lon is None or lat is None:
            continue
        tagged = (tags.get("region_code") == "3276"
                  or (tags.get("city") or "").upper().startswith("DEPOK"))
        if not (tagged or village_of(lon, lat)):
            continue
        out.append({
            "pkey": str(p.get("pkey")),
            "created_at": p.get("created_at"),
            "lat": round(float(lat), 7), "lon": round(float(lon), 7),
            "depth_cm": (p.get("report_data") or {}).get("flood_depth"),
            "status": p.get("status"), "source": p.get("source"),
            "text": p.get("text") or "", "is_training": p.get("is_training"),
        })
    return out


def fetch_window(start: date, end: date) -> dict:
    key = CACHE / f"{start.isoformat()}_{end.isoformat()}.json"
    if key.exists() and key.stat().st_size > 100:
        return json.loads(key.read_text())
    q = urllib.parse.urlencode({
        "start": f"{start.isoformat()}T00:00:00+0700",
        "end": f"{end.isoformat()}T23:59:59+0700",
        "geoformat": "geojson"})
    data = _get(f"{ARCHIVE}?{q}")
    CACHE.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(data))
    return data


def fetch_archive(from_day: date, to_day: date):
    """Records + the last day covered contiguously (scan stops on failures)."""
    records, cursor = [], from_day
    while cursor <= to_day:
        for size in WINDOW_LADDER:
            w_end = min(cursor + timedelta(days=size - 1), to_day)
            try:
                data = fetch_window(cursor, w_end)
            except Exception as exc:
                sys.stderr.write(f"  arsip {cursor}..{w_end} gagal ({str(exc)[:60]}),"
                                 f" coba jendela lebih kecil\n")
                continue
            recs = depok_flood_features(data.get("result"))
            records.extend(recs)
            sys.stderr.write(f"  arsip {cursor}..{w_end}: {len(recs)} laporan Depok\n")
            cursor = w_end + timedelta(days=1)
            break
        else:
            sys.stderr.write(f"  BERHENTI: arsip tak terjangkau mulai {cursor}\n")
            break
    return records, cursor - timedelta(days=1)


def fetch_live() -> dict:
    if LIVE_CACHE.exists() and time.time() - LIVE_CACHE.stat().st_mtime < 6 * 3600:
        return json.loads(LIVE_CACHE.read_text())
    q = urllib.parse.urlencode({"timeperiod": 604800, "disaster": "flood",
                                "geoformat": "geojson"})
    data = _get(f"{LIVE}?{q}")
    LIVE_CACHE.write_text(json.dumps(data))
    return data


def auto_review(rec: dict):
    if rec.get("is_training") is True:
        return "drop", "Bendera is_training=true (otomatis)."
    text = (rec.get("text") or "").strip()
    if not text:
        return "drop", "Teks kosong (otomatis)."
    if MARKER.search(text):
        return "drop", "Penanda pelatihan/uji coba (otomatis)."
    return "pending", "Menunggu kurasi manual (otomatis)."


def merge_new(history: dict, new_recs: list[dict]) -> int:
    have = {r.get("pkey") for r in history["reports"]}
    added = 0
    for rec in new_recs:
        if not rec.get("pkey") or rec["pkey"] in have:
            continue
        review, note = auto_review(rec)
        history["reports"].append({
            "pkey": rec["pkey"], "created_at": rec.get("created_at"),
            "lat": rec["lat"], "lon": rec["lon"],
            "depth_cm": rec.get("depth_cm"), "status": rec.get("status"),
            "source": rec.get("source"), "text": rec.get("text") or "",
            "review": review, "note": note})
        have.add(rec["pkey"])
        added += 1
    history["reports"].sort(key=lambda r: r.get("created_at") or "")
    return added


# --- build ------------------------------------------------------------------

def build_pins(reports: list[dict]):
    keeps = sorted((r for r in reports if r.get("review") == "keep"),
                   key=lambda r: r.get("created_at") or "")
    clusters: list[dict] = []
    for r in keeps:
        target = None
        for c in clusters:
            if dist_m(r["lon"], r["lat"], c["lon"], c["lat"]) <= CLUSTER_M:
                target = c
                break
        if target is None:
            clusters.append({"reports": [r], "lon": r["lon"], "lat": r["lat"]})
        else:
            target["reports"].append(r)
            n = len(target["reports"])
            target["lon"] = sum(x["lon"] for x in target["reports"]) / n
            target["lat"] = sum(x["lat"] for x in target["reports"]) / n

    pins = []
    for c in clusters:
        rs = sorted(c["reports"], key=lambda r: r.get("created_at") or "",
                    reverse=True)
        village = village_of(c["lon"], c["lat"])
        if not village:
            sys.stderr.write(f"  PERINGATAN: titik {rs[0]['pkey']} di luar poligon"
                             f" Kota Depok, tidak dipin\n")
            continue
        depths = [r["depth_cm"] for r in rs
                  if isinstance(r.get("depth_cm"), (int, float))]
        earliest = min(rs, key=lambda r: r.get("created_at") or "")
        pins.append({
            "key": f"flood-{earliest['pkey']}",
            "lat": round(c["lat"], 6), "lon": round(c["lon"], 6),
            "village": village,
            "district": KEL_TO_KEC.get(village.strip().lower(), ""),
            "count": len(rs),
            "first_date": wib_date(earliest.get("created_at")),
            "last_date": wib_date(rs[0].get("created_at")),
            "max_depth_cm": max(depths) if depths else None,
            "last_depth_cm": rs[0].get("depth_cm"),
            "last_status": rs[0].get("status") or "",
            "reports": [{
                "pkey": r["pkey"], "date": wib_date(r.get("created_at")),
                "depth_cm": r.get("depth_cm"),
                "text": (r.get("text") or "").strip()} for r in rs],
        })
    pins.sort(key=lambda p: (p["last_date"], p["key"]), reverse=True)
    return pins


def main() -> None:
    history = json.loads(HISTORY.read_text())
    if os.environ.get("FLOOD_FETCH") == "1":
        scan_from = date.fromisoformat(history["scanned"]["to"]) + timedelta(days=1)
        today = datetime.now(WIB).date()
        new = []
        if scan_from <= today:
            sys.stderr.write(f"memindai arsip {scan_from} .. {today} ...\n")
            recs, covered = fetch_archive(scan_from, today)
            new.extend(recs)
            if covered >= scan_from:
                history["scanned"]["to"] = covered.isoformat()
        else:
            sys.stderr.write("arsip sudah dipindai sampai hari ini\n")
        try:
            live = depok_flood_features(fetch_live().get("result"))
            new.extend(live)
            sys.stderr.write(f"endpoint langsung (7 hari): {len(live)} laporan Depok\n")
        except Exception as exc:
            sys.stderr.write(f"endpoint langsung gagal: {str(exc)[:80]}\n")
        added = merge_new(history, new)
        HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1) + "\n")
        sys.stderr.write(f"riwayat diperbarui: +{added} laporan baru\n")

    reports = history["reports"]
    pins = build_pins(reports)
    stats = Counter(r.get("review") for r in reports)
    pending = [r for r in reports if r.get("review") == "pending"]

    note = (
        "Titik = lokasi laporan publik PetaBencana.id (bukan verifikasi lapangan). "
        f"Dari {len(reports)} laporan banjir Depok yang dipindai "
        f"(arsip {history['scanned']['from']} s.d. {history['scanned']['to']}), "
        f"{stats.get('keep', 0)} laporan nyata dipakai; sisanya simulasi/"
        "pelatihan/uji coba disaring (log lengkap: scripts/data/flood-history.json). "
        "Endpoint laporan langsung PetaBencana hanya mencakup 7 hari; perbarui "
        "dengan FLOOD_FETCH=1 lalu kurasi entri 'pending'.")
    src = ("PetaBencana.id (data.petabencana.id/reports/archive + "
           "api.petabencana.id/reports) -- laporan publik banjir Kota Depok, "
           "disaring manual dari laporan simulasi/pelatihan.")
    payload = {
        "source": src,
        "license": history["license"],
        "generated": "scripts/build-flood.py",
        "scanned": history["scanned"],
        "screening": {"screened": len(reports), "kept": stats.get("keep", 0),
                      "dropped": stats.get("drop", 0),
                      "pending": stats.get("pending", 0)},
        "counts": {"points": len(pins),
                   "reports": sum(p["count"] for p in pins)},
        "note": note,
        "list": pins,
    }
    (ROOT / "src" / "flood.js").write_text(
        "// Generated by scripts/build-flood.py -- do not edit.\n"
        "window.DEPOK_FLOOD = " + json.dumps(payload, ensure_ascii=False, indent=1) + ";\n")

    print(f"kept {payload['counts']['reports']} laporan -> {len(pins)} titik")
    for p in pins:
        newest = p["reports"][0]
        print(f"  {p['key']:<14} {p['last_date']}  {p['village']:<18}"
              f" d={newest['depth_cm']}  {newest['text'][:52]}")
    print("screening:", dict(stats))
    if pending:
        print(f"\n{len(pending)} laporan menunggu kurasi (review='pending'):")
        for r in pending:
            print(f"  {r['pkey']} {r['created_at'][:16]} {r['lat']:.5f},{r['lon']:.5f}"
                  f" | {(r['text'] or '')[:70]}")
    print("wrote src/flood.js")


if __name__ == "__main__":
    main()
