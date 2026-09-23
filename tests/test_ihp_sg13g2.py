"""IHP SG13G2 block package + real PSP-model simulation smoke."""

import os
import re
import shutil
from pathlib import Path

import pytest

import layout_canvas.blocks.ihp_sg13g2  # noqa: F401 — registers the blocks
from layout_canvas.blocks import base
from layout_canvas.compiler.netlist import compile_netlist
from layout_canvas.ir.model import Design
from layout_canvas.tools.sim import simulate_design

IHP_BLOCKS = [
    "ihp_sg13g2.diff_pair",
    "ihp_sg13g2.current_mirror",
    "ihp_sg13g2.guard_ring",
    "ihp_sg13g2.ota_5t",
]


def _design(block_name: str, params: dict | None = None) -> Design:
    return Design.model_validate(
        {
            "name": "ihp_test",
            "pdk": "ihp_sg13g2",
            "instances": [{"id": "x1", "block": block_name, "params": params or {}}],
        }
    )


@pytest.mark.parametrize("name", IHP_BLOCKS)
def test_ihp_blocks_registered(name):
    assert base.get(name).spec.pdk == "ihp_sg13g2"


@pytest.mark.parametrize("name", IHP_BLOCKS)
def test_ihp_blocks_build_component(name):
    comp = base.get(name).component()
    assert comp is not None
    assert len(comp.ports) > 0


def test_diff_pair_netlist_uses_finger_level_devices():
    text = compile_netlist(_design("ihp_sg13g2.diff_pair"))
    # Finger-level reference matching physical extraction: one M card per
    # finger, S/D per the ABBA segment ownership, bulk on the shared vss.
    assert re.search(r"^M\d+ tail inp outp vss sg13_lv_nmos ", text, re.M)
    assert re.search(r"^M\d+ outn inn tail vss sg13_lv_nmos ", text, re.M)
    assert "l=0.34u" in text


def test_ihp_pin_labels_use_datatype_2():
    """LVS extraction on SG13G2 reads labels on (drawing layer, 2)."""
    comp = base.get("ihp_sg13g2.diff_pair").component()
    assert (10, 2) in comp.layers  # metal2 pin-label layer


def test_ihp_pdk_descriptor():
    from layout_canvas.pdk.descriptor import get_pdk

    pdk = get_pdk("ihp_sg13g2")
    assert pdk.pin_label_layer((8, 0)) == (8, 2)
    assert pdk.layer("gatpoly") == (5, 0)


# --- Real foundry-model smoke (ngspice + IHP open PDK) --------------------

_MODELS_DIR = Path(
    os.environ.get(
        "LAYOUT_CANVAS_IHP_MODELS",
        r"E:\Agentic TCAD\PDK\official_sources\IHP-Open-PDK\ihp-sg13g2\libs.tech\ngspice\models",
    )
)
_OSDI_DIRS = [
    Path(os.environ["LAYOUT_CANVAS_OSDI_DIR"]),
] if os.environ.get("LAYOUT_CANVAS_OSDI_DIR") else [
    Path(r"E:\Reliability-PINN-Lab\.tmp\ngspice47\Spice64\lib\ngspice"),
]


def _ngspice() -> str | None:
    return os.environ.get("LAYOUT_CANVAS_NGSPICE") or shutil.which("ngspice")


def _ihp_ready() -> bool:
    return (
        _ngspice() is not None
        and (_MODELS_DIR / "cornerMOSlv.lib").is_file()
        and any((d / "psp103.osdi").is_file() for d in _OSDI_DIRS)
    )


@pytest.mark.skipif(not _ihp_ready(), reason="ngspice or IHP SG13G2 ngspice models not present")
def test_real_ihp_psp_op_smoke(tmp_path):
    """Real PSP103.6 models + real ngspice: DC op of a biased diff pair."""
    design = _design("ihp_sg13g2.diff_pair")
    variant = re.search(r"\.subckt (ihp_sg13g2_diff_pair__\w+)", compile_netlist(design)).group(1)
    models = _MODELS_DIR.as_posix()
    osdi = next(d for d in _OSDI_DIRS if (d / "psp103.osdi").is_file()).as_posix()
    stimulus = f"""
.control
pre_osdi {osdi}/psp103.osdi
pre_osdi {osdi}/psp103_nqs.osdi
.endc
.lib "{models}/cornerMOSlv.lib" mos_tt
.param pre_layout=1
VDD vdd 0 1.2
VSS vss 0 0
VINP inp 0 0.65
VINN inn 0 0.6
IT tail vss 10u
RDP vdd outp 10k
RDN vdd outn 10k
X1 inp inn outp outn tail {variant}
.op
"""
    result = simulate_design(
        design, stimulus, executable=_ngspice(), workdir=tmp_path
    )
    assert result.status == "passed", result.errors
    assert result.log_path and result.log_path.is_file()
