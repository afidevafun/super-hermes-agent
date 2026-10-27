"""Capabilities every new profile starts with.

Profiles are independent islands: nothing is inherited from another profile
after creation, so a capability the operator always wants has to be written into
each new profile's config at creation time. The MCP entries launch through a
bare ``uvx``, which ``_resolve_stdio_command`` maps to Hermes's PM-managed copy —
a machine without ``uvx`` on PATH still runs them, and the version follows the
lockfile instead of a path baked into the seed.

The plugin entries are selection only: profile creation stays offline, and
``builtin_plugins_ensure`` installs the catalog tree at its reviewed pin the
first time the home is used. A selected name whose tree is absent is inert —
plugin discovery only gates directories that exist, and PM's dependency union
skips a member dir that is gone — so a new profile is fully usable before the
install lands.

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

# Catalog plugins every new profile selects. ``builtin_plugins_ensure`` reads the
# same table to install each one's tree on first use, so the two never disagree
# about what a new profile is supposed to have. Order is the seed order.
BUILTIN_PLUGINS: tuple[str, ...] = ("camofox", "advanced-delegation")

# config.yaml marker set by the seed: this home was created with the table above,
# so the ensure hook may install its entries. A pre-existing profile has no
# marker and is never backfilled.
SEEDED_KEY = "_builtin_plugins_seeded"
# config.yaml marker: names whose tree the ensure hook already installed (or found
# already installed). Written only once the plugin dir exists, so a failed install
# keeps retrying while a later `hermes plugins remove` still sticks.
INSTALLED_KEY = "_builtin_plugins_installed"


def seed_builtin_plugins(config: dict) -> list[str]:
    """Select :data:`BUILTIN_PLUGINS` in a new profile's config, and mark the home.

    Mutates *config* in place and returns the names actually added. Idempotent:
    a second call returns ``[]``. Fills ``plugins.enabled`` only — ``plugins.disabled``
    is left alone, so a name a clone's source disabled stays disabled (the loader
    lets the deny-list win, and re-enabling it here would silently undo the
    operator's choice). Writes no other key: in particular ``delegation.backend``
    stays unset, because a backend the plugins are not registered for yet fails
    ``delegate_task`` closed instead of falling back to the default route.

    Never raises — a non-dict ``plugins`` is replaced rather than rejected, because
    a profile must still be creatable from a hand-damaged config.
    """
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
    enabled = plugins.get("enabled")
    enabled = list(enabled) if isinstance(enabled, list) else []
    disabled = plugins.get("disabled")
    disabled = set(map(str, disabled)) if isinstance(disabled, list) else set()

    added: list[str] = []
    for name in BUILTIN_PLUGINS:
        if name in enabled or name in disabled:
            continue
        enabled.append(name)
        added.append(name)
    if added:
        plugins["enabled"] = enabled
        config["plugins"] = plugins
    # The marker is what lets the ensure hook touch this home at all, so a clone
    # whose source already selected both names still records it here.
    config[SEEDED_KEY] = True
    return added


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

    Applies :func:`seed_builtin_capabilities` and :func:`seed_builtin_plugins`
    and persists through :func:`hermes_cli.config.atomic_config_write` only when
    something changed, so an untouched config is never rewritten. Returns the
    names added across both tables. File I/O errors propagate: the caller
    suppresses them so profile creation survives a seed failure.
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
    was_seeded = config.get(SEEDED_KEY) is True
    added = seed_builtin_capabilities(config)
    added += seed_builtin_plugins(config)
    # A clone whose source already selected both plugins adds no names but still
    # needs the marker: it is what lets the ensure hook install for this home.
    if added or not was_seeded:
        atomic_config_write(config_path, config)
    return added
