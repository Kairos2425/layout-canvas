"""PDK descriptor — process data as data, not scattered constants.

A descriptor names the drawing/pin layer map and the few process rules the
compiler and tools actually consult. Block generators keep their existing
`blocks/sky130/layers.py` constants; the descriptor is the contract the
compiler, verifiers and future commercial-PDK adapters program against.

Descriptors can also be loaded from JSON — commercial PDKs arrive as
``*.pdk.json`` files pointed at by ``LAYOUT_CANVAS_PDK_DIR`` (a private
directory scanned for ``*.json``) and/or ``LAYOUT_CANVAS_PDKS`` (a
pathsep-separated list of descriptor files). They register alongside the
built-ins without any code change and unlock the import_gds → DRC-subset →
LVS-extraction → model-prelude chain. Loading is lazy on first
``get_pdk``/``all_pdks`` access and fail-closed: a malformed file is
recorded in ``external_pdk_errors()`` and never kills the registry, and a
descriptor may not shadow a built-in name. See ``docs/PDK_DESCRIPTORS.md``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Layer = tuple[int, int]

# Fixed extraction-role contract. The planar-MOS recipe in
# ``tools/extract.py`` programs against exactly these roles — values may be
# null (role absent in this process), but unknown names are rejected so a
# typo can never silently drop a layer from connectivity.
EXTRACTION_ROLES: tuple[str, ...] = (
    "well_n", "diff", "tap", "poly", "licon", "li1", "mcon",
    "met1", "met2", "met3", "met4", "met5",
    "via1", "via2", "via3", "via4",
    "nsdm", "psdm", "capm",
)

# Roles the MOS derivation cannot work without — a descriptor naming an
# ``extract`` section must supply all three non-null.
_REQUIRED_EXTRACT_ROLES = ("well_n", "diff", "poly")

_DRC_CHECK_KINDS = ("width", "space")

_LEAF_POLARITIES = ("nmos", "pmos")


@dataclass(frozen=True)
class PDK:
    name: str
    layers: dict[str, Layer]
    pin_purpose: int = 16
    rules: dict[str, Any] = field(default_factory=dict)
    model_libs: dict[str, str] = field(default_factory=dict)
    # Tool-facing sections, populated by from_dict (or left None for
    # descriptor-only PDKs where the tools report ``unavailable``):
    #   extract = {"roles": {role: Layer|None},
    #              "text_datatypes": (int, ...),
    #              "leaf_devices": {subckt: (device_class, polarity)}}
    #   drc     = {"rules": {Layer: [(kind, value_um)]},
    #              "enclosure": [(label, Layer, [Layer, ...], value_um)]}
    #   oa_layers = {Layer: oa_layer_name} — GDS (layer,datatype) → OA layer
    #   name for the Virtuoso SKILL export; purpose is always "drawing".
    extract: dict[str, Any] | None = None
    drc: dict[str, Any] | None = None
    oa_layers: dict[Layer, str] | None = None
    # Provenance: the directory/model files a ``*.pdk.json`` resolves
    # ``model_libs`` relative paths against, and the file it was read from
    # (None for built-ins). Never serialised.
    base_dir: str | None = None
    source: str | None = None

    def layer(self, name: str) -> Layer:
        try:
            return self.layers[name]
        except KeyError:
            raise KeyError(
                f"pdk {self.name!r}: unknown layer {name!r}; known: {sorted(self.layers)}"
            ) from None

    def pin_label_layer(self, drawing_layer: Any) -> Layer:
        """Map a port's drawing layer to its LVS pin-label layer.

        Accepts a (layer, datatype) tuple or a bare layer number; the label
        always lands on ``(layer, pin_purpose)``.
        """
        if isinstance(drawing_layer, (tuple, list)) and drawing_layer:
            number = int(drawing_layer[0])
        else:
            number = int(drawing_layer)
        return (number, self.pin_purpose)

    # --- JSON (de)serialisation -----------------------------------------

    @classmethod
    def from_dict(
        cls,
        d: dict[str, Any],
        base_dir: str | Path | None = None,
        source: str | Path | None = None,
    ) -> "PDK":
        """Build a PDK from a ``*.pdk.json`` document.

        ``base_dir`` anchors relative ``model_libs`` paths; ``source`` is
        the descriptor file used for diagnostics. Raises ``ValueError``
        with the offending field named on any malformed input.
        """
        if not isinstance(d, dict):
            raise ValueError("pdk descriptor must be a JSON object")
        name = d.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("pdk descriptor needs a non-empty string 'name'")
        name = name.strip()

        layers_raw = d.get("layers") or {}
        if not isinstance(layers_raw, dict):
            raise ValueError(f"pdk {name!r}: 'layers' must be an object")
        layers = {
            lname: _layer_pair(pair, where=f"{name}.layers.{lname!r}")
            for lname, pair in layers_raw.items()
        }
        pin_purpose = int(d.get("pin_purpose", 16))
        rules = dict(d.get("rules") or {})
        model_libs = {
            str(k): str(v) for k, v in (d.get("model_libs") or {}).items()
        }
        extract = _parse_extract(d.get("extract"), name=name,
                                 pin_purpose=pin_purpose)
        drc = _parse_drc(d.get("drc"), name=name,
                         layers=layers, extract=extract)
        oa_layers = _parse_oa_layers(d.get("oa_layers"), name=name)
        return cls(
            name=name,
            layers=layers,
            pin_purpose=pin_purpose,
            rules=rules,
            model_libs=model_libs,
            extract=extract,
            drc=drc,
            oa_layers=oa_layers,
            base_dir=str(base_dir) if base_dir is not None else None,
            source=str(source) if source is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        """Inverse of :meth:`from_dict` — a JSON-serialisable descriptor.

        Sections the descriptor does not carry are emitted as ``null`` so a
        dump doubles as a template to fill in. Provenance fields
        (``base_dir``/``source``) are intentionally not exported.
        """
        out: dict[str, Any] = {
            "name": self.name,
            "pin_purpose": self.pin_purpose,
            "layers": {k: [pair[0], pair[1]] for k, pair in self.layers.items()},
            "rules": dict(self.rules),
            "model_libs": dict(self.model_libs),
            "extract": None,
            "drc": None,
            "oa_layers": None,
        }
        if self.extract is not None:
            out["extract"] = {
                "roles": {
                    role: ([pair[0], pair[1]] if pair else None)
                    for role, pair in self.extract["roles"].items()
                },
                "text_datatypes": list(self.extract["text_datatypes"]),
                "leaf_devices": {
                    model: [cls_name, pol]
                    for model, (cls_name, pol) in
                    self.extract["leaf_devices"].items()
                },
            }
        if self.drc is not None:
            names = _layer_name_map(self)

            def _nm(pair: Layer) -> str:
                return names.get(pair, f"{pair[0]}/{pair[1]}")

            out["drc"] = {
                "rules": {
                    _nm(pair): [[kind, value] for kind, value in checks]
                    for pair, checks in self.drc["rules"].items()
                },
                "enclosure": [
                    {"label": label, "cut": _nm(cut),
                     "enclosed_by": [_nm(o) for o in outers], "enc": value}
                    for label, cut, outers, value in self.drc["enclosure"]
                ],
            }
            if self.drc.get("layers"):
                out["drc"]["layers"] = {
                    k: [pair[0], pair[1]]
                    for k, pair in self.drc["layers"].items()
                }
        if self.oa_layers is not None:
            out["oa_layers"] = {
                f"{pair[0]}/{pair[1]}": oa_name
                for pair, oa_name in self.oa_layers.items()
            }
        return out


def _layer_pair(spec: Any, *, where: str) -> Layer:
    """Coerce a JSON ``[layer, datatype]`` pair; fail with ``where`` named."""
    try:
        layer, dt = int(spec[0]), int(spec[1])
    except (TypeError, ValueError, IndexError, KeyError):
        raise ValueError(
            f"pdk descriptor {where}: expected [layer, datatype], got {spec!r}"
        ) from None
    return (layer, dt)


def _parse_extract(raw: Any, *, name: str, pin_purpose: int) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"pdk {name!r}: 'extract' must be an object")

    roles_raw = raw.get("roles") or {}
    if not isinstance(roles_raw, dict):
        raise ValueError(f"pdk {name!r}: 'extract.roles' must be an object")
    unknown = sorted(set(roles_raw) - set(EXTRACTION_ROLES))
    if unknown:
        raise ValueError(
            f"pdk {name!r}: unknown extract.roles {unknown}; the planar-MOS "
            f"contract is fixed: {list(EXTRACTION_ROLES)}"
        )
    roles = {
        role: (None if spec is None else
               _layer_pair(spec, where=f"{name}.extract.roles.{role!r}"))
        for role, spec in roles_raw.items()
    }
    missing = [r for r in _REQUIRED_EXTRACT_ROLES if not roles.get(r)]
    if missing:
        raise ValueError(
            f"pdk {name!r}: extract.roles must carry non-null {missing} "
            "(the MOS derivation needs a well, an active and a gate layer)"
        )

    text_raw = raw.get("text_datatypes")
    if text_raw is None:
        text_datatypes = (pin_purpose,)
    else:
        try:
            text_datatypes = tuple(int(t) for t in text_raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"pdk {name!r}: extract.text_datatypes must be a list of ints, "
                f"got {text_raw!r}"
            ) from None

    leaf_devices: dict[str, tuple[str, str]] = {}
    for model, pair in (raw.get("leaf_devices") or {}).items():
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(
                f"pdk {name!r}: extract.leaf_devices[{model!r}] must be "
                f"[device_class, polarity], got {pair!r}"
            )
        cls_name, pol = str(pair[0]), str(pair[1])
        if pol not in _LEAF_POLARITIES:
            raise ValueError(
                f"pdk {name!r}: leaf device {model!r} polarity {pol!r} "
                f"must be one of {_LEAF_POLARITIES}"
            )
        leaf_devices[str(model)] = (cls_name, pol)

    return {
        "roles": roles,
        "text_datatypes": text_datatypes,
        "leaf_devices": leaf_devices,
    }


def _parse_drc(
    raw: Any,
    *,
    name: str,
    layers: dict[str, Layer],
    extract: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Resolve ``drc.rules``/``drc.enclosure`` names to (layer, datatype).

    Keys may name an entry in ``drc.layers`` (extra DRC-only drawings),
    an ``extract.roles`` role, a ``layers`` drawing, or a literal
    ``"68/20"`` pair. Unknown names raise with the usable list.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"pdk {name!r}: 'drc' must be an object")

    drc_layers = {
        lname: _layer_pair(pair, where=f"{name}.drc.layers.{lname!r}")
        for lname, pair in (raw.get("layers") or {}).items()
    }
    role_layers = (extract or {}).get("roles", {})

    def resolve(key: Any, *, where: str) -> Layer:
        if isinstance(key, (list, tuple)):
            return _layer_pair(key, where=where)
        text = str(key)
        for table in (drc_layers, role_layers, layers):
            if table.get(text):
                return table[text]
        m = re.fullmatch(r"(\d+)\s*[/,]\s*(\d+)", text)
        if m:
            return (int(m.group(1)), int(m.group(2)))
        known = sorted(
            set(drc_layers)
            | {r for r, v in role_layers.items() if v}
            | set(layers)
        )
        raise ValueError(
            f"pdk {name!r}: {where}: unknown layer/role name {text!r}; "
            f"known: {known} (or a literal 'L/DT' pair)"
        )

    rules: dict[Layer, list[tuple[str, float]]] = {}
    for lname, checks in (raw.get("rules") or {}).items():
        pair = resolve(lname, where="drc.rules")
        parsed: list[tuple[str, float]] = []
        for chk in checks or []:
            try:
                kind, value = str(chk[0]), float(chk[1])
            except (TypeError, ValueError, IndexError):
                raise ValueError(
                    f"pdk {name!r}: drc.rules[{lname!r}] entries must be "
                    f"[\"width\"|\"space\", um], got {chk!r}"
                ) from None
            if kind not in _DRC_CHECK_KINDS:
                raise ValueError(
                    f"pdk {name!r}: drc.rules[{lname!r}] check {kind!r} "
                    f"must be one of {_DRC_CHECK_KINDS}"
                )
            parsed.append((kind, value))
        rules.setdefault(pair, []).extend(parsed)

    enclosure = []
    for i, entry in enumerate(raw.get("enclosure") or []):
        where = f"drc.enclosure[{i}]"
        try:
            if isinstance(entry, dict):
                label = str(entry["label"])
                cut = entry["cut"]
                outers = entry["enclosed_by"]
                value = float(entry["enc"])
            else:
                label, cut, outers, value = (
                    str(entry[0]), entry[1], entry[2], float(entry[3]))
        except (TypeError, ValueError, IndexError, KeyError):
            raise ValueError(
                f"pdk {name!r}: {where} must be {{label, cut, enclosed_by, enc}} "
                f"or [label, cut, [outers], enc], got {entry!r}"
            ) from None
        enclosure.append((
            label,
            resolve(cut, where=where),
            [resolve(o, where=where) for o in outers],
            value,
        ))
    return {"rules": rules, "enclosure": enclosure, "layers": drc_layers}


def _parse_oa_layers(raw: Any, *, name: str) -> dict[Layer, str] | None:
    """``oa_layers``: ``{"L/DT": "oa_layer_name"}`` for the SKILL export.

    Keys are literal ``layer/datatype`` pairs (the same spelling
    ``drc.rules`` accepts inline); the OA purpose is always ``drawing``.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"pdk {name!r}: 'oa_layers' must be an object")
    out: dict[Layer, str] = {}
    for key, value in raw.items():
        m = re.fullmatch(r"(\d+)\s*[/,]\s*(\d+)", str(key))
        if not m:
            raise ValueError(
                f"pdk {name!r}: oa_layers key {key!r} must be a literal "
                "'L/DT' pair (e.g. \"65/20\")"
            )
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"pdk {name!r}: oa_layers[{key!r}] must be a non-empty OA "
                f"layer name, got {value!r}"
            )
        out[(int(m.group(1)), int(m.group(2)))] = value.strip()
    return out


