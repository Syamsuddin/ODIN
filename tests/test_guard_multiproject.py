"""Test guard multi-project awareness — _detect_project, _get_mode, _sync_mode."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
import odin_guard  # noqa: E402


class TestDetectProject:
    def test_from_settings_json(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "mcpServers": {
                "odin": {
                    "command": "ssh",
                    "args": ["vps-app", "/home/odin/run.sh", "--project", "simuru"],
                }
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(tmp_path)}):
            assert odin_guard._detect_project() == "simuru"

    def test_no_settings_json(self, tmp_path):
        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(tmp_path)}):
            assert odin_guard._detect_project() == ""

    def test_no_project_flag(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "mcpServers": {
                "odin": {
                    "command": "ssh",
                    "args": ["vps-app", "/home/odin/run.sh"],
                }
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(tmp_path)}):
            assert odin_guard._detect_project() == ""

    def test_malformed_json(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("not json")
        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(tmp_path)}):
            assert odin_guard._detect_project() == ""

    def test_no_mcp_servers_key(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {}}))
        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(tmp_path)}):
            assert odin_guard._detect_project() == ""


class TestGetModeMultiProject:
    def test_per_project_mode(self, tmp_path):
        modes_dir = tmp_path / ".odin" / "modes"
        modes_dir.mkdir(parents=True)
        (modes_dir / "simuru").write_text("production\n")

        workdir = tmp_path / "SIMURU"
        workdir.mkdir()
        claude_dir = workdir / ".claude"
        claude_dir.mkdir()
        settings = {"mcpServers": {"odin": {"args": ["x", "y", "--project", "simuru"]}}}
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(workdir)}), \
             patch("os.path.expanduser", side_effect=lambda p: str(tmp_path / p.lstrip("~/")))  :
            result = odin_guard._get_mode()
            assert result == "production"

    def test_fallback_legacy_odin_mode(self, tmp_path):
        legacy_file = tmp_path / ".odin_mode"
        legacy_file.write_text("deploy\n")

        workdir = tmp_path / "NOPROJECT"
        workdir.mkdir()

        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(workdir)}), \
             patch("os.path.expanduser", side_effect=lambda p: str(tmp_path / p.lstrip("~/"))):
            result = odin_guard._get_mode()
            assert result == "deploy"

    def test_fallback_env_var(self, tmp_path):
        workdir = tmp_path / "EMPTY"
        workdir.mkdir()
        with patch.dict(os.environ, {
            "CLAUDE_WORKING_DIRECTORY": str(workdir),
            "ODIN_MODE": "setup"
        }), patch("os.path.expanduser", side_effect=lambda p: str(tmp_path / p.lstrip("~/"))):
            result = odin_guard._get_mode()
            assert result == "setup"

    def test_default_deploy(self, tmp_path):
        workdir = tmp_path / "EMPTY2"
        workdir.mkdir()
        env = {"CLAUDE_WORKING_DIRECTORY": str(workdir)}
        env.pop("ODIN_MODE", None)
        with patch.dict(os.environ, env, clear=False), \
             patch("os.path.expanduser", side_effect=lambda p: str(tmp_path / p.lstrip("~/"))):
            os.environ.pop("ODIN_MODE", None)
            result = odin_guard._get_mode()
            assert result == "deploy"


class TestSyncModeMultiProject:
    def test_sync_to_project_mode_file(self, tmp_path):
        workdir = tmp_path / "SIMURU"
        workdir.mkdir()
        claude_dir = workdir / ".claude"
        claude_dir.mkdir()
        settings = {"mcpServers": {"odin": {"args": ["x", "y", "--project", "simuru"]}}}
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        data = {
            "tool_name": "mcp__odin__inspect_server",
            "tool_result": {"mode": "production"},
        }

        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(workdir)}), \
             patch("os.path.expanduser", side_effect=lambda p: str(tmp_path / p.lstrip("~/"))):
            odin_guard._sync_mode_from_result(data)
            mode_file = tmp_path / ".odin" / "modes" / "simuru"
            assert mode_file.exists()
            assert mode_file.read_text().strip() == "production"

    def test_sync_legacy_without_project(self, tmp_path):
        workdir = tmp_path / "NOPROJECT"
        workdir.mkdir()

        data = {
            "tool_name": "mcp__odin__inspect_server",
            "tool_result": {"mode": "deploy"},
        }

        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(workdir)}), \
             patch("os.path.expanduser", side_effect=lambda p: str(tmp_path / p.lstrip("~/"))):
            odin_guard._sync_mode_from_result(data)
            legacy_file = tmp_path / ".odin_mode"
            assert legacy_file.exists()
            assert legacy_file.read_text().strip() == "deploy"

    def test_skip_non_inspect(self, tmp_path):
        data = {
            "tool_name": "mcp__odin__run_command",
            "tool_result": {"mode": "production"},
        }
        odin_guard._sync_mode_from_result(data)

    def test_skip_invalid_mode(self, tmp_path):
        workdir = tmp_path / "X"
        workdir.mkdir()
        data = {
            "tool_name": "mcp__odin__inspect_server",
            "tool_result": {"mode": "invalid_mode"},
        }
        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(workdir)}):
            odin_guard._sync_mode_from_result(data)


class TestDetectProjectContext:
    def test_returns_tuple(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "mcpServers": {
                "odin": {
                    "args": ["vps-app", "/home/odin/run.sh", "--project", "simuru"],
                }
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(tmp_path)}):
            project, server = odin_guard._detect_project_context()
            assert project == "simuru"
            assert server == "vps-app"

    def test_no_settings(self, tmp_path):
        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(tmp_path)}):
            project, server = odin_guard._detect_project_context()
            assert project == ""
            assert server == ""

    def test_detect_project_wraps_context(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {"mcpServers": {"odin": {"args": ["srv", "run.sh", "--project", "erp"]}}}
        (claude_dir / "settings.json").write_text(json.dumps(settings))
        with patch.dict(os.environ, {"CLAUDE_WORKING_DIRECTORY": str(tmp_path)}):
            assert odin_guard._detect_project() == "erp"


class TestProjectInRiskCard:
    def test_risk_card_shows_project_and_server(self):
        card = odin_guard.risk_card("rm -rf /tmp/x", project="simuru", server="vps-app")
        assert "Prj   : simuru → vps-app" in card

    def test_risk_card_no_project(self):
        card = odin_guard.risk_card("rm -rf /tmp/x")
        assert "Prj" not in card

    def test_service_card_shows_project(self):
        card = odin_guard.service_card("nginx", "restart", project="simuru", server="vps-app")
        assert "Prj   : simuru → vps-app" in card

    def test_service_card_no_project(self):
        card = odin_guard.service_card("nginx", "restart")
        assert "Prj" not in card


class TestWarnProjectMismatch:
    def test_no_project(self):
        assert odin_guard._warn_project_mismatch("") == ""

    def test_no_projects_dir(self, tmp_path):
        with patch("os.path.expanduser", return_value=str(tmp_path / "nope")):
            assert odin_guard._warn_project_mismatch("simuru") == ""

    def test_project_registered(self, tmp_path):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "simuru.yaml").write_text("name: simuru\n")
        with patch("os.path.expanduser", return_value=str(projects_dir)):
            assert odin_guard._warn_project_mismatch("simuru") == ""

    def test_project_not_registered(self, tmp_path):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        with patch("os.path.expanduser", return_value=str(projects_dir)):
            result = odin_guard._warn_project_mismatch("ghost")
            assert "ghost" in result
            assert "tidak terdaftar" in result
