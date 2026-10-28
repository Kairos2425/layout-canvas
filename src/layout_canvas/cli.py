"""CLI entry point."""

import argparse
import sys

from layout_canvas.compiler.compile import export_gds, export_oas
from layout_canvas.compiler.netlist import export_spice
from layout_canvas.ir import json_schema
from layout_canvas.ir.model import Design


def main() -> int:
    # Console-safe output: results contain µm/— which legacy code pages
    # (cp936 GBK) cannot encode — degrade to replacement chars instead of
    # crashing mid-report (observed: `layout-canvas verify` died printing
    # a µm deviation on a GBK console).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
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

    # verify command
    ver = sub.add_parser(
        "verify",
        help="One-shot verification: extract + DRC + LVS + declared "
             "constraint quality (symmetric/match/order/align/abut)")
    ver.add_argument("input", help="Block IR JSON file")
    ver.add_argument("--json", action="store_true",
                     help="print the full result as JSON")

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
        "action", choices=["list", "dump", "check", "init"],
        help="'list' shows every registered PDK; 'dump NAME' prints a "
             "*.pdk.json template for NAME (built-ins included — a "
             "starting point for commercial PDK descriptors); "
             "'check FILE' validates a descriptor offline and reports the "
             "capabilities it would unlock; 'init NAME --extends BASE' "
             "writes a minimal descriptor that inherits BASE's layer "
             "map, extraction recipe and DRC so only the fields that "
             "differ need filling in")
    pdkp.add_argument("name", nargs="?", help="PDK name for 'dump'/'init', "
                        "*.pdk.json path for 'check'")
    pdkp.add_argument("--extends", default="sky130",
                      help="base PDK 'init' inherits from (default sky130)")
    pdkp.add_argument("--layermap", default=None,
                      help="Cadence stream layermap file to ingest into "
                           "layers + oa_layers (the authoritative "
                           "OA-name↔GDS table shipped with real PDKs)")
    pdkp.add_argument("-o", "--output", default=None,
                      help="descriptor path for 'init' (default stdout)")

    # testbench command
    tb = sub.add_parser("testbench", help="Run design testbenches and report spec results")
    tb.add_argument("input", help="Block IR JSON file")
    tb.add_argument("--name", help="Run a single named testbench (default: all)")

    # candidates command
    cand = sub.add_parser(
        "candidates",
        help="Evaluate K design variants side-by-side: each variant's "
             "typed edits are applied to a fresh copy through the transact "
             "boundary, then compiled, PPA-measured, verified "
             "(extract/DRC/LVS/quality) and — unless --no-sim — simulated "
             "on the schematic testbenches. One candidate's failure never "
             "aborts the run")
    cand.add_argument("input", help="Block IR JSON file")
    cand.add_argument("--variants", required=True,
                      help="JSON file: [{name, edits: [edit ops]}]")
    cand.add_argument("--no-sim", action="store_true",
                      help="skip testbench spec evaluation — schematic "
                           "benches report unavailable, not measured")
    cand.add_argument("--json", action="store_true",
                      help="print the full report as JSON")

    # verilog command
    vlg = sub.add_parser(
        "verilog",
        help="Emit a blackbox Verilog stub for a design; --lint compiles "
             "it with iverilog + vvp (fail-closed: a missing tool reports "
             "unavailable and exits 0, like reliability)")
    vlg.add_argument("input", help="Block IR JSON file")
    vlg.add_argument("--lint", action="store_true",
                     help="run the iverilog lint instead of printing the stub")
    vlg.add_argument("--module", default=None,
                     help="override the module name (default: design name)")
    vlg.add_argument("--executable", default=None,
                     help="iverilog binary/command override (default: "
                          "LAYOUT_CANVAS_IVERILOG, then PATH, then wsl)")

    # reliability command
    rel = sub.add_parser(
        "reliability",
        help="JEDEC-aligned reliability coverage matrix (jesd-aligned-v1)")
    rel.add_argument("input", help="Block IR JSON file")
    rel.add_argument("--json", action="store_true",
                     help="print the full report as JSON")
    rel.add_argument("--no-sim", action="store_true",
                     help="structural/static items only — sim-backed "
                          "items report unavailable instead of running")

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

    # bench command
    bp = sub.add_parser(
        "bench",
        help="Run the frozen benchmark suite in bench/ (task scorecards, "
             "baseline regression gate — see bench/README.md)")
    bp.add_argument("--task", help="run a single task id")
    bp.add_argument("--check-baseline", action="store_true",
                    help="gate against tasks/*/results/reference.json; "
                         "exit 1 on any regression")
    bp.add_argument("--freeze", action="store_true",
                    help="rewrite tasks/*/results/reference.json from "
                         "this run (baseline freeze)")
    bp.add_argument("--out", help="scorecard output directory "
                    "(default bench/runs/<timestamp>/)")
    bp.add_argument("--bench-dir",
                    help="bench root (default: repo bench/ or "
                         "LAYOUT_CANVAS_BENCH)")

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
        if args.action == "init":
            if not args.name:
                print("pdk init needs a new PDK name", file=sys.stderr)
                return 2
            return _pdk_init(args.name, args.extends, args.output,
                             layermap=args.layermap)
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

    if args.cmd == "verify":
        import json

        with open(args.input) as f:
            design = Design.from_json(f.read())
        import layout_canvas.blocks.generic  # noqa: F401
        import layout_canvas.blocks.sky130  # noqa: F401
        try:
            import layout_canvas.blocks.ihp_sg13g2  # noqa: F401
        except Exception:
            pass

        from layout_canvas.tools.verify import verify_design

        res = verify_design(design)
        if args.json:
            print(json.dumps(res, indent=1))
            return 0 if res["passed"] else 1
        ext, drc, lvs, qual = (res["extract"], res["drc"],
                               res["lvs"], res["quality"])
        print(f"[verify] {res['design']} (pdk={res['pdk']})")
        print(f"  extract : {ext['status']} — {ext['devices']} devices, "
              f"{ext['nets']} nets"
              + (f", errors: {'; '.join(ext['errors'])}"
                 if ext["errors"] else ""))
        print(f"  drc     : {drc.get('status')} — "
              f"{drc.get('total_violations')} violations")
        unmatched = lvs.get("unmatched_nets") or []
        print(f"  lvs     : {lvs.get('status')} — match={lvs.get('match')}"
              + (f", unmatched nets: {', '.join(unmatched)}"
                 if unmatched else ""))
        print(f"  quality : {qual['status']}"
              + (f" — {qual.get('note')}" if qual.get("note") else ""))
        for chk in qual["checks"]:
            line = f"    {chk['id']}: {chk['status']}"
            if chk.get("note"):
                line += f" — {chk['note']}"
            print(line)
        print(f"  verdict : {'passed' if res['passed'] else 'FAILED'}")
        return 0 if res["passed"] else 1

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

    if args.cmd == "candidates":
        return _candidates(args)

    if args.cmd == "verilog":
        from layout_canvas.tools.verilog import verilog_check, verilog_stub

        with open(args.input) as f:
            design = Design.from_json(f.read())
        if not args.lint:
            print(verilog_stub(design, module_name=args.module), end="")
            return 0
        res = verilog_check(design, executable=args.executable)
        if res["status"] == "fail":
            print(f"verilog lint: fail — {res['reason']}", file=sys.stderr)
            if res.get("output"):
                print(res["output"], file=sys.stderr)
            return 1
        # pass and unavailable both exit 0 — unavailable prints its reason
        # (same convention as reliability: not verified, not a failure)
        print(f"verilog lint: {res['status']} — {res['reason']}")
        return 0

    if args.cmd == "reliability":
        import json

        with open(args.input) as f:
            design = Design.from_json(f.read())
        import layout_canvas.blocks.generic  # noqa: F401
        import layout_canvas.blocks.sky130  # noqa: F401
        try:
            import layout_canvas.blocks.ihp_sg13g2  # noqa: F401
        except Exception:
            pass

        from layout_canvas.tools.reliability import reliability_report

        rep = reliability_report(design, run_sims=not args.no_sim)
        if args.json:
            print(json.dumps(rep, indent=1))
        else:
            cov = rep["coverage"]
            print(f"[{rep['release']}] {rep['design']} — "
                  f"{cov['evaluated']}/{cov['total']} items evaluated")
            for it in rep["items"]:
                line = (f"  {it['id']:<15} {it['status']:<14} "
                        f"{it['jedec']:<24} via {it['evidence_level']}")
                if it.get("reason"):
                    line += f" — {it['reason']}"
                print(line)
        # exit 1 when any evaluated item fails — unavailable is a finding,
        # not a command failure
        return 1 if any(i["status"] == "fail" for i in rep["items"]) else 0

    if args.cmd == "bench":
        return _bench(args)

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