def _layer_name_map(pdk: PDK) -> dict[Layer, str]:
    """Reverse map (layer, datatype) -> preferred name for serialising."""
    names = {pair: lname for lname, pair in pdk.layers.items()}
    for role, pair in (pdk.extract or {}).get("roles", {}).items():
        if pair is not None:
            names.setdefault(pair, role)
    for lname, pair in (pdk.drc or {}).get("layers", {}).items():
        names.setdefault(pair, lname)
    return names


_REGISTRY: dict[str, PDK] = {}

# Subscribers fired by ``register_pdk``/``_ensure_external`` so block
# generators can track the registry without the pdk layer importing the
# blocks layer (that would be an import cycle *and* would pull gdsfactory
# into every descriptor load). Listeners receive the PDK object / name
# directly and must not call back into ``get_pdk``/``all_pdks`` — those
# can re-enter ``_ensure_external`` mid-scan.
_PDK_LISTENERS: list[Callable[["PDK"], None]] = []
_PDK_REMOVED_LISTENERS: list[Callable[[str], None]] = []


def add_pdk_listener(cb: Callable[["PDK"], None]) -> None:
    """Subscribe ``cb(pdk)`` — fired on every ``register_pdk`` call."""
    _PDK_LISTENERS.append(cb)


def add_pdk_removed_listener(cb: Callable[[str], None]) -> None:
    """Subscribe ``cb(name)`` — fired when an external PDK leaves the registry."""
    _PDK_REMOVED_LISTENERS.append(cb)


