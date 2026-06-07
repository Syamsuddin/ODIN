"""Test Guard — classify_command, seg_is_read (23 classifier), assess_command
(26 shell rules + DB assessor), risk_card, _strip_quotes, service_card, mode."""
import sys, types, os, unittest
from pathlib import Path

os.environ["ODIN_SKIP_INSPECT"] = "1"

_mcp_mod = types.ModuleType("mcp")
_mcp_srv = types.ModuleType("mcp.server")
_mcp_fmcp = types.ModuleType("mcp.server.fastmcp")

class FakeMCP:
    def __init__(self, *a, **kw): pass
    def tool(self): return lambda f: f
    def resource(self, uri): return lambda f: f
    def run(self, **kw): pass

_mcp_fmcp.FastMCP = FakeMCP
sys.modules["mcp"] = _mcp_mod
sys.modules["mcp.server"] = _mcp_srv
sys.modules["mcp.server.fastmcp"] = _mcp_fmcp

ROOT = Path(__file__).resolve().parent.parent
import importlib.util
guard_spec = importlib.util.spec_from_file_location("guard", ROOT / "client" / "odin_guard.py")
guard = importlib.util.module_from_spec(guard_spec)
guard_spec.loader.exec_module(guard)


# ===========================================================================
# _strip_quotes
# ===========================================================================
class TestStripQuotes(unittest.TestCase):

    def test_single_quotes_emptied(self):
        self.assertEqual(guard._strip_quotes("mysql -e 'SELECT a>b'"), "mysql -e ''")

    def test_double_quotes_emptied(self):
        self.assertEqual(guard._strip_quotes('echo "hello > world"'), 'echo ""')

    def test_no_quotes_unchanged(self):
        self.assertEqual(guard._strip_quotes("ls -la"), "ls -la")

    def test_ansi_c_quotes(self):
        result = guard._strip_quotes("echo $'hello\\nworld'")
        self.assertNotIn("hello", result)

    def test_mixed_quotes(self):
        result = guard._strip_quotes("""mysql -e "SELECT * FROM t WHERE a > 'x'" """)
        self.assertNotIn(">", result)


