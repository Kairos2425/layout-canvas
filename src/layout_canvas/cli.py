"""CLI entry point."""

import argparse
import sys

from layout_canvas.compiler.compile import export_gds, export_oas
from layout_canvas.compiler.netlist import export_spice
from layout_canvas.ir import json_schema
from layout_canvas.ir.model import Design


def main() -> int:
    parser = argparse.ArgumentParser(prog="layout-canvas")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # schema command
    sub.add_parser("schema", help="Print Block IR JSON schema")

    # compile command
    comp = sub.add_parser("compile", help="Compile Block IR to GDS/OASIS")
    comp.add_argument("input", help="Block IR JSON file")
    comp.add_argument("-o", "--output", required=True, help="Output GDS/OAS path")
    comp.add_argument("-f", "--format", choices=["gds", "oas"], default="gds")

    # netlist command
    net = sub.add_parser("netlist", help="Compile Block IR to SPICE netlist for LVS")
    net.add_argument("input", help="Block IR JSON file")
    net.add_argument("-o", "--output", required=True, help="Output SPICE (.sp / .cir) path")

    # import-netlist command
    imp = sub.add_parser(
        "import-netlist",
        help="Import an upstream SPICE/Spectre netlist as a Block IR draft "
             "(subckts map to parametric blocks by name + pin signature; "
             "unmappable instances are named, never guessed)")
    imp.add_argument("input", help="SPICE (.sp/.cir) or Spectre (.scs) file")
    imp.add_argument("--pdk", default="sky130", help="Target PDK (default sky130)")
    imp.add_argument("--top", help="Top subckt name (required when ambiguous)")
    imp.add_argument("--dialect", choices=["auto", "spice", "spectre"],
                     default="auto")
    imp.add_argument("--name", help="Design name (default: top subckt name)")
    imp.add_argument("-o", "--output", help="Output Block IR JSON path")

    # pdk command
    pdkp = sub.add_parser(
        "pdk",
        help="List registered PDKs or dump a descriptor JSON template")
    pdkp.add_argument(
        "action", choices=["list", "dump", "check"],
        help="'list' shows every registered PDK; 'dump NAME' prints a "
             "*.pdk.json template for NAME (built-ins included — a "
             "starting point for commercial PDK descriptors); "
             "'check FILE' validates a descriptor offline and reports the "
             "capabilities it would unlock")
    pdkp.add_argument("name", nargs="?", help="PDK name for 'dump', "
                        "*.pdk.json path for 'check'")

    # testbench command
    tb = sub.add_parser("testbench", help="Run design testbenches and report spec results")
    tb.add_argument("input", help="Block IR JSON file")
    tb.add_argument("--name", help="Run a single named testbench (default: all)")

    # virtuoso-accept command
    vacc = sub.add_parser(
        "virtuoso-accept",
        help="Virtuoso acceptance: compile → static SKILL/Spectre checks "
             "→ optional remote run on an SSH EDA host")
    vacc.add_argument("input", help="Block IR JSON file")
    vacc.add_argument("--host", help="SSH target for remote acceptance "
                                     "(or LAYOUT_CANVAS_VIRTUOSO_HOST)")
    vacc.add_argument("--lib", default="canvas_lib",
                      help="OA library name for the SKILL replay")
    vacc.add_argument("--tech-lib", default=None,
                      help="OA tech library the replay attaches to (or "
                           "LAYOUT_CANVAS_VIRTUOSO_TECHLIB; needed for LPP "
                           "names to resolve)")
    vacc.add_argument("--dry-run", action="store_true",
                      help="compile + static checks only; never touch SSH")

    # mcp command
    sub.add_parser("mcp", help="Start Model Context Protocol (MCP) stdio server for AI agents")

    # web command
    web = sub.add_parser("web", help="Start the local-first layout review canvas")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8080)

    args = parser.parse_args()

    if args.cmd == "schema":
        import json
        print(json.dumps(json_schema(), indent=2))
        return 0

    if args.cmd == "compile":
        with open(args.input) as f:
            design = Design.from_json(f.read())

        # Auto-import Sky130 blocks to populate registry
        import layout_canvas.blocks.generic  # noqa: F401  external gen_* blocks
        import layout_canvas.blocks.sky130  # noqa: F401

        if args.format == "gds":
            export_gds(design, args.output)
        else:
            export_oas(design, args.output)

        print(f"Compiled {design.name} → {args.output}", file=sys.stderr)
        return 0

    if args.cmd == "pdk":
        import json

        from layout_canvas.pdk import all_pdks, external_pdk_errors

        if args.action == "list":
            import layout_canvas.blocks.generic as _gen  # noqa: F401

            print(json.dumps({
                "pdks": [
                    {
                        "name": p.name,
                        "source": p.source or "built-in",
                        "layers": len(p.layers),
                        "pin_purpose": p.pin_purpose,
                        "extract": p.extract is not None,
                        "drc": p.drc is not None,
                        "gen_blocks": list(_gen.generated_block_names(p.name)),
                    }
                    for p in all_pdks().values()
                ],
                "load_errors": external_pdk_errors(),
                "generation_errors": _gen.generation_errors(),
            }, indent=2))
            return 0
        if args.action == "check":
            if not args.name:
                print("pdk check needs a *.pdk.json path", file=sys.stderr)
                return 2
            return _pdk_check(args.name)
        if not args.name:
            print("pdk dump needs a PDK name (see 'layout-canvas pdk list')",
                  file=sys.stderr)
            return 2
        try:
            template = _pdk_template(args.name)
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(template, indent=2))
        return 0

    if args.cmd == "netlist":
        with open(args.input) as f:
            design = Design.from_json(f.read())

        import layout_canvas.blocks.generic  # noqa: F401
        import layout_canvas.blocks.sky130  # noqa: F401
        export_spice(design, args.output)
        print(f"Generated SPICE netlist {design.name} → {args.output}", file=sys.stderr)
        return 0

    if args.cmd == "import-netlist":
        from pathlib import Path as _Path

        from layout_canvas.compiler.netlist_import import import_netlist

        text = _Path(args.input).read_text(encoding="utf-8")
        res = import_netlist(text, pdk=args.pdk, top=args.top,
                             dialect=args.dialect,
                             design_name=args.name)
        for m in res["mapped"]:
            print(f"  mapped   {m['instance']}  {m['subckt']} -> {m['block']}",
                  file=sys.stderr)
        for u in res["unresolved"]:
            print(f"  skipped  {u['instance']}  {u['subckt']} — {u['reason']}",
                  file=sys.stderr)
        for d in res["diagnostics"]:
            print(f"  note     {d}", file=sys.stderr)
        print(f"status={res['status']} ({len(res['mapped'])} mapped, "
              f"{len(res['unresolved'])} unresolved)", file=sys.stderr)
        if res["ir"] is None:
            return 2
        if args.output:
            _Path(args.output).write_text(res["ir_text"], encoding="utf-8")
            print(f"wrote {args.output}", file=sys.stderr)
        else:
            print(res["ir_text"])
        return 0

    if args.cmd == "testbench":
        with open(args.input) as f:
            design = Design.from_json(f.read())
        import layout_canvas.blocks.generic  # noqa: F401
        import layout_canvas.blocks.sky130  # noqa: F401
        try:
            import layout_canvas.blocks.ihp_sg13g2  # noqa: F401
        except Exception:
            pass

        from layout_canvas.tools.testbench import run_all, run_testbench

        try:
            runs = ([run_testbench(design, args.name)] if args.name
                    else run_all(design))
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        for run in runs:
            print(f"[{run['testbench']}] sim={run.get('status')} "
                  f"spec_status={run.get('spec_status')}")
            for spec in run.get("specs", []):
                bounds = ",".join(
                    f"{k}={spec[k]}{spec['unit']}" for k in ("min", "max")
                    if spec[k] is not None)
                line = (f"  {spec['name']}: {spec['status']} "
                        f"value={spec['value']}{spec['unit']} "
                        f"({spec['measure']} {spec['signal']}; {bounds})")
                if spec.get("reason"):
                    line += f" — {spec['reason']}"
                print(line)
        return 0 if all(r.get("spec_status") == "pass" for r in runs) else 1

    if args.cmd == "virtuoso-accept":
        return _virtuoso_accept(args)

    if args.cmd == "mcp":
        from layout_canvas.mcp.server import run_stdio_server
        run_stdio_server()
        return 0

    if args.cmd == "web":
        from layout_canvas.web import run
        run(host=args.host, port=args.port)
        return 0

    return 1