def register_pdk(pdk: PDK) -> PDK:
    _REGISTRY[pdk.name] = pdk
    # Always notify, even on re-registration: a replaced descriptor must
    # rebuild whatever was generated from the old one.
    for cb in list(_PDK_LISTENERS):
        cb(pdk)
    return pdk


def get_pdk(name: str) -> PDK:
    _ensure_external()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown pdk {name!r}; known: {sorted(_REGISTRY)}") from None


def all_pdks() -> dict[str, PDK]:
    _ensure_external()
    return dict(_REGISTRY)


# --- External descriptor loading -----------------------------------------
# Two env hooks, both optional and local-only:
#   LAYOUT_CANVAS_PDK_DIR — a private directory scanned for *.json
#   LAYOUT_CANVAS_PDKS    — os.pathsep-separated list of descriptor files
# Loading is lazy (first get_pdk/all_pdks call), idempotent, and re-runs
# when the env spec changes (tests monkeypatch freely). Per-file failures
# land in _LOAD_ERRORS — visible via probe_environment — and never abort
# the scan or block the built-ins.

_EXTERNAL_NAMES: set[str] = set()
_EXTERNAL_SOURCES: dict[str, str] = {}  # pdk name -> file it came from
_LOAD_ERRORS: dict[str, str] = {}
_BUILTIN: set[str] = set()
_LOADED_SPEC: tuple[str | None, str | None] | object = object()

