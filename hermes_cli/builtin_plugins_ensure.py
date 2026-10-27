"""Install the catalog plugins every new profile selects, once that home is used.

`profiles_builtin_capabilities` writes the selection at profile creation, offline. This module
turns that selection into an installed tree, at the catalog's reviewed pin, when the home is
first used:

* ``hermes update`` installs them for every profile home sharing the venv (grouped dependency
  answers, like the memory and left-core migrations).
* Agent start installs them for the active home, before plugin discovery, so the plugin's tools
  are in that agent's snapshot. Desktop users update through the app and never run `hermes update`.

Only homes carrying the seed marker are touched, so profiles created before the seed (and any
profile the user assembles by hand) are never backfilled. Within such a home a name is installed
at most once: it must still be in ``plugins.enabled`` (a `hermes plugins remove` drops it, and the
deny-list wins) and must not be in the installed marker, which is written only after the tree
exists — so a failed install retries, while a removal afterwards sticks.

Unattended dependency consent follows the memory-provider precedent: the name is in this home's
own ``plugins.enabled`` and ``security.allow_lazy_installs`` is on, which is the policy that lets
Hermes provision on demand for this home. With a terminal the user is still asked. Failures never
raise: they reach the user as a notice naming the exact install command.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from hermes_cli.left_core_migration import (
    _first_cause, _install_into, _read_config, defer_startup_notice, plugin_present,
)
from hermes_cli.memory_provider_migration import (
    _failed_recently, _home_consent, _home_label, _install_command, _interactive,
    _note_failure, _note_success,
)
from hermes_cli.profiles_builtin_capabilities import (
    BUILTIN_PLUGINS, INSTALLED_KEY, SEEDED_KEY,
)

logger = logging.getLogger(__name__)


def _selected(config: dict, name: str) -> bool:
    """*name* is in ``plugins.enabled`` and not in ``plugins.disabled`` (which wins, as in the loader)."""
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return False
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list) or name not in {str(n) for n in enabled}:
        return False
    disabled = plugins.get("disabled")
    return name not in {str(n) for n in disabled} if isinstance(disabled, list) else True


def _installed(config: dict) -> set[str]:
    done = config.get(INSTALLED_KEY)
    return {str(n) for n in done} if isinstance(done, list) else set()


def _record_installed(home: Path, name: str) -> None:
    """Add *name* to the home's installed marker in one additive config write. Raises on a broken
    config.yaml; callers treat that as "the next run records it then"."""
    from hermes_cli.config import atomic_config_write, read_user_config_raw

    path = home / "config.yaml"
    config = read_user_config_raw(path)
    config[INSTALLED_KEY] = sorted(_installed(config) | {name})
    atomic_config_write(path, config)


def pending(home: Path) -> list[str]:
    """Built-in plugins *home* selects, still lacks and has never had installed. Read-only."""
    config = _read_config(home)
    if config.get(SEEDED_KEY) is not True:
        return []  # not a home this feature seeded: never backfill
    done = _installed(config)
    out: list[str] = []
    for name in BUILTIN_PLUGINS:
        if name in done or not _selected(config, name) or plugin_present(name, home):
            continue
        out.append(name)
    return out


def ensure_home(home: Path, *, install: Callable[[str], dict], say: Callable[[str], None] = print,
                backoff: bool = False) -> list[str]:
    """Install every pending built-in plugin for *home*. Returns the names installed; never raises."""
    installed: list[str] = []
    for name in pending(home):
        if backoff and _failed_recently(home, name):
            logger.info("built-in plugin install failed recently for %s; retrying later or on "
                        "`hermes update`", home)
            continue
        try:
            result = install(name)
        except Exception as exc:  # health: allow BLE001 -- network/uv/kill list: reported as a ⚠ notice, agent start must not fail
            result = {"ok": False, "error": str(exc)}
        if result.get("ok"):
            try:
                _record_installed(home, name)
            except Exception as exc:  # health: allow BLE001 -- an unwritable config.yaml leaves the marker for the next run; the tree is already in place
                logger.debug("built-in install marker not written for %s: %s", home, exc)
            _note_success(home, name)
            say(f"  ✓ Installed the '{name}' plugin from the catalog.")
            installed.append(name)
            continue
        _note_failure(home, name)
        error = _first_cause(str(result.get("error") or ""))
        say(f"  ⚠ The '{name}' plugin could not be installed automatically: {error}. "
            f"Run `{_install_command(name, home)}`.")
    return installed


def migrate_all_homes(*, say: Callable[[str], None] = print) -> list[str]:
    """``hermes update`` hook: every profile home sharing this venv. Homes needing the same plugin
    share one environment, so they share one set of dependency answers."""
    from hermes_cli.plugins_cmd_install import shared_dependency_answers
    from pm.plugins_state import dependency_homes

    def labelled(home: Path) -> Callable[[str], None]:
        return lambda message: say(f"  [{_home_label(home)}] {message.lstrip()}")

    pending_by_home: list[tuple[Path, list[str], bool]] = []
    for home in dependency_homes():
        try:
            names = pending(home)
            consent = bool(names) and _home_consent(home)
        except Exception as exc:  # health: allow BLE001 -- one unreadable profile must not stop the other homes' installs
            logger.debug("built-in plugin seed skipped for %s: %s", home, exc)
            continue
        if names:
            pending_by_home.append((home, names, consent))

    installed: list[str] = []
    try:
        for name in BUILTIN_PLUGINS:
            homes = [home for home, names, _ in pending_by_home if name in names]
            if not homes:
                continue
            if len(homes) > 1 and _interactive():
                say(f"  {name} is selected in {len(homes)} profiles "
                    f"({', '.join(_home_label(h) for h in homes)}); your answers to its dependency "
                    f"questions apply to all of them.")
            with shared_dependency_answers():
                for index, home in enumerate(homes):
                    consent = next(c for h, _n, c in pending_by_home if h == home)
                    if ensure_home(home, install=_install_into(home, consent=consent), say=labelled(home),
                                   backoff=False):
                        installed.append(name)
                        continue
                    rest = homes[index + 1:]
                    if rest:
                        say(f"  ⚠ The '{name}' plugin was not installed for "
                            f"{', '.join(_home_label(h) for h in rest)} either. Run "
                            + ", ".join(f"`{_install_command(name, h)}`" for h in rest) + ".")
                    break
    except KeyboardInterrupt:
        say("  ⚠ Built-in plugin install cancelled. Profiles already done keep their plugin; run "
            "`hermes plugins install <name>` (with `-p <profile>`) for the rest.")
    return installed


_attempted: set[str] = set()


def recover_in(home: Path, *, hydrate_secrets: bool = True) -> None:
    """Install *home*'s pending built-ins in *home*'s runtime scope (its config, secrets and
    allow_lazy_installs), before its plugins are discovered. Blocking: call it off the event loop."""
    from gateway.run import _profile_runtime_scope

    with _profile_runtime_scope(Path(home), hydrate_secrets=hydrate_secrets):
        recover_at_startup()


def recover_at_gateway_start() -> None:
    """Gateway start, mirroring the left-core hook: the launch home plus the default root (the
    multiplex primary). A secondary home is served through :func:`recover_in`."""
    from hermes_constants import get_default_hermes_root, get_hermes_home

    recover_in(Path(get_hermes_home()))
    root = Path(get_default_hermes_root())
    if root.resolve() != Path(get_hermes_home()).resolve():
        recover_in(root)



def recover_at_startup(*, say: Optional[Callable[[str], None]] = None) -> list[str]:
    """Agent start hook for the active home: one attempt per process per home. With *say* (an
    agent's startup-warning sink) outcomes are delivered now; without it they are logged and queued
    for the home's first agent. Runs before plugin discovery, so an install lands in this agent's
    tool snapshot. Returns installed plugin names."""
    from agent.safe_worker_policy import worker_config_snapshot
    if worker_config_snapshot() is not None:
        # A frozen safe/ignore-user-config worker never reads the profile's config.yaml and never
        # installs customizations; the owner process does it for the home.
        return []
    from hermes_constants import get_hermes_home, hermes_home_key

    home = Path(get_hermes_home())
    key = hermes_home_key(home)
    if key in _attempted:
        return []
    _attempted.add(key)

    def report(message: str) -> None:
        message = message.strip()
        logger.warning(message)
        if say is None:
            defer_startup_notice(message)
            return
        try:
            say(message)
        except Exception:
            logger.debug("built-in plugin notice failed", exc_info=True)

    try:
        names = pending(home)
        if not names:
            return []
        from pm.install import lazy_installs_allowed
        if not lazy_installs_allowed():
            for name in names:
                report(f"⚠ The '{name}' plugin is selected in this profile but not installed. "
                       f"security.allow_lazy_installs is off, so Hermes did not fetch it: run "
                       f"`{_install_command(name, home)}`.")
            return []
        # Agent init cannot answer a dependency prompt: under the CLI the chat input owns the
        # terminal, so the question hangs at "Initializing agent...". The profile's own selection
        # plus allow_lazy_installs is the consent that governs on-demand provisioning here.
        return ensure_home(home, install=_install_into(home, consent=True), say=report, backoff=True)
    except Exception as exc:  # never take agent start down
        logger.warning("built-in plugin install failed: %s", exc, exc_info=True)
        return []
