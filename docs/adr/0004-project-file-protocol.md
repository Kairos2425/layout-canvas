# ADR 0004: `.lcproj.json` is the versioned canonical project file

Status: accepted · 2026-09-14

## Context

Designs previously moved around as bare Block IR JSON — no version marker,
no migration path, and loaders raised raw exceptions at agents. Analog
Canvas isolates this in `@icm/project-protocol`: one bounded boundary owns
parse/migrate/serialize and reports load diagnostics.

## Decision

- Envelope: `{kind: "lcproj", schema_version: 1, design: {...}}`.
- `protocol.load_project(path_or_text)` → `LoadResult{status, design,
  source_version, diagnostics[]}`; statuses `ok | migrated | error`.
- A bare Block IR document loads as schema v0 with a `migrated` info
  diagnostic — old examples keep working without a flag day.
- A `schema_version` newer than supported is refused with
  `unsupported-version`, never silently truncated.
- Migrations are explicit ordered steps added at the boundary; the document
  is never rewritten silently.

## Consequences

- `save_project`/`load_project` MCP tools and `open_design(path)` all route
  through this boundary; a session opened from a v0 file reports the
  migration in its load diagnostics.
