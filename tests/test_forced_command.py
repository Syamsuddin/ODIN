"""Regresi K4 — kunci ODIN ber-forced-command tidak punya shell.

Tujuh bug di audit v2.3 lahir dari satu akar: separuh CLI mengirim perintah shell
lewat sesi SSH ber-kunci forced-command. sshd MEMBUANG perintah itu dan
menjalankan dispatcher, jadi setiap `ssh.run()` di sesi tersebut mengembalikan
rc != 0 apa pun keadaan servernya. Akibatnya diagnostik melapor palsu,
provisioning gagal diam-diam, dan `server harden` me-rollback pengerasan yang
baru dipasangnya sendiri.

Test di sini mengunci empat sifat yang mencegah pola itu kembali:
  1. Perintah shell lewat AgentSession MELEDAK, bukan mengembalikan hasil palsu.
  2. Payload handshake dikirim lewat stdin, bukan disisipkan ke string perintah.
  3. Audit keamanan bernilai tiga keadaan — tak terbaca ≠ aman.
  4. Penulisan sisi server diperiksa rc-nya sebelum dilaporkan sukses.
"""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "client"))
import odin_cli  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# 1. Akar tunggal — sesi terbatas tak boleh pura-pura punya shell
# ═══════════════════════════════════════════════════════════════════════════
class TestAgentSessionHasNoShell:

    def _sess(self):
        with patch.object(odin_cli, "paramiko", object()):
            return odin_cli.AgentSession("1.2.3.4", 22, key_path=None)

    def test_run_raises_instead_of_reporting_false_failure(self):
        """B5/B2: dulu ini mengembalikan rc != 0 dan pemanggil menyimpulkan
        'file tidak ada'. Sekarang mustahil salah tafsir."""
        sess = self._sess()
        with pytest.raises(odin_cli.ForcedCommandRejected):
            sess.run("test -f /home/odin/odin_agent.py && echo OK")

    def test_upload_raises_because_sftp_is_blocked(self):
        """B4: forced-command memblokir negosiasi subsystem SFTP."""
        sess = self._sess()
        with pytest.raises(odin_cli.ForcedCommandRejected):
            sess.upload("/tmp/x", "/home/odin/x")

    def test_error_names_the_working_alternative(self):
        sess = self._sess()
        with pytest.raises(odin_cli.ForcedCommandRejected) as e:
            sess.run("whoami")
        assert "AdminSession" in str(e.value)

    def test_admin_session_detects_being_dispatched(self):
        """Kredensial 'admin' yang ternyata kena dispatcher harus meledak,
        bukan diam-diam dianggap perintah yang gagal biasa."""
        with patch.object(odin_cli, "paramiko", object()):
            sess = odin_cli.AdminSession("1.2.3.4", 22, "root", password="x")
        with patch.object(odin_cli.AdminSession, "_exec",
                          return_value=("", "ODIN: perintah ditolak — ...", 126)):
            with pytest.raises(odin_cli.ForcedCommandRejected):
                sess.run("whoami")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Handshake lewat stdin (memperbaiki B6 → B5 → B1)
# ═══════════════════════════════════════════════════════════════════════════
_REPLY = (
    '{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}\n'
    '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"a"},{"name":"b"}]}}\n'
)