# ===========================================================================
# seg_is_read — 23 sub-command classifiers
# ===========================================================================
class TestSegIsRead(unittest.TestCase):

    # --- Pure read commands ---
    def test_basic_read_cmds(self):
        for cmd in ("ls -la", "cat /etc/hosts", "grep foo bar.txt", "df -h",
                     "free -m", "uptime", "whoami", "ps aux", "date",
                     "wc -l file.txt", "head -5 f", "tail -20 f", "stat f",
                     "find . -name '*.py'", "diff a b", "md5sum f"):
            self.assertTrue(guard.seg_is_read(cmd), f"should be READ: {cmd}")

    def test_env_prefix_ignored(self):
        self.assertTrue(guard.seg_is_read("LANG=C ls -la"))
        self.assertTrue(guard.seg_is_read("FOO=bar BAZ=1 cat file"))

    def test_env_only_is_read(self):
        self.assertTrue(guard.seg_is_read("FOO=bar"))

    # --- git ---
    def test_git_read(self):
        for sub in ("status", "log", "diff", "show", "rev-parse", "blame",
                     "ls-files", "reflog", "grep", "shortlog"):
            self.assertTrue(guard.seg_is_read(f"git {sub}"), f"git {sub} should be READ")

    def test_git_write(self):
        for sub in ("push", "commit", "merge", "rebase", "checkout", "reset"):
            self.assertFalse(guard.seg_is_read(f"git {sub}"), f"git {sub} should be WRITE")

    def test_git_no_args(self):
        self.assertFalse(guard.seg_is_read("git"))

    # --- php ---
    def test_php_read(self):
        for flag in ("-v", "--version", "-m", "-i", "-l"):
            self.assertTrue(guard.seg_is_read(f"php {flag}"), f"php {flag} should be READ")

    def test_php_write(self):
        self.assertFalse(guard.seg_is_read("php artisan migrate"))
        self.assertFalse(guard.seg_is_read("php script.php"))

    # --- composer ---
    def test_composer_read(self):
        for sub in ("show", "--version", "diagnose", "validate", "outdated", "status"):
            self.assertTrue(guard.seg_is_read(f"composer {sub}"), f"composer {sub} should be READ")

    def test_composer_write(self):
        for sub in ("install", "update", "require", "remove"):
            self.assertFalse(guard.seg_is_read(f"composer {sub}"), f"composer {sub} should be WRITE")

    # --- systemctl ---
    def test_systemctl_read(self):
        for sub in ("status", "is-active", "is-enabled", "list-units", "show", "cat"):
            self.assertTrue(guard.seg_is_read(f"systemctl {sub}"), f"systemctl {sub} should be READ")

    def test_systemctl_write(self):
        for sub in ("restart", "stop", "start", "enable", "disable", "reload"):
            self.assertFalse(guard.seg_is_read(f"systemctl {sub}"), f"systemctl {sub} should be WRITE")

    # --- docker ---
    def test_docker_read(self):
        for sub in ("ps", "images", "logs", "inspect", "version", "info", "stats"):
            self.assertTrue(guard.seg_is_read(f"docker {sub}"), f"docker {sub} should be READ")

    def test_docker_write(self):
        for sub in ("run", "exec", "rm", "stop", "start", "build", "pull"):
            self.assertFalse(guard.seg_is_read(f"docker {sub}"), f"docker {sub} should be WRITE")

    # --- sed ---
    def test_sed_read(self):
        self.assertTrue(guard.seg_is_read("sed -n '5p' file.txt"))
        self.assertTrue(guard.seg_is_read("sed 's/a/b/g' file"))

    def test_sed_write(self):
        self.assertFalse(guard.seg_is_read("sed -i 's/a/b/g' file"))
        self.assertFalse(guard.seg_is_read("sed -i.bak 's/a/b/' f"))

    # --- find ---
    def test_find_read(self):
        self.assertTrue(guard.seg_is_read("find . -name '*.log' -type f"))
        self.assertTrue(guard.seg_is_read("find /var/log -mtime +30"))

    def test_find_write(self):
        self.assertFalse(guard.seg_is_read("find . -name '*.tmp' -delete"))
        self.assertFalse(guard.seg_is_read("find . -exec rm {} ;"))

    # --- awk ---
    def test_awk_read(self):
        self.assertTrue(guard.seg_is_read("awk '{print $1}' file"))
        self.assertTrue(guard.seg_is_read("awk -F: '{print $1}' /etc/passwd"))

    def test_awk_write(self):
        self.assertFalse(guard.seg_is_read("awk '{print > \"out.txt\"}' f"))
        self.assertFalse(guard.seg_is_read("awk '{system(\"rm \" $1)}' f"))

    # --- DB clients ---
    def test_mysql_select_read(self):
        self.assertTrue(guard.seg_is_read("mysql -e 'SELECT * FROM users'"))

    def test_mysql_show_read(self):
        self.assertTrue(guard.seg_is_read("mysql -e 'SHOW TABLES'"))

    def test_mysql_insert_write(self):
        self.assertFalse(guard.seg_is_read("mysql -e 'INSERT INTO t VALUES(1)'"))

    def test_mysql_drop_write(self):
        self.assertFalse(guard.seg_is_read("mysql -e 'DROP TABLE users'"))

    def test_mysqldump_always_write(self):
        self.assertFalse(guard.seg_is_read("mysqldump mydb"))

    def test_mysqladmin_always_write(self):
        self.assertFalse(guard.seg_is_read("mysqladmin status"))

    def test_psql_select_read(self):
        self.assertTrue(guard.seg_is_read("psql -c 'SELECT 1'"))

    def test_sqlite3_read(self):
        self.assertTrue(guard.seg_is_read("sqlite3 db.sqlite '.tables'"))

    def test_mysql_from_file_write(self):
        self.assertFalse(guard.seg_is_read("mysql mydb < dump.sql"))

    def test_mysql_interactive_write(self):
        self.assertFalse(guard.seg_is_read("mysql mydb"))

    # --- apt/dpkg ---
    def test_apt_read(self):
        for sub in ("list", "show", "search", "policy"):
            self.assertTrue(guard.seg_is_read(f"apt {sub}"), f"apt {sub} should be READ")
        self.assertTrue(guard.seg_is_read("apt-cache search nginx"))

    def test_apt_write(self):
        self.assertFalse(guard.seg_is_read("apt install nginx"))
        self.assertFalse(guard.seg_is_read("apt-get remove nginx"))

    def test_dpkg_read(self):
        for flag in ("-l", "--list", "-s", "--status", "-S", "--search"):
            self.assertTrue(guard.seg_is_read(f"dpkg {flag}"), f"dpkg {flag} should be READ")

    def test_dpkg_write(self):
        self.assertFalse(guard.seg_is_read("dpkg -i package.deb"))

    # --- pip ---
    def test_pip_read(self):
        for sub in ("list", "show", "freeze", "check", "--version"):
            self.assertTrue(guard.seg_is_read(f"pip {sub}"), f"pip {sub} should be READ")
            self.assertTrue(guard.seg_is_read(f"pip3 {sub}"), f"pip3 {sub} should be READ")

    def test_pip_write(self):
        self.assertFalse(guard.seg_is_read("pip install flask"))
        self.assertFalse(guard.seg_is_read("pip3 uninstall flask"))

    # --- npm/yarn/pnpm ---
    def test_npm_read(self):
        for sub in ("list", "ls", "view", "info", "outdated", "audit", "--version"):
            self.assertTrue(guard.seg_is_read(f"npm {sub}"), f"npm {sub} should be READ")

    def test_npm_write(self):
        self.assertFalse(guard.seg_is_read("npm install"))
        self.assertFalse(guard.seg_is_read("npm run build"))
        self.assertFalse(guard.seg_is_read("yarn add express"))

    # --- ip ---
    def test_ip_read(self):
        self.assertTrue(guard.seg_is_read("ip addr"))
        self.assertTrue(guard.seg_is_read("ip route"))
        self.assertTrue(guard.seg_is_read("ip link"))

    def test_ip_write(self):
        self.assertFalse(guard.seg_is_read("ip addr add 10.0.0.1/24 dev eth0"))
        self.assertFalse(guard.seg_is_read("ip route del default"))

    def test_ip_no_args(self):
        self.assertTrue(guard.seg_is_read("ip"))

    # --- ufw ---
    def test_ufw_read(self):
        self.assertTrue(guard.seg_is_read("ufw status"))
        self.assertTrue(guard.seg_is_read("ufw show"))

    def test_ufw_write(self):
        self.assertFalse(guard.seg_is_read("ufw allow 80"))
        self.assertFalse(guard.seg_is_read("ufw deny 22"))

    # --- nginx ---
    def test_nginx_read(self):
        self.assertTrue(guard.seg_is_read("nginx -t"))
        self.assertTrue(guard.seg_is_read("nginx -T"))
        self.assertTrue(guard.seg_is_read("nginx -V"))

    def test_nginx_write(self):
        self.assertFalse(guard.seg_is_read("nginx -s reload"))

    def test_apache_read(self):
        self.assertTrue(guard.seg_is_read("apache2ctl -t"))
        self.assertTrue(guard.seg_is_read("httpd -V"))

    # --- fail2ban-client ---
    def test_fail2ban_read(self):
        self.assertTrue(guard.seg_is_read("fail2ban-client status"))
        self.assertTrue(guard.seg_is_read("fail2ban-client ping"))

    def test_fail2ban_write(self):
        self.assertFalse(guard.seg_is_read("fail2ban-client set sshd unbanip 1.2.3.4"))

    # --- certbot ---
    def test_certbot_read(self):
        self.assertTrue(guard.seg_is_read("certbot certificates"))
        self.assertTrue(guard.seg_is_read("certbot --help"))

    def test_certbot_write(self):
        self.assertFalse(guard.seg_is_read("certbot renew"))
        self.assertFalse(guard.seg_is_read("certbot certonly"))

    # --- timedatectl ---
    def test_timedatectl_read(self):
        self.assertTrue(guard.seg_is_read("timedatectl"))
        self.assertTrue(guard.seg_is_read("timedatectl status"))
        self.assertTrue(guard.seg_is_read("timedatectl list-timezones"))

    def test_timedatectl_write(self):
        self.assertFalse(guard.seg_is_read("timedatectl set-timezone Asia/Jakarta"))

    # --- loginctl ---
    def test_loginctl_read(self):
        self.assertTrue(guard.seg_is_read("loginctl list-sessions"))
        self.assertTrue(guard.seg_is_read("loginctl list-users"))

    def test_loginctl_write(self):
        self.assertFalse(guard.seg_is_read("loginctl terminate-session 5"))

    # --- curl ---
    def test_curl_read(self):
        self.assertTrue(guard.seg_is_read("curl https://example.com"))
        self.assertTrue(guard.seg_is_read("curl -s https://api.example.com/status"))

    def test_curl_write(self):
        self.assertFalse(guard.seg_is_read("curl -X POST https://api.example.com"))
        self.assertFalse(guard.seg_is_read("curl --data 'key=val' https://api.com"))
        self.assertFalse(guard.seg_is_read("curl -o file.tar.gz https://example.com/f"))
        self.assertFalse(guard.seg_is_read("curl -F 'file=@data' https://api.com"))

    # --- sudo ---
    def test_sudo_always_write(self):
        self.assertFalse(guard.seg_is_read("sudo ls"))
        self.assertFalse(guard.seg_is_read("sudo cat /etc/shadow"))

    # --- full path ---
    def test_full_path_stripped(self):
        self.assertTrue(guard.seg_is_read("/usr/bin/cat /etc/hosts"))
        self.assertTrue(guard.seg_is_read("/bin/ls -la"))


