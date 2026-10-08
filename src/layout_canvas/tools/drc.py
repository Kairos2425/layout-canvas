"""Fail-closed KLayout batch DRC runner."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class DRCResult:
    status: str  # passed | failed | unavailable | error
    clean: bool | None
    violations: list[dict[str, Any]]
    total_violations: int | None
    report_path: Path | None
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["report_path"] = str(self.report_path) if self.report_path else None
        return value


def run_drc(gds_path: str, tech: str = "sky130", deck_path: str | None = None,
            engine: str = "auto") -> DRCResult:
    gds = Path(gds_path)
    if engine == "pya" or (
        engine == "auto" and deck_path is None
        and shutil.which("klayout") is None and not Path("klayout").is_file()
    ):
        return run_pya_drc(gds, tech)
    return run_klayout_drc(gds, Path(deck_path) if deck_path else None, tech=tech)


def run_pya_drc(gds_path: Path, tech: str = "sky130") -> DRCResult:
    """In-process DRC via the KLayout engine's Region checks.

    Runs a representative min-width / min-spacing subset per tech — real
    geometry checks on the same engine foundry .drc decks use, but NOT a
    substitute for the full foundry deck (that still needs the klayout
    executable). Violations list each offending edge-pair bbox.
    """
    try:
        import klayout.db as db
    except ImportError:
        return _unavailable(gds_path.with_suffix(".drc.txt"),
                            "klayout python module not installed")
    rules, enclosure = _drc_tables_for(tech)
    report = gds_path.with_suffix(".drc.txt")
    if rules is None:
        return _unavailable(report, f"no pya DRC rule subset for tech {tech!r}")
    gds = Path(gds_path)
    if not gds.is_file():
        return _unavailable(report, f"GDS not found: {gds}")

    ly = db.Layout()
    ly.read(str(gds))
    tops = list(ly.top_cells())
    if not tops:
        return DRCResult("error", None, [], None, None, "", "", None,
                         ["GDS has no top cell"])
    top = tops[0]
    violations: list[dict[str, Any]] = []
    regions: dict[tuple[int, int], Any] = {}

    def _region(layer: int, dt: int):
        key = (layer, dt)
        if key not in regions:
            it = db.RecursiveShapeIterator(ly, top, [ly.layer(layer, dt)])
            regions[key] = db.Region(it)
        return regions[key]

    for (layer, dt), checks in rules.items():
        region = _region(layer, dt)
        if region.count() == 0:
            continue
        name = f"{layer}/{dt}"
        for kind, value in checks:
            if value <= 0:
                continue
            dbu_value = int(round(value / ly.dbu))
            if kind == "width":
                pairs = region.width_check(dbu_value)
            else:
                pairs = region.space_check(dbu_value)
            for ep in pairs.each():
                bb = ep.bbox()
                violations.append({
                    "rule": f"{name}.{'w' if kind == 'width' else 's'}",
                    "check": f"min_{kind} < {value}um",
                    "bbox": [bb.left * ly.dbu, bb.bottom * ly.dbu,
                             bb.right * ly.dbu, bb.top * ly.dbu],
                })

    # Enclosure checks: every cut shape must sit fully inside its enclosing
    # layer(s), shrunk by the minimum enclosure. `not_inside` on the sized
    # region flags vias/contacts whose edge is closer than the rule value —
    # same construction the .drc decks use (enclosed-by with distance).
    for label, (inner_l, inner_dt), outers, enc in enclosure:
        inner = _region(inner_l, inner_dt)
        if inner.count() == 0:
            continue
        outer = db.Region()
        for ol, odt in outers:
            outer += _region(ol, odt)
        if outer.count() == 0:
            violations.append({
                "rule": f"{label}.enc",
                "check": "no enclosing layer present",
                "bbox": None,
            })
            continue
        shrunk = outer.sized(-int(round(enc / ly.dbu)))
        for poly in inner.not_inside(shrunk).each():
            bb = poly.bbox()
            violations.append({
                "rule": f"{label}.enc",
                "check": f"enclosure < {enc}um",
                "bbox": [bb.left * ly.dbu, bb.bottom * ly.dbu,
                         bb.right * ly.dbu, bb.top * ly.dbu],
            })
    total = len(violations)
    report.write_text(
        f"DRC (klayout-pya engine, {tech} subset): "
        f"{total} violation(s)\n"
        + "\n".join(f"{v['rule']} {v['check']} bbox={v['bbox']}"
                    for v in violations[:200]),
        encoding="utf-8",
    )
    if total:
        return DRCResult("failed", False, violations, total, report,
                         errors=["DRC violations reported"])
    return DRCResult("passed", True, [], 0, report, errors=[])


def _drc_tables_for(
    tech: str,
) -> tuple[dict[tuple[int, int], list[tuple[str, float]]] | None,
           list[tuple[str, tuple[int, int], list[tuple[int, int]], float]]]:
    """(width/space rules, enclosure rules) for ``tech``.

    Descriptor-first: a PDK with a ``drc`` section resolves its named
    layers/roles at load time; the built-in tables below are the fallback.
    ``rules is None`` means no rule subset exists — callers report
    ``unavailable``, never a vacuous pass.
    """
    from layout_canvas.pdk import get_pdk

    try:
        pdk = get_pdk(tech)
    except KeyError:
        pdk = None
    if pdk is not None and pdk.drc is not None:
        rules = pdk.drc["rules"]
        enclosure = pdk.drc["enclosure"]
        if not rules and not enclosure:
            # An explicitly empty drc section declares no checkable rules —
            # report unavailable rather than a vacuous "0 violations" pass.
            return None, []
        return rules, enclosure
    return _PYA_RULES.get(tech), _PYA_ENCLOSURE.get(tech, [])


# Representative min-width/min-spacing subset (um). Values are the commonly
# published headline rules per layer — the full foundry deck covers far more
# rule classes and remains the authority for tapeout.
_PYA_RULES: dict[str, dict[tuple[int, int], list[tuple[str, float]]]] = {
    "sky130": {
        (64, 20): [("width", 0.84), ("space", 1.27)],   # nwell
        (65, 20): [("width", 0.15), ("space", 0.27)],   # diff
        (66, 20): [("width", 0.15), ("space", 0.21)],   # poly
        (67, 20): [("width", 0.17), ("space", 0.17)],   # li1
        (68, 20): [("width", 0.14), ("space", 0.14)],   # met1
        (69, 20): [("width", 0.14), ("space", 0.14)],   # met2
        (70, 20): [("width", 0.30), ("space", 0.30)],   # met3
        (71, 20): [("width", 0.30), ("space", 0.30)],   # met4
        (72, 20): [("width", 0.36), ("space", 0.34)],   # met5
        (93, 44): [("width", 0.38), ("space", 0.38)],   # nsdm
        (94, 20): [("width", 0.38), ("space", 0.38)],   # psdm
    },
    # IHP SG13G2 — values extracted verbatim from the foundry-published
    # KLayout deck (libs.tech/klayout/tech/drc/rule_decks/
    # sg13g2_tech_default.json). Same subset idea as sky130: headline
    # min-width/min-space only; the full deck remains tapeout authority.
    "ihp_sg13g2": {
        (31, 0): [("width", 0.62), ("space", 0.62)],    # nwell   NW.a/b
        (1, 0): [("width", 0.15), ("space", 0.21)],     # activ   Act.a/b
        (5, 0): [("width", 0.13), ("space", 0.18)],     # gatpoly Gat.a/b
        (6, 0): [("width", 0.16), ("space", 0.18)],     # cont    Cnt.a/b
        (7, 0): [("width", 0.31), ("space", 0.31)],     # nsd     nSDB.a/b
        (14, 0): [("width", 0.31), ("space", 0.31)],    # psd     pSD.a/b
        (8, 0): [("width", 0.16), ("space", 0.18)],     # metal1  M1.a/b
        (19, 0): [("width", 0.19), ("space", 0.22)],    # via1    V1.a/b
        (10, 0): [("width", 0.20), ("space", 0.21)],    # metal2  Mn.a/b
        (29, 0): [("width", 0.19), ("space", 0.22)],    # via2    Vn.a/b
        (30, 0): [("width", 0.20), ("space", 0.21)],    # metal3  Mn.a/b
        (49, 0): [("width", 0.19), ("space", 0.22)],    # via3    Vn.a/b
        (50, 0): [("width", 0.20), ("space", 0.21)],    # metal4  Mn.a/b
    },
}

# Minimum enclosure rules (um): (label, cut-layer, enclosing-layers, enc).
# The cut shape must be fully covered by the union of the enclosing layers
# shrunk by ``enc``. IHP values are Cnt.c/Cnt.d, V1.c/V1.c1, Vn.c verbatim
# from sg13g2_tech_default.json; sky130 values are the published headline
# licon/mcon/via enclosure rules.
_PYA_ENCLOSURE: dict[str, list[tuple[str, tuple[int, int], list[tuple[int, int]], float]]] = {
    "sky130": [
        ("licon.li", (66, 44), [(67, 20)], 0.06),                       # li1 encloses licon
        ("mcon.li", (67, 44), [(67, 20)], 0.03),                        # li1 encloses mcon
        ("mcon.m1", (67, 44), [(68, 20)], 0.03),                        # met1 encloses mcon
        ("via.m1", (68, 44), [(68, 20)], 0.055),                        # met1 encloses via
        ("via.m2", (68, 44), [(69, 20)], 0.055),                        # met2 encloses via
        ("via2.m2", (69, 44), [(69, 20)], 0.065),                       # met2 encloses via2
        ("via2.m3", (69, 44), [(70, 20)], 0.065),                       # met3 encloses via2
    ],
    "ihp_sg13g2": [
        ("cnt.act", (6, 0), [(1, 0), (5, 0)], 0.07),                    # Cnt.c: activ|gatpoly enc cont
        ("cnt.m1", (6, 0), [(8, 0)], 0.07),                             # Cnt.d: metal1 enc cont
        ("v1.m1", (19, 0), [(8, 0)], 0.01),                             # V1.c: m1 enc via1
        ("v1.m2", (19, 0), [(10, 0)], 0.01),                            # V1.c: m2 enc via1
        ("v2.m2", (29, 0), [(10, 0)], 0.005),                           # Vn.c: m2 enc via2
        ("v2.m3", (29, 0), [(30, 0)], 0.005),                           # Vn.c: m3 enc via2
        ("v3.m3", (49, 0), [(30, 0)], 0.005),                           # Vn.c: m3 enc via3
        ("v3.m4", (49, 0), [(50, 0)], 0.005),                           # Vn.c: m4 enc via3
    ],
}


def run_klayout_drc(gds_path: Path, deck_path: Path | None = None, *, tech: str = "sky130",
                    executable: str = "klayout", timeout: int = 60) -> DRCResult:
    """Run KLayout DRC; missing prerequisites never become a clean result."""
    gds = Path(gds_path)
    report = gds.with_suffix(".drc.txt")
    if not gds.is_file():
        return _unavailable(report, f"GDS not found: {gds}")
    if shutil.which(executable) is None and not Path(executable).is_file():
        return _unavailable(report, f"KLayout executable not found: {executable}")
    if deck_path is None:
        return _unavailable(report, f"DRC deck is required for {tech}; pass deck_path")
    deck = Path(deck_path)
    if not deck.is_file():
        return _unavailable(report, f"DRC deck not found: {deck}")
    cmd = [executable, "-b", "-r", str(deck), "-rd", f"input={gds}", "-rd", f"report={report}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return DRCResult("error", None, [], None, report if report.exists() else None,
                         _text(exc.stdout), _text(exc.stderr), None, ["KLayout timed out"])
    except OSError as exc:
        return DRCResult("error", None, [], None, None, "", str(exc), None, [str(exc)])
    if not report.is_file():
        status = "failed" if proc.returncode else "error"
        return DRCResult(status, None, [], None, None, proc.stdout, proc.stderr, proc.returncode,
                         ["KLayout did not produce a DRC report"])
    text = report.read_text(encoding="utf-8", errors="replace")
    count = _count_violations(text)
    if count is None:
        return DRCResult("error", None, [], None, report, proc.stdout, proc.stderr,
                         proc.returncode, ["Unable to parse DRC report"])
    if proc.returncode != 0 or count > 0:
        errors = ["DRC violations reported"] if count > 0 else ["KLayout exited non-zero"]
        return DRCResult("failed", False, [], count, report, proc.stdout, proc.stderr,
                         proc.returncode, errors)
    return DRCResult("passed", True, [], count, report, proc.stdout, proc.stderr, proc.returncode, [])


def _unavailable(report: Path, message: str) -> DRCResult:
    return DRCResult("unavailable", None, [], None, report if report.exists() else None, errors=[message])


def _text(value: Any) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def _count_violations(report: str) -> int | None:
    if re.search(r"\b(?:no\s+violations|total\s+violations\s*[:=]\s*0)\b", report, re.I):
        return 0
    matches = re.findall(r"(?:total\s+)?violations?\s*[:=]\s*(\d+)|\b(\d+)\s+violations?\b", report, re.I)
    if matches:
        pair = matches[-1]
        return int(pair[0] or pair[1])
    xml = re.search(r"<item[^>]*(?:count|num)=[\"'](\d+)[\"']", report, re.I)
    return int(xml.group(1)) if xml else None