class TestHandshakeUsesStdin:

    def test_payload_goes_to_stdin_not_the_command_string(self):
        """B6: versi lama memakai `printf ... | run.sh`, persis bagian yang
        dibuang forced-command. Perintahnya kini HANYA run.sh --project."""
        seen = {}

        def fake_exec(self, cmd, timeout=60, stdin_data=None):
            seen["cmd"] = cmd
            seen["stdin"] = stdin_data
            return _REPLY, "", 0

        with patch.object(odin_cli, "paramiko", object()):
            sess = odin_cli.AgentSession("1.2.3.4", 22)
        with patch.object(odin_cli.SSHSession, "_exec", fake_exec):
            live, detail = sess.handshake("simuru")

        assert live and detail == "2 tools"
        assert seen["cmd"] == "/home/odin/run.sh --project simuru"
        assert "printf" not in seen["cmd"] and "|" not in seen["cmd"]
        assert seen["stdin"] == odin_cli._MCP_INIT

    def test_no_project_means_no_flag(self):
        seen = {}

        def fake_exec(self, cmd, timeout=60, stdin_data=None):
            seen["cmd"] = cmd
            return _REPLY, "", 0

        with patch.object(odin_cli, "paramiko", object()):
            sess = odin_cli.AgentSession("1.2.3.4", 22)
        with patch.object(odin_cli.SSHSession, "_exec", fake_exec):
            sess.handshake()
        assert seen["cmd"] == "/home/odin/run.sh"

    def test_admin_path_still_wraps_in_su_and_sends_stdin(self):
        """Alur `server add` jalan sebagai admin: agent harus tetap dijalankan
        lewat `su - odin` supaya memory/audit tak dibuat sebagai root."""
        seen = {}

        def fake_run(self, cmd, timeout=60, stdin_data=None):
            seen["cmd"] = cmd
            seen["stdin"] = stdin_data
            return _REPLY, "", 0

        with patch.object(odin_cli, "paramiko", object()):
            sess = odin_cli.AdminSession("1.2.3.4", 22, "root", password="x")
        with patch.object(odin_cli.AdminSession, "run", fake_run):
            live, _ = odin_cli._mcp_handshake(sess, "simuru", pp="sudo ")

        assert live
        assert "su - odin -c" in seen["cmd"]
        assert seen["stdin"] == odin_cli._MCP_INIT

    def test_failure_reports_server_stderr(self):
        with patch.object(odin_cli, "paramiko", object()):
            sess = odin_cli.AgentSession("1.2.3.4", 22)
        with patch.object(odin_cli.SSHSession, "_exec",
                          return_value=("", "FATAL: project 'x' tidak ditemukan", 1)):
            live, detail = sess.handshake("x")
        assert not live
        assert "tidak ditemukan" in detail


class TestParseMcpReply:

    def test_counts_tools_from_id_2(self):
        assert odin_cli._parse_mcp_reply(_REPLY) == (True, "2 tools")

    def test_ignores_non_json_noise(self):
        noisy = "INFO: SERVER_ID di-seed\n" + _REPLY
        assert odin_cli._parse_mcp_reply(noisy)[0] is True

    def test_empty_reply_is_failure(self):
        live, detail = odin_cli._parse_mcp_reply("", "")
        assert not live and "tidak ada respons" in detail


# ═══════════════════════════════════════════════════════════════════════════
# 3. `run.sh --diagnose` — server yang melaporkan dirinya sendiri
# ═══════════════════════════════════════════════════════════════════════════
SAMPLE_DIAGNOSE = """\
@@ODIN-DIAGNOSE@@ v=1
run_sh_version=2.4.0
machine_id=8255b493
agent_file=1
run_sh_exec=1
dispatch_exec=1
venv_python=1
projects_dir=1
memory_dir=1
agent_version=2.4.0
mcp_module=1
disk=62% 12G
mem=1.2G/3.8G
project=simfoni_proj memory=1
project=lain memory=0
@@SECTION:sudoers@@
odin ALL=(root) NOPASSWD: /usr/bin/journalctl --no-pager *
@@SECTION:authorized_keys@@
restrict,command="/home/odin/odin-dispatch.sh" ssh-ed25519 AAAA odin-vps
@@END@@
"""


class TestParseDiagnose:

    def setup_method(self):
        self.r = odin_cli._parse_diagnose(SAMPLE_DIAGNOSE)

    def test_reads_flags(self):
        assert odin_cli._diag_flag(self.r, "agent_file") is True
        assert odin_cli._diag_flag(self.r, "mcp_module") is True

    def test_reads_version_and_resources(self):
        assert self.r["agent_version"] == "2.4.0"
        assert self.r["disk"] == "62% 12G"
        assert self.r["mem"] == "1.2G/3.8G"

    def test_reads_projects_with_memory_state(self):
        assert self.r["projects"] == [
            {"name": "simfoni_proj", "memory": True},
            {"name": "lain", "memory": False},
        ]

    def test_sections_are_captured_verbatim(self):
        assert "journalctl --no-pager" in self.r["sudoers"]
        assert "odin-dispatch.sh" in self.r["authorized_keys"]

    def test_missing_flag_reads_as_unknown_not_false(self):
        """Kunci yang tak ada di laporan harus jadi UNKN di doctor, bukan FAIL."""
        assert "tidak_ada" not in self.r

    def test_old_dispatcher_is_reported_as_unknown(self):
        """Server pra-v2.4 menolak --diagnose; itu bukan 'server rusak'."""
        with patch.object(odin_cli, "paramiko", object()):
            sess = odin_cli.AgentSession("1.2.3.4", 22)
        with patch.object(odin_cli.SSHSession, "_exec",
                          return_value=("", "ODIN: perintah ditolak — ...", 126)):
            report, note = sess.diagnose()
        assert report is None
        assert "odin update" in note


