"""Upstream netlist → Block IR importer.

Upstream schematic tools (e.g. analog-canvas) end their pipeline at a
deterministic structural netlist — SPICE ``.subckt``/``X`` cards or Spectre
``.scs`` ``subckt``/instance statements. This module bridges that endpoint
into our Block IR starting point: each instantiated subckt is matched to a
registered parametric block by name and port-name signature, top-level
connectivity becomes nets/ports, and the result is an ordinary ``Design``
that flows through the same compile → DRC → LVS → PEX loop.

Fail-closed by contract (ADR 0002/0005): subckts that cannot be mapped to a
block — name mismatch, pin-set mismatch, or raw device primitives at top
level — are *named* in ``unresolved``/``diagnostics`` and dropped from the
IR. The importer never invents a block, never guesses a pin binding, and
never emits an instance it cannot compile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from layout_canvas.blocks import base
from layout_canvas.ir.model import Design

# ---------------------------------------------------------------------------
# Parsed-netlist intermediate model
# ---------------------------------------------------------------------------


@dataclass
class _Instance:
    name: str
    nodes: list[str]
    master: str
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class _Subckt:
    name: str
    pins: list[str]
    params: dict[str, str] = field(default_factory=dict)
    instances: list[_Instance] = field(default_factory=list)
    leaf_devices: int = 0
    polarities: set[str] = field(default_factory=set)  # {"nmos"} / {"pmos"}


@dataclass
class _Parsed:
    dialect: str
    subckts: dict[str, _Subckt] = field(default_factory=dict)
    top_instances: list[_Instance] = field(default_factory=list)
    top_devices: int = 0
    globals: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


_LEAF_PREFIXES = ("m", "r", "c", "d", "q", "v", "i", "l", "e", "f", "g", "h")
_PNMOS = ("nch", "nmos", "nfet")
_PPMOS = ("pch", "pmos", "pfet")
_SI_SCALE = {
    "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3, "u": 1e-6,
    "n": 1e-9, "p": 1e-12, "f": 1e-15,
}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KEYVAL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(\S+)$")
_NUM = re.compile(r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)([A-Za-z]*)$")


def _polarity_of(model_name: str) -> str | None:
    low = model_name.lower()
    if any(tok in low for tok in _PPMOS):
        return "pmos"
    if any(tok in low for tok in _PNMOS):
        return "nmos"
    return None


def _si_to_um(text: str) -> float | None:
    """Parse a SPICE/Spectre magnitude and convert metres → micrometres."""
    m = _NUM.match(text.strip())
    if not m:
        return None
    scale = _SI_SCALE.get(m.group(2).lower(), None)
    if m.group(2) and scale is None:
        return None
    return float(m.group(1)) * (scale or 1.0) * 1e6


def _sanitise_id(raw: str, taken: set[str]) -> str:
    out = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    if not out or out[0].isdigit():
        out = "I_" + out
    name = out
    i = 2
    while name in taken:
        name = f"{out}_{i}"
        i += 1
    taken.add(name)
    return name


# ---------------------------------------------------------------------------
# SPICE dialect
# ---------------------------------------------------------------------------


def _spice_lines(text: str) -> list[str]:
    """Join ``+`` continuations and strip comments/blank lines."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("*") or line.lstrip().startswith("//"):
            continue
        if line.lstrip().startswith("+") and out:
            out[-1] += " " + line.lstrip()[1:]
            continue
        # inline comments: ';' or '$' preceded by whitespace (HSPICE style)
        out.append(re.split(r"\s+[;$]", line)[0].strip())
    return out


