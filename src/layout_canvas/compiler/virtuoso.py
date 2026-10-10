"""Virtuoso bridge: emit a SKILL replay script that rebuilds a layout
hierarchy inside the OA database.

The generated ``.il`` file creates one cellview per GDS cell with real
geometry (``dbCreateRect``/``dbCreatePolygon``), pin labels
(``dbCreateLabel``) and subcell instances (``dbCreateInst``). Running it in
Virtuoso (``load("file.il")``) reproduces the layout without a GDS license
path — the layout-side analog of the Spectre dialect export.

Cell names are sanitized to OA-legal identifiers. Layer numbers resolve
through ``oa_layer_map`` — the descriptor's ``oa_layers`` section first,
then the per-PDK table below; unmapped layers emit ``L<layer>_D<dt>``
names so nothing is silently dropped.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# (layer, datatype) -> (oa layer name, purpose)
_OA_LAYERS: dict[str, dict[tuple[int, int], tuple[str, str]]] = {
    "sky130": {
        (64, 20): ("nwell", "drawing"), (65, 20): ("diff", "drawing"),
        (65, 44): ("tap", "drawing"), (66, 20): ("poly", "drawing"),
        (66, 44): ("licon1", "drawing"), (67, 20): ("li1", "drawing"),
        (67, 44): ("mcon", "drawing"), (68, 20): ("met1", "drawing"),
        (68, 44): ("via", "drawing"), (69, 20): ("met2", "drawing"),
        (69, 44): ("via2", "drawing"), (70, 20): ("met3", "drawing"),
        (70, 44): ("via3", "drawing"), (71, 20): ("met4", "drawing"),
        (71, 44): ("via4", "drawing"), (72, 20): ("met5", "drawing"),
        (93, 44): ("nsdm", "drawing"), (94, 20): ("psdm", "drawing"),
    },
    "ihp_sg13g2": {
        (1, 0): ("Activ", "drawing"), (5, 0): ("GatPoly", "drawing"),
        (6, 0): ("Cont", "drawing"), (7, 0): ("nSD", "drawing"),
        (8, 0): ("Metal1", "drawing"), (10, 0): ("Metal2", "drawing"),
        (14, 0): ("pSD", "drawing"), (19, 0): ("Via1", "drawing"),
        (29, 0): ("Via2", "drawing"), (30, 0): ("Metal3", "drawing"),
        (31, 0): ("nWell", "drawing"), (49, 0): ("Via3", "drawing"),
        (50, 0): ("Metal4", "drawing"), (66, 0): ("Via4", "drawing"),
        (67, 0): ("Metal5", "drawing"), (125, 0): ("TopVia1", "drawing"),
        (126, 0): ("TopMetal1", "drawing"),
    },
}

# label datatype -> OA purpose
_LABEL_PURPOSE = "pin"

# tech name -> default OA tech library. Layout .il replays create shapes by
# LPP name, so the target library must be attached to a tech library that
# defines them — a bare ddCreateLib() library has no techfile and every
# dbCreateRect errors on a real Virtuoso. Override per installation with
# LAYOUT_CANVAS_VIRTUOSO_TECHLIB (the PDK's own tech lib name varies).
TECHLIB_ENV = "LAYOUT_CANVAS_VIRTUOSO_TECHLIB"
_DEFAULT_TECHLIBS = {
    "sky130": "sky130_fd_pr",
    "ihp_sg13g2": "SG13G2",
}


def resolve_tech_lib(tech: str, tech_lib: str | None = None) -> str | None:
    """Tech library the emitted .il attaches the design library to.

    Priority: explicit argument → ``LAYOUT_CANVAS_VIRTUOSO_TECHLIB`` →
    per-tech default → ``None`` (emitted script warns instead of attaching).
    """
    import os

    return (tech_lib or os.environ.get(TECHLIB_ENV)
            or _DEFAULT_TECHLIBS.get(tech))


def oa_layer_map(tech: str) -> dict[tuple[int, int], tuple[str, str]]:
    """Resolved ``(layer, datatype) -> (oa layer name, purpose)`` table.

    A registered PDK descriptor's ``oa_layers`` section wins per key over
    the built-in table below; pairs absent from both fall back to
    ``L<layer>_D<dt>`` at emit time so nothing is silently dropped.
    """
    resolved = dict(_OA_LAYERS.get(tech, {}))
    try:
        from layout_canvas.pdk import get_pdk

        pdk = get_pdk(tech)
    except Exception:
        pdk = None
    if pdk is not None:
        for pair, oa_name in (pdk.oa_layers or {}).items():
            resolved[pair] = (oa_name, "drawing")
    return resolved

_ORIENTS = {
    0: "R0", 1: "R90", 2: "R180", 3: "R270",
    4: "MX", 5: "MXR90", 6: "MY", 7: "MYR90",
}


def _oa_name(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    return f"c_{name}" if name and name[0].isdigit() else (name or "cell")


def _fmt(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def export_skill(
    gds_path: str | Path,
    library: str,
    tech: str = "sky130",
    cell_name_map: dict[str, str] | None = None,
    tech_lib: str | None = None,
) -> str:
    """Render a SKILL script that rebuilds ``gds_path`` in ``library``.

    ``tech_lib`` is the OA technology library the new design library is
    attached to (``techSetTechLibName``) — required for the LPP names in
    ``dbCreate*`` calls to resolve on a real Virtuoso. Resolution order:
    argument → ``LAYOUT_CANVAS_VIRTUOSO_TECHLIB`` → per-tech default.
    """
    try:
        import klayout.db as db
    except ImportError:
        raise RuntimeError("klayout python module required for SKILL export")

    ly = db.Layout()
    ly.read(str(gds_path))
    layer_map = oa_layer_map(tech)
    names: dict[str, str] = {}
    for cell in ly.each_cell():
        names[cell.name] = _oa_name(cell.name)
    if cell_name_map:
        names.update(cell_name_map)

    # children must exist before parents instantiate them
    depth: dict[int, int] = {}

    def _depth(cell) -> int:
        if cell.cell_index() in depth:
            return depth[cell.cell_index()]
        d = 0
        for inst in cell.each_inst():
            d = max(d, _depth(ly.cell(inst.cell_inst.cell_index)) + 1)
        depth[cell.cell_index()] = d
        return d

    cells = sorted(ly.each_cell(), key=_depth)

    tech_lib = resolve_tech_lib(tech, tech_lib)
    out = [
        "; generated by layout-canvas virtuoso bridge",
        f'; source gds: {Path(gds_path).name}',
        f'let((cv master libId)',
        f'  when(not ddGetObj("{library}")',
        f'    ddCreateLib("{library}" "{library}_path"))',
        f'  libId = ddGetObj("{library}")',
    ]
    if tech_lib:
        # LPP names only resolve while a tech library that defines them is
        # attached — a bare ddCreateLib() library breaks every dbCreate*.
        out.append(
            f'  if(libId && ddGetObj("{tech_lib}") '
            f'&& getd(quote(techSetTechLibName)) then\n'
            f'    techSetTechLibName(libId "{tech_lib}")\n'
            f'  else\n'
            f'    printf("layout-canvas: tech library {tech_lib} not visible '
            f'- dbCreate* will fail on undefined LPPs\\n"))')
    else:
        out.append(
            '  printf("layout-canvas: no tech library configured - set '
            'LAYOUT_CANVAS_VIRTUOSO_TECHLIB or pass tech_lib. '
            'dbCreate* requires LPPs defined by the PDK techfile\\n")')
    for cell in cells:
        out.append(f'  cv = dbOpenCellViewByType("{library}" "{names[cell.name]}" '
                   f'"layout" "maskLayout" "w")')
        for li in ly.layer_indexes():
            info = ly.get_info(li)
            lp = layer_map.get((info.layer, info.datatype))
            if lp is None:
                # label/marker datatypes inherit the drawing layer's OA name
                lp = layer_map.get((info.layer, 20),
                                   (f"L{info.layer}_D{info.datatype}", "drawing"))
            lpp = f'list("{lp[0]}" "{lp[1]}")'
            label_lpp = f'list("{lp[0]}" "{_LABEL_PURPOSE}")'
            for shape in cell.shapes(li).each():
                if shape.is_text():
                    t = shape.text
                    pos = t.trans.disp
                    out.append(
                        f'  dbCreateLabel(cv {label_lpp} '
                        f'{_fmt(pos.x * ly.dbu)}:{_fmt(pos.y * ly.dbu)} '
                        f'"{t.string}" "centerCenter" "R0" "stick" 0.1)')
                elif shape.is_box():
                    b = shape.bbox()
                    out.append(
                        f'  dbCreateRect(cv {lpp} '
                        f'list({_fmt(b.left * ly.dbu)}:{_fmt(b.bottom * ly.dbu)} '
                        f'{_fmt(b.right * ly.dbu)}:{_fmt(b.top * ly.dbu)}))')
                else:
                    pts = " ".join(
                        f"{_fmt(p.x * ly.dbu)}:{_fmt(p.y * ly.dbu)}"
                        for p in shape.polygon.each_point_hull())
                    out.append(f'  dbCreatePolygon(cv {lpp} list({pts}))')
        inst_no = 0  # instance names must be unique per cellview — two
        # instances of the same child would otherwise both emit "<child>_I0_0"
        for inst in cell.each_inst():
            cia = inst.cell_inst
            child_name = ly.cell(cia.cell_index).name
            child = names.get(child_name, _oa_name(child_name))
            na = cia.na if cia.is_regular_array() else 1
            nb = cia.nb if cia.is_regular_array() else 1
            ct = inst.cplx_trans  # angle+mirror+mag — GDS mirrors/mags survive
            rot = int(round(ct.angle / 45.0)) % 4 + (4 if ct.is_mirror() else 0)
            orient = _ORIENTS.get(rot, "R0")
            for i in range(na):
                for j in range(nb):
                    inst_no += 1
                    tx = ct.disp.x + cia.a.x * i + cia.b.x * j
                    ty = ct.disp.y + cia.a.y * i + cia.b.y * j
                    out.append(
                        f'  master = dbOpenCellViewByType("{library}" "{child}" "layout")')
                    out.append(
                        f'  dbCreateInst(cv master "I{inst_no}_{child}" '
                        f'{_fmt(tx * ly.dbu)}:{_fmt(ty * ly.dbu)} '
                        f'"{orient}" {_fmt(ct.mag)})')
        out.append("  dbSave(cv)")
        out.append("  dbClose(cv)")
    out.append(")")
    return "\n".join(out) + "\n"


def export_spectre(spice_text: str) -> str:
    """Translate our generated SPICE dialect into a Spectre ``.scs`` netlist.

    Covers the constructs ``compile_netlist`` emits: ``.subckt/.ends``,
    X-cards, M-cards, R/C/V/I cards, ``.model``, ``.param``, ``.global``.
    Unknown directives pass through as comments rather than being dropped.
    """
    out = ["// generated by layout-canvas spectre export",
           "simulator lang=spectre", ""]
    for raw in spice_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("*"):
            out.append("// " + line.lstrip("* "))
            continue
        toks = line.split()
        head = toks[0].lower()
        if head == ".subckt":
            name, *pins = toks[1:]
            pins = [p for p in pins if "=" not in p]
            params = [p for p in toks[2:] if "=" in p]
            body = f"subckt {name} ( {' '.join(pins)} )"
            if params:
                body += f"\n    parameters {' '.join(params)}"
            out.append(body)
        elif head == ".ends":
            out.append(f"ends {toks[1] if len(toks) > 1 else ''}".rstrip())
        elif head == ".model":
            out.append("model " + " ".join(toks[1:]))
        elif head == ".param":
            out.append("parameters " + " ".join(toks[1:]))
        elif head == ".global":
            out.append("global " + " ".join(toks[1:]))
        elif head == ".include":
            out.append(f'include "{toks[1].strip(chr(34))}"')
        elif head.startswith("."):
            out.append(f"// unsupported: {line}")
        elif head.startswith(("x", "m", "r", "c", "v", "i", "d", "q", "l")):
            name = toks[0]
            rest = toks[1:]
            # split trailing model/master + params from leading nodes
            param_start = next((i for i, t in enumerate(rest) if "=" in t),
                               len(rest))
            nodes, params = rest[:param_start], rest[param_start:]
            if not params and nodes:
                master = nodes.pop()  # x/m cards: last node-token is master
            elif nodes and "=" not in nodes[-1] and head[0] in "xm":
                master = nodes.pop()
            else:
                master = None
            if head.startswith(("r", "c", "l")) and nodes and master is None:
                # RC primitives: last positional token is the value
                nodes, val = nodes[:-1], nodes[-1]
                kind = {"r": "resistor r", "c": "capacitor c", "l": "inductor l"}[head[0]]
                out.append(f"{name} ( {' '.join(nodes)} ) {kind}={val}")
            elif master is not None:
                out.append(f"{name} ( {' '.join(nodes)} ) {master}"
                           + (f" {' '.join(params)}" if params else ""))
            else:
                out.append(f"{name} ( {' '.join(nodes)} )"
                           + (f" {' '.join(params)}" if params else ""))
        else:
            out.append(line)
    return "\n".join(out) + "\n"