def _bench(args) -> int:
    """``bench`` — delegate to ``bench/run.py`` in the repository checkout.

    The bench is repo data, not package code: it is located by walking
    parents of this file for ``bench/benchmark.toml`` (editable install
    or source tree), then ``LAYOUT_CANVAS_BENCH`` and the cwd.
    """
    import importlib.util
    import os
    from pathlib import Path

    candidates: list[Path] = []
    if args.bench_dir:
        candidates.append(Path(args.bench_dir))
    env = os.environ.get("LAYOUT_CANVAS_BENCH")
    if env:
        candidates.append(Path(env))
    candidates.extend(base / "bench"
                      for base in Path(__file__).resolve().parents)
    candidates.append(Path.cwd() / "bench")
    bench_dir = next(
        (c for c in candidates if (c / "benchmark.toml").is_file()), None)
    if bench_dir is None:
        print("layout-canvas bench: no bench/benchmark.toml found "
              "(searched package parents, LAYOUT_CANVAS_BENCH, cwd)",
              file=sys.stderr)
        return 2
    run_py = bench_dir / "run.py"
    spec = importlib.util.spec_from_file_location("lc_bench_run", run_py)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses in run.py resolve their module through sys.modules —
    # register before exec_module or __module__ lookups return None.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    argv = []
    if args.task:
        argv += ["--task", args.task]
    if args.check_baseline:
        argv += ["--check-baseline"]
    if args.freeze:
        argv += ["--freeze"]
    if args.out:
        argv += ["--out", args.out]
    argv += ["--bench-dir", str(bench_dir)]
    return mod.main(argv)