def _parse_spice(text: str) -> _Parsed:
    parsed = _Parsed(dialect="spice")
    current: _Subckt | None = None
    for line in _spice_lines(text):
        toks = line.split()
        head = toks[0].lower()
        if head == ".subckt":
            if len(toks) < 2:
                parsed.diagnostics.append("malformed .subckt line skipped")
                continue
            pins: list[str] = []
            params: dict[str, str] = {}
            past_params = False
            for tok in toks[2:]:
                if tok.lower() == "params:":
                    past_params = True
                    continue
                kv = _KEYVAL.match(tok)
                if past_params or kv:
                    if kv:
                        params[kv.group(1)] = kv.group(2)
                    continue
                pins.append(tok)
            current = _Subckt(name=toks[1], pins=pins, params=params)
            parsed.subckts[current.name] = current
            continue
        if head == ".ends":
            current = None
            continue
        if head == ".global":
            parsed.globals.extend(toks[1:])
            continue
        if head.startswith("."):
            if head in (".include", ".inc", ".lib"):
                parsed.diagnostics.append(
                    f"{head} card skipped — resolve includes before import")
            elif head in (".model", ".param", ".options", ".end", ".temp"):
                pass  # irrelevant to structural import
            else:
                parsed.diagnostics.append(f"unhandled card {head!r} skipped")
            continue
        first = toks[0][0].lower()
        is_instance = first == "x"
        is_leaf = first in _LEAF_PREFIXES
        if is_instance or is_leaf:
            if is_instance:
                # X name n1 n2 … master key=value…
                body, params = toks[1:], {}
                while body and _KEYVAL.match(body[-1]):
                    kv = _KEYVAL.match(body.pop())
                    params[kv.group(1)] = kv.group(2)
                if len(body) < 2:
                    parsed.diagnostics.append(
                        f"instance {toks[0]!r}: no nodes/master, skipped")
                    continue
                inst = _Instance(name=toks[0], nodes=body[:-1],
                                 master=body[-1], params=params)
            else:
                inst = None
            if inst is not None:
                (current.instances if current is not None
                 else parsed.top_instances).append(inst)
            else:
                if current is not None:
                    current.leaf_devices += 1
                    # M d g s b model … — the 5th field is the device model
                    if first == "m" and len(toks) > 5:
                        pol = _polarity_of(toks[5])
                        if pol:
                            current.polarities.add(pol)
                else:
                    parsed.top_devices += 1
            continue
        parsed.diagnostics.append(f"unparsed line skipped: {line[:60]!r}")
    return parsed


# ---------------------------------------------------------------------------
# Spectre dialect  (subckt name ( pins ) … ends name ; name ( nodes ) master p=v)
# ---------------------------------------------------------------------------


def _spectre_lines(text: str) -> list[str]:
    out: list[str] = []
    in_block_comment = False
    for raw in text.splitlines():
        line = raw
        if in_block_comment:
            if "*/" in line:
                line = line.split("*/", 1)[1]
                in_block_comment = False
            else:
                continue
        while "/*" in line:
            pre, _, post = line.partition("/*")
            if "*/" in post:
                line = pre + post.split("*/", 1)[1]
            else:
                line = pre
                in_block_comment = True
                break
        line = line.split("//", 1)[0].strip()
        if not line or line.startswith("*"):
            continue
        if out and out[-1].endswith("\\"):
            out[-1] = out[-1][:-1] + " " + line
            continue
        out.append(line)
    return out


_SPECTRE_INST = re.compile(
    r"^([A-Za-z_][\w.]*)\s*\(([^)]*)\)\s*([A-Za-z_][\w.]*)\s*(.*)$")


def _parse_spectre(text: str) -> _Parsed:
    parsed = _Parsed(dialect="spectre")
    current: _Subckt | None = None
    for line in _spectre_lines(text):
        toks = line.split()
        head = toks[0].lower()
        if head == "subckt":
            m = re.match(r"subckt\s+(\S+)\s*\(([^)]*)\)(.*)$", line,
                         flags=re.IGNORECASE)
            if not m:
                parsed.diagnostics.append(
                    f"malformed subckt header skipped: {line[:60]!r}")
                continue
            params = {}
            for tok in m.group(3).split():
                kv = _KEYVAL.match(tok)
                if kv:
                    params[kv.group(1)] = kv.group(2)
            current = _Subckt(name=m.group(1),
                              pins=m.group(2).split(), params=params)
            parsed.subckts[current.name] = current
            continue
        if head in ("ends", "end"):
            current = None
            continue
        if head in ("include", "ahdl_include", "model", "simulatoroptions",
                    "parameters", "global"):
            if head == "global":
                parsed.globals.extend(toks[1:])
            elif head != "parameters":
                parsed.diagnostics.append(
                    f"{head} statement skipped: {line[:60]!r}")
            continue
        m = _SPECTRE_INST.match(line)
        if m:
            params = {}
            for tok in m.group(4).split():
                kv = _KEYVAL.match(tok)
                if kv:
                    params[kv.group(1).lower()] = kv.group(2)
            master = m.group(3)
            inst = _Instance(name=m.group(1), nodes=m.group(2).split(),
                             master=master, params=params)
            # A 'master' that resolves to a device model with primitive-like
            # params (w/l/nf…) is a leaf device, not a block call.
            is_leaf = master.lower() in {
                "nch", "pch", "nmos", "pmos", "resistor", "capacitor",
                "res", "cap", "diode", "bjt", "inductor",
            } or master.lower().startswith(("nch_", "pch_"))
            if current is not None:
                if is_leaf:
                    current.leaf_devices += 1
                    pol = _polarity_of(master)
                    if pol:
                        current.polarities.add(pol)
                else:
                    current.instances.append(inst)
            else:
                if is_leaf:
                    parsed.top_devices += 1
                else:
                    parsed.top_instances.append(inst)
            continue
        parsed.diagnostics.append(f"unparsed line skipped: {line[:60]!r}")
    return parsed