# ===========================================================================
# classify_command — integration (pipes, chains, redirects, danger)
# ===========================================================================
class TestClassifyCommand(unittest.TestCase):

    def test_empty_is_ask(self):
        self.assertEqual(guard.classify_command(""), "ask")
        self.assertEqual(guard.classify_command("   "), "ask")

    def test_pure_read(self):
        self.assertEqual(guard.classify_command("ls -la"), "allow")
        self.assertEqual(guard.classify_command("cat /etc/hosts"), "allow")

    def test_pipe_reads(self):
        self.assertEqual(guard.classify_command("ps aux | grep nginx"), "allow")
        self.assertEqual(guard.classify_command("cat f | sort | uniq"), "allow")

    def test_chain_reads(self):
        self.assertEqual(guard.classify_command("ls && pwd"), "allow")
        self.assertEqual(guard.classify_command("df -h; free -m"), "allow")

    def test_mixed_read_write_is_ask(self):
        self.assertEqual(guard.classify_command("ls && rm file"), "ask")

    def test_redirect_is_ask(self):
        self.assertEqual(guard.classify_command("echo hello > file.txt"), "ask")
        self.assertEqual(guard.classify_command("cat a >> b"), "ask")

    def test_redirect_devnull_ok(self):
        self.assertEqual(guard.classify_command("ls 2>/dev/null"), "allow")
        self.assertEqual(guard.classify_command("grep foo bar 2>&1"), "allow")

    def test_tee_is_ask(self):
        self.assertEqual(guard.classify_command("echo hello | tee file.txt"), "ask")

    def test_subshell_is_ask(self):
        self.assertEqual(guard.classify_command("echo $(cat secret)"), "ask")
        self.assertEqual(guard.classify_command("echo `cat secret`"), "ask")

    def test_danger_is_ask(self):
        self.assertEqual(guard.classify_command("rm -rf /"), "ask")
        self.assertEqual(guard.classify_command("shutdown now"), "ask")
        self.assertEqual(guard.classify_command("reboot"), "ask")
        self.assertEqual(guard.classify_command("mkfs.ext4 /dev/sda"), "ask")

    def test_sql_operator_not_confused_with_redirect(self):
        self.assertEqual(guard.classify_command("mysql -e 'SELECT a>b FROM t'"), "allow")
        self.assertEqual(guard.classify_command('mysql -e "SELECT count(*) FROM t WHERE id > 5"'), "allow")

    def test_kill_is_ask(self):
        self.assertEqual(guard.classify_command("kill 1234"), "ask")
        self.assertEqual(guard.classify_command("killall nginx"), "ask")
        self.assertEqual(guard.classify_command("pkill php"), "ask")

    def test_drop_database_is_ask(self):
        self.assertEqual(guard.classify_command("mysql -e 'DROP DATABASE mydb'"), "ask")

    def test_write_command(self):
        self.assertEqual(guard.classify_command("rm file.txt"), "ask")
        self.assertEqual(guard.classify_command("systemctl restart nginx"), "ask")
        self.assertEqual(guard.classify_command("composer install"), "ask")


