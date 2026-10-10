# Releasing layout-canvas

Releases are automated: **push a `v*` tag → GitHub Actions builds,
verifies and publishes to PyPI via OIDC trusted publishing (no stored
secrets), and attaches the wheel to a GitHub Release** —
`.github/workflows/release.yml`.

## One-time setup (5 min, web UI only)

1. **PyPI trusted publisher** — PyPI account → *Publishing* → *add a new
   pending publisher* (works before the project exists; the first workflow
   run creates it):
   - owner `Kairos2425` · repo `layout-canvas` · workflow `release.yml` ·
     environment `pypi`
2. **GitHub environment** — repo → *Settings* → *Environments* → new
   environment named `pypi`. Optional: require a reviewer so publishing
   needs one human click.
3. That's it — **no API token, no repo secrets**. The OIDC token is issued
   per-run and dies with the job.

Then releasing is just:

```bash
git tag -a v0.2.0 -m "v0.2.0" && git push origin v0.2.0
```

## Checklist (before tagging)

1. Version bump in `pyproject.toml` **and** `src/layout_canvas/__init__.py`.
2. Full suite green with a real simulator:

   ```powershell
   $env:LAYOUT_CANVAS_NGSPICE = "<path to ngspice_con.exe>"
   .venv\Scripts\python.exe -m pytest tests -q
   ```

3. README test/tool counts, `docs/USAGE.md`, `skills/layout-canvas/SKILL.md`
   and `docs/index.html` in sync with reality.
4. Tag: `git tag -a v<version> -m "v<version>"` and push with the branch.

## Manual path (fallback)

CI does all of this itself on a tag — the steps below are for a manual
upload or a private index:

```bash
python -m pip install build twine
python -m build              # produces dist/layout_canvas-<ver>.{tar.gz,whl}
python -m twine check dist/* # metadata validation — must print PASSED

# real upload (needs a PyPI API token — CI uses OIDC instead)
python -m twine upload dist/*
pip install layout-canvas==<ver>
layout-canvas schema | head -5   # smoke: fresh install resolves + runs
```

Credentials live in `~/.pypirc` or `TWINE_USERNAME`/`TWINE_PASSWORD`
(`__token__` + the API token). Never commit tokens — that's exactly why
the CI path exists.

## After publishing

- Verify `https://pypi.org/project/layout-canvas/` renders the README and
  shows `License: MIT` (the `License-Expression: MIT` metadata).
- Bump `version` to the next `-dev`-ish value if you keep a dev cycle, or
  leave it until the next release PR — either is fine, just be consistent.
