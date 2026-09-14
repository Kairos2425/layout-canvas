"""Pure read-only connectivity projection over a Design.

Modeled on Analog Canvas's ``@icm/derived`` and its workflow rule "read
electrical facts before drawing": an agent must be able to ask which pins
are connected, which are dangling, and which nets are degenerate *before*
submitting edits — instead of discovering shorts after compilation.
"""

from __future__ import annotations

from typing import Any

from layout_canvas.blocks import base
from layout_canvas.ir.model import Design


def inspect_connectivity(design: Design) -> dict[str, Any]:
    """Return pin→net resolution, unconnected pins, and net diagnostics."""
    pin_net: dict[str, str] = {}
    diagnostics: list[dict[str, Any]] = []

    for net in design.nets:
        if len(net.pins) < 2:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "degenerate-net",
                    "message": f"net {net.name!r} connects {len(net.pins)} pin(s)",
                    "object_id": net.name,
                }
            )
        for pin in net.pins:
            if pin in pin_net:
                diagnostics.append(
                    {
                        "severity": "error",
                        "code": "pin-multi-driven",
                        "message": (
                            f"pin {pin!r} appears on both net "
                            f"{pin_net[pin]!r} and {net.name!r}"
                        ),
                        "object_id": pin,
                    }
                )
            else:
                pin_net[pin] = net.name

    port_pin = {port.pin for port in design.ports}

    instances: dict[str, Any] = {}
    for inst in design.instances:
        declared: list[str] | None = None
        try:
            declared = [p.name for p in base.get(inst.block).spec.ports]
        except KeyError:
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "unknown-block",
                    "message": f"block {inst.block!r} is not registered; "
                    "cannot enumerate its pins",
                    "object_id": inst.id,
                }
            )
        pins: dict[str, Any] = {}
        pin_names = declared if declared is not None else _referenced_pins(inst.id, design)
        for name in pin_names:
            ref = f"{inst.id}.{name}"
            pins[name] = {
                "net": pin_net.get(ref),
                "is_port": ref in port_pin,
            }
        unconnected = [n for n, info in pins.items() if info["net"] is None]
        instances[inst.id] = {
            "block": inst.block,
            "pins": pins,
            "unconnected": sorted(unconnected),
        }

    return {
        "nets": {net.name: {"pins": list(net.pins)} for net in design.nets},
        "instances": instances,
        "ports": [{"name": p.name, "pin": p.pin, "direction": p.direction} for p in design.ports],
        "diagnostics": diagnostics,
    }


def _referenced_pins(inst_id: str, design: Design) -> list[str]:
    """Pins of an unregistered instance that nets/ports still reference."""
    names: set[str] = set()
    for net in design.nets:
        for pin in net.pins:
            inst, _, port = pin.partition(".")
            if inst == inst_id and port:
                names.add(port)
    for port in design.ports:
        inst, _, pname = port.pin.partition(".")
        if inst == inst_id and pname:
            names.add(pname)
    return sorted(names)