# ===========================================================================
# _assess_db — database risk assessment
# ===========================================================================
class TestAssessDb(unittest.TestCase):

    def test_non_db_returns_none(self):
        self.assertIsNone(guard._assess_db("ls -la"))

    def test_select_is_aman(self):
        t, *_ = guard._assess_db("mysql -e 'SELECT * FROM users'")
        self.assertEqual(t, "AMAN")

    def test_show_is_aman(self):
        t, *_ = guard._assess_db("mysql -e 'SHOW TABLES'")
        self.assertEqual(t, "AMAN")

    def test_insert_is_rendah(self):
        t, *_ = guard._assess_db("mysql -e 'INSERT INTO t VALUES(1)'")
        self.assertEqual(t, "RENDAH")

    def test_create_table_is_rendah(self):
        t, *_ = guard._assess_db("mysql -e 'CREATE TABLE t (id INT)'")
        self.assertEqual(t, "RENDAH")

    def test_delete_with_where_is_sedang(self):
        t, *_ = guard._assess_db("mysql -e 'DELETE FROM t WHERE id=5'")
        self.assertEqual(t, "SEDANG")

    def test_update_with_where_is_sedang(self):
        t, *_ = guard._assess_db("mysql -e 'UPDATE t SET name=\"x\" WHERE id=1'")
        self.assertEqual(t, "SEDANG")

    def test_alter_table_is_sedang(self):
        t, *_ = guard._assess_db("mysql -e 'ALTER TABLE t ADD COLUMN c INT'")
        self.assertEqual(t, "SEDANG")

    def test_grant_is_sedang(self):
        t, *_ = guard._assess_db("mysql -e 'GRANT ALL ON db.* TO user'")
        self.assertEqual(t, "SEDANG")

    def test_delete_no_where_is_tinggi(self):
        t, *_ = guard._assess_db("mysql -e 'DELETE FROM t'")
        self.assertEqual(t, "TINGGI")

    def test_update_no_where_is_tinggi(self):
        t, *_ = guard._assess_db("mysql -e 'UPDATE t SET x=1'")
        self.assertEqual(t, "TINGGI")

    def test_truncate_is_tinggi(self):
        t, *_ = guard._assess_db("mysql -e 'TRUNCATE TABLE t'")
        self.assertEqual(t, "TINGGI")

    def test_drop_table_is_tinggi(self):
        t, *_ = guard._assess_db("mysql -e 'DROP TABLE users'")
        self.assertEqual(t, "TINGGI")

    def test_drop_database_is_kritis(self):
        t, *_ = guard._assess_db("mysql -e 'DROP DATABASE mydb'")
        self.assertEqual(t, "KRITIS")

    def test_mysqldump_is_rendah(self):
        t, *_ = guard._assess_db("mysqldump --single-transaction mydb")
        self.assertEqual(t, "RENDAH")

    def test_sql_from_file_is_tinggi(self):
        t, *_ = guard._assess_db("mysql mydb < dump.sql")
        self.assertEqual(t, "TINGGI")

    def test_outfile_is_sedang(self):
        t, *_ = guard._assess_db("mysql -e 'SELECT * INTO OUTFILE \"/tmp/out\" FROM t'")
        self.assertEqual(t, "SEDANG")

    def test_unknown_verb_is_sedang(self):
        t, *_ = guard._assess_db("mysql mydb")
        self.assertEqual(t, "SEDANG")

    def test_psql_select(self):
        t, *_ = guard._assess_db("psql -c 'SELECT 1'")
        self.assertEqual(t, "AMAN")