def _candidates(args) -> int:
    """``candidates`` — load design + variants file, run the sweep, print
    the per-candidate table and the mechanical ranking."""
    import json
    from pathlib import Path

    from layout_canvas.tools.candidates import eval_candidates

    with open(args.input) as f:
        design = Design.from_json(f.read())
    import layout_canvas.blocks.generic  # noqa: F401
    import layout_canvas.blocks.sky130  # noqa: F401
    try:
        import layout_canvas.blocks.ihp_sg13g2  # noqa: F401
    except Exception:
        pass

    try:
        variants = json.loads(Path(args.variants).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"candidates: cannot read variants file "
              f"{args.variants!r} — {exc}", file=sys.stderr)
        return 2
    rep = eval_candidates(design, variants, run_sims=not args.no_sim)
    if args.json:
        print(json.dumps(rep, indent=1))
    else:
        n = len(rep["candidates"])
        print(f"[candidates] {rep['design']} · pdk={rep['pdk']} · "
              f"{n} variant(s) · run_sims={rep['run_sims']} · "
              f"status={rep['status']}")
        if rep.get("reason"):
            print(f"  {rep['reason']}")
        rows = []
        for c in rep["candidates"]:
            v = c.get("verify") or {}
            sp = c.get("specs") or {}
            area = (f"{c['area_um2']:.2f}" if c.get("area_um2") is not None
                    else "-")
            drc = (str(v["drc_violations"])
                   if v.get("drc_violations") is not None else "-")
            lvs_m = v.get("lvs_match")
            lvs = ("match" if lvs_m is True
                   else "MISMATCH" if lvs_m is False
                   else (v.get("lvs_status") or "-"))
            if sp.get("passed") is not None:
                spec = f"{sp['passed']}/{sp['total']} {sp['status']}"
            else:
                spec = sp.get("status") or "-"
            rows.append((c["name"], c["status"], area, drc, lvs, spec,
                         c.get("reason") or ""))
        if rows:
            head = ("name", "status", "area_um2", "drc", "lvs", "specs")
            widths = [max(len(str(r[i])) for r in [head, *rows])
                      for i in range(6)]
            print("  " + "  ".join(h.ljust(widths[i])
                                   for i, h in enumerate(head)))
            for r in rows:
                line = "  " + "  ".join(str(r[i]).ljust(widths[i])
                                        for i in range(6))
                if r[6]:
                    line += "  " + r[6]
                print(line)
        order = " > ".join(r["name"] for r in rep["ranking"])
        print(f"  ranking (mechanical: verify → specs → area): "
              f"{order or '-'}")
    # Exit 1 only when the run failed — i.e. every candidate failed.
    return 1 if rep["status"] == "fail" else 0


