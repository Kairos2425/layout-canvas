"""Virtuoso acceptance harness — static (Cadence-free) + SSH-remote checks.

Layout-canvas emits two Cadence-facing artifacts from a compiled GDS:

- ``compiler/virtuoso.export_skill`` — a SKILL replay script (``*.il``) that
  rebuilds every cellview inside an OA library, and
- ``compiler/virtuoso.export_spectre`` — the design netlist translated to
  the Spectre dialect (``*.scs``).

This module verifies them in two layers, both fail-closed:

``static_report`` runs anywhere (needs only the ``klayout`` pip module):
it re-counts every shape/instance in the GDS and cross-checks the emitted
SKILL line-by-line, checks ``.il`` paren balance and cellview coverage,
and validates ``.scs`` subckt/ends balance, X-card references and port
counts. Nothing here pretends Cadence ran — the verdict is ``static_ok``.

``remote_acceptance`` drives a real EDA host over SSH (pure subprocess —
``ssh``/``scp`` binaries, no new dependencies): it ships the artifacts
plus a generated ``verify.il`` audit script, replays them in
``virtuoso -nograph -replay``, pulls back the CIW logs and a per-cellview
shape/instance census, diffs the census against the GDS truth, and — when
Spectre models are supplied — elaborates the netlist in ``spectre``. Only
a clean replay *and* a matching census *and* a clean Spectre run report
``verified``; anything else is ``failed`` or ``unavailable`` with the
evidence attached. See ``docs/VIRTUOSO_ACCEPTANCE.md``.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from layout_canvas.compiler.virtuoso import (
    TECHLIB_ENV,
    _oa_name,
    export_skill,
    export_spectre,
    oa_layer_map,
    resolve_tech_lib,
)

HOST_ENV = "LAYOUT_CANVAS_VIRTUOSO_HOST"
DIR_ENV = "LAYOUT_CANVAS_VIRTUOSO_DIR"
MODELS_ENV = "LAYOUT_CANVAS_SPECTRE_MODELS"

_SETUP_HINT = (
    "Remote Virtuoso acceptance needs a Linux EDA host reachable over SSH:\n"
    f"  {HOST_ENV}=user@eda-host   (ssh target or ~/.ssh/config alias)\n"
    f"  {DIR_ENV}=~/layout_canvas_accept   (remote working directory)\n"
    f"  {MODELS_ENV}=/pdk/models/tt.scs   (optional, enables the spectre leg)\n"
    f"  {TECHLIB_ENV}=sky130_fd_pr        (OA tech library for LPP names)\n"
    "Auth is key-based only (ssh -o BatchMode=yes — no passwords). "
    "Full setup guide: docs/VIRTUOSO_ACCEPTANCE.md"
)

# CIW markers Virtuoso prints for errors/warnings in its log.
_CIW_ERROR_RE = re.compile(r"\*Error\*")
_CIW_WARN_RE = re.compile(r"\*WARNING\*")


# ---------------------------------------------------------------------------
# Shared emit/inspect helpers
# ---------------------------------------------------------------------------


def _layout_stats(gds_path: str | Path) -> tuple[Any, dict[str, dict[str, int]], set[tuple[int, int]]]:
    """Per-cell expected counts and the (layer, datatype) set a GDS uses.

    Counts mirror ``export_skill``'s emit order exactly: text →
    ``dbCreateLabel``, box → ``dbCreateRect``, other shapes →
    ``dbCreatePolygon``, and each instance expands to ``na*nb``
    ``dbCreateInst`` calls (regular arrays unrolled).
    """
    import klayout.db as db

    ly = db.Layout()
    ly.read(str(gds_path))
    cells: dict[str, dict[str, int]] = {}
    layers_used: set[tuple[int, int]] = set()
    for cell in ly.each_cell():
        counts = {"rects": 0, "polygons": 0, "labels": 0, "insts": 0}
        for li in ly.layer_indexes():
            info = ly.get_info(li)
            layers_used.add((info.layer, info.datatype))
            for shape in cell.shapes(li).each():
                if shape.is_text():
                    counts["labels"] += 1
                elif shape.is_box():
                    counts["rects"] += 1
                else:
                    counts["polygons"] += 1
        for inst in cell.each_inst():
            cia = inst.cell_inst
            na = cia.na if cia.is_regular_array() else 1
            nb = cia.nb if cia.is_regular_array() else 1
            counts["insts"] += na * nb
        cells[cell.name] = counts
    return ly, cells, layers_used


def _unmapped_layers(
    layers_used: set[tuple[int, int]], tech: str
) -> list[str]:
    """``L/DT`` strings whose emitted OA name falls back to ``L<l>_D<dt>``."""
    layer_map = oa_layer_map(tech)
    out = []
    for layer, dt in sorted(layers_used):
        if (layer, dt) in layer_map:
            continue
        # export_skill's fallback: label/marker datatypes inherit (layer, 20)
        if (layer, 20) in layer_map:
            continue
        out.append(f"{layer}/{dt}")
    return out


def _oa_cell_names(ly: Any) -> dict[str, str]:
    """GDS cell name → OA-legal cellview name (same rule as export_skill)."""
    return {cell.name: _oa_name(cell.name) for cell in ly.each_cell()}


def _check(name: str, status: str, detail: str = "",
           expected: Any = None, actual: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "status": status, "detail": detail}
    if expected is not None:
        entry["expected"] = expected
    if actual is not None:
        entry["actual"] = actual
    return entry


def _extract_spice(gds_path: str | Path, tech: str) -> tuple[str | None, str]:
    """Best-effort netlist for a GDS: real LVS extraction, honestly reported.

    Returns ``(spice_text_or_None, reason)`` — ``None`` when the tech has no
    extraction recipe or extraction itself failed.
    """
    from layout_canvas.tools.extract import extract_netlist

    ext = extract_netlist(str(gds_path), tech)
    if ext.status != "ok" or ext.errors:
        reason = "; ".join(ext.errors or [ext.status])
        return None, f"extract_netlist({tech}): {reason}"
    return ext.netlist_text, ""


def _emit_artifacts(
    gds_path: str | Path,
    tech: str,
    library: str,
    spice_text: str | None,
    tech_lib: str | None = None,
) -> dict[str, Any]:
    """Generate the .il/.scs texts plus the GDS-side truth tables.

    ``spice_text`` lets the caller supply the golden schematic netlist;
    ``None`` means "derive it from the GDS" via real extraction.
    """
    tech_lib = resolve_tech_lib(tech, tech_lib)
    il_text = export_skill(gds_path, library, tech, tech_lib=tech_lib)
    ly, cells, layers_used = _layout_stats(gds_path)
    out: dict[str, Any] = {
        "il": il_text,
        "ly": ly,
        "cells": cells,
        "oa_names": _oa_cell_names(ly),
        "unmapped_layers": _unmapped_layers(layers_used, tech),
        "scs": None,
        "scs_reason": "",
        "tech_lib": tech_lib,
    }
    spice = spice_text
    reason = ""
    if spice is None:
        spice, reason = _extract_spice(gds_path, tech)
    if spice is not None:
        out["scs"] = export_spectre(spice)
    else:
        out["scs_reason"] = reason
    return out


# ---------------------------------------------------------------------------
# A1 — static checks
# ---------------------------------------------------------------------------


def _strip_skill_strings(text: str) -> str:
    """Remove ``"..."`` literals and ``;`` comments so paren counting is real."""
    out = []
    for line in text.splitlines():
        # strings first — a ";" inside a literal is not a comment
        line = re.sub(r'"(?:[^"\\]|\\.)*"', '""', line)
        out.append(line.split(";", 1)[0])
    return "\n".join(out)


def _check_skill(
    il_text: str, expected: dict[str, dict[str, int]], oa_names: dict[str, str]
) -> list[dict[str, Any]]:
    """Cross-check an emitted SKILL script against the GDS truth table."""
    checks: list[dict[str, Any]] = []

    # Paren balance over the whole file (strings/comments stripped).
    stripped = _strip_skill_strings(il_text)
    depth, min_depth = 0, 0
    for ch in stripped:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            min_depth = min(min_depth, depth)
    checks.append(_check(
        "skill_parens", "pass" if depth == 0 and min_depth == 0 else "fail",
        f"final depth {depth}, min depth {min_depth}",
        expected="balanced", actual=f"depth={depth} min={min_depth}"))

    # Per-cell blocks: `cv = dbOpenCellViewByType(...)` opens a context and
    # `dbClose(cv)` closes it; count the dbCreate* calls inside.
    open_re = re.compile(
        r'cv\s*=\s*dbOpenCellViewByType\("([^"]+)"\s+"([^"]+)"\s+'
        r'"layout"\s+"maskLayout"\s+"w"\)')
    create_re = re.compile(r"\bdbCreate(Rect|Polygon|Label|Inst)\b")
    blocks: dict[str, dict[str, int]] = {}
    saves: set[str] = set()
    current: str | None = None
    for line in il_text.splitlines():
        m = open_re.search(line)
        if m:
            current = m.group(2)
            blocks.setdefault(current, {
                "rects": 0, "polygons": 0, "labels": 0, "insts": 0})
            continue
        if current is None:
            continue
        for cm in create_re.finditer(line):
            kind = {"Rect": "rects", "Polygon": "polygons",
                    "Label": "labels", "Inst": "insts"}[cm.group(1)]
            blocks[current][kind] += 1
        if "dbSave(cv)" in line:
            saves.add(current)
        if "dbClose(cv)" in line:
            current = None

    # Coverage: every GDS cell appears exactly once, saved before close.
    expected_cells = {oa_names[c]: c for c in expected}
    missing = sorted(set(expected_cells) - set(blocks))
    checks.append(_check(
        "skill_cells_covered",
        "pass" if not missing else "fail",
        f"{len(blocks)} cellviews emitted, {len(expected_cells)} expected",
        expected=sorted(expected_cells), actual=sorted(blocks)))
    unsaved = sorted(set(blocks) - saves)
    checks.append(_check(
        "skill_cellviews_saved",
        "pass" if not unsaved else "fail",
        "every cellview has dbSave before dbClose" if not unsaved
        else f"missing dbSave: {unsaved}",
        actual=unsaved or None))

    kind_label = {"rects": "dbCreateRect", "polygons": "dbCreatePolygon",
                  "labels": "dbCreateLabel", "insts": "dbCreateInst"}
    for oa_cell, gds_cell in sorted(expected_cells.items()):
        got = blocks.get(oa_cell)
        if got is None:
            continue  # reported by skill_cells_covered
        for kind, gds_key in (("rects", "rects"), ("polygons", "polygons"),
                              ("labels", "labels"), ("insts", "insts")):
            want = expected[gds_cell][gds_key]
            checks.append(_check(
                f"skill:{oa_cell}:{kind}",
                "pass" if got[kind] == want else "fail",
                f"{kind_label[kind]} count",
                expected=want, actual=got[kind]))
    return checks


def _scs_subckts(scs_text: str) -> dict[str, list[str]]:
    """``subckt <name> ( <ports> )`` declarations in a Spectre netlist."""
    decls: dict[str, list[str]] = {}
    for m in re.finditer(
            r"(?m)^\s*subckt\s+(\S+)\s*\(\s*([^)]*?)\s*\)", scs_text):
        decls[m.group(1)] = m.group(2).split()
    return decls


def _check_spectre(scs_text: str) -> list[dict[str, Any]]:
    """Structural checks on a Spectre-dialect netlist (no simulator needed)."""
    checks: list[dict[str, Any]] = []
    body = "\n".join(
        line for line in scs_text.splitlines()
        if not line.strip().startswith("//"))
    decls = _scs_subckts(body)
    ends = re.findall(r"(?m)^\s*ends\b", body)
    checks.append(_check(
        "scs_subckt_ends", "pass" if len(decls) == len(ends) else "fail",
        "subckt/ends balance",
        expected=len(decls), actual=len(ends)))

    # X-cards: `Xname ( nodes ) master [params]` — master must be a defined
    # subckt and the node count must match its port list.
    refs: list[tuple[str, list[str], str]] = []
    for m in re.finditer(
            r"(?m)^\s*(X\S*)\s*\(\s*([^)]*?)\s*\)\s*(\S+)", body):
        refs.append((m.group(1), m.group(2).split(), m.group(3)))
    missing = sorted({master for _, _, master in refs} - set(decls))
    checks.append(_check(
        "scs_subckt_refs",
        "pass" if not missing else "fail",
        "every X-card master resolves to a defined subckt" if not missing
        else f"undefined subckts: {missing}",
        actual=missing or None))
    bad_ports = [
        f"{inst}->{master} ({len(nodes)} nodes vs {len(decls[master])} ports)"
        for inst, nodes, master in refs
        if master in decls and len(nodes) != len(decls[master])
    ]
    checks.append(_check(
        "scs_port_counts",
        "pass" if not bad_ports else "fail",
        "X-card node counts match subckt port lists" if not bad_ports
        else "; ".join(bad_ports),
        actual=bad_ports or None))
    return checks


def _rollup(checks: list[dict[str, Any]], *, ok_status: str) -> str:
    """``ok_status`` only when every check passed: any fail → ``failed``,
    else any unavailable → ``unavailable``, else ``ok_status``."""
    statuses = {c["status"] for c in checks}
    if "fail" in statuses:
        return "failed"
    if "unavailable" in statuses:
        return "unavailable"
    return ok_status


def static_report(
    gds_path: str | Path,
    tech: str,
    library: str = "canvas_lib",
    *,
    spice_text: str | None = None,
    workdir: str | Path | None = None,
    tech_lib: str | None = None,
) -> dict[str, Any]:
    """Cadence-free acceptance of the SKILL/Spectre exports of ``gds_path``.

    Emits the ``.il``/``.scs`` artifacts (written beside the GDS, or into
    ``workdir`` when given) and checks them against the GDS truth: per-cell
    rect/polygon/label/instance counts, ``.il`` paren balance, cellview
    coverage + ``dbSave``, ``.scs`` subckt/ends balance, X-card reference
    resolution and port counts.

    ``status`` is ``static_ok`` only when every check ran green, ``failed``
    on a real mismatch, and ``unavailable`` when part of the chain (klayout,
    netlist extraction) could not produce evidence — never a fabricated pass.
    """
    gds_path = Path(gds_path)
    try:
        import klayout.db  # noqa: F401
    except ImportError:
        return {"status": "unavailable",
                "error": "klayout python module required for SKILL export",
                "checks": [_check("klayout", "unavailable",
                                  "pip module 'klayout' not installed")],
                "unmapped_layers": [],
                "gds": str(gds_path)}

    art = _emit_artifacts(gds_path, tech, library, spice_text, tech_lib)
    checks: list[dict[str, Any]] = []
    checks.extend(_check_skill(art["il"], art["cells"], art["oa_names"]))

    if art["scs"] is not None:
        checks.extend(_check_spectre(art["scs"]))
    else:
        checks.append(_check(
            "scs_source", "unavailable",
            art["scs_reason"] or "no netlist source for the .scs export"))

    out_dir = Path(workdir) if workdir else gds_path.parent
    stem = gds_path.stem
    paths: dict[str, str] = {"gds": str(gds_path)}
    il_path = out_dir / f"{stem}.il"
    il_path.write_text(art["il"], encoding="utf-8")
    paths["il"] = str(il_path)
    if art["scs"] is not None:
        scs_path = out_dir / f"{stem}.scs"
        scs_path.write_text(art["scs"], encoding="utf-8")
        paths["scs"] = str(scs_path)

    return {
        "status": _rollup(checks, ok_status="static_ok"),
        "checks": checks,
        "unmapped_layers": art["unmapped_layers"],
        "cells": len(art["cells"]),
        "library": library,
        "tech": tech,
        "tech_lib": art["tech_lib"],
        "paths": paths,
    }


# ---------------------------------------------------------------------------
# A2 — remote acceptance over SSH
# ---------------------------------------------------------------------------


def _verify_skill(library: str, cell_names: list[str]) -> str:
    """SKILL audit script: census every rebuilt cellview into verify_report.txt.

    Opens each cellview read-only and writes one line per cell:
    ``<name> shapes=N rects=N polygons=N labels=N others=N insts=N``
    (or ``<name> MISSING``). ``exit()`` terminates the ``-replay`` session.
    """
    cells = " ".join(f'"{n}"' for n in cell_names)
    return f"""; generated by layout-canvas virtuoso acceptance (read-only audit)
