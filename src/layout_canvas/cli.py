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

    # testbench command
    tb = sub.add_parser("testbench", help="Run design testbenches and report spec results")
    tb.add_argument("input", help="Block IR JSON file")
    tb.add_argument("--name", help="Run a single named testbench (default: all)")

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
        import layout_canvas.blocks.sky130  # noqa: F401

        if args.format == "gds":
            export_gds(design, args.output)
        else:
            export_oas(design, args.output)

        print(f"Compiled {design.name} → {args.output}", file=sys.stderr)
        return 0

    if args.cmd == "netlist":
        with open(args.input) as f:
            design = Design.from_json(f.read())

        import layout_canvas.blocks.sky130  # noqa: F401
        export_spice(design, args.output)
        print(f"Generated SPICE netlist {design.name} → {args.output}", file=sys.stderr)
        return 0

    if args.cmd == "testbench":
        with open(args.input) as f:
            design = Design.from_json(f.read())
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

    if args.cmd == "mcp":
        from layout_canvas.mcp.server import run_stdio_server
        run_stdio_server()
        return 0

    if args.cmd == "web":
        from layout_canvas.web import run
        run(host=args.host, port=args.port)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