def detect_dialect(text: str) -> str:
    lowered = text.lower()
    if re.search(r"(?m)^\s*\.subckt\b", lowered):
        return "spice"
    if re.search(r"(?m)^\s*subckt\s+\S+\s*\(", lowered):
        return "spectre"
    # flat netlists: X-cards imply spice; 'name ( n ) master' implies spectre
    if re.search(r"(?m)^\s*x\S+\s", lowered):
        return "spice"
    if re.search(r"(?m)^\s*\S+\s*\([^)]*\)\s*\S+", lowered):
        return "spectre"
    return "spice"


# ---------------------------------------------------------------------------
# Block matching
# ---------------------------------------------------------------------------


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _match_subckt(sub: _Subckt, pdk: str, blocks: dict) -> tuple[str | None, str]:
    """Map a netlist subckt to ``<pdk>.<block>``. Returns (block, reason)."""
    candidates = {n: b for n, b in blocks.items() if b.spec.pdk == pdk}
    if not candidates:
        return None, f"no blocks registered for pdk {pdk!r}"

    sub_pins = {p.lower() for p in sub.pins}
    sub_norm = _norm(sub.name)

    # 1. name match — upstream subckt named like the block ('ota_5t',
    #    'current_mirror', optionally pdk-prefixed) — verified against pins.
    name_hits = [
        n for n in candidates
        if _norm(n.split(".", 1)[1]) == sub_norm or _norm(n) == sub_norm
    ]
    pin_hits = [
        n for n, b in candidates.items()
        if {p.name.lower() for p in b.spec.ports} == sub_pins
    ]
    for n in name_hits:
        if n in pin_hits:
            return n, "name+pin match"
        return None, (
            f"name matches {n!r} but pins differ: "
            f"netlist={sorted(sub_pins)} "
            f"block={sorted(p.name.lower() for p in candidates[n].spec.ports)}")
    if len(pin_hits) == 1:
        return pin_hits[0], "pin-signature match"
    if len(pin_hits) > 1:
        return None, f"ambiguous pin signature — candidates {pin_hits}"
    return None, f"no block in pdk {pdk!r} matches pins {sorted(sub_pins)}"


# Netlist param spelling → IR block param name. `m` deliberately excluded:
# SPICE multiplicity (parallel devices) is not finger count.
_PARAM_ALIASES = {
    "fingers": {"nf", "nfing", "fingers", "nfin", "seg", "segments"},
    "width": {"w", "width", "wf"},
    "length": {"l", "length", "lg"},
    "type": {"type", "devtype"},
}


def _map_params(inst: _Instance, sub: _Subckt, block, diags: list[str],
                label: str) -> dict:
    """Translate instance/subckt-default params onto block params.

    Only confident aliases land; anything unmappable or out-of-range is
    dropped with a diagnostic — the IR must stay compilable.
    """
    pspec = {p.name: p for p in block.spec.params}
    merged = {**{k.lower(): v for k, v in sub.params.items()},
              **{k.lower(): v for k, v in inst.params.items()}}
    out: dict = {}
    for key, raw in merged.items():
        target = next((t for t, aliases in _PARAM_ALIASES.items()
                       if key in aliases and t in pspec), None)
        if target is None:
            diags.append(f"{label}: param {key}={raw!r} has no block alias "
                         "— dropped")
            continue
        if pspec[target].type in ("int", "float"):
            if target in ("width", "length"):
                val = _si_to_um(raw)
            else:
                m = _NUM.match(raw.strip())
                val = float(m.group(1)) if m else None
            if val is None:
                diags.append(f"{label}: param {key}={raw!r} unparsable "
                             "— dropped")
                continue
            out[target] = val
        else:
            out[target] = raw
    # Device polarity inside the subckt body is a real fact: a current
    # mirror built from pch devices is a PMOS mirror. Its 'vss' pin is
    # then the interior source strap (often wired to vdd), not the global
    # substrate — getting this wrong produces a genuine LVS mismatch.
    if ("type" in pspec and "type" not in out
            and len(sub.polarities) == 1):
        pol = next(iter(sub.polarities))
        choices = pspec["type"].choices
        if choices is None or pol in choices:
            out["type"] = pol
    elif "type" in pspec and len(sub.polarities) > 1:
        diags.append(f"{label}: mixed device polarities "
                     f"{sorted(sub.polarities)} in subckt — 'type' left "
                     "at block default")
    return _checked_params(block, out, diags, label)