def _summarize_checks(checks: list[dict]) -> str:
    tally: dict[str, int] = {}
    for c in checks:
        tally[c["status"]] = tally.get(c["status"], 0) + 1
    return ", ".join(f"{v} {k}" for k, v in sorted(tally.items()))


def _pdk_template(name: str) -> dict:
    """JSON-able ``*.pdk.json`` template for ``pdk dump NAME``.

    External descriptors serialise their own sections; for built-ins the
    extract/DRC truth lives in the tool tables — merged into one
    standalone document by ``_full_descriptor_dict`` (shared with
    ``extends`` resolution).
    """
    from layout_canvas.pdk import get_pdk
    from layout_canvas.pdk.descriptor import _full_descriptor_dict

    get_pdk(name)  # raises KeyError naming the unknown PDK
    return _full_descriptor_dict(name)


def _pdk_init(name: str, extends: str, output: str | None,
              layermap: str | None = None) -> int:
    """Write a minimal ``extends``-style descriptor for ``pdk init``.

    The file inherits the base PDK's layer map, extraction recipe and
    DRC; the author only overrides what actually differs — layer
    numbers, leaf-device model names, OA layer names. With
    ``--layermap`` the Cadence stream table is ingested directly: real
    OA layer names land in ``layers``/``oa_layers`` instead of being
    hand-copied from PDK docs.
    """
    import json
    from pathlib import Path

    from layout_canvas.pdk import get_pdk

    try:
        base = get_pdk(extends)
    except KeyError:
        print(f"pdk init: base PDK {extends!r} unknown "
              f"(see 'layout-canvas pdk list')", file=sys.stderr)
        return 2
    layers: dict = {}
    oa_layers: dict = {}
    if layermap:
        from layout_canvas.pdk.layermap import parse_layermap_file

        try:
            lm = parse_layermap_file(layermap)
        except Exception as exc:
            print(f"pdk init: layermap {layermap!r} unreadable — {exc}",
                  file=sys.stderr)
            return 2
        layers = lm["layers"]
        oa_layers = lm["oa_layers"]
        print(f"layermap: {lm['entries']} OA layers ingested, "
              f"{len(lm['skipped'])} rows skipped", file=sys.stderr)
    doc = {
        "$doc": "inherits layers/extract/drc/oa_layers from "
                f"'{extends}' — override only what differs; validate "
                "with 'layout-canvas pdk check FILE'",
        "name": name,
        "extends": extends,
        "layers": layers,
        "extract": {
            "leaf_devices": {
                f"{name}_nch": [f"{name}_nch", "nmos"],
                f"{name}_pch": [f"{name}_pch", "pmos"],
            }
        },
        "oa_layers": oa_layers,
    }
    text = json.dumps(doc, indent=2) + "\n"
    if output:
        Path(output).write_text(text, encoding="utf-8")
        print(f"wrote {output} (inherits {base.name}: "
              f"{len(base.layers)} layers)", file=sys.stderr)
    else:
        print(text, end="")
    return 0


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
        "extends": doc.get("extends"),
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
