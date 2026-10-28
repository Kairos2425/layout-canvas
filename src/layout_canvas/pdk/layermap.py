"""Cadence stream layermap ingest.

Real commercial PDKs ship a ``streamInLayermap``/``streamOutLayermap``
(or ``layermap``) file — the authoritative OA-layer-name ↔ GDS
(layer, datatype) table used by every ``strmIn``/``strmOut``. Parsing it
fills the two descriptor fields that are otherwise hand-copied from PDK
docs:

- ``layers``:  ``{oa_layer_name: [gds_layer, gds_datatype]}``
- ``oa_layers``: ``{"L/DT": "oa_layer_name"}`` (what the SKILL exporter
  needs to spell ``dbCreateRect`` purposes)

Format (NCSU CDK ``pipo/streamInLayermap`` and most foundry variants)::

    oaLayerName   oaPurpose    gdsLayer  gdsDatatype   [extra cols]
    metal1        drawing      49        0

Lines that are comments (``#``/``;``), blank, or malformed are skipped
and counted so the caller can report coverage honestly.
"""

from __future__ import annotations

import re
from pathlib import Path

_COLS = re.compile(r"\s+")
_INT = re.compile(r"^-?\d+$")


def parse_stream_layermap(text: str) -> dict:
    """Parse a stream layermap. Returns ``{"layers", "oa_layers",
    "entries", "skipped"}`` — layers/oa_layers are descriptor-fragment
    dicts; entries is the raw row count used; skipped lists bad lines.
    """
    layers: dict[str, list[int]] = {}
    oa_layers: dict[str, str] = {}
    entries = 0
    skipped: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        toks = _COLS.split(line)
        # Need at least: name purpose L DT. Extra columns (fill/purpose
        # variants, direction flags) are ignored. 'NAME' rows are the
        # EDI/Innovus text-label convention, not a layer.
        if toks[0] == "NAME":
            continue
        if len(toks) < 4 or not (_INT.match(toks[2]) and
                                 _INT.match(toks[3])):
            skipped.append(line[:60])
            continue
        name, _purpose, gl, gdt = toks[0], toks[1], toks[2], toks[3]
        entries += 1
        layers.setdefault(name, [int(gl), int(gdt)])
        # Same OA name may stream to several GDS pairs (e.g. NCSU 'cc'
        # maps to four contact cuts) — first row wins, mirroring what a
        # single-purpose SKILL replay can express.
        oa_layers.setdefault(f"{gl}/{gdt}", name)
    return {
        "layers": layers,
        "oa_layers": oa_layers,
        "entries": entries,
        "skipped": skipped,
    }


def parse_layermap_file(path: str | Path) -> dict:
    """Read a layermap file (UTF-8 with BOM tolerance, GBK fallback for
    vendor files that carry CJK comments) and parse it."""
    p = Path(path)
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return parse_stream_layermap(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{p}: not decodable as utf-8 or gbk")