# ===========================================================================
# assess_command — shell rules (26 rules)
# ===========================================================================
class TestAssessCommand(unittest.TestCase):

    def _tier(self, cmd):
        return guard.assess_command(cmd)[0]

    # KRITIS
    def test_rm_rf_root_kritis(self):
        self.assertEqual(self._tier("rm -rf /"), "KRITIS")

    def test_mkfs_kritis(self):
        self.assertEqual(self._tier("mkfs.ext4 /dev/sda1"), "KRITIS")

    def test_dd_of_dev_kritis(self):
        self.assertEqual(self._tier("dd if=/dev/zero of=/dev/sda bs=1M"), "KRITIS")

    def test_shutdown_kritis(self):
        self.assertEqual(self._tier("shutdown -h now"), "KRITIS")

    def test_reboot_kritis(self):
        self.assertEqual(self._tier("reboot"), "KRITIS")

    def test_chmod_777_root_kritis(self):
        self.assertEqual(self._tier("chmod -R 777 /"), "KRITIS")

    def test_fork_bomb_kritis(self):
        self.assertEqual(self._tier(":(){ :|:& };:"), "KRITIS")

    # TINGGI
    def test_rm_rf_path_tinggi(self):
        self.assertEqual(self._tier("rm -rf /var/www/app"), "TINGGI")

    def test_git_reset_hard_tinggi(self):
        self.assertEqual(self._tier("git reset --hard HEAD~3"), "TINGGI")

    def test_git_clean_f_tinggi(self):
        self.assertEqual(self._tier("git clean -fd"), "TINGGI")

    def test_killall_tinggi(self):
        self.assertEqual(self._tier("killall nginx"), "TINGGI")

    def test_pkill_tinggi(self):
        self.assertEqual(self._tier("pkill php-fpm"), "TINGGI")

    def test_find_delete_tinggi(self):
        self.assertEqual(self._tier("find /tmp -name '*.log' -delete"), "TINGGI")

    def test_find_exec_tinggi(self):
        self.assertEqual(self._tier("find . -exec rm {} ;"), "TINGGI")

    # SEDANG
    def test_apt_install_sedang(self):
        self.assertEqual(self._tier("apt install nginx"), "SEDANG")

    def test_systemctl_restart_sedang(self):
        self.assertEqual(self._tier("systemctl restart nginx"), "SEDANG")

    def test_artisan_migrate_sedang(self):
        self.assertEqual(self._tier("php artisan migrate"), "SEDANG")

    def test_chown_sedang(self):
        self.assertEqual(self._tier("chown www-data:www-data file"), "SEDANG")

    def test_chmod_sedang(self):
        self.assertEqual(self._tier("chmod 755 file"), "SEDANG")

    def test_mv_sedang(self):
        self.assertEqual(self._tier("mv old new"), "SEDANG")

    def test_git_checkout_sedang(self):
        self.assertEqual(self._tier("git checkout main"), "SEDANG")

    def test_crontab_sedang(self):
        self.assertEqual(self._tier("crontab -e"), "SEDANG")

    def test_npm_build_sedang(self):
        self.assertEqual(self._tier("npm run build"), "SEDANG")

    def test_composer_install_sedang(self):
        self.assertEqual(self._tier("composer install"), "SEDANG")

    def test_rm_file_sedang(self):
        self.assertEqual(self._tier("rm file.txt"), "SEDANG")

    # RENDAH
    def test_systemctl_reload_rendah(self):
        self.assertEqual(self._tier("systemctl reload nginx"), "RENDAH")

    def test_nginx_reload_rendah(self):
        self.assertEqual(self._tier("nginx -s reload"), "RENDAH")

    def test_mkdir_rendah(self):
        self.assertEqual(self._tier("mkdir -p /var/www/new"), "RENDAH")

    def test_cp_rendah(self):
        self.assertEqual(self._tier("cp a b"), "RENDAH")

    def test_git_commit_rendah(self):
        self.assertEqual(self._tier("git commit -m 'fix'"), "RENDAH")

    def test_git_push_rendah(self):
        self.assertEqual(self._tier("git push origin main"), "RENDAH")

    def test_redirect_rendah(self):
        self.assertEqual(self._tier("echo hello > /tmp/test"), "RENDAH")

    # Fallback: unclassified write
    def test_unknown_write_sedang(self):
        self.assertEqual(self._tier("some_random_write_tool"), "SEDANG")

    # Compound: highest tier wins
    def test_compound_highest_tier(self):
        tier = self._tier("git status && rm -rf /var/www/app")
        self.assertEqual(tier, "TINGGI")