def _checked_params(block, out: dict, diags: list[str], label: str) -> dict:
    """Apply resolve_params per-key so one bad value can't sink the rest."""
    good: dict = {}
    for k, v in out.items():
        try:
            block.resolve_params({k: v})
            good[k] = v
        except ValueError as exc:
            diags.append(f"{label}: param {k}={v!r} rejected ({exc})")
    return good


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def import_netlist(
    text: str,
    pdk: str = "sky130",
    top: str | None = None,
    dialect: str = "auto",
    design_name: str | None = None,
) -> dict:
    """Import a structural SPICE/Spectre netlist into a Block IR draft.

    Returns a fail-closed envelope::

        {"status": "ok" | "partial" | "failed",
         "dialect": ..., "pdk": ..., "design_name": ...,
         "ir": <design dict> | None,
         "mapped":   [{"instance", "subckt", "block"}],
         "unresolved":[{"instance"|"subckt", "reason", "candidates"?}],
         "top_level_devices_skipped": int,
         "diagnostics": [str, ...]}
    """
    dialect = detect_dialect(text) if dialect == "auto" else dialect
    parsed = (_parse_spice(text) if dialect == "spice"
              else _parse_spectre(text))
    diags: list[str] = list(parsed.diagnostics)

    import layout_canvas.blocks  # noqa: F401 — populate the registry
    blocks = base.all_blocks()

    # ---- choose the top ----------------------------------------------------
    instantiated = {
        inst.master for s in parsed.subckts.values() for inst in s.instances
    }
    if top:
        top_sub = parsed.subckts.get(top)
        if top_sub is None:
            return {"status": "failed", "dialect": dialect, "pdk": pdk,
                    "design_name": design_name or top,
                    "ir": None, "mapped": [], "unresolved": [],
                    "top_level_devices_skipped": parsed.top_devices,
                    "diagnostics": diags + [
                        f"top subckt {top!r} not found; defined: "
                        f"{sorted(parsed.subckts)}"]}
    else:
        uninstantiated = [s for s in parsed.subckts.values()
                          if s.name not in instantiated]
        if parsed.top_instances:
            # Top-level instances ARE the top of a flat/mixed netlist;
            # uninstantiated subckt defs are just unused wrappers.
            top_sub = _Subckt(name=design_name or "imported_top", pins=[],
                              instances=parsed.top_instances)
            diags.append("flat top-level instances imported; "
                         "uninstantiated subckt defs "
                         f"{[s.name for s in uninstantiated]} ignored, "
                         "no ports derivable")
        elif len(uninstantiated) == 1:
            top_sub = uninstantiated[0]
        elif len(uninstantiated) > 1:
            return {"status": "failed", "dialect": dialect, "pdk": pdk,
                    "design_name": design_name or "imported",
                    "ir": None, "mapped": [], "unresolved": [],
                    "top_level_devices_skipped": parsed.top_devices,
                    "diagnostics": diags + [
                        "ambiguous top: uninstantiated subckts "
                        f"{[s.name for s in uninstantiated]} — pass top="]}
        else:
            return {"status": "failed", "dialect": dialect, "pdk": pdk,
                    "design_name": design_name or "imported",
                    "ir": None, "mapped": [], "unresolved": [],
                    "top_level_devices_skipped": parsed.top_devices,
                    "diagnostics": diags + [
                        "no top subckt and no flat instances found"]}

    name = design_name or top_sub.name
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not name or name[0].isdigit():
        name = "D_" + name

    # ---- resolve each top-level instance to a block -------------------------
    mapped: list[dict] = []
    unresolved: list[dict] = []
    instances_ir: list[dict] = []
    taken: set[str] = set()
    bindings: dict[str, dict[str, str]] = {}   # inst_id -> port -> node

    for inst in top_sub.instances:
        sub = parsed.subckts.get(inst.master)
        if sub is None:
            unresolved.append({"instance": inst.name, "subckt": inst.master,
                               "reason": "subckt definition not in file"})
            continue
        if len(inst.nodes) != len(sub.pins):
            unresolved.append({
                "instance": inst.name, "subckt": inst.master,
                "reason": f"pin count mismatch: {len(inst.nodes)} node(s) "
                          f"on instance vs {len(sub.pins)} pin(s) on subckt"})
            continue
        block_name, why = _match_subckt(sub, pdk, blocks)
        if block_name is None:
            unresolved.append({"instance": inst.name, "subckt": inst.master,
                               "reason": why})
            continue
        block = blocks[block_name]
        inst_id = _sanitise_id(inst.name, taken)
        params = _map_params(inst, sub, block, diags, inst.name)
        instances_ir.append({
            "id": inst_id,
            "block": block_name,
            "params": params,
            "placement": (
                {"x": 0.0, "y": 0.0} if not instances_ir else {
                    "relative_to": instances_ir[-1]["id"],
                    "relation": "right_of", "margin": 5.0}),
        })
        # port binding: subckt pin order drives the instance node order
        port_names = {p.name.lower(): p.name for p in block.spec.ports}
        binding = {}
        for pin, node in zip(sub.pins, inst.nodes, strict=False):
            binding[port_names.get(pin.lower(), pin)] = node
        bindings[inst_id] = binding
        mapped.append({"instance": inst_id, "subckt": inst.master,
                       "block": block_name,
                       **({"params": params} if params else
                          {"params_defaulted": True})})

    # ---- nets & ports -------------------------------------------------------
    node_pins: dict[str, list[str]] = {}
    for inst_id, binding in bindings.items():
        for port_name, node in binding.items():
            node_pins.setdefault(node, []).append(f"{inst_id}.{port_name}")

    nets_ir: list[dict] = []
    for node, pins in sorted(node_pins.items()):
        if len(pins) >= 2:
            nets_ir.append({"name": node, "pins": pins})
        elif not top_sub.pins or node not in top_sub.pins:
            diags.append(f"node {node!r} connects a single pin "
                         f"({pins[0]}) — left floating")

    supply = {"vdd", "vss", "gnd", "vcc", "vsub", "vdda", "vssa"}
    ports_ir: list[dict] = []
    for pin in top_sub.pins:
        pins = node_pins.get(pin, [])
        if not pins:
            diags.append(f"top pin {pin!r} connects to no instance pin — "
                         "port skipped")
            continue
        refs = [p.rsplit(".", 1) for p in pins]
        dirs = set()
        for inst_id, pname in refs:
            blk = blocks[next(i["block"] for i in instances_ir
                              if i["id"] == inst_id)]
            d = next((p.direction for p in blk.spec.ports
                      if p.name == pname), "inout")
            dirs.add(d)
        direction = ("supply" if pin.lower().rstrip("!") in supply
                     or "supply" in dirs
                     else "output" if "output" in dirs
                     else "input" if "input" in dirs else "inout")
        ports_ir.append({"name": pin, "pin": pins[0],
                         "direction": direction})

    if not instances_ir:
        return {"status": "failed", "dialect": dialect, "pdk": pdk,
                "design_name": name, "ir": None, "mapped": mapped,
                "unresolved": unresolved,
                "top_level_devices_skipped": parsed.top_devices,
                "diagnostics": diags + [
                    "no top-level instances mapped to blocks — nothing "
                    "to compile"]}

    design = Design.model_validate({
        "name": name, "pdk": pdk,
        "instances": instances_ir, "nets": nets_ir, "ports": ports_ir,
        "meta": {
            "imported_via": "netlist_import",
            "dialect": dialect,
            "unresolved": len(unresolved),
            "params_defaulted": ",".join(
                m["instance"] for m in mapped if m.get("params_defaulted")),
            "top_leaf_devices_skipped": parsed.top_devices,
        },
    })

    if parsed.top_devices:
        diags.append(f"{parsed.top_devices} raw device(s) at top level "
                     "cannot map to parametric blocks — skipped")
    for s in parsed.subckts.values():
        if s is not top_sub and s.name not in instantiated:
            diags.append(f"subckt {s.name!r} defined but never instantiated")
    if parsed.globals:
        diags.append(f"global nets: {parsed.globals}")

    return {
        "status": "ok" if not unresolved and not parsed.top_devices
                  else "partial",
        "dialect": dialect, "pdk": pdk, "design_name": name,
        "ir": design.model_dump(),
        "ir_text": design.to_json(),
        "mapped": mapped, "unresolved": unresolved,
        "top_level_devices_skipped": parsed.top_devices,
        "diagnostics": diags,
    }
