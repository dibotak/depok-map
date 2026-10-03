#!/usr/bin/env python3
"""Fetch school data for Kota Depok from Sekolah Kita (Kemendikdasmen).

Why this exists
---------------
The older pipeline (build-schools-kemdik.py) scrapes the DataTable pages on
referensi.data.kemendikdasmen.go.id for names and addresses, then has to guess
coordinates from OpenStreetMap. It could only place 21 of 250 schools, because
OSM simply has not mapped most of the city's private secondary schools.

Sekolah Kita is the same ministry's newer public portal, and its API returns
`lintang` / `bujur` (latitude / longitude) per school, officially sourced. That
closes the gap: no geocoding, no false matches, and a real point for schools
that have never been in OSM.

The API
-------
The site is an Angular SSG app; all data comes from JSON endpoints under the
same origin, with no authentication:

    POST /v1/sekolah-service/sekolah/cari-sekolah
         {page, size, keyword, kabupaten_kota, bentuk_pendidikan, status_sekolah}
         -> {status_code, message, total, data: [ {...} ]}
    GET  /v1/sekolah-service/sekolah/full-detail/{sekolah_id}
         -> {status_code, message, data: { sekolah: [ {...} ], ... } }

Two details that cost a wrong turn each, and are easy to get wrong again:

  * `kabupaten_kota` takes the NAME ("Kota Depok"), not `kode_kabupaten`
    ("026600"). The wilayah reference endpoint returns both, and passing the
    code silently returns total=0 rather than an error.
  * `page` is 0-based. Page 1 skips the first 100 rows.

Only `full-detail` carries coordinates, and the list row does not have them at
all, so coordinates cost one request per school. Every level (SD, SMP, SMA,
TK, ...) is fetched and kept in the cache; `build-schools-kemdik.py` decides
which levels end up in the app.

Rate limiting
-------------
This is the reason the earlier geocoding run failed, so the policy here is
deliberately conservative rather than fast:

  * 0.45s between detail requests (~2 req/s), and 0.6s between list pages.
  * A 429 or 503 is never treated as a data result: the request is retried with
    a growing delay, up to a 5-attempt cap, then recorded as unresolved.
  * Every response is cached to .cache/sekolah/ by sekolah_id, so a rerun makes
    no network requests at all. Re-running is how the output is verified, and
    that is only cheap because of this cache.
  * A descriptive User-Agent, so the ministry can identify and contact us.

Run it with --refresh to ignore the cache for details, or --all-levels to also
walk every education level (much larger; not needed for the app).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://sekolah.data.kemendikdasmen.go.id"
UA = "depok-map-school-builder/1.0 (civic map data; https://github.com/dibotak/depok-map)"
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "sekolah"

CITY = "Kota Depok"
# The app shows secondary schools. MA is grouped with SMA/SMK in the sidebar.
SECONDARY = ("SMA", "SMK", "MA")

LIST_WAIT = 0.6
DETAIL_WAIT = 0.45
LIST_TRIES = 4
DETAIL_TRIES = 5


def _request(url: str, body: dict | None = None, tries: int = 3):
    """One HTTP call, with backoff on 429/503.

    Returns (status, parsed_json_or_None). A 429 is surfaced rather than
    silently turned into an empty result, because an empty result and a
    throttled one look identical downstream and only one of them is true.
    """
    delay = 10.0
    for attempt in range(tries):
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "User-Agent": UA,
            "Accept": "application/json",
            "Referer": f"{BASE}/sekolah",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                print(f"    HTTP {e.code}, waiting {delay:.0f}s "
                      f"(attempt {attempt + 1}/{tries})", file=sys.stderr, flush=True)
                time.sleep(delay)
                delay *= 2
                continue
            return e.code, None
        except Exception as e:  # noqa: BLE001
            print(f"    {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            time.sleep(delay)
            delay *= 2
    return 0, None


def fetch_list(city: str = CITY, page_size: int = 100) -> list[dict]:
    """All schools in a city, via 0-based pagination."""
    path = CACHE / "depok-all.json"
    if path.exists():
        rows = json.loads(path.read_text())
        print(f"list: {len(rows)} schools (cached)")
        return rows

    rows: list[dict] = []
    page = 0
    total = None
    while page < 60:
        status, body = _request(
            f"{BASE}/v1/sekolah-service/sekolah/cari-sekolah",
            {"page": page, "size": page_size, "keyword": "",
             "kabupaten_kota": city, "bentuk_pendidikan": "", "status_sekolah": ""},
            tries=LIST_TRIES,
        )
        if status != 200 or not body:
            print(f"list: HTTP {status} at page {page}", file=sys.stderr)
            break
        total = body.get("total")
        data = body.get("data") or []
        rows += data
        page += 1
        if not data or len(rows) >= (total or 0):
            break
        time.sleep(LIST_WAIT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=1))
    print(f"list: {len(rows)} schools in {page} requests (API total {total})")
    return rows


def fetch_detail(sekolah_id: str, refresh: bool = False) -> dict | None:
    """One school, including lintang/bujur. Cached by sekolah_id."""
    path = CACHE / f"detail-{sekolah_id}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    status, body = _request(f"{BASE}/v1/sekolah-service/sekolah/full-detail/{sekolah_id}",
                            tries=DETAIL_TRIES)
    if status != 200 or not body:
        print(f"    detail {sekolah_id}: HTTP {status}", file=sys.stderr)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))
    return body


def coordinates(sk: dict) -> tuple[float | None, float | None]:
    """(lat, lon) from a school record, or (None, None).

    The portal writes 0 / 0 for schools it has no point for, which is not a
    real location anywhere on Earth, so it is treated as missing. Valid ranges
    are also checked, since the field is free text in the source data.
    """
    lat, lon = sk.get("lintang"), sk.get("bujur")
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if not (abs(lat) <= 90 and abs(lon) <= 180):
        return None, None
    if lat == 0 and lon == 0:
        return None, None
    return lat, lon


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true",
                    help="ignore the detail cache and refetch")
    ap.add_argument("--all-levels", action="store_true",
                    help="fetch details for every education level, not just secondary")
    args = ap.parse_args()

    rows = fetch_list()
    if not rows:
        sys.exit("no schools fetched")

    targets = rows if args.all_levels else [r for r in rows
                                           if r.get("bentuk_pendidikan") in SECONDARY]
    print(f"details: {len(targets)} schools"
          + (" (all levels)" if args.all_levels else " (SMA/SMK/MA)"))

    ok = 0
    t0 = time.time()
    for i, row in enumerate(targets, 1):
        body = fetch_detail(row["sekolah_id"], refresh=args.refresh)
        sk = (body or {}).get("data", {}).get("sekolah") or []
        lat, lon = coordinates(sk[0]) if sk else (None, None)
        if lat is not None:
            ok += 1
        if i % 25 == 0 or i == len(targets):
            print(f"  {i}/{len(targets)}  with coordinates {ok}"
                  f"  ({time.time() - t0:.0f}s)", flush=True)
        time.sleep(DETAIL_WAIT)

    print(f"\n{ok}/{len(targets)} schools have official coordinates "
          f"({time.time() - t0:.0f}s)")
    print("cache: .cache/sekolah/")


if __name__ == "__main__":
    main()