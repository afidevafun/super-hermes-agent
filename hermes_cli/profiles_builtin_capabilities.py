"""MCP servers every new profile starts with.

Profiles are independent islands: nothing is inherited from another profile
after creation, so a capability the operator always wants has to be written into
each new profile's config at creation time. Both entries launch through a bare
``uvx``, which ``_resolve_stdio_command`` maps to Hermes's PM-managed copy — a
machine without ``uvx`` on PATH still runs them, and the version follows the
lockfile instead of a path baked into the seed.

Additive only by design: an entry the profile already carries is left alone, so
a clone keeps whatever its source customised and a removal the user made is
never undone.
"""

from __future__ import annotations

import copy
from pathlib import Path

# Bare launcher + ``--from`` so PM resolves the toolchain. The key is the
# ``mcp_servers`` name, which is also the toolset key the model sees.
BUILTIN_MCP_SERVERS: dict[str, dict] = {
    "office-word": {
        "command": "uvx",
        "args": ["--from", "office-word-mcp-server", "word_mcp_server"],
    },
    "codebase-memory": {
        "command": "uvx",
        "args": ["--from", "codebase-memory-mcp", "codebase-memory-mcp"],
    },
}


def seed_builtin_capabilities(config: dict) -> list[str]:
    """Add the built-in MCP servers a new profile starts with.

    Mutates *config* in place and returns the names actually added, so a caller
    can persist only when something changed. Idempotent: a second call on the
    same config returns ``[]``. Never raises — a non-dict ``mcp_servers`` is
    replaced rather than rejected, because a profile must still be creatable
    from a hand-damaged config.
    """
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        servers = {}
    added: list[str] = []
    for name, entry in BUILTIN_MCP_SERVERS.items():
        if name in servers:
            continue
        # Deepcopy: the written entry is later mutated by the profile editor and
        # `hermes mcp` tool filters, which must not corrupt the module template.
        servers[name] = copy.deepcopy(entry)
        added.append(name)
    if added:
        config["mcp_servers"] = servers
    return added


def seed_profile_config(profile_dir: Path) -> list[str]:
    """Seed the built-ins into a profile directory's ``config.yaml``.

    Applies :func:`seed_builtin_capabilities` and persists through
    :func:`hermes_cli.config.atomic_config_write` only when something was
    actually added, so an untouched config is never rewritten. Returns the names
    added. File I/O errors propagate: the caller suppresses them so profile
    creation survives a seed failure.
    """
    from hermes_cli.config import atomic_config_write
    from hermes_cli.profiles import _load_yaml_dict

    config_path = Path(profile_dir) / "config.yaml"
    if config_path.is_file():
        # A config we cannot read as a mapping is the operator's file to repair,
        # not ours to overwrite; `_load_yaml_dict` maps both absent and damaged
        # to None, so the check above keeps them apart.
        config = _load_yaml_dict(config_path)
        if config is None:
            return []
    else:
        config = {}
    added = seed_builtin_capabilities(config)
    if added:
        atomic_config_write(config_path, config)
    return added
