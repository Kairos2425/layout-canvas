"""Block IR v0.

A design is a graph of *block instances* (parametric generators bound to a PDK),
their *nets*, *constraints* and exported *ports*. GDS/OASIS is an export
product of compiling this IR, not the source of truth.

The IR deliberately stays independent of gdsfactory/KLayout types so it can be
serialized as JSON, diffed, forked and compiled by other backends later.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IR_VERSION = "0.1"

ParamValue = int | float | str | bool

PinRef = str  # "<instance_id>.<port_name>"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Placement(_Strict):
    x: float = 0.0
    y: float = 0.0
    rotation: Literal[0, 90, 180, 270] = 0
    mirror: bool = False


class Instance(_Strict):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    block: str = Field(description="Registered block name, e.g. 'sky130.current_mirror'")
    params: dict[str, ParamValue] = Field(default_factory=dict)
    placement: Placement = Field(default_factory=Placement)


class Net(_Strict):
    name: str
    pins: list[PinRef] = Field(min_length=1)


class ConstraintType(str, Enum):
    symmetric = "symmetric"
    align = "align"
    abut = "abut"
    match = "match"
    order = "order"


class Constraint(_Strict):
    type: ConstraintType
    instances: list[str] = Field(min_length=1)
    axis: Literal["vertical", "horizontal"] | None = None
    edge: Literal["left", "right", "top", "bottom", "center_x", "center_y"] | None = None
    direction: Literal["left_to_right", "bottom_to_top"] | None = None
    note: str | None = None


class Port(_Strict):
    name: str
    pin: PinRef
    direction: Literal["input", "output", "inout", "supply"] = "inout"


class PortSpec(_Strict):
    """Static port contract exposed by a block."""

    name: str
    layer: str
    direction: Literal["input", "output", "inout", "supply"] = "inout"


class ParamSpec(_Strict):
    name: str
    type: Literal["int", "float", "str", "bool"]
    default: ParamValue
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    choices: list[ParamValue] | None = None
    description: str = ""


class BlockSpec(_Strict):
    """Metadata describing a registered block (what the canvas shows in its palette)."""

    name: str
    pdk: str
    level: Literal["L0", "L1", "L2", "L3"]
    summary: str
    params: list[ParamSpec]
    ports: list[PortSpec]
    constraints: list[str] = Field(
        default_factory=list, description="Constraints the generator enforces internally"
    )
    tags: list[str] = Field(default_factory=list)
    version: str = "0.1"


class Design(_Strict):
    ir_version: str = IR_VERSION
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    pdk: str
    instances: list[Instance]
    nets: list[Net] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    ports: list[Port] = Field(default_factory=list)
    meta: dict[str, ParamValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_refs(self) -> Design:
        ids = {i.id for i in self.instances}
        if len(ids) != len(self.instances):
            raise ValueError("duplicate instance ids")

        def check_pin(pin: str, where: str) -> None:
            inst, _, port = pin.partition(".")
            if not port or inst not in ids:
                raise ValueError(f"{where}: bad pin reference {pin!r}")

        for n in self.nets:
            for p in n.pins:
                check_pin(p, f"net {n.name}")
        for p in self.ports:
            check_pin(p.pin, f"port {p.name}")
        for c in self.constraints:
            for i in c.instances:
                if i not in ids:
                    raise ValueError(f"constraint {c.type}: unknown instance {i!r}")
        return self

    def instance(self, inst_id: str) -> Instance:
        for i in self.instances:
            if i.id == inst_id:
                return i
        raise KeyError(inst_id)

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent, exclude_defaults=False)

    @classmethod
    def from_json(cls, text: str) -> Design:
        return cls.model_validate_json(text)
