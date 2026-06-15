"""Test CLI commands — unit test tanpa SSH (mock paramiko)."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))
import odin_cli  # noqa: E402


# ── Fixtures ────────────────────────────────────────────────────────────────

def _setup_dirs(tmp_path):
    """Setup ~/.odin/ dirs di tmp_path."""
    servers = tmp_path / "servers"
    projects = tmp_path / "projects"
    keys = tmp_path / "keys"
    modes = tmp_path / "modes"
    for d in (servers, projects, keys, modes):
        d.mkdir(parents=True, exist_ok=True)
    return servers, projects, keys, modes


def _patch_dirs(tmp_path):
    """Patch ODIN_DIR etc ke tmp_path."""
    return patch.multiple(odin_cli,
        ODIN_DIR=tmp_path,
        SERVERS_DIR=tmp_path / "servers",
        PROJECTS_DIR=tmp_path / "projects",
        KEYS_DIR=tmp_path / "keys",
        MODES_DIR=tmp_path / "modes",
    )


# ── YAML/JSON helpers ──────────────────────────────────────────────────────

class TestSaveLoadServer:
    def test_roundtrip(self, tmp_path):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            data = {"name": "vps-app", "host": "1.2.3.4", "port": 22}
            odin_cli.save_server("vps-app", data)
            loaded = odin_cli.load_server("vps-app")
            assert loaded["name"] == "vps-app"
            assert loaded["host"] == "1.2.3.4"
            assert loaded["port"] == 22


class TestSaveLoadProject:
    def test_roundtrip(self, tmp_path):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            data = {"name": "simuru", "server": "vps-app",
                    "remote_root": "/var/www/simuru",
                    "local_workdir": "/Users/test/SIMURU"}
            odin_cli.save_project("simuru", data)
            loaded = odin_cli.load_project("simuru")
            assert loaded["name"] == "simuru"
            assert loaded["server"] == "vps-app"


# ── list_servers / list_projects ────────────────────────────────────────────

class TestListServers:
    def test_empty(self, tmp_path):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            assert odin_cli.list_servers() == []

    def test_populated(self, tmp_path):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            odin_cli.save_server("vps-app", {"name": "vps-app"})
            odin_cli.save_server("vps-db", {"name": "vps-db"})
            result = odin_cli.list_servers()
            assert result == ["vps-app", "vps-db"]


class TestListProjects:
    def test_empty(self, tmp_path):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            assert odin_cli.list_projects() == []

    def test_populated(self, tmp_path):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            odin_cli.save_project("simuru", {"name": "simuru"})
            odin_cli.save_project("erp", {"name": "erp"})
            result = odin_cli.list_projects()
            assert result == ["erp", "simuru"]


# ── cmd_server_remove ───────────────────────────────────────────────────────

class TestServerRemove:
    def test_not_found(self, tmp_path, capsys):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            odin_cli.cmd_server_remove("nonexistent")
        assert "tidak ditemukan" in capsys.readouterr().err

    def test_blocked_by_project(self, tmp_path, capsys):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            odin_cli.save_server("vps-app", {"name": "vps-app"})
            odin_cli.save_project("simuru", {"name": "simuru", "server": "vps-app"})
            odin_cli.cmd_server_remove("vps-app")
        assert "masih dipakai" in capsys.readouterr().err

    def test_remove_ok(self, tmp_path):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            odin_cli.save_server("vps-app", {"name": "vps-app"})
            key = tmp_path / "keys" / "vps-app"
            key.write_text("fake-key")
            key.with_suffix(".pub").write_text("fake-pub")
            with patch("builtins.input", return_value="y"):
                odin_cli.cmd_server_remove("vps-app")
            assert "vps-app" not in odin_cli.list_servers()
            assert not key.exists()


# ── cmd_project_remove ──────────────────────────────────────────────────────

class TestProjectRemove:
    def test_not_found(self, tmp_path, capsys):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            odin_cli.cmd_project_remove("nonexistent")
        assert "tidak ditemukan" in capsys.readouterr().err

    def test_remove_cleans_settings(self, tmp_path):
        _setup_dirs(tmp_path)
        workdir = tmp_path / "SIMURU"
        workdir.mkdir()
        claude_dir = workdir / ".claude"
        claude_dir.mkdir()
        settings = {"mcpServers": {"odin": {"args": ["vps", "run.sh", "--project", "simuru"]}}}
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        with _patch_dirs(tmp_path):
            odin_cli.save_project("simuru", {
                "name": "simuru", "server": "vps-app",
                "remote_root": "/var/www/simuru",
                "local_workdir": str(workdir),
            })
            with patch("builtins.input", return_value="y"):
                odin_cli.cmd_project_remove("simuru")
            assert "simuru" not in odin_cli.list_projects()
            # settings.json should be cleaned up
            if (claude_dir / "settings.json").exists():
                s = json.loads((claude_dir / "settings.json").read_text())
                assert "odin" not in s.get("mcpServers", {})


# ── settings.json generation ───────────────────────────────────────────────

class TestSettingsJsonGeneration:
    def test_generates_correct_mcp_args(self, tmp_path):
        _setup_dirs(tmp_path)
        workdir = tmp_path / "SIMURU"
        workdir.mkdir()

        mock_paramiko = MagicMock()
        with _patch_dirs(tmp_path), \
             patch.object(odin_cli, "paramiko", mock_paramiko):
            odin_cli.save_server("vps-app", {
                "name": "vps-app", "host": "1.2.3.4", "port": 22,
                "key": str(tmp_path / "keys" / "vps-app"),
            })

            with patch.object(odin_cli.SSHSession, "connect"), \
                 patch.object(odin_cli.SSHSession, "run", return_value=("", "", 0)), \
                 patch.object(odin_cli.SSHSession, "close"), \
                 patch.object(odin_cli, "ask_input", side_effect=[
                     "simuru",             # nama project
                     "/var/www/simuru",     # path di server
                     str(workdir),          # workdir lokal
                 ]), \
                 patch.object(odin_cli, "ask_choice", return_value=1), \
                 patch.object(odin_cli, "confirm", return_value=True):
                odin_cli.cmd_project_add()

            settings_path = workdir / ".claude" / "settings.json"
            assert settings_path.exists()
            settings = json.loads(settings_path.read_text())
            odin_mcp = settings["mcpServers"]["odin"]
            assert odin_mcp["args"] == [
                "vps-app", "/home/odin/run.sh", "--project", "simuru"
            ]
            assert "mcp__odin__server_info" in settings["permissions"]["allow"]


# ── run.sh tests ────────────────────────────────────────────────────────────

class TestRunSh:
    def _run_sh(self, tmp_path, args="", env=None):
        """Helper: jalankan run.sh (hanya parsing, tanpa exec python)."""
        import subprocess
        run_sh = Path(__file__).resolve().parent.parent / "server" / "run.sh"
        test_sh = tmp_path / "test_run.sh"
        content = run_sh.read_text()
        # Ganti exec line dengan echo untuk test parsing
        content = content.replace(
            'exec "$ODIN_HOME/.venv/bin/python" "$ODIN_HOME/odin_agent.py"',
            'echo "PROJECT_ROOT=$PROJECT_ROOT MEMORY_DIR=$MEMORY_DIR ALLOWED_LOG_DIRS=$ALLOWED_LOG_DIRS"'
        )
        # Ganti cd check agar tidak gagal di test
        content = content.replace(
            'cd "$PROJECT_ROOT" || { echo "FATAL: $PROJECT_ROOT tidak bisa diakses" >&2; exit 1; }',
            ': # skip cd'
        )
        test_sh.write_text(content)
        test_sh.chmod(0o755)

        cmd = f"bash {test_sh} {args}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                env={**(env or {}), "PATH": os.environ.get("PATH", "")})
        return result

    def test_project_flag(self, tmp_path):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "simuru.conf").write_text(
            "PROJECT_NAME=simuru\nPROJECT_ROOT=/var/www/simuru\nALLOWED_LOG_DIRS=/var/log,/var/www/simuru\n"
        )
        (tmp_path / "memory").mkdir()

        # Buat test run.sh yang ODIN_HOME = tmp_path
        run_sh = Path(__file__).resolve().parent.parent / "server" / "run.sh"
        test_sh = tmp_path / "run.sh"
        content = run_sh.read_text()
        content = content.replace(
            'ODIN_HOME="$(cd "$(dirname "$0")" && pwd)"',
            f'ODIN_HOME="{tmp_path}"'
        )
        content = content.replace(
            'exec "$ODIN_HOME/.venv/bin/python" "$ODIN_HOME/odin_agent.py"',
            'echo "PROJECT_ROOT=$PROJECT_ROOT|MEMORY_DIR=$MEMORY_DIR"'
        )
        content = content.replace(
            'cd "$PROJECT_ROOT" || { echo "FATAL: $PROJECT_ROOT tidak bisa diakses" >&2; exit 1; }',
            ': # skip cd'
        )
        test_sh.write_text(content)
        test_sh.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_sh), "--project", "simuru"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "PROJECT_ROOT=/var/www/simuru" in result.stdout
        assert f"MEMORY_DIR={tmp_path}/memory/simuru" in result.stdout

    def test_invalid_project(self, tmp_path):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()

        run_sh = Path(__file__).resolve().parent.parent / "server" / "run.sh"
        test_sh = tmp_path / "run.sh"
        content = run_sh.read_text()
        content = content.replace(
            'ODIN_HOME="$(cd "$(dirname "$0")" && pwd)"',
            f'ODIN_HOME="{tmp_path}"'
        )
        test_sh.write_text(content)
        test_sh.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_sh), "--project", "nonexistent"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "FATAL" in result.stderr

    def test_single_conf_auto(self, tmp_path):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "erp.conf").write_text(
            "PROJECT_NAME=erp\nPROJECT_ROOT=/var/www/erp\nALLOWED_LOG_DIRS=/var/log\n"
        )

        run_sh = Path(__file__).resolve().parent.parent / "server" / "run.sh"
        test_sh = tmp_path / "run.sh"
        content = run_sh.read_text()
        content = content.replace(
            'ODIN_HOME="$(cd "$(dirname "$0")" && pwd)"',
            f'ODIN_HOME="{tmp_path}"'
        )
        content = content.replace(
            'exec "$ODIN_HOME/.venv/bin/python" "$ODIN_HOME/odin_agent.py"',
            'echo "PROJECT_ROOT=$PROJECT_ROOT|MEMORY_DIR=$MEMORY_DIR"'
        )
        content = content.replace(
            'cd "$PROJECT_ROOT" || { echo "FATAL: $PROJECT_ROOT tidak bisa diakses" >&2; exit 1; }',
            ': # skip cd'
        )
        test_sh.write_text(content)
        test_sh.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_sh)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "PROJECT_ROOT=/var/www/erp" in result.stdout


import subprocess  # noqa: E402 - needed for TestRunSh


# ── _detect_current_project ────────────────────────────────────────────────

class TestDetectCurrentProject:
    def test_match(self, tmp_path):
        _setup_dirs(tmp_path)
        workdir = tmp_path / "SIMURU"
        workdir.mkdir()
        with _patch_dirs(tmp_path):
            odin_cli.save_project("simuru", {
                "name": "simuru", "server": "vps",
                "local_workdir": str(workdir),
            })
            with patch.object(Path, "cwd", return_value=workdir):
                assert odin_cli._detect_current_project() == "simuru"

    def test_no_match(self, tmp_path):
        _setup_dirs(tmp_path)
        workdir = tmp_path / "SIMURU"
        workdir.mkdir()
        other = tmp_path / "OTHER"
        other.mkdir()
        with _patch_dirs(tmp_path):
            odin_cli.save_project("simuru", {
                "name": "simuru", "server": "vps",
                "local_workdir": str(workdir),
            })
            with patch.object(Path, "cwd", return_value=other):
                assert odin_cli._detect_current_project() is None

    def test_empty_projects(self, tmp_path):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            with patch.object(Path, "cwd", return_value=tmp_path):
                assert odin_cli._detect_current_project() is None


# ── odin project list enhanced ─────────────────────────────────────────────

class TestProjectListEnhanced:
    def test_mode_column(self, tmp_path, capsys):
        _setup_dirs(tmp_path)
        modes = tmp_path / "modes"
        modes.mkdir(exist_ok=True)
        (modes / "simuru").write_text("production\n")
        with _patch_dirs(tmp_path):
            odin_cli.save_project("simuru", {
                "name": "simuru", "server": "vps",
                "local_workdir": "/tmp/x",
            })
            with patch.object(Path, "cwd", return_value=Path("/tmp/nowhere")):
                odin_cli.cmd_project_list()
        out = capsys.readouterr().out
        assert "production" in out
        assert "Mode" in out


# ── odin project status ────────────────────────────────────────────────────

class TestProjectStatus:
    def test_not_found(self, tmp_path, capsys):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            with patch.object(Path, "cwd", return_value=tmp_path):
                odin_cli.cmd_project_status()
        assert "tidak cocok" in capsys.readouterr().err

    def test_by_name(self, tmp_path, capsys):
        _setup_dirs(tmp_path)
        workdir = tmp_path / "SIM"
        workdir.mkdir()
        claude_dir = workdir / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            json.dumps({"mcpServers": {"odin": {"args": ["vps", "--project", "simuru"]}}})
        )
        mock_paramiko = MagicMock()
        with _patch_dirs(tmp_path), \
             patch.object(odin_cli, "paramiko", mock_paramiko):
            odin_cli.save_server("vps", {"name": "vps", "host": "1.2.3.4", "port": 22, "key": ""})
            odin_cli.save_project("simuru", {
                "name": "simuru", "server": "vps",
                "remote_root": "/var/www/simuru",
                "local_workdir": str(workdir),
            })
            with patch.object(odin_cli.SSHSession, "connect"), \
                 patch.object(odin_cli.SSHSession, "run", return_value=("", "", 0)), \
                 patch.object(odin_cli.SSHSession, "close"):
                odin_cli.cmd_project_status("simuru")
        out = capsys.readouterr().out
        assert "simuru" in out
        assert "vps" in out


# ── odin project switch ────────────────────────────────────────────────────

class TestProjectSwitch:
    def test_not_found(self, tmp_path, capsys):
        import pytest
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path), pytest.raises(SystemExit):
            odin_cli.cmd_project_switch("ghost")
        assert "tidak ditemukan" in capsys.readouterr().err

    def test_workdir_missing(self, tmp_path, capsys):
        _setup_dirs(tmp_path)
        with _patch_dirs(tmp_path):
            odin_cli.save_project("simuru", {
                "name": "simuru", "server": "vps",
                "local_workdir": "/nonexistent/path",
            })
            odin_cli.cmd_project_switch("simuru")
        assert "tidak ditemukan" in capsys.readouterr().err


# ── run.sh PROJECT_NAME export ─────────────────────────────────────────────

class TestRunShProjectName:
    def test_project_name_exported(self, tmp_path):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "simuru.conf").write_text(
            "PROJECT_NAME=simuru\nPROJECT_ROOT=/var/www/simuru\nALLOWED_LOG_DIRS=/var/log\n"
        )

        run_sh = Path(__file__).resolve().parent.parent / "server" / "run.sh"
        test_sh = tmp_path / "run.sh"
        content = run_sh.read_text()
        content = content.replace(
            'ODIN_HOME="$(cd "$(dirname "$0")" && pwd)"',
            f'ODIN_HOME="{tmp_path}"'
        )
        content = content.replace(
            'exec "$ODIN_HOME/.venv/bin/python" "$ODIN_HOME/odin_agent.py"',
            'echo "PROJECT_NAME=$PROJECT_NAME|PROJECT_ROOT=$PROJECT_ROOT"'
        )
        content = content.replace(
            'cd "$PROJECT_ROOT" || { echo "FATAL: $PROJECT_ROOT tidak bisa diakses" >&2; exit 1; }',
            ': # skip cd'
        )
        test_sh.write_text(content)
        test_sh.chmod(0o755)

        result = subprocess.run(
            ["bash", str(test_sh), "--project", "simuru"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "PROJECT_NAME=simuru" in result.stdout