(let ((outf (outfile "verify_report.txt")) cv rects polys lbls others)
  foreach(cell '( {cells} )
    cv = dbOpenCellViewByType("{library}" cell "layout" "maskLayout" "r")
    if(cv then
      rects = 0 polys = 0 lbls = 0 others = 0
      foreach(sh cv~>shapes
        case(sh~>objType
          ("rect"    rects = rects + 1)
          ("polygon" polys = polys + 1)
          ("label"   lbls = lbls + 1)
          (t         others = others + 1)))
      fprintf(outf "%s shapes=%d rects=%d polygons=%d labels=%d others=%d insts=%d\\n"
              cell length(cv~>shapes) rects polys lbls others
              length(cv~>instances))
      dbClose(cv)
    else
      fprintf(outf "%s MISSING\\n" cell)))
  close(outf)
  exit())
"""


def _spectre_harness(
    scs_text: str, top_hint: str | None, models: list[str]
) -> str:
    """Top-level Spectre bench: include the design .scs, instantiate its
    root subckt with all ports on node 0, run a dc operating point.

    Elaboration is the check — every subckt reference, model name and port
    count must resolve against the real model deck. No stimulus is
    invented: an all-zero bias point is the honest minimal analysis.
    """
    decls = _scs_subckts(scs_text)
    masters = {m.group(1) for m in re.finditer(
        r"(?m)^\s*X\S*\s*\(\s*[^)]*?\s*\)\s*(\S+)",
        "\n".join(l for l in scs_text.splitlines()
                  if not l.strip().startswith("//")))}
    top = None
    if top_hint and top_hint in decls:
        top = top_hint
    elif top_hint:
        # OA-sanitised cell name vs subckt name (design names pass through
        # export_spectre unchanged, but stay tolerant).
        for name in decls:
            if _oa_name(name) == top_hint or name == top_hint:
                top = name
                break
    if top is None:
        roots = [n for n in decls if n not in masters]
        if len(roots) == 1:
            top = roots[0]
    if top is None:
        raise ValueError(
            "cannot identify the root subckt in the .scs "
            f"(declared: {sorted(decls)})")

    lines = ["// generated by layout-canvas spectre acceptance harness",
             "simulator lang=spectre", ""]
    for mdl in models:
        lines.append(f'include "{mdl}"')
    if models:
        lines.append("")
    lines.append(scs_text.rstrip())
    lines.append("")
    ports = decls[top]
    nodes = " ".join("0" for _ in ports)
    lines.append(f"Xdut ( {nodes} ) {top}" if ports else f"Xdut ( ) {top}")
    lines.append("")
    lines.append("op dc")
    lines.append("")
    return "\n".join(lines)


class _RemoteUnavailable(Exception):
    """Transport/tool absence on the remote leg — maps to ``unavailable``."""


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise _RemoteUnavailable(
            f"{cmd[0]!r} not found on PATH — the remote leg needs local "
            "ssh/scp clients") from exc
    except subprocess.TimeoutExpired as exc:
        raise _RemoteUnavailable(
            f"`{' '.join(cmd)}` timed out after {timeout}s") from exc


def _remote_quote(path: str) -> str:
    """Shell-quote a remote path, keeping a leading ``~`` expandable."""
    if path.startswith("~"):
        return "~" + shlex.quote(path[1:])
    return shlex.quote(path)


def _ssh_argv(host: str) -> list[str]:
    # BatchMode: fail immediately instead of prompting for a password —
    # authentication is key-based only (ssh agent / ~/.ssh/config).
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host]


def _scp(argv: list[str], timeout: int, what: str) -> None:
    proc = _run(["scp", "-q", "-o", "BatchMode=yes", *argv], timeout)
    if proc.returncode != 0:
        raise _RemoteUnavailable(
            f"scp {what} failed (rc={proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}")


def _ciw_findings(log_text: str) -> tuple[list[str], int]:
    """``(*Error* lines, *WARNING* count)`` from a CIW log."""
    errors, warns = [], 0
    for line in log_text.splitlines():
        if _CIW_ERROR_RE.search(line):
            errors.append(line.strip())
        elif _CIW_WARN_RE.search(line):
            warns += 1
    return errors, warns


def _parse_verify_report(text: str) -> dict[str, dict[str, int] | str]:
    out: dict[str, dict[str, int] | str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, rest = line.partition(" ")
        if rest.strip() == "MISSING":
            out[name] = "MISSING"
            continue
        fields: dict[str, int] = {}
        for tok in rest.split():
            k, _, v = tok.partition("=")
            try:
                fields[k] = int(v)
            except ValueError:
                pass
        out[name] = fields
    return out


def _spectre_findings(log_text: str, rc: int | None) -> dict[str, Any]:
    """Classify a spectre run: error markers, analysis completion, rc."""
    errors = [
        line.strip() for line in log_text.splitlines()
        if "*Error*" in line or re.search(
            r"(?:error|Error).*(?:found by spectre|detected)", line)
        or re.match(r"^\s*Error\b", line)
    ]
    completed = bool(re.search(
        r"Analysis `?dc'?|spectre completes|aggregate audit", log_text))
    ok = rc == 0 and not errors and completed
    return {"errors": errors, "completed": completed, "rc": rc, "ok": ok}


def remote_acceptance(
    host: str | None,
    workdir: str | None,
    gds_path: str | Path,
    tech: str,
    *,
    library: str = "canvas_lib",
    spice_text: str | None = None,
    spectre_models: str | list[str] | None = None,
    tech_lib: str | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Real Virtuoso/Spectre acceptance on a remote EDA host over SSH.

    ``host``/``workdir``/``spectre_models`` default to
    ``LAYOUT_CANVAS_VIRTUOSO_HOST`` / ``LAYOUT_CANVAS_VIRTUOSO_DIR`` /
    ``LAYOUT_CANVAS_SPECTRE_MODELS``. With no host configured the result is
    ``unavailable`` with the setup recipe attached — never a silent skip.

    Flow: scp the ``.il``/``.scs``/``verify.il``/harness up → replay the
    SKILL in ``virtuoso -nograph -replay`` → census every rebuilt cellview
    into ``verify_report.txt`` → pull the report + CIW logs back → diff the
    census against the GDS truth → optionally run the top-level spectre
    harness. ``status`` is ``verified`` only when all of that is clean.
    """
    host = host or os.environ.get(HOST_ENV)
    workdir = workdir or os.environ.get(DIR_ENV)
    if spectre_models is None:
        spectre_models = os.environ.get(MODELS_ENV)
    models = ([spectre_models] if isinstance(spectre_models, str)
              else list(spectre_models or []))
    tech_lib = resolve_tech_lib(tech, tech_lib)

    result: dict[str, Any] = {
        "status": "unavailable",
        "host": host, "workdir": workdir,
        "tech_lib": tech_lib,
        "checks": [], "unmapped_layers": [],
    }
    if not host:
        result["error"] = f"{HOST_ENV} not set"
        result["setup"] = _SETUP_HINT
        return result
    if not workdir:
        result["error"] = f"{DIR_ENV} not set"
        result["setup"] = _SETUP_HINT
        return result

    host = re.sub(r"^ssh\s+", "", host.strip())  # tolerate "ssh user@host"
    gds_path = Path(gds_path)
    staging = Path(tempfile.mkdtemp(prefix="lc_virt_"))
    result["staging"] = str(staging)

    try:
        import klayout.db  # noqa: F401
    except ImportError:
        result["error"] = "klayout python module required for SKILL export"
        return result

    try:
        art = _emit_artifacts(gds_path, tech, library, spice_text, tech_lib)
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"artifact generation failed: {exc}"
        return result
    result["unmapped_layers"] = art["unmapped_layers"]
    checks: list[dict[str, Any]] = result["checks"]
    stem = gds_path.stem
    checks.append(_check(
        "tech_lib", "pass" if tech_lib else "unavailable",
        "OA tech library the .il attaches to (LPP names need it)" if tech_lib
        else f"no tech library known for tech '{tech}' — set {TECHLIB_ENV} "
             "or pass tech_lib; the replay will fail on undefined LPPs"))

    # The replayed .il gets an explicit exit() so the -replay session ends.
    il_local = staging / f"{stem}.il"
    il_local.write_text(art["il"] + "\nexit()\n", encoding="utf-8")
    uploads = [il_local]
    scs_local = None
    if art["scs"] is not None:
        scs_local = staging / f"{stem}.scs"
        scs_local.write_text(art["scs"], encoding="utf-8")
        uploads.append(scs_local)
    oa_names = [art["oa_names"][c] for c in art["cells"]]
    verify_local = staging / "verify.il"
    verify_local.write_text(_verify_skill(library, oa_names), encoding="utf-8")
    uploads.append(verify_local)

    harness_local = None
    harness_err = None
    if scs_local is not None and models:
        try:
            tops = list(art["ly"].top_cells())
            top_hint = tops[0].name if len(tops) == 1 else None
            harness_text = _spectre_harness(art["scs"], top_hint, models)
        except ValueError as exc:
            harness_err = str(exc)
        else:
            harness_local = staging / f"{stem}_harness.scs"
            harness_local.write_text(harness_text, encoding="utf-8")
            uploads.append(harness_local)

    qdir = _remote_quote(workdir)
    try:
        # Transport up.
        proc = _run(_ssh_argv(host) + [f"mkdir -p {qdir}"], timeout)
        if proc.returncode != 0:
            raise _RemoteUnavailable(
                f"ssh mkdir failed (rc={proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}")
        _scp([str(p) for p in uploads] + [f"{host}:{qdir}/"],
             timeout, "upload")

        # Replay the layout SKILL — builds every cellview in the OA library.
        proc = _run(_ssh_argv(host) + [
            f"cd {qdir} && virtuoso -nograph -replay "
            f"{shlex.quote(stem + '.il')} -log ciw.log"], timeout)
        if proc.returncode == 127 or "not found" in proc.stderr:
            raise _RemoteUnavailable(
                f"virtuoso not found on {host}: {proc.stderr.strip()}")
        checks.append(_check(
            "ssh_virtuoso_replay_ran",
            "pass" if proc.returncode == 0 else "fail",
            f"rc={proc.returncode}",
            actual=proc.stderr.strip() or None))

        # Audit pass: census the rebuilt cellviews.
        proc = _run(_ssh_argv(host) + [
            f"cd {qdir} && virtuoso -nograph -replay verify.il "
            f"-log ciw_verify.log"], timeout)
        checks.append(_check(
            "ssh_verify_replay_ran",
            "pass" if proc.returncode == 0 else "fail",
            f"rc={proc.returncode}",
            actual=proc.stderr.strip() or None))

        # Pull evidence back — each file optional, absence handled below.
        fetched: dict[str, str] = {}
        for remote_name in ("ciw.log", "ciw_verify.log", "verify_report.txt"):
            local = staging / remote_name
            proc = _run(["scp", "-q", "-o", "BatchMode=yes",
                         f"{host}:{qdir}/{remote_name}", str(local)],
                        timeout)
            if proc.returncode == 0 and local.is_file():
                fetched[remote_name] = local.read_text(
                    encoding="utf-8", errors="replace")

        # CIW log findings.
        ciw_errors, ciw_warns = _ciw_findings(fetched.get("ciw.log", ""))
        ciw_verr, ciw_vwarn = _ciw_findings(fetched.get("ciw_verify.log", ""))
        result["ciw"] = {
            "errors": len(ciw_errors), "warnings": ciw_warns,
            "verify_errors": len(ciw_verr), "verify_warnings": ciw_vwarn,
        }
        checks.append(_check(
            "ciw_errors",
            "pass" if not ciw_errors else "fail",
            f"{len(ciw_errors)} *Error* lines, {ciw_warns} *WARNING* lines "
            "in ciw.log" + ("" if "ciw.log" in fetched
                            else " (ciw.log NOT fetched — replay may not "
                                 "have run)"),
            actual=ciw_errors[:10] or None))
        if "ciw.log" not in fetched:
            checks.append(_check(
                "ciw_log_fetched", "fail",
                "ciw.log missing on remote — virtuoso did not produce it"))
        checks.append(_check(
            "ciw_verify_errors",
            "pass" if not ciw_verr else "fail",
            f"{len(ciw_verr)} *Error* lines, {ciw_vwarn} *WARNING* lines "
            "in ciw_verify.log",
            actual=ciw_verr[:10] or None))

        # Census diff.
        report = fetched.get("verify_report.txt")
        if report is None:
            checks.append(_check(
                "verify_report", "fail",
                "verify_report.txt not produced — verify.il did not run "
                "to completion"))
        else:
            census = _parse_verify_report(report)
            for oa_cell, gds_cell in sorted(
                    (art["oa_names"][c], c) for c in art["cells"]):
                got = census.get(oa_cell)
                want = art["cells"][gds_cell]
                if got is None:
                    checks.append(_check(
                        f"oa:{oa_cell}", "fail",
                        "cellview absent from verify_report",
                        expected=want))
                elif got == "MISSING":
                    checks.append(_check(
                        f"oa:{oa_cell}", "fail",
                        "cellview missing in OA library after replay",
                        expected=want))
                else:
                    for key, label in (("rects", "rects"),
                                       ("polygons", "polygons"),
                                       ("labels", "labels"),
                                       ("insts", "insts")):
                        checks.append(_check(
                            f"oa:{oa_cell}:{key}",
                            "pass" if got.get(key) == want[label] else "fail",
                            f"OA {key} count vs GDS",
                            expected=want[label],
                            actual=got.get(key)))

        # Spectre leg: only real when models were supplied — a device-level
        # netlist without a model deck cannot elaborate, so report the
        # missing precondition honestly instead of running a doomed check.
        if scs_local is None:
            result["spectre"] = {
                "status": "unavailable",
                "reason": art["scs_reason"] or "no .scs produced",
            }
        elif not models:
            result["spectre"] = {
                "status": "unavailable",
                "reason": f"no spectre models configured ({MODELS_ENV}); "
                          "device elaboration needs the PDK model deck",
            }
        elif harness_err is not None:
            result["spectre"] = {
                "status": "unavailable",
                "reason": f"harness not generated: {harness_err}",
            }
        else:
            proc = _run(_ssh_argv(host) + [
                f"cd {qdir} && spectre "
                f"{shlex.quote(stem + '_harness.scs')} -log spectre.log"],
                timeout)
            local_log = staging / "spectre.log"
            _run(["scp", "-q", "-o", "BatchMode=yes",
                  f"{host}:{qdir}/spectre.log", str(local_log)], timeout)
            log_text = (local_log.read_text(encoding="utf-8",
                                            errors="replace")
                        if local_log.is_file() else proc.stdout + proc.stderr)
            findings = _spectre_findings(log_text, proc.returncode)
            result["spectre"] = {
                "status": "ok" if findings["ok"] else "failed",
                "rc": proc.returncode,
                "errors": findings["errors"][:10],
                "analysis_completed": findings["completed"],
                "log_tail": "\n".join(log_text.splitlines()[-15:]),
            }
            checks.append(_check(
                "spectre_op",
                "pass" if findings["ok"] else "fail",
                "spectre dc-op elaboration of the exported netlist",
                actual=findings["errors"][:5] or None))
    except _RemoteUnavailable as exc:
        result["status"] = "unavailable"
        result["error"] = str(exc)
        result["setup"] = _SETUP_HINT
        return result

    spectre_status = result.get("spectre", {}).get("status")
    if spectre_status == "unavailable":
        checks.append(_check(
            "spectre", "unavailable",
            result["spectre"].get("reason", "")))
    result["status"] = _rollup(checks, ok_status="verified")
    return result