_PDK_DIR_ENV = "LAYOUT_CANVAS_PDK_DIR"
_PDK_LIST_ENV = "LAYOUT_CANVAS_PDKS"


def _env_spec() -> tuple[str | None, str | None]:
    return (os.environ.get(_PDK_DIR_ENV), os.environ.get(_PDK_LIST_ENV))


def _external_files() -> list[Path]:
    files: list[Path] = []
    pdk_dir = os.environ.get(_PDK_DIR_ENV)
    if pdk_dir:
        directory = Path(pdk_dir)
        if directory.is_dir():
            files.extend(sorted(directory.glob("*.json")))
        else:
            _LOAD_ERRORS[str(directory)] = (
                f"{_PDK_DIR_ENV} is not a directory"
            )
    listed = os.environ.get(_PDK_LIST_ENV)
    if listed:
        for item in listed.split(os.pathsep):
            item = item.strip()
            if item:
                files.append(Path(item))
    return files


def load_external_pdks() -> dict[str, str]:
    """Scan the PDK env hooks and register every valid descriptor found.

    Returns ``{file: error_message}`` for files that failed — a malformed
    or non-JSON file is a diagnostic, never a fatal error. Names already
    taken (built-ins or an earlier external file) are refused, so a
    descriptor can never silently shadow a verified layer map.
    """
    _LOAD_ERRORS.clear()
    for path in _external_files():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pdk = PDK.from_dict(data, base_dir=path.parent, source=str(path))
        except Exception as exc:
            _LOAD_ERRORS[str(path)] = str(exc)
            continue
        if pdk.name in _BUILTIN:
            _LOAD_ERRORS[str(path)] = (
                f"pdk name {pdk.name!r} is reserved (built-in); "
                "rename the descriptor"
            )
            continue
        prior = _EXTERNAL_SOURCES.get(pdk.name)
        if prior is not None and prior != str(path):
            _LOAD_ERRORS[str(path)] = (
                f"pdk name {pdk.name!r} is already loaded from {prior}; "
                "keeping the first descriptor"
            )
            continue
        register_pdk(pdk)
        _EXTERNAL_NAMES.add(pdk.name)
        _EXTERNAL_SOURCES[pdk.name] = str(path)
    return dict(_LOAD_ERRORS)


