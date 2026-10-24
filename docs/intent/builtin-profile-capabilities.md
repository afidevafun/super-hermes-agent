# Built-in Profile Capabilities — Statement of Intent

- **Status:** confirmed (2026-10-24)
- **Scope:** fork `super-hermes-agent` (Hermes agent)

## Outcome

Every new profile created from this fork (fresh and `--clone`, including the
first profile on a fresh VM install) automatically ships with two MCP servers
in `mcp_servers`:

- `office-word` — `office-word-mcp-server` via bare `uvx`
  (`uvx --from office-word-mcp-server word_mcp_server`), resolved to the
  PM-managed copy by `_resolve_stdio_command`. No VM-owned `uvx` needed.
- `codebase-memory` — `codebase-memory-mcp` native binary (bare invocation
  is a stdio MCP server). One shared seeding function writes the entry;
  `hermes setup` guarantees the binary exists once per VM (Option C: run
  upstream install non-interactively to a conventional location, e.g. via
  the `codebase-memory-mcp` PyPI wrapper). No new PM package or lock entry.

`anydoc` (firecrawl/anydoc) gets **no config entry**: there is no MCP server
in that repo (CLI + libs + Agent Skill only). `firecrawl-anydoc` is already
bundled as a Python dependency and `read_file` consumes it via the
`doc-extract` extra self-heal. The work only ensures that dependency stays
present; nothing is seeded for it.

Seeding happens through **one centralized seed function** called from
`_bootstrap_profile_dir` (`hermes_cli/profiles.py`), covering both the fresh
and clone paths.

## User

Operator of this fork, managing many profiles across many VMs.

## Why now

Manual per-profile, per-VM setup of the same three capabilities is repeated
toil on every new profile and every new machine.

## Success

- `hermes profile create` always succeeds, including offline: write config
  (declare), resolve/install dependencies lazily (self-heal on first use).
- In the normal flow both servers are available with zero manual steps.
- A failed install degrades gracefully: the profile is still created, the
  failing item is skipped, and the failure is visible via existing server
  status and `doctor --live` (which already probes all configured servers).
- No new `doctor` check is added.

## Constraint

- Seed-once, additive-only, idempotent: never overwrite an existing entry;
  a manual removal is respected and never restored.
- Existing profiles are untouched (no backfill).
- Reuse existing mechanisms (PM-managed launchers, PM lazy-install policy,
  existing doctor probes) — no new layer.
- Non-interactive only: never use the interactive catalog install path
  (env prompts, curses checklist) inside profile creation.
- Seeded entries must pass `validate_mcp_server_entry` (bare `uvx` and an
  absolute binary path are safe; shell-interpreter wrappers are not).

## Out of scope

- Backfilling old profiles.
- Auto-restore after manual deletion.
- A new PM package or lock entry for the codebase-memory binary.
- Bundling or vendoring anydoc as an MCP server.

## Defaults adopted

- Server names in `mcp_servers`: `office-word`, `codebase-memory`.
- The seeder also ensures the `mcp` Python SDK extra is available (without
  it both servers silently fail to start).
- No new doctor check; rely on `doctor --live`.
