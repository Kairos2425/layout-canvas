from pathlib import Path

from layout_canvas.tools.drc import _count_violations, run_klayout_drc
from layout_canvas.tools.lvs import _check_match, run_lvs


def test_drc_missing_gds_is_unavailable(tmp_path: Path):
    result = run_klayout_drc(tmp_path / "missing.gds", tmp_path / "deck.drc")
    assert result.status == "unavailable"
    assert result.clean is None


def test_drc_report_parser_is_not_optimistic():
    assert _count_violations("Total violations: 0") == 0
    assert _count_violations("Total violations: 3") == 3
    assert _count_violations("3 violations") == 3
    assert _count_violations("tool crashed") is None


def test_lvs_missing_inputs_is_unavailable(tmp_path: Path):
    result = run_lvs(layout_path=tmp_path / "missing.gds", schematic_path=tmp_path / "missing.sp")
    assert result.status == "unavailable"
    assert result.match is None


def test_lvs_report_parser_requires_explicit_result():
    assert _check_match("Circuits match uniquely") is True
    assert _check_match("net mismatch") is False
    assert _check_match("Netgen finished") is None