def _virtuoso_accept(args) -> int:
    """``virtuoso-accept``: compile → static checks → optional SSH remote leg."""
    import os
    import tempfile
    from pathlib import Path

    from layout_canvas.compiler.compile import compile_design
    from layout_canvas.compiler.netlist import compile_netlist
    from layout_canvas.tools import virtuoso_check

    with open(args.input) as f:
        design = Design.from_json(f.read())

    import layout_canvas.blocks.generic  # noqa: F401
    import layout_canvas.blocks.sky130  # noqa: F401
    try:
        import layout_canvas.blocks.ihp_sg13g2  # noqa: F401
    except Exception:
        pass

    root = Path(tempfile.mkdtemp(prefix="lc_virt_"))
    gds = root / f"{design.name}.gds"
    compile_design(design).write_gds(str(gds))
    print(f"[compile] {design.name} → {gds}")
    spice = compile_netlist(design)

    rep = virtuoso_check.static_report(
        gds, design.pdk, library=args.lib, spice_text=spice,
        tech_lib=args.tech_lib)
    print(f"[static] status={rep['status']} "
          f"({_summarize_checks(rep['checks'])})")
    for chk in rep["checks"]:
        if chk["status"] != "pass":
            print(f"  {chk['status']}: {chk['name']} — {chk['detail']}")
    if rep.get("unmapped_layers"):
        print(f"  unmapped_layers (emit as L*_D* fallback): "
              f"{rep['unmapped_layers']} — map them via the descriptor's "
              "oa_layers section if the OA techfile defines them")
    print(f"  artifacts: {rep['paths']}")

    remote = None
    host = args.host or os.environ.get(virtuoso_check.HOST_ENV)
    if args.dry_run:
        print("[remote] skipped (--dry-run)")
    elif not host:
        print(f"[remote] skipped — set {virtuoso_check.HOST_ENV} or pass "
              "--host to run the SSH acceptance leg "
              "(docs/VIRTUOSO_ACCEPTANCE.md)")
    else:
        remote = virtuoso_check.remote_acceptance(
            host, None, gds, design.pdk,
            library=args.lib, spice_text=spice, tech_lib=args.tech_lib)
        print(f"[remote] status={remote['status']} host={host}")
        for chk in remote.get("checks", []):
            if chk["status"] != "pass":
                print(f"  {chk['status']}: {chk['name']} — {chk['detail']}")
        if remote.get("error"):
            print(f"  error: {remote['error']}")
        spectre = remote.get("spectre")
        if spectre:
            print(f"  spectre: {spectre['status']}"
                  + (f" — {spectre.get('reason')}"
                     if spectre.get("reason") else ""))

    # Exit non-zero unless every executed stage is green — an unavailable
    # leg means acceptance could not complete, which is not a pass.
    ok = rep["status"] == "static_ok" and (
        remote is None or remote["status"] == "verified")
    return 0 if ok else 1