def _ensure_external() -> None:
    """Run the external scan once per env spec; idempotent thereafter."""
    global _LOADED_SPEC
    spec = _env_spec()
    if spec == _LOADED_SPEC:
        return
    # Env moved on (or first call): drop previous externals and rescan.
    for name in _EXTERNAL_NAMES:
        if name in _REGISTRY:
            _REGISTRY.pop(name, None)
            for cb in list(_PDK_REMOVED_LISTENERS):
                cb(name)
    _EXTERNAL_NAMES.clear()
    _EXTERNAL_SOURCES.clear()
    load_external_pdks()
    _LOADED_SPEC = spec


def external_pdks() -> dict[str, PDK]:
    """PDKs loaded from the env hooks (never the built-ins)."""
    _ensure_external()
    return {n: _REGISTRY[n] for n in sorted(_EXTERNAL_NAMES) if n in _REGISTRY}


def builtin_pdk_names() -> frozenset[str]:
    """Names reserved by the code-defined built-ins (never descriptor-driven)."""
    return frozenset(_BUILTIN)


def external_pdk_errors() -> dict[str, str]:
    """``{file: error}`` diagnostics from the most recent external scan."""
    _ensure_external()
    return dict(_LOAD_ERRORS)


def _build_sky130() -> PDK:
    return PDK(
        name="sky130",
        layers={
            "diff": (65, 20),
            "tap": (65, 44),
            "nwell": (64, 20),
            "poly": (66, 20),
            "licon": (66, 44),
            "li1": (67, 20),
            "mcon": (67, 44),
            "met1": (68, 20),
            "via1": (68, 44),
            "met2": (69, 20),
            "via2": (69, 44),
            "met3": (70, 20),
            "via3": (70, 44),
            "met4": (71, 20),
            "via4": (71, 44),
            "met5": (72, 20),
            "nsdm": (93, 44),
            "psdm": (94, 20),
        },
        rules={
            "min_instance_margin_um": 0.5,
            "default_routing_layer": "met2",
            "drc_deck": "sky130A.drc",
        },
    )


def _build_ihp_sg13g2() -> PDK:
    # Layer map verified against libs.tech/klayout/tech/sg13g2.lyt symbols.
    return PDK(
        name="ihp_sg13g2",
        layers={
            "activ": (1, 0),
            "gatpoly": (5, 0),
            "cont": (6, 0),
            "metal1": (8, 0),
            "via1": (19, 0),
            "metal2": (10, 0),
            "via2": (29, 0),
            "metal3": (30, 0),
            "via3": (49, 0),
            "metal4": (50, 0),
            "via4": (66, 0),
            "metal5": (67, 0),
            "topvia1": (125, 0),
            "topmetal1": (126, 0),
            "topvia2": (133, 0),
            "topmetal2": (134, 0),
            "salblock": (28, 0),
            "nwell": (31, 0),
        },
        pin_purpose=2,
        rules={
            "grid_um": 0.005,
            "default_routing_layer": "metal2",
        },
        model_libs={
            # ngspice corner decks ship inside the open PDK.
            "ngspice_dir": "libs.tech/ngspice/models",
            "mos_corner": "cornerMOSlv.lib",
            "cap_corner": "cornerCAP.lib",
            "res_corner": "cornerRES.lib",
            "dio_corner": "cornerDIO.lib",
            "hbt_corner": "cornerHBT.lib",
        },
    )


register_pdk(_build_sky130())
register_pdk(_build_ihp_sg13g2())
_BUILTIN.update(_REGISTRY)
