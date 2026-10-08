"""Pluggable EDA tool adapter registry.

Every external tool — simulator or verifier — is described by one adapter:
how to probe it (binary names, version flag, license expectation) and, for
supported tools, how to run a job. The registry lets the product degrade
gracefully: a missing binary is ``unavailable``, a license-gated tool that
is installed but unprobed is reported honestly, and no path fabricates a
clean result.

Device-domain coverage targets the tools engineers actually have:

- FOSS:  ngspice, Xyce, LTspice (free), KLayout, Netgen, Magic
- EDA:   Spectre, HSPICE, Eldo, PrimeSim, Calibre (license-gated;
         adapters wire the interface and report license status)
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ToolKind = Literal["spice_simulator", "drc", "lvs", "layout_viewer"]


@dataclass(frozen=True)
class ToolAdapter:
    name: str
    kind: ToolKind
    executables: tuple[str, ...]
    version_args: tuple[str, ...] = ("-v",)
    version_pattern: str = r"[\d]+\.[\d.]+"
    license_required: bool = False
    can_run: bool = True
    deck_hint: str = "spice"  # spice | spectre | none
    notes: str = ""

    def find_binary(self) -> str | None:
        import os

        override = os.environ.get(f"LAYOUT_CANVAS_{self.name.upper()}")
        if override and Path(override).is_file():
            return str(Path(override).resolve())
        for exe in self.executables:
            found = shutil.which(exe)
            if found:
                return found
            if Path(exe).is_file():
                return str(Path(exe).resolve())
        return None

    def probe(self, timeout: int = 10) -> dict[str, Any]:
        binary = self.find_binary()
        if binary is None:
            return {
                "name": self.name,
                "kind": self.kind,
                "status": "unavailable",
                "binary": None,
                "version": None,
                "license_required": self.license_required,
                "can_run": self.can_run,
                "detail": f"none of {self.executables} on PATH",
            }
        version = self._probe_version(binary, timeout)
        return {
            "name": self.name,
            "kind": self.kind,
            "status": "available",
            "binary": binary,
            "version": version,
            "license_required": self.license_required,
            "can_run": self.can_run,
            "detail": (
                "license-gated tool detected; run still fails closed if the "
                "license checkout fails" if self.license_required else "ready"
            ),
        }

    def _probe_version(self, binary: str, timeout: int) -> str | None:
        for args in (self.version_args, ("--version",), ("-version",), ("-V",)):
            try:
                proc = subprocess.run(
                    [binary, *args],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            text = f"{proc.stdout}\n{proc.stderr}"
            match = re.search(self.version_pattern, text)
            if match:
                return match.group(0)
        return None


# --- Simulator adapters -------------------------------------------------

NGSPICE = ToolAdapter(
    name="ngspice",
    kind="spice_simulator",
    executables=("ngspice",),
    version_args=("-v",),
    notes="FOSS SPICE; batch: ngspice -b -o log deck.cir",
)

XYCE = ToolAdapter(
    name="xyce",
    kind="spice_simulator",
    executables=("Xyce", "xyce"),
    version_args=("-v", "-V"),
    notes="Sandia parallel SPICE; batch: Xyce -o log deck.cir",
)

LTSPICE = ToolAdapter(
    name="ltspice",
    kind="spice_simulator",
    executables=(
        "LTspice.exe",
        "XVIIx64.exe",
        "ltspice",
        r"C:\Program Files\LTC\LTspiceXVII\XVIIx64.exe",
    ),
    version_args=("-help",),
    notes="Free (not open) SPICE; batch: ltspice -b deck.cir",
)

SPECTRE = ToolAdapter(
    name="spectre",
    kind="spice_simulator",
    executables=("spectre",),
    version_args=("-V", "-W"),
    license_required=True,
    notes="Cadence; SPICE decks run via 'spectre +spice deck.cir'",
)

HSPICE = ToolAdapter(
    name="hspice",
    kind="spice_simulator",
    executables=("hspice",),
    version_args=("-v",),
    license_required=True,
    notes="Synopsys; batch: hspice deck.sp -o out",
)

ELDO = ToolAdapter(
    name="eldo",
    kind="spice_simulator",
    executables=("eldo",),
    version_args=("-v",),
    license_required=True,
    notes="Siemens; batch: eldo deck.cir",
)

# --- Verification adapters ----------------------------------------------

KLAYOUT = ToolAdapter(
    name="klayout",
    kind="drc",
    executables=("klayout", "klayout_app"),
    version_args=("-v",),
    notes="FOSS; batch DRC: klayout -b -r deck.drc -rd input=... -rd report=...",
)

NETGEN = ToolAdapter(
    name="netgen",
    kind="lvs",
    executables=("netgen",),
    version_args=("-batch", "noop"),
    notes="FOSS LVS; batch: netgen -batch lvs ...",
)

MAGIC = ToolAdapter(
    name="magic",
    kind="drc",
    executables=("magic",),
    version_args=("--version",),
    can_run=False,
    notes="FOSS DRC/extraction; adapter probed, scripted runner not wired yet",
)

CALIBRE = ToolAdapter(
    name="calibre",
    kind="drc",
    executables=("calibre",),
    version_args=("-version",),
    license_required=True,
    can_run=False,
    notes="Siemens sign-off DRC/LVS; interface reserved, license-gated",
)


_REGISTRY: dict[str, ToolAdapter] = {a.name: a for a in (
    NGSPICE, XYCE, LTSPICE, SPECTRE, HSPICE, ELDO, KLAYOUT, NETGEN, MAGIC, CALIBRE,
)}


def get_adapter(name: str) -> ToolAdapter:
    try:
        return _REGISTRY[name.lower()]
    except KeyError:
        raise KeyError(f"unknown tool {name!r}; known: {sorted(_REGISTRY)}") from None


def adapters(kind: ToolKind | None = None) -> list[ToolAdapter]:
    if kind is None:
        return list(_REGISTRY.values())
    return [a for a in _REGISTRY.values() if a.kind == kind]


def probe_environment(timeout: int = 10) -> dict[str, Any]:
    """Probe every registered tool; the report is data, not verdicts."""
    reports = [a.probe(timeout) for a in _REGISTRY.values()]
    pya = _probe_pya()
    verification = [
        r["name"] for r in reports
        if r["kind"] in ("drc", "lvs") and r["status"] == "available" and r["can_run"]
    ]
    if pya["status"] == "available":
        verification.append("klayout-pya")
    # Descriptor PDKs loaded from LAYOUT_CANVAS_PDK_DIR/LAYOUT_CANVAS_PDKS —
    # what they unlock (extract/drc/prelude) plus per-file load diagnostics.
    from layout_canvas.pdk import external_pdk_errors, external_pdks

    ext_pdks = {
        name: {
            "source": pdk.source,
            "layers": len(pdk.layers),
            "drc": pdk.drc is not None,
            "extract": pdk.extract is not None,
            "leaf_devices": len((pdk.extract or {}).get("leaf_devices", {})),
        }
        for name, pdk in external_pdks().items()
    }
    return {
        "tools": reports,
        "in_process_engines": [pya],
        "external_pdks": ext_pdks,
        "external_pdk_errors": external_pdk_errors(),
        "simulators": {
            "available": [r["name"] for r in reports
                          if r["kind"] == "spice_simulator" and r["status"] == "available" and r["can_run"]],
            "detected_needs_license": [r["name"] for r in reports
                                       if r["kind"] == "spice_simulator" and r["license_required"]
                                       and r["status"] == "available"],
        },
        "verification": {
            "available": verification,
        },
    }


def _probe_pya() -> dict[str, Any]:
    """The klayout pip module exposes the same LVS/DRC engine in-process."""
    try:
        import klayout.db as db
        ok = all(
            hasattr(db, name)
            for name in ("LayoutToNetlist", "DeviceExtractorMOS4Transistor",
                         "NetlistComparer", "Region")
        )
        ver = None
        try:
            import importlib.metadata as md
            ver = md.version("klayout")
        except Exception:
            pass
        return {
            "name": "klayout-pya",
            "kind": "drc+lvs",
            "status": "available" if ok else "unavailable",
            "version": ver,
            "license_required": False,
            "can_run": ok,
            "notes": "in-process engine: Region geometry checks + LayoutToNetlist extraction + NetlistComparer",
        }
    except ImportError:
        return {
            "name": "klayout-pya",
            "kind": "drc+lvs",
            "status": "unavailable",
            "version": None,
            "license_required": False,
            "can_run": False,
            "notes": "klayout python module not installed (pip install klayout)",
        }
