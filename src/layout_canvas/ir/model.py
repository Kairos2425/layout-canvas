"""Block IR v0.

A design is a graph of *block instances* (parametric generators bound to a PDK),
their *nets*, *constraints* and exported *ports*. GDS/OASIS is an export
product of compiling this IR, not the source of truth.

The IR deliberately stays independent of gdsfactory/KLayout types so it can be
serialized as JSON, diffed, forked and compiled by other backends later.
"""

from __future__ import annotations

from enum import StrEnum
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
    relative_to: str | None = Field(
        default=None, description="Target instance ID for relative placement"
    )
    relation: Literal["right_of", "left_of", "above", "below"] | None = Field(
        default=None, description="Spatial relation relative to target instance"
    )
    align: Literal["bottom", "top", "left", "right", "center_x", "center_y"] | None = Field(
        default=None, description="Alignment edge relative to target instance"
    )
    margin: float = Field(default=0.0, description="Clearance margin in um between instances")


class Instance(_Strict):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    block: str = Field(description="Registered block name, e.g. 'sky130.current_mirror'")
    params: dict[str, ParamValue] = Field(default_factory=dict)
    placement: Placement = Field(default_factory=Placement)


class Net(_Strict):
    name: str
    pins: list[PinRef] = Field(min_length=1)


class ConstraintType(StrEnum):
    symmetric = "symmetric"
    align = "align"
    abut = "abut"
    match = "match"
    order = "order"


class Constraint(_Strict):
    type: ConstraintType
    instances: list[str] = Field(min_length=1)
    # Optional explicit net pair for routing constraints.  ``instances``
    # remains the placement/matching scope; keeping the two concepts
    # separate prevents the router from guessing net names from instance IDs.
    nets: list[str] | None = Field(default=None, min_length=2)
    axis: Literal["vertical", "horizontal"] | None = None
    edge: Literal["left", "right", "top", "bottom", "center_x", "center_y"] | None = None
    direction: Literal["left_to_right", "bottom_to_top"] | None = None
    note: str | None = None


class Port(_Strict):
    name: str
    pin: PinRef
    direction: Literal["input", "output", "inout", "supply"] = "inout"


