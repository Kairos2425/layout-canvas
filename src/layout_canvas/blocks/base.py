"""Block registry.

A *block* is a parametric layout generator plus its contract: parameter schema,
port list, constraints it guarantees internally and a SPICE netlist emitter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import gdsfactory as gf

from layout_canvas.ir.model import BlockSpec, ParamSpec

BuildFn = Callable[..., gf.Component]
NetlistFn = Callable[..., str]


@dataclass(frozen=True)
class Block:
    spec: BlockSpec
    build: BuildFn
    netlist: NetlistFn | None = None

    @property
    def name(self) -> str:
        return self.spec.name

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.spec.params}

    def resolve_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Merge user params with defaults, coerce types and range-check."""
        specs = {p.name: p for p in self.spec.params}
        unknown = set(params) - set(specs)
        if unknown:
            raise ValueError(f"{self.name}: unknown params {sorted(unknown)}")
        out: dict[str, Any] = {}
        for name, ps in specs.items():
            out[name] = _coerce(ps, params.get(name, ps.default), self.name)
        return out

    def component(self, **params: Any) -> gf.Component:
        resolved = self.resolve_params(params)
        key = (self.name, tuple(sorted(resolved.items())))
        cached = _COMPONENT_CACHE.get(key)
        if cached is not None:
            return cached
        component = self.build(**resolved)
        _COMPONENT_CACHE[key] = component
        return component

    def spice(self, **params: Any) -> str:
        if self.netlist is None:
            raise NotImplementedError(f"{self.name} has no netlist emitter")
        return self.netlist(**self.resolve_params(params))


def _coerce(ps: ParamSpec, value: Any, block: str) -> Any:
    try:
        if ps.type == "int":
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
            v: Any = int(value)
        elif ps.type == "float":
            v = float(value)
        elif ps.type == "bool":
            v = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
        else:
            v = str(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{block}.{ps.name}: expected {ps.type}, got {value!r}") from e
    if ps.min is not None and v < ps.min:
        raise ValueError(f"{block}.{ps.name}={v} below min {ps.min}")
    if ps.max is not None and v > ps.max:
        raise ValueError(f"{block}.{ps.name}={v} above max {ps.max}")
    if ps.choices is not None and v not in ps.choices:
        raise ValueError(f"{block}.{ps.name}={v!r} not in {ps.choices}")
    return v


_REGISTRY: dict[str, Block] = {}
# gdsfactory stores cells in a process-wide KCLayout and rejects duplicate
# cell names. Repeated compilation in an MCP session must therefore reuse the
# immutable component for an identical block/parameter tuple.
_COMPONENT_CACHE: dict[tuple[str, tuple[tuple[str, Any], ...]], gf.Component] = {}


def register(spec: BlockSpec, netlist: NetlistFn | None = None) -> Callable[[BuildFn], BuildFn]:
    def deco(fn: BuildFn) -> BuildFn:
        _REGISTRY[spec.name] = Block(spec=spec, build=fn, netlist=netlist)
        return fn

    return deco


def unregister(name: str) -> None:
    """Drop a block and its cached components (generated-block removal)."""
    _REGISTRY.pop(name, None)
    for key in [k for k in _COMPONENT_CACHE if k[0] == name]:
        _COMPONENT_CACHE.pop(key, None)


def _sync_external_pdks() -> None:
    """Re-scan the external-PDK env hooks when they changed.

    External descriptors load lazily; their ``gen_*`` blocks register
    through the ``on_pdk_registered`` hook during that scan, so a caller
    that never touched ``get_pdk`` still sees them.
    """
    from layout_canvas.pdk.descriptor import _ensure_external

    _ensure_external()


def on_pdk_registered(
    cb: Callable[[Any], None],
    *,
    on_removed: Callable[[str], None] | None = None,
) -> None:
    """Subscribe ``cb(pdk)`` to PDK registrations — present and future.

    The external-PDK scan runs first, so ``cb`` sees every currently
    registered PDK exactly once (import-order independent), then subscribes
    for future ``register_pdk`` calls. ``on_removed`` (optional) fires with
    a name when an external PDK leaves the registry on an env rescan.
    """
    from layout_canvas.pdk import all_pdks
    from layout_canvas.pdk.descriptor import (
        add_pdk_listener,
        add_pdk_removed_listener,
    )

    existing = list(all_pdks().values())  # triggers the lazy external scan
    add_pdk_listener(cb)
    if on_removed is not None:
        add_pdk_removed_listener(on_removed)
    for pdk in existing:
        cb(pdk)


def get(name: str) -> Block:
    _sync_external_pdks()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown block {name!r}; known: {sorted(_REGISTRY)}") from None


def describe_pdk_block_gap(pdk_name: str) -> str | None:
    """Explain a missing block in terms of the PDK registry.

    A JSON-loaded (descriptor-only) PDK has no parametric generators — the
    honest answer names that boundary instead of a bare "unknown block".
    Returns None when the PDK is unknown or already has blocks.
    """
    from layout_canvas.pdk import get_pdk

    try:
        get_pdk(pdk_name)
    except KeyError:
        return None
    if any(b.spec.pdk == pdk_name for b in _REGISTRY.values()):
        return None
    return (
        f"no blocks registered for pdk {pdk_name!r} — descriptor PDKs get "
        "gen_diff_pair/gen_current_mirror/gen_guard_ring automatically when "
        "extract.roles covers the contact stack; this one does not. Bring "
        "cells in via import_gds or register_cell, or write a Python block "
        "generator"
    )


def all_blocks() -> dict[str, Block]:
    _sync_external_pdks()
    return dict(_REGISTRY)


def clear_component_cache() -> None:
    """Release cached Python references without invalidating the global layout."""

    _COMPONENT_CACHE.clear()
