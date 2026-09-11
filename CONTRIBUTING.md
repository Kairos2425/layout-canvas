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

All contributions to core IR, compiler, and open-source block libraries are licensed under Apache-2.0.
