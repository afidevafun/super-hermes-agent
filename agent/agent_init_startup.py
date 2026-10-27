"""Plugin features that are provisioned before tool discovery: one call site for agent init.

Two catalog plugins have to be installed before discovery so their tools land in the agent's
snapshot for the same turn they are installed in:

* a feature that left core (Home Assistant, Camofox) for a home that used it
  (``left_core_migration.recover_at_startup``);
* the plugins every profile selects at creation (``builtin_plugins_ensure.recover_at_startup``).

The built-in seed runs first: it queues its notices for the home's first agent, and the left-core
hook below drains them when it has a sink — so a single call orders both and delivers both.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def recover_startup_plugins(agent: Any) -> None:
    """Install what this home's config selects, before ``_load_tools`` discovers plugins."""
    say = getattr(agent, "_emit_startup_warning", None)
    try:
        from hermes_cli.builtin_plugins_ensure import recover_at_startup as ensure_builtin_plugins
        ensure_builtin_plugins(say=say)
    except Exception as exc:  # health: allow BLE001 -- plugin provisioning must never take agent start down
        _note(logger, "Built-in plugin install failed during agent setup", exc)
    try:
        from hermes_cli.left_core_migration import recover_at_startup
        recover_at_startup(say=say)
    except Exception as exc:  # health: allow BLE001 -- plugin provisioning must never take agent start down
        _note(logger, "Left-core plugin install failed during agent setup", exc)


def _note(log: logging.Logger, message: str, exc: BaseException) -> None:
    log.warning("%s: %s", message, exc, exc_info=True)