# ===========================================================================
# risk_card — format dan mode production
# ===========================================================================
class TestRiskCard(unittest.TestCase):

    def test_contains_tier(self):
        card = guard.risk_card("ls -la")
        self.assertIn("RISIKO:", card)

    def test_contains_cmd(self):
        card = guard.risk_card("rm -rf /tmp/x")
        self.assertIn("rm -rf /tmp/x", card)

    def test_contains_cwd(self):
        card = guard.risk_card("rm file", cwd="/var/www")
        self.assertIn("Dir   : /var/www", card)

    def test_production_tier_shift(self):
        card_deploy = guard.risk_card("systemctl reload nginx", mode="deploy")
        card_prod = guard.risk_card("systemctl reload nginx", mode="production")
        self.assertIn("RENDAH", card_deploy)
        self.assertIn("SEDANG", card_prod)
        self.assertIn("PRODUCTION", card_prod)

    def test_kritis_not_shifted(self):
        card = guard.risk_card("rm -rf /", mode="production")
        self.assertIn("KRITIS", card)

    def test_kritis_shows_allow_dangerous_warning(self):
        card = guard.risk_card("rm -rf /")
        self.assertIn("allow_dangerous", card)

    def test_extra_text_appended(self):
        card = guard.risk_card("rm file", extra="CUSTOM WARNING")
        self.assertIn("CUSTOM WARNING", card)


