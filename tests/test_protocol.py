"""Tests for the .lcproj.json load/migrate/save boundary."""

import json

from layout_canvas.ir.model import Design
from layout_canvas.protocol import (
    PROJECT_SCHEMA_VERSION,
    load_project,
    save_project,
    serialize_project,
)


def _design() -> Design:
    return Design.model_validate(
        {
            "name": "proto_test",
            "pdk": "sky130",
            "instances": [{"id": "m1", "block": "sky130.diff_pair", "params": {}}],
        }
    )


def test_roundtrip(tmp_path):
    p = save_project(_design(), tmp_path / "cell.lcproj.json")
    result = load_project(p)
    assert result.ok and result.status == "ok"
    assert result.source_version == PROJECT_SCHEMA_VERSION
    assert result.design.name == "proto_test"


def test_bare_ir_migrates():
    bare = _design().model_dump()
    result = load_project(json.dumps(bare))
    assert result.ok and result.status == "migrated"
    assert result.source_version == 0
    assert any(d.code == "migrated" for d in result.diagnostics)


def test_newer_version_refused():
    payload = {"kind": "lcproj", "schema_version": PROJECT_SCHEMA_VERSION + 1, "design": {}}
    result = load_project(json.dumps(payload))
    assert not result.ok and result.status == "error"
    assert result.diagnostics[0].code == "unsupported-version"


def test_invalid_json_is_error_not_exception():
    result = load_project("{not json")
    assert not result.ok
    assert result.diagnostics[0].code == "invalid-json"


def test_missing_file_is_error_not_exception(tmp_path):
    result = load_project(tmp_path / "nope.lcproj.json")
    assert not result.ok
    assert result.diagnostics[0].code == "io-error"


def test_serialize_is_valid_versioned():
    payload = json.loads(serialize_project(_design()))
    assert payload["kind"] == "lcproj"
    assert payload["schema_version"] == PROJECT_SCHEMA_VERSION
    assert payload["design"]["name"] == "proto_test"