def _summarize_checks(checks: list[dict]) -> str:
    tally: dict[str, int] = {}
    for c in checks:
        tally[c["status"]] = tally.get(c["status"], 0) + 1
    return ", ".join(f"{v} {k}" for k, v in sorted(tally.items()))


def _pdk_template(name: str) -> dict:
    """JSON-able ``*.pdk.json`` template for ``pdk dump NAME``.

    External descriptors serialise their own sections; for built-ins the
    extract/DRC truth lives in the tool tables (``tools/extract.py``,
    ``tools/drc.py``), which are merged in here so the dump is a complete
    starting point for commercial descriptors.
    """
    from layout_canvas.pdk import get_pdk

    pdk = get_pdk(name)
    out = pdk.to_dict()

    if out.get("extract") is None:
        from layout_canvas.tools.extract import _RECIPES, LEAF_DEVICES

        recipe = _RECIPES.get(name)
        if recipe is not None:
            from layout_canvas.pdk.descriptor import EXTRACTION_ROLES

            out["extract"] = {
                "roles": {
                    role: ([*recipe[role]] if recipe.get(role) else None)
                    for role in EXTRACTION_ROLES
                },
                "text_datatypes": list(recipe.get("text_datatypes", ())),
                "leaf_devices": {
                    model: [cls_name, pol]
                    for model, (cls_name, pol) in
                    LEAF_DEVICES.get(name, {}).items()
                },
            }

    # (layer, datatype) -> preferred name: drawing names win, then roles.
    names = {pair: lname for lname, pair in pdk.layers.items()}
    for role, pair in (out.get("extract") or {}).get("roles", {}).items():
        if pair:
            names.setdefault(tuple(pair), role)

    def _nm(pair) -> str:
        return names.get(tuple(pair), f"{pair[0]}/{pair[1]}")

    if out.get("drc") is None:
        from layout_canvas.tools.drc import _PYA_ENCLOSURE, _PYA_RULES

        rules = _PYA_RULES.get(name)
        enclosure = _PYA_ENCLOSURE.get(name)
        if rules or enclosure:
            out["drc"] = {
                "rules": {
                    _nm(pair): [[kind, value] for kind, value in checks]
                    for pair, checks in (rules or {}).items()
                },
                "enclosure": [
                    {"label": label, "cut": _nm(cut),
                     "enclosed_by": [_nm(o) for o in outers], "enc": value}
                    for label, cut, outers, value in (enclosure or [])
                ],
            }
    return out


