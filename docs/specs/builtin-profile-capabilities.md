# Spec: Built-in Profile Capabilities (office-word, codebase-memory, anydoc)

- **Status:** approved (2026-10-24)
- **Intent:** `docs/intent/builtin-profile-capabilities.md`
- **Repo:** `super-hermes-agent`

> **Design note (final).** `codebase-memory-mcp` is published on PyPI and is
> runnable through `uvx`. Verified live on this machine:
> `uvx --from codebase-memory-mcp codebase-memory-mcp --version` → `0.12.1`
> after the wrapper verified the upstream checksum, and
> `echo '{}' | uvx --from codebase-memory-mcp codebase-memory-mcp` → a JSON-RPC
> `Parse error` response, proving the stdio server runs.
> Both servers therefore use the identical bare-`uvx` entry shape. There is
> **no** provisioning module, **no** path resolution, and **no** `pm install`
> hook. The work is one pure seed function plus one call site.

## Objective

Every **new** profile created from this fork starts with two MCP servers already
configured, and the third capability (anydoc) already usable, with zero manual
setup per profile or per VM.

The user is the operator of this fork, running many profiles across many VMs.
Success means: create a profile on a fresh VM, start Hermes, and both servers
connect without the operator running a single install command.

Non-goals are explicit: existing profiles are untouched, manual deletion is
respected forever, and nothing re-adds a removed entry.

## Assumptions

1. Both servers launch through a **bare `uvx`**. `_resolve_stdio_command`
   (`tools/mcp_tool_config.py:366-407`) maps bare `uvx` to the PM-managed copy
   via `_managed_launcher`, which calls `pm.ensure("uv")`. A VM with no `uvx`
   on `PATH` therefore still works; a VM where PM cannot provision `uv` fails
   as an honest spawn error and parks the server.
2. `codebase-memory-mcp` is a PyPI distribution whose console entry point
   downloads, checksum-verifies, and caches the native binary, then execs it
   (wrapper source: `codebase_memory_mcp/_cli.py`, `_verify_checksum` at
   `:404-457`, `main` at `:1420`).
3. The `mcp` Python SDK is already in the default install: `pyproject.toml`
   includes `"hermes-agent[mcp]"` in the `all` extra, and `pm install` with no
   arguments syncs `["all"]` (`pm/install.py:787`). No extra work is needed.
4. `scripts/install.sh:1153-1159` skips `hermes setup` when there is no
   terminal, so setup cannot be the provisioning trigger. With bare-`uvx`
   entries there is nothing to provision up front — PM resolves `uv` lazily on
   first use — so this no longer blocks the design.
5. Seeded entries are validated by `validate_mcp_server_entry`
   (`hermes_cli/mcp_security.py:89-135`). Only shell-interpreter commands are
   inspected for egress/persistence; a bare `uvx` with `--from` args passes
   clean.
6. `anydoc` needs no config entry. `firecrawl-anydoc==0.2.4` is a core
   dependency (`pyproject.toml:70`, `uv.lock`) and `read_file` self-heals it
   through the `doc-extract` extra (`tools/read_extract.py:63-89`).

## Commands

```
Test:          scripts/run_tests.sh tests/hermes_cli/test_profiles_builtin_capabilities.py
Full check:    python scripts/check
Manual smoke:  HERMES_HOME=$(mktemp -d) hermes profile create probe --no-alias
               cat "$HERMES_HOME/profiles/probe/config.yaml"
```

## Project Structure

```
hermes_cli/profiles_builtin_capabilities.py            → NEW. Entry table + pure seed function
hermes_cli/profiles_initialization.py                  → one call site in finish_profile_layout
tests/hermes_cli/test_profiles_builtin_capabilities.py → NEW. Seed behaviour, idempotency, validation
```

## Code Style

Follow existing `hermes_cli` conventions: module docstring stating the WHY, lazy
imports only where needed to avoid a cycle, `logger` for warnings, no defensive
wrappers around code that cannot fail.

```python
def seed_builtin_capabilities(config: dict) -> list[str]:
    """Add the built-in MCP servers a new profile starts with. Additive only:
    an entry the profile already carries (or the user removed) is never written.
    Returns the names actually added."""
    added: list[str] = []
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        servers = {}
    for name, entry in BUILTIN_MCP_SERVERS.items():
        if name in servers:
            continue
        servers[name] = copy.deepcopy(entry)
        added.append(name)
    if added:
        config["mcp_servers"] = servers
    return added
```

`copy.deepcopy` matters: `BUILTIN_MCP_SERVERS` is module state, and a later
mutation of the written entry (profile editor, `hermes mcp` tool filters) must
never leak back into the template.

## Contracts

### The single entry point

```python
def seed_builtin_capabilities(config: dict) -> list[str]:
    """Mutate *config* in place; return the names actually added."""
```

- **Pure**: no file I/O, no subprocess, no network, no printing.
- **Additive only**: `if name in servers: continue`.
- **Idempotent**: a second call returns `[]` and leaves the dict unchanged.
- **Never raises** for any dict-shaped input; a non-dict `mcp_servers` is
  replaced with a fresh dict.
- Sets `config["mcp_servers"]` only when something was actually added, so an
  untouched config stays byte-identical.

### Seeded entries

