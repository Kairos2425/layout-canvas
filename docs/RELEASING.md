# Releasing layout-canvas

One-time: create the PyPI project by uploading the first release (PyPI
allows first-upload project creation; use a scoped API token afterwards).

## Checklist

1. Version bump in `pyproject.toml` **and** `src/layout_canvas/__init__.py`.
2. Full suite green with a real simulator:

   ```powershell
   $env:LAYOUT_CANVAS_NGSPICE = "<path to ngspice_con.exe>"
   .venv\Scripts\python.exe -m pytest tests -q
   ```

3. README test/tool counts, `docs/USAGE.md`, `skills/layout-canvas/SKILL.md`
   and `docs/index.html` in sync with reality.
4. Tag: `git tag -a v<version> -m "v<version>"` and push with the branch.

## Build + verify

```bash
python -m pip install build twine
python -m build              # produces dist/layout_canvas-<ver>.{tar.gz,whl}
python -m twine check dist/* # metadata validation — must print PASSED
```

Wheel contents worth eyeballing once per release: `dist-info/licenses/`
must contain `LICENSE` **and** `NOTICE` (the name-reservation notice ships
with the package).

## Publish

```bash
# staging check first (optional but recommended on first release)
python -m twine upload --repository testpypi dist/*
pip install -i https://test.pypi.org/simple/ --extra-index-url \
    https://pypi.org/simple/ layout-canvas==<ver>

# real upload
python -m twine upload dist/*    # needs a PyPI API token
pip install layout-canvas==<ver> # smoke: fresh install resolves + runs
layout-canvas --help
```

Credentials live in `~/.pypirc` or `TWINE_USERNAME`/`TWINE_PASSWORD`
(`__token__` + the API token). Never commit tokens.

## After publishing

- Verify `https://pypi.org/project/layout-canvas/` renders the README and
  shows `License: MIT` (the `License-Expression: MIT` metadata).
- Bump `version` to the next `-dev`-ish value if you keep a dev cycle, or
  leave it until the next release PR — either is fine, just be consistent.