class Spec(_Strict):
    """One measurable criterion against a simulation waveform."""

    name: str
    signal: str
    measure: Literal["final", "min", "max", "mean", "pp", "db", "bw_3db",
                     "settling", "slew", "overshoot"] = "final"
    min: float | None = None
    max: float | None = None
    unit: str = "V"
    # Fractional band for the transient measures (settling); None = 0.02
    # (±2 % of the final value).
    tol: float | None = None
    # Which run the spec is checked on. "nominal" (default) reduces the
    # signal on the nominal run. "stress" reduces the same signal/measure
    # on the testbench's declared stress corner (Testbench.stress). "drift"
    # reduces it on both runs and checks |stress - nominal|. A "stress" or
    # "drift" spec on a testbench with no declared corner evaluates
    # ``unavailable`` — never a nominal-only stand-in verdict.
    on: Literal["nominal", "stress", "drift"] = "nominal"

    @model_validator(mode="after")
    def _check_bounds(self) -> Spec:
        if self.min is None and self.max is None:
            raise ValueError(f"spec {self.name}: needs min and/or max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"spec {self.name}: min {self.min} > max {self.max}")
        if self.tol is not None and self.tol <= 0:
            raise ValueError(f"spec {self.name}: tol {self.tol} must be > 0")
        return self


class DigitalSignal(_Strict):
    """One Verilog net converted into a SPICE PWL drive on one node."""

    verilog_net: str = Field(
        min_length=1,
        description="Wire/reg name inside the rtl to dump — a plain net "
                    "name or a select like 'count[0]'; may also be a "
                    "hierarchical path under the rtl top (e.g. 'sub.q')",
    )
    node: str = Field(
        min_length=1,
        description="SPICE node the converted PWL source drives",
    )
    vhi: float = 1.8
    vlo: float = 0.0
    # Constant offset added to every transition time, in seconds.
    delay: float = 0.0


class DigitalStimulus(_Strict):
    """A Verilog control block whose dumped nets drive SPICE nodes.

    ``rtl`` must contain exactly one top-level (uninstantiated) module —
    that module is instantiated unconnected by a generated dump bench, so
    it must be **self-contained**: it provides its own clock/initial
    blocks and ends the run with ``$finish`` (a watchdog bounds runaway
    simulations). The wrapper adds no stimulus of its own. Digital
    stimulus only applies to ``analysis="tran"`` testbenches.
    """

    rtl: str = Field(min_length=1, description="Verilog source text")
    signals: list[DigitalSignal] = Field(min_length=1)
    timescale: str = Field(
        default="1ns/1ps",
        pattern=r"^\s*\d+(\.\d+)?\s*(fs|ps|ns|us|ms|s)\s*/"
                r"\s*\d+(\.\d+)?\s*(fs|ps|ns|us|ms|s)\s*$",
        description="'<unit>/<precision>' applied to the written rtl "
                    "file and the generated dump bench — dump timestamps "
                    "come out in <unit> and convert to seconds",
    )


class Testbench(_Strict):
    """A named simulation setup: source, analysis and spec checks."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    source: Literal["schematic", "extracted"] = "schematic"
    analysis: Literal["op", "tran", "ac", "dc"] = "op"
    vdd: float = 1.8
    # Nominal simulation temperature in °C; None = the simulator's own
    # default (ngspice: 27 °C). Emitted as a `.temp` card, which takes
    # precedence over `.options temp` but never rewrites model `tnom`.
    temp: float | None = None
    # Accelerated stress corner applied on a second run of the same
    # analysis/probes. "vdd"/"temp" are the HTOL-style operating-point
    # overrides (still not an aging model); "dvth_nmos"/"dvth_pmos"/
    # "mu0_scale" are *parametric device-stress proxies*: they are injected
    # onto MOS instance cards as BSIM4 ``delvto``/``mulu0`` so the stress
    # run sees an explicit Vth/mobility shift. That is a transparent proxy
    # for NBTI/HCI-style drift — it is NOT a foundry reliability/aging
    # model (no time dependence, no wearout statistics). For a pfet the
    # sign follows the model's vth0 convention — a |Vth| *increase* (the
    # NBTI direction) is ``dvth_pmos`` negative. Only these five keys are
    # legal.
    stress: dict[str, float] | None = None
    stimulus: str | None = None
    probes: list[str] = Field(default_factory=list)
    specs: list[Spec] = Field(default_factory=list)
    # Mixed-signal stimulus: a self-contained Verilog control block whose
    # listed nets are dumped and replayed as PWL voltage sources on the
    # named nodes (tran only — see tools/mixed_signal.py).
    digital: DigitalStimulus | None = None

    @model_validator(mode="after")
    def _check_source_args(self) -> Testbench:
        if self.source == "schematic" and self.stimulus is not None:
            raise ValueError(
                f"testbench {self.name}: stimulus is only supported for "
                "source='extracted' (schematic uses the auto testbench)"
            )
        if self.source == "schematic" and self.probes:
            raise ValueError(
                f"testbench {self.name}: probes only apply to source='extracted'"
            )
        if self.digital is not None:
            nodes = [s.node for s in self.digital.signals]
            if len(set(nodes)) != len(nodes):
                raise ValueError(
                    f"testbench {self.name}: duplicate digital node(s) — "
                    "one PWL source per node")
        names = [s.name for s in self.specs]
        if len(set(names)) != len(names):
            raise ValueError(f"testbench {self.name}: duplicate spec names")
        if self.stress is not None:
            allowed = ("temp", "vdd", "dvth_nmos", "dvth_pmos",
                       "mu0_scale")
            unknown = sorted(set(self.stress) - set(allowed))
            if unknown:
                raise ValueError(
                    f"testbench {self.name}: unknown stress key(s) "
                    f"{unknown}; allowed: {sorted(allowed)}")
            if not self.stress:
                raise ValueError(
                    f"testbench {self.name}: stress table is empty; "
                    f"allowed keys: {sorted(allowed)}")
        return self


class PortSpec(_Strict):
    """Static port contract exposed by a block."""

    name: str
    layer: str
    direction: Literal["input", "output", "inout", "supply"] = "inout"
    # Physical drawing layer this pin actually connects to (e.g. a gate pin
    # declared on met1 but tapping 'poly'). None = taps its own declared layer.
    tap_layer: str | None = None


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


class SensitiveNet(_Strict):
    """A net whose electrical role makes it layout-sensitive."""

    net: str
    kind: Literal[
        "high_impedance", "low_impedance", "critical_bias", "clock", "other"
    ]
    reason: str


class ParamRationale(_Strict):
    """Why one parameter carries its value.

    ``target`` is ``<instance_id>.<param_name>``; the instance must exist,
    the param name is deliberately unvalidated — block schemas differ.
    """

    target: str
    note: str


class DesignIntent(_Strict):
    """Structured design intent — the machine-readable sibling of ``meta``.

    Where ``meta`` is a flat key→value bag, ``intent`` carries the *why* in
    typed fields so agents and tools can consume it without parsing prose.
    Every instance/net reference is resolved by ``Design._check_refs`` —
    an intent entry pointing at a nonexistent object fails validation.
    """

    matched_groups: list[list[str]] = Field(
        default_factory=list,
        description="Groups of instance ids that must stay matched",
    )
    sensitive_nets: list[SensitiveNet] = Field(default_factory=list)
    param_rationale: list[ParamRationale] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Design(_Strict):
    ir_version: str = IR_VERSION
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    pdk: str
    instances: list[Instance]
    nets: list[Net] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    ports: list[Port] = Field(default_factory=list)
    testbenches: list[Testbench] = Field(default_factory=list)
    meta: dict[str, ParamValue] = Field(default_factory=dict)
    # Structured design intent — optional; ``meta`` stays the free-form bag.
    intent: DesignIntent | None = None

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
        net_names = {n.name for n in self.nets}
        for c in self.constraints:
            for i in c.instances:
                if i not in ids:
                    raise ValueError(f"constraint {c.type}: unknown instance {i!r}")
            if c.nets is not None:
                unknown_nets = set(c.nets) - net_names
                if unknown_nets:
                    raise ValueError(
                        f"constraint {c.type}: unknown net(s) {sorted(unknown_nets)!r}"
                    )
        if self.intent is not None:
            for group in self.intent.matched_groups:
                if not group:
                    raise ValueError("intent matched_groups: empty group")
                for iid in group:
                    if iid not in ids:
                        raise ValueError(
                            f"intent matched_groups: unknown instance {iid!r}"
                        )
            for sn in self.intent.sensitive_nets:
                if sn.net not in net_names:
                    raise ValueError(
                        f"intent sensitive_nets: unknown net {sn.net!r}"
                    )
            for pr in self.intent.param_rationale:
                inst, sep, _param = pr.target.partition(".")
                if not sep or not _param or inst not in ids:
                    raise ValueError(
                        f"intent param_rationale: bad target {pr.target!r} "
                        "(expected '<instance>.<param>' naming a real instance)"
                    )
        tb_names = [t.name for t in self.testbenches]
        if len(set(tb_names)) != len(tb_names):
            raise ValueError("duplicate testbench names")
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
