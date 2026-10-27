"""The built-in plugin seed: a new profile's selected plugins get their catalog tree once, on first
use, and a removal afterwards sticks.

Real config files under temp homes; the install is a stand-in. The seed side (profile creation)
lives in test_profiles_builtin_capabilities.py; this file covers the ensure hook only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hermes_cli.builtin_plugins_ensure as bpe
import hermes_yaml as yaml
from hermes_cli.profiles_builtin_capabilities import BUILTIN_PLUGINS, INSTALLED_KEY, SEEDED_KEY


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(bpe, "_attempted", set())
    managed = tmp_path / "managed"
    managed.mkdir()
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    return tmp_path


def _home(tmp_path: Path, name: str = "home", *, config: dict | None = None) -> Path:
    home = tmp_path / name
    home.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return home


def _seeded_config(**plugins) -> dict:
    return {SEEDED_KEY: True, "plugins": {"enabled": list(BUILTIN_PLUGINS), **plugins}}


def _config(home: Path) -> dict:
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}


def _write_config(home: Path, config: dict) -> None:
    (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")


def _installer(home: Path, calls: list):
    def install(name):
        calls.append(name)
        (home / "plugins" / name).mkdir(parents=True, exist_ok=True)
        return {"ok": True}
    return install


class TestPending:
    def test_seeded_home_without_trees_lists_both(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config())
        assert bpe.pending(home) == list(BUILTIN_PLUGINS)

    def test_marker_absent_means_never_backfilled(self, tmp_path):
        # A profile created before the seed (or assembled by hand) is nobody's to upgrade.
        home = _home(tmp_path, config={"plugins": {"enabled": list(BUILTIN_PLUGINS)}})
        assert bpe.pending(home) == []

    def test_removal_from_enabled_ends_it_for_good(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config())
        config = _config(home)
        config["plugins"]["enabled"] = ["camofox"]  # the user removed advanced-delegation
        _write_config(home, config)
        assert bpe.pending(home) == ["camofox"]

    def test_disabled_wins_over_enabled(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config(disabled=["advanced-delegation"]))
        assert bpe.pending(home) == ["camofox"]

    def test_recorded_install_is_not_pending(self, tmp_path):
        config = _seeded_config()
        config[INSTALLED_KEY] = list(BUILTIN_PLUGINS)
        home = _home(tmp_path, config=config)
        assert bpe.pending(home) == []

    def test_tree_present_is_not_pending(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config())
        for name in BUILTIN_PLUGINS:
            (home / "plugins" / name).mkdir(parents=True)
        assert bpe.pending(home) == []

    def test_config_without_a_mapping_is_ignored(self, tmp_path):
        home = _home(tmp_path)
        (home / "config.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
        assert bpe.pending(home) == []


class TestEnsureHome:
    def test_installs_every_pending_name_and_records_them(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config())
        calls: list[str] = []
        assert bpe.ensure_home(home, install=_installer(home, calls)) == list(BUILTIN_PLUGINS)
        assert calls == list(BUILTIN_PLUGINS)
        assert bpe._installed(_config(home)) == set(BUILTIN_PLUGINS)

    def test_second_run_does_nothing(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config())
        calls: list[str] = []
        bpe.ensure_home(home, install=_installer(home, calls))
        calls.clear()
        assert bpe.ensure_home(home, install=_installer(home, calls)) == []
        assert calls == []

    def test_failed_install_reports_the_command_and_leaves_no_marker(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config())
        said: list[str] = []

        def failing(name):
            return {"ok": False, "error": "fatal: could not read from remote repository"}

        assert bpe.ensure_home(home, install=failing, say=said.append) == []
        assert any("plugins install" in message for message in said)
        assert any("could not read from remote repository" in message for message in said)
        # No marker: the next hook run tries again.
        assert bpe._installed(_config(home)) == set()

    def test_a_failed_install_is_retried_on_the_next_run(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config())

        def failing(name):
            return {"ok": False, "error": "offline"}

        assert bpe.ensure_home(home, install=failing, say=lambda _m: None) == []
        calls: list[str] = []
        assert bpe.ensure_home(home, install=_installer(home, calls)) == list(BUILTIN_PLUGINS)
        assert calls == list(BUILTIN_PLUGINS)

    def test_backoff_skips_a_recent_failure(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config())
        bpe.ensure_home(home, install=lambda name: {"ok": False, "error": "offline"}, say=lambda _m: None)
        calls: list[str] = []
        assert bpe.ensure_home(home, install=_installer(home, calls), backoff=True) == []
        assert calls == []

    def test_removal_after_install_sticks(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config())
        calls: list[str] = []
        assert bpe.ensure_home(home, install=_installer(home, calls)) == list(BUILTIN_PLUGINS)
        config = _config(home)
        config["plugins"]["enabled"] = []
        _write_config(home, config)
        calls.clear()
        assert bpe.ensure_home(home, install=_installer(home, calls)) == []
        assert calls == []

    def test_raising_installer_is_reported_not_propagated(self, tmp_path):
        home = _home(tmp_path, config=_seeded_config())
        said: list[str] = []

        def boom(name):
            raise RuntimeError("uv exploded")

        assert bpe.ensure_home(home, install=boom, say=said.append) == []
        assert any("uv exploded" in message for message in said)


class TestRecoverAtStartup:
    def test_installs_for_the_active_seeded_home(self, tmp_path, monkeypatch):
        home = _home(tmp_path, "active", config=_seeded_config())
        monkeypatch.setenv("HERMES_HOME", str(home))
        # The hermetic test harness disables lazy installs process-wide; this home allows them.
        monkeypatch.setattr("pm.install.lazy_installs_allowed", lambda: True)
        calls: list[str] = []
        monkeypatch.setattr(bpe, "_install_into", lambda home_, *, consent=None: _installer(home, calls))
        assert bpe.recover_at_startup(say=lambda _m: None) == list(BUILTIN_PLUGINS)
        assert calls == list(BUILTIN_PLUGINS)
        assert bpe.pending(home) == []

    def test_a_profile_without_the_seed_is_never_touched(self, tmp_path, monkeypatch):
        home = _home(tmp_path, "home", config={"plugins": {"enabled": ["camofox"]}})
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr(bpe, "_install_into", lambda home_, *, consent=None: pytest.fail("installed"))
        assert bpe.recover_at_startup(say=lambda _m: None) == []

    def test_lazy_installs_off_reports_instead_of_installing(self, tmp_path, monkeypatch):
        home = _home(tmp_path, "home", config=_seeded_config())
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr("pm.install.lazy_installs_allowed", lambda: False)
        monkeypatch.setattr(bpe, "_install_into", lambda home_, *, consent=None: pytest.fail("installed"))
        said: list[str] = []
        assert bpe.recover_at_startup(say=said.append) == []
        assert any("allow_lazy_installs" in message for message in said)

    def test_one_attempt_per_process_per_home(self, tmp_path, monkeypatch):
        home = _home(tmp_path, "home", config=_seeded_config())
        monkeypatch.setenv("HERMES_HOME", str(home))
        calls: list[str] = []
        monkeypatch.setattr(bpe, "_install_into", lambda home_, *, consent=None: _installer(home, calls))
        bpe.recover_at_startup(say=lambda _m: None)
        calls.clear()
        assert bpe.recover_at_startup(say=lambda _m: None) == []
        assert calls == []