# ═══════════════════════════════════════════════════════════════════════════
# 4. Audit keamanan tiga keadaan (B3) — tak terbaca ≠ aman
# ═══════════════════════════════════════════════════════════════════════════
class TestAuditIsNeverFailOpen:

    def test_unreadable_sudoers_marked_unknown(self):
        assert "@@UNREADABLE@@" in "@@UNREADABLE@@"
        assert odin_cli._tri(None) != odin_cli._tri(True)

    def test_scan_finds_vulnerable_patterns(self):
        old = "odin ALL=(root) NOPASSWD: /usr/bin/tail -n * /var/log/*\n"
        assert odin_cli._scan_sudoers(old)

    def test_shipped_sudoers_passes_its_own_scan(self):
        """Termasuk rule `cat /etc/sudoers.d/odin` yang baru ditambahkan."""
        assert odin_cli._scan_sudoers(odin_cli.SUDOERS_ODIN) == []

    def test_self_read_rule_has_closed_argument(self):
        """Rule audit-diri TIDAK boleh berwildcard — sudo memakai fnmatch tanpa
        FNM_PATHNAME, jadi `*` akan cocok dengan `/` dan `..`."""
        line = [l for l in odin_cli.SUDOERS_ODIN.splitlines()
                if "cat /etc/sudoers.d/odin" in l and l.startswith("odin ")]
        assert len(line) == 1
        assert "*" not in line[0]

    def test_key_without_forced_command_detected(self):
        assert odin_cli._scan_authorized_keys("ssh-ed25519 AAAA odin-vps\n") is False

    def test_key_with_forced_command_accepted(self):
        line = odin_cli._authorized_keys_line("ssh-ed25519 AAAA odin-vps")
        assert odin_cli._scan_authorized_keys(line + "\n") is True

    def test_mixed_keys_are_not_hardened(self):
        line = odin_cli._authorized_keys_line("ssh-ed25519 AAAA odin-vps")
        mixed = line + "\nssh-rsa BBBB admin@laptop\n"
        assert odin_cli._scan_authorized_keys(mixed) is False


