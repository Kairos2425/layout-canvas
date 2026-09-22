# ADR 0009: Deployment architecture — containerized canvas, git-backed gallery

## Status

Accepted (2026-09-22)

## Context

Analog Canvas ships production through a disciplined pipeline: a hardened
simulator container (read-only rootfs, dropped caps, internal network, token
isolated in a gateway process), a single production channel, commit-tagged
images, and post-deploy verification with automatic rollback. Its community
layer is Cloudflare Workers + Durable Objects — real infrastructure we do not
yet have and do not need to reach the same product shape.

## Decision

- **One container** (`deploy/Dockerfile`) serves the web canvas + gallery,
  hardened the same way the analog-canvas executor is: read-only root,
  `cap_drop: ALL`, `no-new-privileges`, unprivileged uid, tmpfs `/tmp`,
  resource limits, healthcheck on `/api/health`.
- **Commit-tagged images**: `layout-canvas:<sha12>`; the served app answers
  its own build via `/api/version` (SOURCE_COMMIT file first, git checkout
  fallback). Verification compares served bytes to the deployed commit.
- **Runtime bindings, not baked config**: gallery path, remote, port, and
  tool paths all come from environment variables. EDA binaries stay out of
  the image — the adapter layer probes and reports `unavailable` fail-closed.
- **The gallery is a git repo**: publish = commit, sync = pull --rebase +
  push. Co-building needs nothing but a shared remote (URL, LAN share, or
  bare directory). A future hosted backend is a sync target, not a rewrite.
- **PWA shell**: `manifest.json` + a minimal service worker make the canvas
  installable and offline-tolerant for the app shell; API calls always hit
  the network.

## Consequences

- Community upload works today with zero cloud spend — two instances sharing
  any git remote exchange published designs (tested with a bare repo).
- The hosted-product path (accounts, moderation, search) is a real future
  milestone, deliberately deferred rather than faked.
- Docker builds need PyPI reachability; air-gapped deploys vendor wheels.
