"""Endpoint table for the intra-city angkot trayek, resolved against OSM.

The 14 trayek that Dishub Depok confirmed still operating (Zamrowi, 30 Sep 2024,
berita.depok.go.id) have official endpoints but NO OSM route geometry -- nobody
has mapped an angkot path as a relation.

Rather than inventing coordinates, this table stores only the *endpoint names*
as Dishub states them. build-transport.py resolves each name against real OSM
nodes (fetched by probe-endpoints.py) and routes between resolved points along
real streets. An endpoint that cannot be resolved is reported, and the trayek is
then emitted WITHOUT geometry rather than with a fabricated line.

`match` is the substring used to find the OSM node. It is deliberately
specific: "Depok" alone matches forty different nodes in this bbox, and a wrong
match would put a terminal in the wrong kecamatan.
"""
ENDPOINTS = {
    "Terminal Depok":      dict(match="Terminal Depok Margonda", what="terminal"),
    "Terminal Jatijajar": dict(match="Terminal Bus Jatijajar", what="terminal"),
    "Citayam":             dict(match="Citayam", what="station"),
    "Cisalak":             dict(match="Cisalak", what="village"),
    "Cisalak Pasar":       dict(match="Cisalak Pasar", what="village"),
    "Rawa Denok":          dict(match=["Rawa Denok", "Pasar Rawa Denok", "Denok"], what="any"),
    "Leuwinanggung":       dict(match=["Leuwinanggung", "Leuw in Anggung", "Leuwin"], what="any"),
    "Kampung Sawah":       dict(match="Srengseng Sawah", what="any"),
    "Parung":              dict(match=["Pasar Parung", "Parung"], what="any"),
    "Kukusan":             dict(match="Kukusan", what="village"),
    "Bojong Gede":         dict(match=["Bojonggede", "Bojong Gede"], what="any"),
    "Palsigunung":         dict(match=["Palsigunung", "Palsi"], what="any"),
    "Studio Alam":         dict(match=["Studio Alam", "Studioalam"], what="any"),
    "Jatimulya":           dict(match="Jatimulya", what="village"),
    "Desa Tengah":         dict(match=["Desa Tengah", "Desatengah"], what="any"),
    "Akses UI":            dict(match="Stasiun KRL Universitas Indonesia", what="station"),
    "Depok I Dalam":       dict(match="Depok Jaya", what="village"),
    "Depok Timur":         dict(match="Depok Jaya", what="village"),
    "Pitara":              dict(match=["Pitara", "Pita Rara"], what="any"),
    "Gas Alam":            dict(match="Srengseng Sawah", what="any"),
}

# The 14 trayek Dishub Depok confirmed operating (berita.depok.go.id, 30 Sep 2024:
# "dari 22 trayek angkot yang ada, hanya 14 trayek yang masih beroperasi").
#
# `fare` is the tariff set by Perwali 52/2022, not a live 2026 quote -- the UI
# labels it as the regulated base fare. Dishub is separately re-setting tariffs
# (Perwali 7/2025 amends the 2018 route regulation), so treat these as indicative.
TRAYEK = [
    dict(ref="D01", a="Terminal Depok", b="Depok I Dalam", fare="Rp5.000"),
    dict(ref="D02", a="Terminal Depok", b="Depok Timur", fare="Rp6.000",
         busiest="Faktor muat tertinggi di Kota Depok (31,21%) dan waktu tempuh "
                 "terlama (47 menit)."),
    dict(ref="D03", a="Terminal Depok", b="Parung", fare="Rp8.000"),
    dict(ref="D04", a="Terminal Depok", b="Kukusan", fare="Rp6.000", via="Beji"),
    dict(ref="D05", a="Terminal Depok", b="Bojong Gede", fare="Rp8.000", via="Citayam"),
    dict(ref="D06", a="Terminal Depok", b="Cisalak Pasar", fare="Rp6.000", via="Cisalak"),
    dict(ref="D07", a="Terminal Depok", b="Rawa Denok", fare="Rp6.500", via="Pitara"),
    dict(ref="D07A", a="Terminal Depok", b="Citayam", fare="Rp6.500"),
    dict(ref="D08", a="Terminal Depok", b="Kampung Sawah", fare="Rp6.500"),
    dict(ref="D09", a="Terminal Depok", b="Jatimulya", fare="Rp7.000", via="Studio Alam"),
    dict(ref="D10", a="Terminal Depok", b="Desa Tengah", fare="Rp7.000"),
    dict(ref="D11", a="Terminal Depok", b="Palsigunung", fare="Rp7.000", via="Akses UI"),
    dict(ref="107", a="Cisalak", b="Leuwinanggung", fare="Rp5.000", via="Gas Alam"),
    dict(ref="69", a="Cisalak Pasar", b="Leuwinanggung", fare="Rp5.000", via="Pekapuran"),
]

# The 8 Dishub named as no longer operating. Kept so the UI can show them as
# inactive rather than silently omitting them -- "this one is gone" is itself
# the answer a rider needs.
TRAYEK_INACTIVE = [
    dict(ref="D15", name="Terminal Depok – Simpangan Limo"),
    dict(ref="D21", name="Terminal Sawangan – Duren Seribu"),
    dict(ref="D25", name="Bedahan Curug – BSI"),
    dict(ref="D26", name="Terminal Sawangan – Citayam"),
    dict(ref="D27", name="Perum Arco – Pondok Cabe Udik"),
    dict(ref="D35", name="Pasar Palsigunung – Pangkalan Sugutamu"),
    dict(ref="D35A", name="Palsigunung – Pasar Cisalak"),
    dict(ref="D17", name="Terminal Jatijajar – Tapos – Cibubur"),
]