```python
BUILTIN_MCP_SERVERS = {
    "office-word": {
        "command": "uvx",
        "args": ["--from", "office-word-mcp-server", "word_mcp_server"],
    },
    "codebase-memory": {
        "command": "uvx",
        "args": ["--from", "codebase-memory-mcp", "codebase-memory-mcp"],
    },
}
```

No `enabled` key: `mcp_server_enabled` (`tools/mcp_tool_common.py:140-144`)
treats an absent key as on, and writing it would pin a value the user then has
to flip in two places.

### Call site

`finish_profile_layout` (`hermes_cli/profiles_initialization.py`), not
`_bootstrap_profile_dir`. Two reasons, both learned during implementation:

- `_bootstrap_profile_dir` is skipped when `clone_all and source_dir`, so
  `--clone-all` profiles would have gone unseeded.
- `profiles.py` is already over its code-health line budget and may only shrink,
  so new behaviour belongs in the topical sibling (`AGENTS.md`, facade + siblings).

`finish_profile_layout` is the seam `create_profile` calls on every path, and it
runs after the channel strip. The seed sits **before** the config migration, so a
migration keeps the seeded entries.

Shape:

```python
with contextlib.suppress(Exception):  # creation must not fail over this
    from hermes_cli.profiles_builtin_capabilities import seed_profile_config

    seed_profile_config(profile_dir)
```

`seed_profile_config` reads `profile_dir/config.yaml`, calls the pure seeder, and
persists through `atomic_config_write` (`hermes_cli/config.py:2067`) only when the
seeder returned anything. Distinct handling worth naming: a **missing** config is
seeded as `{}`, while a config that exists but is not a mapping is left
byte-identical — that is the operator's file to repair, and `atomic_config_write`
would refuse the write anyway, so touching it would only emit a misleading
"nothing was written" warning during profile creation.

## Failure matrix

| Failure | Behaviour | Operator sees |
|---|---|---|
| VM offline on first server use | Entries are written regardless | `doctor --live` probe fails; server parks and retries every 300 s |
| PM cannot provision `uv` | Entries still written | Server parks with `Hermes-managed uvx is not installed; run hermes pm install uv` |
| `mcp` SDK missing | Entries still written | Existing "requires the 'mcp' Python SDK" error from `_run_stdio` |
| Seeder raises | `contextlib.suppress` | Profile creation succeeds; no entries written |
| `config.yaml` exists but is not a mapping | Left byte-identical, no write attempted | Profile creation succeeds; operator repairs the file |
| User deletes an entry | Never re-added | Entry stays gone |
| Existing profile (pre-change) | Untouched — no backfill | No change |
| Cloned source already has a customised entry | Preserved verbatim | Clone keeps the source's tuning |

## Testing Strategy

Framework: pytest through `scripts/run_tests.sh` (never bare `pytest`). Tests
live in `tests/hermes_cli/`, mirroring the source module name.

Unit tests (`test_profiles_builtin_capabilities.py`):
- Empty config → both names added, returned in order, `mcp_servers` populated.
- Second call returns `[]` and leaves the config equal to the first result.
- An existing `office-word` entry with custom args is preserved verbatim; only
  the missing `codebase-memory` is added.
- `mcp_servers` present but not a dict is replaced, not crashed on.
- Both seeded entries pass `validate_mcp_server_entry` with zero issues.
- `BUILTIN_MCP_SERVERS` cannot be poisoned: mutating the written entry does not
  change the module template.

Profile-level tests (same file, `profile_env` fixture from
`tests/hermes_cli/test_profiles.py`):
- `create_profile("probe", no_alias=True)` writes both entries to
  `probe/config.yaml`.
- `create_profile(..., clone_config=True)` from a source lacking the entries
  also ends with both.
- A source with a customised `office-word` entry clones without that entry
  being rewritten.

Every assertion is on observable config state or return values — no source-text
assertions, no mock-call assertions.

## Boundaries

- **Always:** run `scripts/run_tests.sh` before claiming done; keep the seeder
  pure and I/O-free; deepcopy the template; `logger` at the call site.
- **Ask first:** changing `pm/` or `pyproject.toml`; adding a config key outside
  `mcp_servers`; touching `hermes_cli/setup.py`.
- **Never:** backfill existing profiles; re-add a deleted entry; invoke the
  interactive catalog path (`install_entry`, env prompts, curses checklist) from
  profile creation; hardcode a machine-specific absolute path.

## Success Criteria

1. `hermes profile create <name>` on a clean `HERMES_HOME` produces
   `config.yaml` with `mcp_servers.office-word` and
   `mcp_servers.codebase-memory`, exit 0.
2. Re-running the seeder against that config changes nothing (`[]`).
3. Deleting either entry and running any Hermes command does not restore it.
4. An existing profile created before this change has an unchanged
   `config.yaml`.
5. Both seeded entries pass `validate_mcp_server_entry` with zero issues.
6. A clone from a source lacking the entries ends with both.
7. The test file passes via `scripts/run_tests.sh`.

## Resolved decisions

- **Q1 provisioning trigger:** moot. Bare `uvx` needs no up-front provisioning;
  PM resolves `uv` lazily on first use.
- **Q2 tool surface:** unfiltered. All ~50 `office-word` tools ship. Revisit only
  if the tool surface becomes a measured problem.
- **Q3 naming:** `office-word`, `codebase-memory` as `mcp_servers` keys.
- **anydoc:** no config entry; bundled dependency only.