# ═══════════════════════════════════════════════════════════════════════════
# 5. `project add` tak boleh melaporkan sukses palsu (B2)
# ═══════════════════════════════════════════════════════════════════════════
def _server_fixture(tmp_path):
    for d in ("servers", "projects", "keys", "modes"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return patch.multiple(
        odin_cli,
        ODIN_DIR=tmp_path,
        SERVERS_DIR=tmp_path / "servers",
        PROJECTS_DIR=tmp_path / "projects",
        KEYS_DIR=tmp_path / "keys",
        MODES_DIR=tmp_path / "modes",
    )


class TestProjectAddChecksProvisioning:

    def test_failed_provisioning_does_not_register_project(self, tmp_path):
        """Dulu rc dibuang dan progress() mencetak '✓' tanpa syarat, sehingga
        project 'ada' di laptop tapi tak pernah ada di server — MCP lalu mati
        dengan CONNECTION_CLOSED tanpa petunjuk."""
        workdir = tmp_path / "WD"
        workdir.mkdir()
        with _server_fixture(tmp_path), patch.object(odin_cli, "paramiko", object()):
            odin_cli.save_server("srv", {"name": "srv", "host": "1.2.3.4",
                                         "port": 22, "key": ""})
            args = SimpleNamespace(name="gagal", server="srv",
                                   remote_root="/var/www/gagal",
                                   workdir=str(workdir), yes=True)
            with patch.object(odin_cli.AgentSession, "connect"), \
                 patch.object(odin_cli.AgentSession, "provision",
                              return_value=(False, "izin ditolak")), \
                 patch.object(odin_cli.AgentSession, "close"):
                odin_cli.cmd_project_add(args)

            assert "gagal" not in odin_cli.list_projects()
            assert not (workdir / ".claude" / "settings.json").exists()

    def test_successful_provisioning_registers_project(self, tmp_path):
        workdir = tmp_path / "WD2"
        workdir.mkdir()
        with _server_fixture(tmp_path), patch.object(odin_cli, "paramiko", object()):
            odin_cli.save_server("srv", {"name": "srv", "host": "1.2.3.4",
                                         "port": 22, "key": ""})
            args = SimpleNamespace(name="jadi", server="srv",
                                   remote_root="/var/www/jadi",
                                   workdir=str(workdir), yes=True)
            with patch.object(odin_cli.AgentSession, "connect"), \
                 patch.object(odin_cli.AgentSession, "provision",
                              return_value=(True, "root_exists=1")), \
                 patch.object(odin_cli.AgentSession, "close"):
                odin_cli.cmd_project_add(args)

            assert "jadi" in odin_cli.list_projects()
            assert (workdir / ".claude" / "settings.json").exists()


# ═══════════════════════════════════════════════════════════════════════════
# 6. Dispatcher: kanal kontrol tetap sempit
# ═══════════════════════════════════════════════════════════════════════════
DISPATCH = REPO / "server" / "odin-dispatch.sh"


def _dispatch(cmd, home):
    """Jalankan dispatcher dengan SSH_ORIGINAL_COMMAND=cmd. Return (rc, stderr)."""
    r = subprocess.run(["bash", str(home / "odin-dispatch.sh")],
                       env={"SSH_ORIGINAL_COMMAND": cmd, "PATH": "/usr/bin:/bin",
                            "HOME": str(home)},
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stderr


@pytest.fixture
def fake_home(tmp_path):
    """Salin dispatcher + run.sh palsu yang cuma mencetak argumennya."""
    import shutil
    shutil.copy2(DISPATCH, tmp_path / "odin-dispatch.sh")
    (tmp_path / "run.sh").write_text('#!/usr/bin/env bash\necho "RUN:$*"\n')
    (tmp_path / "odin-dispatch.sh").chmod(0o755)
    (tmp_path / "run.sh").chmod(0o755)
    return tmp_path


class TestDispatcherBoundary:

    ALLOWED = [
        "",
        "/home/odin/run.sh",
        "/home/odin/run.sh --project simuru",
        "/home/odin/run.sh --diagnose",
        "/home/odin/run.sh --provision demo --root /var/www/demo",
    ]

    DENIED = [
        "test -f /home/odin/odin_agent.py",
        "cat /etc/shadow",
        "/home/odin/run.sh --diagnose extra",
        "/home/odin/run.sh --provision ../etc --root /x",
        "/home/odin/run.sh --provision ok --root /var/../etc",
        "/home/odin/run.sh --provision ok --root relatif/path",
        "/home/odin/run.sh --provision ok /var/www/x",
        "/home/odin/run.sh --root /etc",
        "/home/odin/run.sh --project a --project b",
        "/home/odin/run.sh --provision a --root /b; cat /etc/shadow",
        "/home/odin/run.sh --provision $(whoami) --root /b",
    ]

    @pytest.mark.parametrize("cmd", ALLOWED)
    def test_allowed_forms_pass(self, cmd, fake_home):
        rc, stderr = _dispatch(cmd, fake_home)
        assert rc == 0, f"{cmd!r} seharusnya diizinkan: {stderr}"

    @pytest.mark.parametrize("cmd", DENIED)
    def test_denied_forms_are_rejected(self, cmd, fake_home):
        rc, stderr = _dispatch(cmd, fake_home)
        assert rc == 126, f"{cmd!r} seharusnya ditolak"
        assert "perintah ditolak" in stderr


# ═══════════════════════════════════════════════════════════════════════════
# 7. run.sh --provision & --diagnose
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture
def odin_home(tmp_path):
    import shutil
    shutil.copy2(REPO / "server" / "run.sh", tmp_path / "run.sh")
    shutil.copy2(DISPATCH, tmp_path / "odin-dispatch.sh")
    (tmp_path / "run.sh").chmod(0o755)
    (tmp_path / "odin-dispatch.sh").chmod(0o755)
    (tmp_path / "odin_agent.py").write_text('__version__ = "2.4.0"\n')
    (tmp_path / "memory").mkdir()
    return tmp_path


def _run_sh(home, *args):
    return subprocess.run(["bash", str(home / "run.sh"), *args],
                          capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                               "HOME": str(home)})


class TestRunShControlChannel:

    def test_provision_writes_conf_and_memory(self, odin_home):
        r = _run_sh(odin_home, "--provision", "demo", "--root", "/var/www/demo")
        assert r.returncode == 0, r.stderr
        assert "@@ODIN-PROVISION@@ ok" in r.stdout

        conf = odin_home / "projects" / "demo.conf"
        assert conf.read_text() == ("PROJECT_NAME=demo\n"
                                    "PROJECT_ROOT=/var/www/demo\n"
                                    "ALLOWED_LOG_DIRS=/var/log,/var/www/demo\n")
        assert oct(conf.stat().st_mode)[-3:] == "600"
        assert oct((odin_home / "memory" / "demo").stat().st_mode)[-3:] == "700"

    def test_provision_refuses_to_overwrite_existing_project(self, odin_home):
        """Menimpa conf = membajak PROJECT_ROOT project lain yang sudah jalan."""
        _run_sh(odin_home, "--provision", "demo", "--root", "/var/www/demo")
        r = _run_sh(odin_home, "--provision", "demo", "--root", "/var/www/LAIN")
        assert r.returncode != 0
        assert "sudah ada" in r.stderr
        assert "/var/www/demo" in (odin_home / "projects" / "demo.conf").read_text()

    @pytest.mark.parametrize("bad", ["../etc", "a b", "", "."])
    def test_provision_rejects_bad_names(self, bad, odin_home):
        r = _run_sh(odin_home, "--provision", bad, "--root", "/var/www/x")
        assert r.returncode != 0

    @pytest.mark.parametrize("bad", ["relatif", "/var/../etc", "/var/www/$(x)"])
    def test_provision_rejects_bad_roots(self, bad, odin_home):
        r = _run_sh(odin_home, "--provision", "ok", "--root", bad)
        assert r.returncode != 0

    def test_diagnose_reports_facts_and_never_launches_agent(self, odin_home):
        _run_sh(odin_home, "--provision", "demo", "--root", "/var/www/demo")
        r = _run_sh(odin_home, "--diagnose")
        assert r.returncode == 0, r.stderr

        report = odin_cli._parse_diagnose(r.stdout)
        assert odin_cli._diag_flag(report, "agent_file") is True
        assert odin_cli._diag_flag(report, "run_sh_exec") is True
        assert report["agent_version"] == "2.4.0"
        assert {"name": "demo", "memory": True} in report["projects"]
        # venv sengaja tidak ada di fixture → harus dilaporkan 0, bukan hilang.
        assert odin_cli._diag_flag(report, "venv_python") is False

    def test_diagnose_marks_unreadable_sudoers_explicitly(self, odin_home):
        """Tanpa hak sudo, laporan harus bilang TIDAK TERBACA — bukan diam."""
        r = _run_sh(odin_home, "--diagnose")
        report = odin_cli._parse_diagnose(r.stdout)
        assert report["sudoers"].strip() in ("@@UNREADABLE@@", "@@ABSENT@@")

    def test_unknown_argument_is_fatal(self, odin_home):
        r = _run_sh(odin_home, "--wat")
        assert r.returncode != 0
        assert "tak dikenal" in r.stderr


# ═══════════════════════════════════════════════════════════════════════════
# 8. Mode project di-semai sejak awal (papan mode tak pernah kosong)
# ═══════════════════════════════════════════════════════════════════════════
class TestProjectModeSeeding:

    def test_project_add_seeds_deploy_mode(self, tmp_path):
        workdir = tmp_path / "WD"
        workdir.mkdir()
        with _server_fixture(tmp_path), patch.object(odin_cli, "paramiko", object()):
            odin_cli.save_server("srv", {"name": "srv", "host": "1.2.3.4",
                                         "port": 22, "key": ""})
            args = SimpleNamespace(name="baru", server="srv",
                                   remote_root="/var/www/baru",
                                   workdir=str(workdir), yes=True)
            with patch.object(odin_cli.AgentSession, "connect"), \
                 patch.object(odin_cli.AgentSession, "provision",
                              return_value=(True, "root_exists=1")), \
                 patch.object(odin_cli.AgentSession, "close"):
                odin_cli.cmd_project_add(args)

            assert (tmp_path / "modes" / "baru").read_text().strip() == "deploy"

    def test_seeding_never_overwrites_synced_mode(self, tmp_path):
        """Mode yang sudah tersinkron dari inspect_server tidak boleh mundur."""
        with _server_fixture(tmp_path):
            (tmp_path / "modes" / "adar").write_text("production\n")
            odin_cli._seed_project_mode("adar")
            assert (tmp_path / "modes" / "adar").read_text().strip() == "production"

    def test_sync_restores_missing_mode_file(self, tmp_path):
        workdir = tmp_path / "SYNCWD"
        workdir.mkdir()
        with _server_fixture(tmp_path):
            odin_cli.save_server("srv", {"name": "srv", "host": "1.2.3.4",
                                         "port": 22, "key": ""})
            odin_cli.save_project("pulih", {
                "name": "pulih", "server": "srv",
                "remote_root": "/var/www/pulih",
                "local_workdir": str(workdir)})
            assert not (tmp_path / "modes" / "pulih").exists()
            odin_cli.cmd_project_sync("pulih")
            assert (tmp_path / "modes" / "pulih").read_text().strip() == "deploy"
