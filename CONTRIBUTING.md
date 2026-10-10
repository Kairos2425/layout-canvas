# Contributing to Layout Canvas

## Development Setup

```bash
git clone https://github.com/Kairos2425/layout-canvas.git
cd layout-canvas
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
ruff check src tests
```

## Adding a New Block

1. Create `src/layout_canvas/blocks/{pdk}/{block_name}.py`
2. Define `BlockSpec` with params, ports, constraints
3. Implement build function (returns `gf.Component`)
4. Optionally implement netlist emitter
5. Register with `@register(spec, netlist=...)`
6. Import in `src/layout_canvas/blocks/{pdk}/__init__.py`

Example:

```python
from layout_canvas.blocks.base import register
from layout_canvas.ir.model import BlockSpec, ParamSpec, PortSpec

spec = BlockSpec(
    name="sky130.my_block",
    pdk="sky130",
    level="L1",
    summary="My parametric block",
    params=[
        ParamSpec(name="width", type="float", default=1.0, unit="um", min=0.5, max=10.0),
    ],
    ports=[
        PortSpec(name="in", layer="met1", direction="input"),
    ],
)

@register(spec)
def _build(width: float) -> gf.Component:
    c = gf.Component()
    # ... add geometry
    return c
```

## Code Style

- Line length: 100
- Imports: sorted with `ruff`
- Type hints: required for public APIs
- Docstrings: Google style for public functions

## License

All contributions are licensed under MIT — see `LICENSE`. Bundled Sky130 model
files under `examples/models/sky130/` remain Apache-2.0 (upstream SkyWater),
and `NOTICE` reserves the "Layout Canvas" name.

By contributing you certify the [Developer Certificate of Origin](https://developercertificate.org/)
(DCO 1.1): you wrote the change or have the right to submit it under MIT, and
you understand it becomes part of the open core. If you can, add
`Signed-off-by: Name <email>` to the commit; either way, opening a PR
constitutes the certification.
