"""Golden-path smoke test for the real IR -> artifacts -> verification contract."""

from __future__ import annotations

import json
from pathlib import Path

from layout_canvas.compiler.compile import compile_design
from layout_canvas.compiler.netlist import export_spice
from layout_canvas.tools.drc import run_klayout_drc
from layout_canvas.tools.lvs import run_lvs
from layout_canvas.ir.model import Design


def test_example_generates_artifacts_and_fail_closed_verification(tmp_path: Path) -> None:
    """The example must compile; unavailable external EDA tools are explicit."""
    import layout_canvas.blocks.sky130  # noqa: F401

    example = Path(__file__).parents[1] / "examples" / "diffamp.json"
    design = Design.model_validate(json.loads(example.read_text(encoding="utf-8")))
    component = compile_design(design)
    gds_path = tmp_path / "diffamp.gds"
    spice_path = tmp_path / "diffamp.sp"
    component.write_gds(gds_path)
    export_spice(design, spice_path)

    assert gds_path.is_file() and gds_path.stat().st_size > 0
    assert spice_path.is_file() and ".subckt simple_diffamp" in spice_path.read_text(encoding="utf-8")

    drc = run_klayout_drc(gds_path)
    lvs = run_lvs(layout_path=gds_path, schematic_path=spice_path)
    assert drc.status in {"passed", "failed", "unavailable", "error"}
    assert lvs.status in {"passed", "failed", "unavailable", "error"}
    if drc.status != "passed":
        assert drc.clean is not True
    if lvs.status != "passed":
        assert lvs.match is not True
