"""Typed edits applied atomically to a Block IR Design.

Each edit is a dict with an ``op`` field. ``apply_edits`` deep-copies the
design, applies every edit in order, then re-validates the whole document
through the pydantic model — a rejected transaction never leaves a
half-mutated design behind.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from layout_canvas.ir.model import Constraint, Design, Instance, Net, Placement, Port

from .envelope import Diagnostic

OPS = (
    "set_placement",
    "set_params",
    "add_instance",
    "remove_instance",
    "add_net",
    "remove_net",
    "set_net_pins",
    "add_port",
    "remove_port",
    "add_constraint",
    "remove_constraint",
    "set_meta",
)


def apply_edits(design: Design, edits: list[dict[str, Any]]) -> tuple[Design | None, list[Diagnostic]]:
    """Apply ``edits`` to a copy of ``design``. Returns (new_design, []) on
    success or (None, diagnostics) on the first failing edit."""
    candidate = design.model_copy(deep=True)
    for index, edit in enumerate(edits):
        diags = _apply_one(candidate, edit)
        if diags:
            for d in diags:
                d.message = f"edit[{index}]: {d.message}"
            return None, diags
    try:
        Design.model_validate(candidate.model_dump())
    except ValidationError as exc:
        return None, [
            Diagnostic("error", "invalid-design", f"resulting design fails validation: {exc}")
        ]
    return candidate, []


def _apply_one(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    op = edit.get("op")
    if op not in OPS:
        return [Diagnostic("error", "unknown-op", f"unknown op {op!r}; known: {list(OPS)}")]
    handler = _HANDLERS[op]
    try:
        return handler(design, edit)
    except Exception as exc:  # defensive: malformed payloads become diagnostics
        return [Diagnostic("error", "edit-failed", str(exc))]


def _get_instance(design: Design, edit: dict[str, Any]) -> Instance | Diagnostic:
    inst_id = edit.get("instance")
    if not isinstance(inst_id, str):
        return Diagnostic("error", "bad-edit", "'instance' must be a string id")
    for inst in design.instances:
        if inst.id == inst_id:
            return inst
    return Diagnostic("error", "unknown-instance", f"no instance {inst_id!r}", inst_id)


def _set_placement(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    target = _get_instance(design, edit)
    if isinstance(target, Diagnostic):
        return [target]
    fields = {k: v for k, v in edit.items() if k in Placement.model_fields}
    if not fields:
        return [Diagnostic("error", "bad-edit", "set_placement carries no placement fields", target.id)]
    try:
        merged = {**target.placement.model_dump(), **fields}
        target.placement = Placement.model_validate(merged)
    except ValidationError as exc:
        return [Diagnostic("error", "invalid-placement", str(exc), target.id)]
    return []


def _set_params(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    target = _get_instance(design, edit)
    if isinstance(target, Diagnostic):
        return [target]
    params = edit.get("params")
    if not isinstance(params, dict):
        return [Diagnostic("error", "bad-edit", "'params' must be an object", target.id)]
    # Range/unknown-param checks run at compile time via resolve_params; the
    # session stays agnostic of block schemas so any registered block works.
    target.params.update(params)
    return []


def _add_instance(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    payload = edit.get("instance")
    if not isinstance(payload, dict):
        return [Diagnostic("error", "bad-edit", "add_instance needs an 'instance' object")]
    try:
        inst = Instance.model_validate(payload)
    except ValidationError as exc:
        return [Diagnostic("error", "invalid-instance", str(exc))]
    if any(i.id == inst.id for i in design.instances):
        return [Diagnostic("error", "duplicate-instance", f"instance {inst.id!r} already exists", inst.id)]
    design.instances.append(inst)
    return []


def _remove_instance(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    target = _get_instance(design, edit)
    if isinstance(target, Diagnostic):
        return [target]
    inst_id = target.id
    referrers: list[str] = []
    for net in design.nets:
        if any(p.partition(".")[0] == inst_id for p in net.pins):
            referrers.append(f"net {net.name!r}")
    for port in design.ports:
        if port.pin.partition(".")[0] == inst_id:
            referrers.append(f"port {port.name!r}")
    for c in design.constraints:
        if inst_id in c.instances:
            referrers.append(f"constraint {c.type}")
    if referrers:
        return [
            Diagnostic(
                "error",
                "instance-referenced",
                f"instance {inst_id!r} is referenced by {', '.join(referrers)}; "
                "remove the references first",
                inst_id,
            )
        ]
    design.instances.remove(target)
    return []


def _find_net(design: Design, edit: dict[str, Any]) -> Net | Diagnostic:
    name = edit.get("net")
    if not isinstance(name, str):
        return Diagnostic("error", "bad-edit", "'net' must be a string name")
    for net in design.nets:
        if net.name == name:
            return net
    return Diagnostic("error", "unknown-net", f"no net {name!r}", name)


def _add_net(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    payload = edit.get("net")
    if not isinstance(payload, dict):
        return [Diagnostic("error", "bad-edit", "add_net needs a 'net' object {name, pins}")]
    try:
        net = Net.model_validate(payload)
    except ValidationError as exc:
        return [Diagnostic("error", "invalid-net", str(exc))]
    if any(n.name == net.name for n in design.nets):
        return [Diagnostic("error", "duplicate-net", f"net {net.name!r} already exists", net.name)]
    design.nets.append(net)
    return []


def _remove_net(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    target = _find_net(design, edit)
    if isinstance(target, Diagnostic):
        return [target]
    design.nets.remove(target)
    return []


def _set_net_pins(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    target = _find_net(design, edit)
    if isinstance(target, Diagnostic):
        return [target]
    pins = edit.get("pins")
    if not isinstance(pins, list) or not all(isinstance(p, str) for p in pins) or not pins:
        return [Diagnostic("error", "bad-edit", "'pins' must be a non-empty string list", target.name)]
    target.pins = list(pins)
    return []


def _add_port(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    payload = edit.get("port")
    if not isinstance(payload, dict):
        return [Diagnostic("error", "bad-edit", "add_port needs a 'port' object")]
    try:
        port = Port.model_validate(payload)
    except ValidationError as exc:
        return [Diagnostic("error", "invalid-port", str(exc))]
    if any(p.name == port.name for p in design.ports):
        return [Diagnostic("error", "duplicate-port", f"port {port.name!r} already exists", port.name)]
    design.ports.append(port)
    return []


def _remove_port(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    name = edit.get("port")
    if not isinstance(name, str):
        return [Diagnostic("error", "bad-edit", "'port' must be a string name")]
    for port in design.ports:
        if port.name == name:
            design.ports.remove(port)
            return []
    return [Diagnostic("error", "unknown-port", f"no port {name!r}", name)]


def _add_constraint(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    payload = edit.get("constraint")
    if not isinstance(payload, dict):
        return [Diagnostic("error", "bad-edit", "add_constraint needs a 'constraint' object")]
    try:
        design.constraints.append(Constraint.model_validate(payload))
    except ValidationError as exc:
        return [Diagnostic("error", "invalid-constraint", str(exc))]
    return []


def _remove_constraint(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    index = edit.get("index")
    if not isinstance(index, int) or not (0 <= index < len(design.constraints)):
        return [
            Diagnostic(
                "error",
                "bad-edit",
                f"'index' must be an integer in [0, {len(design.constraints)})",
            )
        ]
    design.constraints.pop(index)
    return []


def _set_meta(design: Design, edit: dict[str, Any]) -> list[Diagnostic]:
    key = edit.get("key")
    if not isinstance(key, str) or not key:
        return [Diagnostic("error", "bad-edit", "'key' must be a non-empty string")]
    design.meta[key] = edit.get("value")
    return []


_HANDLERS = {
    "set_placement": _set_placement,
    "set_params": _set_params,
    "add_instance": _add_instance,
    "remove_instance": _remove_instance,
    "add_net": _add_net,
    "remove_net": _remove_net,
    "set_net_pins": _set_net_pins,
    "add_port": _add_port,
    "remove_port": _remove_port,
    "add_constraint": _add_constraint,
    "remove_constraint": _remove_constraint,
    "set_meta": _set_meta,
}
