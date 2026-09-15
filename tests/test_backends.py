"""Tests for the pluggable EDA tool adapter registry."""

from layout_canvas.tools import backends
from layout_canvas.tools.sim import _select_backend


def test_registry_covers_domain_tools():
    names = {a.name for a in backends.adapters()}
    assert {"ngspice", "xyce", "ltspice", "spectre", "hspice", "eldo"} <= names
    assert {"klayout", "netgen", "magic", "calibre"} <= names


def test_probe_shape_and_graceful_missing():
    report = backends.get_adapter("ngspice").probe()
    assert report["kind"] == "spice_simulator"
    assert report["status"] in ("available", "unavailable")
    if report["status"] == "unavailable":
        assert report["binary"] is None
        assert "PATH" in report["detail"]


def test_probe_environment_report():
    env = backends.probe_environment()
    assert "tools" in env and len(env["tools"]) == len(backends.adapters())
    assert "available" in env["simulators"]
    assert "detected_needs_license" in env["simulators"]


def test_unknown_tool_named():
    try:
        backends.get_adapter("femtorfast")
    except KeyError as exc:
        assert "femtorfast" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_select_named_missing_simulator():
    adapter, binary, early = _select_backend("spectre", None)
    if adapter is None:
        assert early is not None and early.status == "unavailable"
        assert "spectre" in early.errors[0]
    else:
        assert adapter.name == "spectre" and binary


def test_select_non_simulator_refused():
    _, _, early = _select_backend("klayout", None)
    assert early is not None
    assert early.status == "unavailable"
    assert "not a runnable simulator" in early.errors[0]


def test_explicit_missing_executable_unavailable():
    _, _, early = _select_backend("auto", "no-such-sim-xyz")
    assert early is not None and early.status == "unavailable"
