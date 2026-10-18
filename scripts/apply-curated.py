#!/usr/bin/env python3
"""Terapkan kurasi Studio Atlas (scripts/data/curated.json) ke payload situs.

Skrip ini adalah langkah build sisi depok-map untuk kurasi:

  python3 scripts/apply-curated.py            # terapkan + tulis berkas berubah
  python3 scripts/apply-curated.py --check    # validasi sasaran, tanpa menulis

Yang ditulis: src/schools.js, src/puskesmas.js, src/faskes.js, src/roads.js
(hanya bila berubah) dan src/kurasi.js (titik tambahan, selalu dibuat ulang
dari berkas kurasi). Idempoten dan reversibel: menjalankan ulang tanpa perubahan
kurasi tidak menyentuh berkas; mengosongkan kurasi mengembalikan semua berkas
ke rakitan aslinya (lihat scripts/curated.py).

Sasaran yang tidak ditemukan = galat keras, tidak ada berkas ditulis sebagian.
Penerbitan (git commit + push) dilakukan manual; lihat README bagian Kurasi.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import curated  # noqa: E402


def run(root, check=False, quiet=False):
    """Kembalikan kode keluar; cetak laporan. Tidak menulis saat check/bergalat."""
    out = []          # baris laporan
    def say(line=""):
        out.append(line)
        if not quiet:
            print(line)

    doc = curated.load_doc(root)
    if doc is None:
        say(f"kurasi: {curated.CURATED_REL} belum ada — dianggap kosong")
        doc = curated.empty_doc()
    errs = curated.validate(doc)
    if errs:
        for e in errs:
            say(f"  !! {e}")
        return 2

    layers = curated.POI_LAYERS + ("roads",)
    current = {}
    try:
        for layer in layers:
            current[layer] = curated.read_payload(layer, root)
    except Exception as exc:
        say(f"  !! gagal membaca payload: {exc}")
        return 2
    kpath = Path(root) / curated.LAYERS["kurasi"]["file"]
    current["kurasi"] = kpath.read_text(encoding="utf-8") if kpath.is_file() else None

    result = curated.apply_doc(doc, current)
    errs, report = result["errors"], result["report"]
    if errs:
        for e in errs:
            say(f"  !! {e}")
        if not check:
            say("tidak ada berkas ditulis (perbaiki sasaran lalu jalankan ulang)")
        return 1

    for layer in layers:
        rep = report["layers"][layer]
        bits = []
        if rep.get("reverted"):
            bits.append(f"{rep['reverted']} dibalikkan")
        if rep.get("set"):
            bits.append(f"{rep['set']} set")
        if rep.get("remove"):
            bits.append(f"{rep['remove']} remove")
        status = ", ".join(bits) if bits else ("tidak ada entri" if not rep["entries"]
                                               else "tak berubah")
        say(f"  {layer:9} {rep['entries']:>2} entri · {status}")
        for line in rep.get("lines") or []:
            say(f"      {line}")
    krep = report.get("kurasi") or {}
    say(f"  kurasi    {krep.get('total', 0)} titik tambahan")
    for rel in sorted(result["files"]):
        say(f"      tulis {rel}")
    if not result["files"]:
        say("  tidak ada berkas berubah")

    if check:
        say("check: OK — semua sasaran ditemukan; tidak ada berkas ditulis")
        return 0

    for rel, text in result["files"].items():
        curated.write_text(Path(root) / rel, text)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Terapkan kurasi Studio Atlas ke payload situs.")
    ap.add_argument("--check", action="store_true",
                    help="validasi sasaran tanpa menulis apa pun")
    ap.add_argument("--root", default=str(curated.ROOT),
                    help="akar repo depok-map (untuk uji)")
    ap.add_argument("--quiet", action="store_true", help="tanpa keluaran")
    args = ap.parse_args(argv)
    return run(Path(args.root), check=args.check, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