# ===========================================================================
# service_card
# ===========================================================================
class TestServiceCard(unittest.TestCase):

    def test_reload_rendah(self):
        card = guard.service_card("nginx", "reload")
        self.assertIn("RENDAH", card)

    def test_restart_sedang(self):
        card = guard.service_card("nginx", "restart")
        self.assertIn("SEDANG", card)

    def test_stop_tinggi(self):
        card = guard.service_card("mysql", "stop")
        self.assertIn("TINGGI", card)

    def test_start_rendah(self):
        card = guard.service_card("nginx", "start")
        self.assertIn("RENDAH", card)

    def test_production_shift(self):
        card = guard.service_card("nginx", "restart", mode="production")
        self.assertIn("TINGGI", card)
        self.assertIn("PRODUCTION", card)


# ===========================================================================
# _shift_tier
# ===========================================================================
class TestShiftTier(unittest.TestCase):

    def test_aman_to_rendah(self):
        self.assertEqual(guard._shift_tier("AMAN"), "RENDAH")

    def test_rendah_to_sedang(self):
        self.assertEqual(guard._shift_tier("RENDAH"), "SEDANG")

    def test_sedang_to_tinggi(self):
        self.assertEqual(guard._shift_tier("SEDANG"), "TINGGI")

    def test_tinggi_to_kritis(self):
        self.assertEqual(guard._shift_tier("TINGGI"), "KRITIS")

    def test_kritis_stays_kritis(self):
        self.assertEqual(guard._shift_tier("KRITIS"), "KRITIS")


# ===========================================================================
# DANGER pattern — catastrophic commands
# ===========================================================================
class TestDangerPattern(unittest.TestCase):

    def test_rm_rf_root(self):
        self.assertTrue(guard.DANGER.search("rm -rf /"))

    def test_rm_rf_home(self):
        self.assertTrue(guard.DANGER.search("rm -rf ~"))

    def test_mkfs(self):
        self.assertTrue(guard.DANGER.search("mkfs.ext4 /dev/sda"))

    def test_dd_of_dev(self):
        self.assertTrue(guard.DANGER.search("dd if=/dev/zero of=/dev/sda"))

    def test_fork_bomb(self):
        self.assertTrue(guard.DANGER.search(":(){ :|:& };:"))

    def test_shutdown(self):
        self.assertTrue(guard.DANGER.search("shutdown -h now"))

    def test_reboot(self):
        self.assertTrue(guard.DANGER.search("reboot"))

    def test_drop_database(self):
        self.assertTrue(guard.DANGER.search("drop database mydb"))

    def test_kill(self):
        self.assertTrue(guard.DANGER.search("kill 1234"))
        self.assertTrue(guard.DANGER.search("killall nginx"))
        self.assertTrue(guard.DANGER.search("pkill php"))

    def test_safe_not_matched(self):
        self.assertIsNone(guard.DANGER.search("ls -la"))
        self.assertIsNone(guard.DANGER.search("cat /etc/hosts"))
        self.assertIsNone(guard.DANGER.search("git status"))


# ===========================================================================
# _get_mode / mode behavior
# ===========================================================================
class TestGetMode(unittest.TestCase):

    def test_default_mode_is_deploy(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            mode = guard._get_mode()
            self.assertIn(mode, ("setup", "deploy", "production"))

    def test_env_var_production(self):
        with unittest.mock.patch.dict(os.environ, {"ODIN_MODE": "production"}):
            self.assertEqual(guard._get_mode(), "production")

    def test_env_var_invalid(self):
        with unittest.mock.patch.dict(os.environ, {"ODIN_MODE": "invalid"}):
            self.assertEqual(guard._get_mode(), "deploy")


import unittest.mock

if __name__ == "__main__":
    unittest.main()