def _pdk_check(path: str) -> int:
    """Offline-validate one ``*.pdk.json`` and report what it would unlock.

    Loads the descriptor exactly the way ``LAYOUT_CANVAS_PDK_DIR`` scanning
    would (``PDK.from_dict`` validation — bad role names, shadowed built-ins
    and malformed sections are rejected here, not discovered later), then
    reports capability coverage using the same gates ``blocks/generic.py``
    applies when registering ``gen_*`` blocks. Exit 0 = loadable,
    2 = rejected.
    """
    import json
    from pathlib import Path

    from layout_canvas.blocks import generic as _gen
    from layout_canvas.pdk.descriptor import EXTRACTION_ROLES, PDK

    file = Path(path)
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"pdk check {file}: invalid JSON — {exc}", file=sys.stderr)
        return 2
    try:
        pdk = PDK.from_dict(doc, base_dir=file.parent, source=str(file))
    except Exception as exc:
        print(f"pdk check {file}: descriptor rejected — {exc}",
              file=sys.stderr)
        return 2

    roles = (pdk.extract or {}).get("roles") or {}
    covered = [r for r in EXTRACTION_ROLES if roles.get(r)]
    leaf = (pdk.extract or {}).get("leaf_devices") or {}
    polarities = sorted({pol for _, pol in leaf.values()})

    blocks = []
    mos_ok = all(roles.get(r) for r in _gen._MOS_ROLES) and polarities
    if mos_ok:
        blocks.append(f"{pdk.name}.gen_diff_pair")
        if "pmos" in polarities or _gen._can_ptap(pdk):
            blocks.append(f"{pdk.name}.gen_current_mirror")
    if all(roles.get(r) for r in _gen._GUARD_RING_ROLES):
        blocks.append(f"{pdk.name}.gen_guard_ring")

    warnings = []
    if mos_ok and not _gen._can_ptap(pdk):
        warnings.append("no tap/psdm bulk path — blocks omit substrate rings")
    if mos_ok and "pmos" in polarities and not _gen._can_pmos(pdk):
        warnings.append("pmos leaf present but no well_n/ntap path — "
                        "pmos variants will be refused")
    if not roles:
        warnings.append("no extract.roles — PDK loads but unlocks nothing "
                        "(no gen_* blocks, no extraction)")

    print(json.dumps({
        "file": str(file),
        "pdk": pdk.name,
        "status": "ok",
        "layers": len(pdk.layers),
        "pin_purpose": pdk.pin_purpose,
        "extract_roles": {"covered": covered,
                          "missing": [r for r in EXTRACTION_ROLES
                                      if not roles.get(r)]},
        "leaf_devices": sorted(leaf),
        "drc_rules": len((pdk.drc or {}).get("rules", {})),
        "drc_enclosures": len((pdk.drc or {}).get("enclosure", [])),
        "model_libs": sorted(pdk.model_libs or {}),
        "oa_layers": len(pdk.oa_layers or {}),
        "gen_blocks": blocks,
        "warnings": warnings,
    }, indent=2))
    if warnings:
        for w in warnings:
            print(f"  warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
