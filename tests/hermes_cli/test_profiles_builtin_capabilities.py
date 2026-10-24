"""Tests for hermes_cli.profiles_builtin_capabilities.seed_builtin_capabilities.

Everything here asserts observable config state or return values: the seeded
entries present, the return list, the untouched custom entry. Profile-creation
coverage lives in TestCreateProfileBuiltinCapabilities below.
"""

import copy
from pathlib import Path

import hermes_yaml as yaml
import pytest

from hermes_cli.mcp_security import validate_mcp_server_entry
from hermes_cli.profiles import create_profile
from hermes_cli.profiles_builtin_capabilities import (
    BUILTIN_MCP_SERVERS,
    seed_builtin_capabilities,
)


class TestSeedEmptyConfig:
    def test_seed_adds_both_servers(self):
        config: dict = {}
        added = seed_builtin_capabilities(config)
        assert added == ["office-word", "codebase-memory"]
        assert config["mcp_servers"]["office-word"] == BUILTIN_MCP_SERVERS["office-word"]
        assert config["mcp_servers"]["codebase-memory"] == BUILTIN_MCP_SERVERS["codebase-memory"]

    def test_seed_is_idempotent(self):
        config: dict = {}
        first = seed_builtin_capabilities(config)
        snapshot = copy.deepcopy(config)
        second = seed_builtin_capabilities(config)
        assert second == []
        assert config == snapshot
        assert first == ["office-word", "codebase-memory"]

    def test_non_dict_mcp_servers_replaced(self):
        config: dict = {"mcp_servers": ["not", "a", "dict"]}
        added = seed_builtin_capabilities(config)
        assert added == ["office-word", "codebase-memory"]
        assert isinstance(config["mcp_servers"], dict)
        assert set(config["mcp_servers"]) == {"office-word", "codebase-memory"}

    def test_other_config_keys_untouched(self):
        config: dict = {"model": {"default": "x"}, "mcp_servers": {}}
        seed_builtin_capabilities(config)
        assert config["model"] == {"default": "x"}


class TestSeedAdditiveOnly:
    def test_existing_custom_entry_preserved_verbatim(self):
        custom = {
            "command": "uvx",
            "args": ["--from", "office-word-mcp-server", "word_mcp_server", "--debug"],
            "enabled": False,
        }
        config: dict = {"mcp_servers": {"office-word": dict(custom)}}
        added = seed_builtin_capabilities(config)
        assert added == ["codebase-memory"]
        assert config["mcp_servers"]["office-word"] == custom
        assert "codebase-memory" in config["mcp_servers"]

    def test_both_present_adds_nothing_and_writes_nothing(self):
        config: dict = {"mcp_servers": {
            "office-word": {"command": "uvx", "args": []},
            "codebase-memory": {"command": "uvx", "args": []},
        }}
        snapshot = copy.deepcopy(config)
        assert seed_builtin_capabilities(config) == []
        assert config == snapshot
        # When nothing was added the key itself is left alone too.
        assert "mcp_servers" in config


class TestSeedTemplateIsolation:
    def test_mutating_written_entry_does_not_poison_template(self):
        config: dict = {}
        seed_builtin_capabilities(config)
        config["mcp_servers"]["office-word"]["args"].append("--debug")
        config["mcp_servers"]["new-key"] = True
        assert BUILTIN_MCP_SERVERS["office-word"] == {
            "command": "uvx",
            "args": ["--from", "office-word-mcp-server", "word_mcp_server"],
        }


class TestSeededEntriesValidate:
    def test_both_entries_pass_security_validation(self):
        for name, entry in BUILTIN_MCP_SERVERS.items():
            assert validate_mcp_server_entry(name, entry) == [], name


# ---------------------------------------------------------------------------
# Profile creation: the seed must land in every new profile, verbatim from the
# module table, without disturbing anything else the profile carries.
# ---------------------------------------------------------------------------

@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    """Isolated home for create_profile() (mirrors tests/hermes_cli/test_profiles.py)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    return tmp_path

class TestCreateProfileSeedsBuiltins:
    def test_fresh_profile_carries_both_servers(self, profile_env):
        profile_dir = create_profile("probe", no_alias=True)
        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8-sig"))
        assert cfg["mcp_servers"] == BUILTIN_MCP_SERVERS

    def test_fresh_profile_keeps_its_inherited_model(self, profile_env):
        (profile_env / ".hermes" / "config.yaml").write_text(
            "model:\n  provider: nous\n  default: some/model\n", encoding="utf-8"
        )
        profile_dir = create_profile("probe", no_alias=True)
        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8-sig"))
        assert cfg["model"] == {"provider": "nous", "default": "some/model"}
        assert cfg["mcp_servers"] == BUILTIN_MCP_SERVERS

    def test_clone_from_source_without_entries_still_gets_them(self, profile_env):
        (profile_env / ".hermes" / "config.yaml").write_text("model: test\n", encoding="utf-8")
        profile_dir = create_profile("probe", clone_config=True, no_alias=True)
        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8-sig"))
        assert cfg["mcp_servers"] == BUILTIN_MCP_SERVERS

    def test_clone_keeps_a_customised_entry_verbatim(self, profile_env):
        (profile_env / ".hermes" / "config.yaml").write_text(
            "mcp_servers:\n"
            "  office-word:\n"
            "    command: uvx\n"
            "    args: ['--from', 'office-word-mcp-server', 'word_mcp_server', '--debug']\n"
            "    enabled: false\n",
            encoding="utf-8",
        )
        profile_dir = create_profile("probe", clone_config=True, no_alias=True)
        cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8-sig"))
        assert cfg["mcp_servers"]["office-word"] == {
            "command": "uvx",
            "args": ["--from", "office-word-mcp-server", "word_mcp_server", "--debug"],
            "enabled": False,
        }
        # The missing one is still added, so the clone is never left short.
        assert cfg["mcp_servers"]["codebase-memory"] == BUILTIN_MCP_SERVERS["codebase-memory"]

    def test_seeding_a_damaged_config_yaml_does_not_fail_creation(self, profile_env):
        # A source config that is valid YAML but not a mapping is the operator's
        # file to repair, not ours to overwrite: the profile is still created,
        # and the file passes through byte-identical.
        (profile_env / ".hermes" / "config.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
        profile_dir = create_profile("probe", clone_config=True, no_alias=True)
        assert (profile_dir / "config.yaml").read_text(encoding="utf-8-sig") == "- just\n- a\n- list\n"
