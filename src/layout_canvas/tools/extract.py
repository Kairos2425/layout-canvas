"""In-process layout netlist extraction via the KLayout engine (klayout.db).

Uses ``LayoutToNetlist`` + ``DeviceExtractorMOS4Transistor`` — the same
geometry/netlist engine the ``klayout -b -r *.lvs`` application runs, driven
directly through the Python API so no external binary is needed.

Layer connectivity recipes follow the official PDK LVS decks (IHP SG13G2
``rule_decks/*.lvs``) adapted to the layers our generators emit. Extraction is
truthful: if a generated layout has no internal wiring, terminals float on
separate nets — that is a real finding, not a tool error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExtractionResult:
    status: str  # ok | unavailable | error
    netlist_text: str = ""
    devices: int = 0
    nets: int = 0
    circuits: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    netlist_object: Any = None  # klayout.db.Netlist, for LVS comparison
    _owners: tuple = ()  # keeps LayoutToNetlist/Layout alive (netlist depends on them)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "devices": self.devices,
            "nets": self.nets,
            "circuits": self.circuits,
            "errors": self.errors,
            "netlist_text": self.netlist_text,
        }


# Per-PDK extraction recipe. Layers are (gds_layer, datatype).
_RECIPES: dict[str, dict[str, Any]] = {
    "sky130": {
        "well_n": (64, 20), "diff": (65, 20), "tap": (65, 44),
        "poly": (66, 20), "licon": (66, 44), "li1": (67, 20),
        "mcon": (67, 44), "met1": (68, 20), "via1": (68, 44),
        "met2": (69, 20), "via2": (69, 44), "met3": (70, 20),
        "via3": (70, 44), "met4": (71, 20), "via4": (71, 44), "met5": (72, 20),
        "nsdm": (93, 44), "psdm": (94, 20),
        "text_datatypes": (16,),
    },
    "ihp_sg13g2": {
        "well_n": (31, 0), "diff": (1, 0), "tap": None,
        "poly": (5, 0), "licon": (6, 0), "li1": (8, 0),
        "mcon": (19, 0), "met1": (10, 0), "via1": (29, 0),
        "met2": (30, 0), "via2": (49, 0), "met3": (50, 0),
        "via3": (66, 0), "met4": (67, 0), "via4": (125, 0), "met5": (126, 0),
        "nsdm": (7, 0), "psdm": (14, 0),
        "text_datatypes": (2,),
    },
}

# Leaf subcircuit name -> (device class name, polarity) used both by the
# extractor and by the generated LVS reference wrappers.
LEAF_DEVICES: dict[str, dict[str, tuple[str, str]]] = {
    "sky130": {
        "sky130_fd_pr__nfet_01v8": ("nfet_01v8", "nmos"),
        "sky130_fd_pr__pfet_01v8": ("pfet_01v8", "pmos"),
    },
    "ihp_sg13g2": {
        "sg13_lv_nmos": ("sg13_lv_nmos_dev", "nmos"),
        "sg13_lv_pmos": ("sg13_lv_pmos_dev", "pmos"),
    },
}


def lvs_device_wrappers(tech: str) -> str:
    """SPICE device-abstract wrappers for the reference side of LVS.

    Each leaf ``X``-card in a generated netlist resolves to a single MOS4
    device, so the flattened reference matches the extracted device-level
    netlist. These describe connectivity shape only — not electrical models.
    """
    out = []
    for subckt, (model, pol) in LEAF_DEVICES.get(tech, {}).items():
        out.append(
            f".subckt {subckt} d g s b w=1u l=1u nf=1 mult=1\n"
            f"m1 d g s b {model} w='w*nf*mult' l=l\n"
            f".model {model} {pol}\n"
            f".ends {subckt}"
        )
    return "\n".join(out)


def extract_netlist(gds_path: str | Path, tech: str = "sky130") -> ExtractionResult:
    """Extract a device-level netlist from GDS using the KLayout engine."""
    try:
        import klayout.db as db
    except ImportError:
        return ExtractionResult(
            "unavailable", errors=["klayout python module not installed"]
        )
    recipe = _RECIPES.get(tech)
    if recipe is None:
        return ExtractionResult("unavailable", errors=[f"no extraction recipe for tech {tech!r}"])
    gds = Path(gds_path)
    if not gds.is_file():
        return ExtractionResult("unavailable", errors=[f"GDS not found: {gds}"])

    ly = db.Layout()
    ly.read(str(gds))
    tops = list(ly.top_cells())
    if not tops:
        return ExtractionResult("error", errors=["GDS has no top cell"])
    top_name_0 = tops[0].name

    # Canonicalise shape order before extraction: this engine resolves
    # derived-region connectivity (e.g. nwell<->ntap) in insertion order,
    # and GDS writers emit records in arbitrary order — identical layouts
    # written differently produced different nets (observed: a pmos bulk
    # merging into vdd or staying a private net). Re-inserting every
    # cell's shapes sorted by position makes extraction deterministic.
    ly2 = db.Layout()
    ly2.dbu = ly.dbu
    cell_map = {}
    for cell in sorted(ly.each_cell(), key=lambda c: c.name):
        cell_map[cell.cell_index()] = ly2.create_cell(cell.name)
    for cell in sorted(ly.each_cell(), key=lambda c: c.name):
        dst = cell_map[cell.cell_index()]
        for li in sorted(ly.layer_indexes(), key=lambda i: ly.get_info(i).to_s()):
            shapes = [sh for sh in cell.each_shape(li)]
            if not shapes:
                continue
            shapes.sort(key=lambda s: s.bbox().to_s())
            li2 = ly2.layer(ly.get_info(li))
            for sh in shapes:
                dst.shapes(li2).insert(sh)
        insts = sorted(cell.each_inst(),
                       key=lambda i: i.cell_inst.to_s())
        for inst in insts:
            ci = inst.cell_inst
            dst.insert(db.CellInstArray(
                cell_map[ci.cell_index].cell_index(),
                ci.trans, ci.a, ci.b, ci.na, ci.nb))
    ly = ly2
    top = next(c for c in ly.each_cell() if c.name == top_name_0)

    def L(key: str):
        spec = recipe[key]
        return ly.layer(spec[0], spec[1]) if spec else None

    l2n = db.LayoutToNetlist(db.RecursiveShapeIterator(ly, top, []))
    rnwell = l2n.make_layer(L("well_n"), "nwell")
    rdiff = l2n.make_layer(L("diff"), "diff")
    rtap = l2n.make_layer(L("tap"), "tap") if recipe["tap"] else None
    rpoly = l2n.make_layer(L("poly"), "poly")
    layers = {
        k: (l2n.make_layer(L(k), k) if L(k) else None)
        for k in ("licon", "li1", "mcon", "met1", "via1", "met2", "via2",
                  "met3", "via3", "met4", "via4", "met5", "nsdm", "psdm")
    }
    # text layers for pin names (every layer carrying texts with the PDK's
    # label datatype)
    text_layers = []
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        if info.datatype not in recipe["text_datatypes"]:
            continue
        has_text = any(
            any(True for _ in cell.shapes(li).each(db.Shapes.STexts))
            for cell in ly.each_cell()
        )
        if has_text:
            text_layers.append(
                (info, l2n.make_text_layer(li, f"texts_{info.layer}_{info.datatype}"))
            )

    # interconnect stack: diff/tap/poly -> contact -> li1 -> via -> metals.
    # Raw rdiff is NOT connected to contacts: it is one contiguous polygon
    # (one cluster) spanning every S/D segment — connecting it would bridge
    # all segment straps through the rail. Contacts must land on the
    # derived S/D regions (diff minus gate), connected further below.
    l2n.connect(rpoly, layers["licon"])
    # Gate strapping workaround: in this engine, a raw polygon layer chained
    # through a contact layer (poly->licon->li1) does not propagate into
    # derived-region connectivity, while derived regions do (nsd->licon->li1
    # works). Generated blocks always overlap the pad and the li riser at the
    # contact site, so a direct poly<->li link is geometrically equivalent.
    l2n.connect(rpoly, layers["li1"])
    if rtap is not None:
        l2n.connect(rtap, layers["licon"])
    l2n.connect(layers["licon"], layers["li1"])
    l2n.connect(layers["li1"], layers["mcon"])
    l2n.connect(layers["mcon"], layers["met1"])
    # Same-layer merging is NOT implied by pairwise connects in this engine:
    # connect(a,b) only joins touching a/b shape pairs — two touching shapes
    # on the same layer still form separate clusters unless the layer is
    # self-connected (observed: an li1 riser+strap blob, merged() == 1 shape
    # in a Region, produced 3 clusters). Without self-connects every strap
    # only reaches the pins that touch it cross-layer.
    for reg in [
        rpoly, layers["licon"], layers["li1"], layers["mcon"],
        layers["met1"], layers["via1"], layers["met2"], layers["via2"],
        layers["met3"], layers["via3"], layers["met4"], layers["via4"],
        layers["met5"], rtap,
    ]:
        if reg is not None:
            l2n.connect(reg, reg)
    stack = [("met1", "via1"), ("via1", "met2"), ("met2", "via2"),
             ("via2", "met3"), ("met3", "via3"), ("via3", "met4"),
             ("met4", "via4"), ("via4", "met5")]
    for a, b in stack:
        if layers[a] is not None and layers[b] is not None:
            l2n.connect(layers[a], layers[b])
    conductors = [rdiff, rpoly, layers["licon"], layers["li1"], layers["mcon"],
                  layers["met1"], layers["met2"], layers["met3"],
                  layers["met4"], layers["met5"]]
    if rtap is not None:
        conductors.append(rtap)
    # Pin texts live on (drawing_layer, pin_datatype) — a label must name
    # exactly the conductor it is stamped on. Connecting every conductor to
    # every text layer lets a label straddling the met1 pad AND the diff
    # rail merge the pin net into the shared rail (observed as gates shorted
    # to S/D). Map each text layer to the drawing layer of the same number.
    conductor_by_gds = {
        recipe[k]: pl for k, pl in (
            ("diff", rdiff), ("tap", rtap), ("poly", rpoly),
            ("licon", layers["licon"]), ("li1", layers["li1"]),
            ("mcon", layers["mcon"]), ("met1", layers["met1"]),
            ("met2", layers["met2"]), ("met3", layers["met3"]),
            ("met4", layers["met4"]), ("met5", layers["met5"]),
        ) if pl is not None
    }
    for info, tl in text_layers:
        pl = (conductor_by_gds.get((info.layer, 20))  # sky130-style (L,20)
              or conductor_by_gds.get((info.layer, 0)))  # IHP-style (L,0)
        if pl is not None:
            l2n.connect(pl, tl)

    # device derivations (official-deck recipe: active & implant, minus gate)
    # implant masks are optional in our generated blocks — fall back to the
    # diff/well split when they carry no shapes.
    rnsdm = layers["nsdm"] if layers["nsdm"] is not None and layers["nsdm"].count() > 0 else rdiff
    rpsdm = layers["psdm"] if layers["psdm"] is not None and layers["psdm"].count() > 0 else rdiff
    rpactive = rdiff & rnwell & rpsdm
    rpgate = rpactive & rpoly
    rpsd = rpactive - rpgate
    rnactive = (rdiff & rnsdm) - rnwell
    rngate = rnactive & rpoly
    rnsd = rnactive - rngate
    # bulk ties: n-well taps for pmos, p-sub taps for nmos. Techs with a
    # dedicated tap layer use it directly; IHP derives taps from implants:
    # p+ tap = psd-marked diff outside nwell, n+ tap = nsd diff inside
    # nwell. With no implants drawn there is simply no tap — falling back
    # to (rdiff - rnwell) would declare every nmos diffusion a p-tap and
    # short it to the global substrate.
    if rtap is not None:
        rntap = rtap & rnwell
        rptap = rtap - rnwell
    elif layers["psdm"] is not None and layers["psdm"].count() > 0:
        rntap = (rdiff & rnsdm) & rnwell
        rptap = (rdiff & rpsdm) - rnwell
    else:
        rntap = db.Region()
        rptap = db.Region()
    # Shared substrate: without a contiguous bulk region every device gets
    # its own implicit bulk net (nc_1..nc_N), which blocks device
    # combination and mismatches a reference that ties all bulks to vss.
    # A cell-extent p-substrate region declared as a global net gives every
    # nmos the same bulk — matching how real LVS treats the substrate.
    extent = db.DBox()
    for li in ly.layer_indexes():
        extent += db.Region(top.begin_shapes_rec(li)).bbox()
    rpsub = l2n.make_polygon_layer("psub")
    rpsub.insert(db.Box(
        int(round(extent.left * 1000)), int(round(extent.bottom * 1000)),
        int(round(extent.right * 1000)), int(round(extent.top * 1000))))
    l2n.connect_global(rpsub, "vss")
    # p-taps (guard rings, bulk ties) are global too: a physical ring then
    # IS the vss net — connecting a plain cluster to a global net does not
    # merge them (observed as a separate vss$1 pin).
    if rptap is not None:
        l2n.connect_global(rptap, "vss")
    for name, reg in (("psd", rpsd), ("nsd", rnsd),
                      ("ntap_d", rntap), ("ptap_d", rptap)):
        # empty derived regions compare equal and registering two of them
        # under different names fails ("layer already registered")
        if not reg.is_empty():
            l2n.register(reg, name)
    # derived terminal layers join connectivity through the contact stack.
    # Gate regions must NOT be connected: registering a derived region into
    # connectivity splits the parent poly shape into per-region clusters, so
    # every finger's gate would land on its own fragment (observed as gates
    # failing to merge through the poly/li1 strap). Gates are recognised
    # inputs only; their terminal net is taken from tG on the poly layer.
    for reg in (rnsd, rpsd, rptap, rntap):
        l2n.connect(reg, layers["licon"])
    l2n.connect(rnwell, rntap)
    # The well itself must be a connectivity participant even with no taps
    # in it — otherwise every pmos gets a private implicit bulk (nc_1..N)
    # and parallel devices never combine.
    l2n.connect(rnwell, rnwell)

    n_model, p_model = (
        LEAF_DEVICES[tech]["sg13_lv_nmos"][0] if tech == "ihp_sg13g2" else "nfet_01v8",
        LEAF_DEVICES[tech]["sg13_lv_pmos"][0] if tech == "ihp_sg13g2" else "pfet_01v8",
    )
    l2n.extract_devices(
        db.DeviceExtractorMOS4Transistor(p_model),
        {"SD": rpsd, "G": rpgate, "tS": rpsd, "tD": rpsd, "tG": rpoly, "W": rnwell},
    )
    l2n.extract_devices(
        db.DeviceExtractorMOS4Transistor(n_model),
        {"SD": rnsd, "G": rngate, "tS": rnsd, "tD": rnsd, "tG": rpoly, "W": rpsub},
    )
    l2n.extract_netlist()
    nl = l2n.netlist()
    errors = [e.description for e in l2n.each_error()]

    import tempfile
    sp = Path(tempfile.mkdtemp()) / "extracted.cir"
    writer = db.NetlistSpiceWriter()
    writer.use_net_names = True
    nl.write(str(sp), writer)
    return ExtractionResult(
        "ok",
        netlist_text=sp.read_text(),
        devices=sum(len(list(c.each_device())) for c in nl.each_circuit()),
        nets=sum(len(list(c.each_net())) for c in nl.each_circuit()),
        circuits=[c.name for c in nl.each_circuit()],
        errors=errors,
        netlist_object=nl,
        _owners=(l2n, ly),
    )
