# Deployment

Layout Canvas follows the analog-canvas deployment discipline adapted to this
stack: one production channel, release-gated deploys, verify-then-rollback,
and environment bindings injected at runtime rather than baked into images.

## Channels

- **Public site** (static promo + docs entry, `docs/index.html`): served from
  two mirrors of the same source —
  - GitHub Pages: https://kairos2425.github.io/layout-canvas/ (auto on push to `main`)
  - Cloudflare Workers static assets: https://layout-canvas.79402635.workers.dev
    — `npx wrangler deploy -c deploy/cloudflare/wrangler.toml` (needs a
    wrangler login with `workers:write`; `docs/.assetsignore` keeps markdown
    out of the bundle). No server code runs there — the app itself stays
    local-first.
- **Production** (the app): the only hosted channel. Served by the container built from
  `deploy/Dockerfile` behind `deploy/compose.yaml`. Local commits never reach
  it — a release is a deliberate act.
- **Local**: `layout-canvas web` on a developer machine. The same code path
  serves both; the difference is the runtime bindings.

## Release flow

1. Tag the image with the source commit: `layout-canvas:$(git rev-parse
   --short=12 HEAD)`. `deploy.sh` (or the CI step) writes `SOURCE_COMMIT`
   into the image context so `/api/version` reports the exact bytes served.
2. `docker compose -f deploy/compose.yaml up -d` builds and replaces the
   container. The previous image tag stays in the local image cache — the
   rollback target is the previous commit tag.
3. Verify **before** calling the release done:
   - `curl http://<host>:8080/api/health` → `{"status":"ok"}`
   - `curl http://<host>:8080/api/version` → the deployed commit
   - `curl http://<host>:8080/` → the served entry page
   - `curl -X POST http://<host>:8080/api/sample` → sample IR
   - one real `preview` call on the sample IR (compile path exercised)
4. Rollback: redeploy the previous image tag, then verify the same list.
   Rolling back the container does not roll back gallery data — the gallery
   volume has its own history (it is a git repo; `git revert` inside it if a
   bad publish must be undone).

## Runtime bindings (never baked into the image)

| Variable | Purpose |
|---|---|
| `LAYOUT_CANVAS_GALLERY` | Gallery repo path (default `/data/gallery`, a named volume) |
| `LAYOUT_CANVAS_GALLERY_REMOTE` | Git remote for community sync — GitHub URL, LAN share, or bare dir |
| `LAYOUT_CANVAS_PORT` | Host-side port mapping (default 8080) |
| `LAYOUT_CANVAS_NGSPICE` | Explicit simulator path inside the container, if installed |

## Hardening

The container runs read-only with all capabilities dropped, an unprivileged
uid, a private tmpfs `/tmp`, and resource limits — the same posture
analog-canvas uses for its simulator executor, applied to the whole canvas
service. External EDA binaries (ngspice/klayout/netgen) are *not* in the
image: the tool adapters probe at runtime and report `unavailable`
fail-closed. A licensed-tool deployment mounts or extends the image with
those binaries; this repository never ships PDK bytes or credentials.

## Community gallery

The gallery is a git repository (auto-initialized when git is available).
Every publish is a commit; `gallery/sync` is a real `pull --rebase` + `push`.
Co-building requires only a shared git remote — set
`LAYOUT_CANVAS_GALLERY_REMOTE` and the service syncs at startup, or press
**Sync** in the UI. A managed multi-user backend (auth, moderation, hosted
remote) is a future layer on top of this protocol, not a rewrite of it.

## Known limitation

`docker build` requires PyPI reachability for `pip install`. Air-gapped
deployments pre-build the wheel on a networked machine and install it with
`pip install --no-index --find-links`, or vendor wheels into the build
context.
