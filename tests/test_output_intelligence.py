"""Test Output Intelligence — _analyze_output untuk semua 23 error pattern + edge cases."""
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
spec = importlib.util.spec_from_file_location("odin_agent", ROOT / "server" / "odin_agent.py")
da = importlib.util.module_from_spec(spec)
spec.loader.exec_module(da)


def _fail(stderr="", stdout="", exit_code=1):
    return {"success": False, "exit_code": exit_code, "stdout": stdout, "stderr": stderr}


def _ok(stdout="", stderr=""):
    return {"success": True, "exit_code": 0, "stdout": stdout, "stderr": stderr}


# ===========================================================================
# _analyze_output — semua 23 error patterns
# ===========================================================================
class TestAnalyzeOutput(unittest.TestCase):

    def test_success_returns_empty(self):
        self.assertEqual(da._analyze_output(_ok("all good")), {})

    def test_timeout_detected(self):
        r = {"success": False, "exit_code": None, "timeout": True, "stdout": "", "stderr": ""}
        a = da._analyze_output(r)
        self.assertEqual(a["error_type"], "timeout")
        self.assertTrue(any("timeout" in h.lower() for h in a["hints"]))

    # --- Database patterns ---
    def test_db_auth_1045(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[HY000] [1045] Access denied for user 'root'"))
        self.assertEqual(a["error_type"], "db_auth")

    def test_db_auth_access_denied(self):
        a = da._analyze_output(_fail(stderr="Access denied for user 'simuru'@'localhost'"))
        self.assertEqual(a["error_type"], "db_auth")

    def test_db_conn_2002(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[HY000] [2002] Connection refused"))
        self.assertEqual(a["error_type"], "db_conn")

    def test_db_conn_cant_connect(self):
        a = da._analyze_output(_fail(stderr="Can't connect to local MySQL server"))
        self.assertEqual(a["error_type"], "db_conn")

    def test_db_table_missing(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[42S02] Base table 'users' not found"))
        self.assertEqual(a["error_type"], "db_table_missing")

    def test_db_table_missing_alt(self):
        a = da._analyze_output(_fail(stderr="Table 'mydb.sessions' doesn't exist"))
        self.assertEqual(a["error_type"], "db_table_missing")

    def test_db_column_missing(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[42S22] Unknown column 'foo'"))
        self.assertEqual(a["error_type"], "db_column_missing")

    def test_db_constraint(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[23000] Integrity constraint violation: Duplicate entry '1'"))
        self.assertEqual(a["error_type"], "db_constraint")

    def test_db_max_conn(self):
        a = da._analyze_output(_fail(stderr="Too many connections"))
        self.assertEqual(a["error_type"], "db_max_conn")

    def test_db_lock(self):
        a = da._analyze_output(_fail(stderr="Deadlock found when trying to get lock"))
        self.assertEqual(a["error_type"], "db_lock")

    def test_db_lock_timeout(self):
        a = da._analyze_output(_fail(stderr="lock wait timeout exceeded"))
        self.assertEqual(a["error_type"], "db_lock")

    def test_db_generic_sqlstate(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[99999] Something weird"))
        self.assertEqual(a["error_type"], "db_error")

    # --- PHP / Laravel ---
    def test_class_not_found(self):
        a = da._analyze_output(_fail(stderr="Class 'App\\Models\\User' not found"))
        self.assertEqual(a["error_type"], "class_not_found")

    def test_reflection_exception(self):
        a = da._analyze_output(_fail(stderr="ReflectionException: Class does not exist"))
        self.assertEqual(a["error_type"], "class_not_found")

    def test_php_fatal(self):
        a = da._analyze_output(_fail(stderr="PHP Fatal error: Uncaught Error in /var/www"))
        self.assertEqual(a["error_type"], "php_fatal")

    def test_php_parse_error(self):
        a = da._analyze_output(_fail(stderr="PHP Parse error: syntax error in file.php"))
        self.assertEqual(a["error_type"], "php_fatal")

    def test_php_oom(self):
        a = da._analyze_output(_fail(stderr="Allowed memory size of 134217728 bytes exhausted"))
        self.assertEqual(a["error_type"], "php_oom")

    def test_php_timeout(self):
        a = da._analyze_output(_fail(stderr="Maximum execution time of 30 seconds exceeded"))
        self.assertEqual(a["error_type"], "php_timeout")

    def test_composer_lock(self):
        a = da._analyze_output(_fail(stderr="Your lock file does not contain a compatible set of packages"))
        self.assertEqual(a["error_type"], "composer_lock")

    def test_composer_detected_issues(self):
        a = da._analyze_output(_fail(stderr="Composer detected issues in your platform"))
        self.assertEqual(a["error_type"], "composer_lock")

    # --- Sistem ---
    def test_disk_full(self):
        a = da._analyze_output(_fail(stderr="No space left on device"))
        self.assertEqual(a["error_type"], "disk_full")

    def test_oom_kill(self):
        a = da._analyze_output(_fail(stderr="Out of memory: Killed process 1234"))
        self.assertEqual(a["error_type"], "killed")

    def test_killed_sigkill(self):
        a = da._analyze_output(_fail(stderr="signal 9: SIGKILL"))
        self.assertEqual(a["error_type"], "killed")

    def test_cannot_allocate(self):
        a = da._analyze_output(_fail(stderr="Cannot allocate memory"))
        self.assertEqual(a["error_type"], "killed")

    def test_permission_denied(self):
        a = da._analyze_output(_fail(stderr="Permission denied"))
        self.assertEqual(a["error_type"], "permission")

    def test_command_not_found(self):
        a = da._analyze_output(_fail(stderr="bash: foo: command not found"))
        self.assertEqual(a["error_type"], "missing_cmd")

    def test_conn_refused(self):
        a = da._analyze_output(_fail(stderr="Connection refused"))
        self.assertEqual(a["error_type"], "conn_refused")

    def test_file_not_found(self):
        a = da._analyze_output(_fail(stderr="failed to open stream: No such file or directory"))
        self.assertEqual(a["error_type"], "file_not_found")

    def test_no_such_file(self):
        a = da._analyze_output(_fail(stderr="No such file or directory"))
        self.assertEqual(a["error_type"], "file_not_found")

    def test_port_in_use(self):
        a = da._analyze_output(_fail(stderr="Address already in use"))
        self.assertEqual(a["error_type"], "port_in_use")

    def test_port_in_use_alt(self):
        a = da._analyze_output(_fail(stderr="port 80 already in use"))
        self.assertEqual(a["error_type"], "port_in_use")

    # --- Tool-specific ---
    def test_nginx_config(self):
        a = da._analyze_output(_fail(stderr="nginx: [emerg] unexpected end of file"))
        self.assertEqual(a["error_type"], "nginx_config")

    def test_nginx_test_failed(self):
        a = da._analyze_output(_fail(stderr="nginx: configuration file test failed"))
        self.assertEqual(a["error_type"], "nginx_config")

    def test_ssl_error(self):
        a = da._analyze_output(_fail(stderr="SSL certificate expired"))
        self.assertEqual(a["error_type"], "ssl")

    def test_ssl_error_generic(self):
        a = da._analyze_output(_fail(stderr="SSL_ERROR_HANDSHAKE_FAILURE"))
        self.assertEqual(a["error_type"], "ssl")

    def test_npm_error(self):
        a = da._analyze_output(_fail(stderr="npm ERR! code ELIFECYCLE"))
        self.assertEqual(a["error_type"], "npm_error")

    # --- Edge cases ---
    def test_generic_failure_nonzero_exit(self):
        a = da._analyze_output(_fail(stderr="something unknown", exit_code=2))
        self.assertEqual(a["error_type"], "generic_failure")
        self.assertTrue(any("Exit code 2" in h for h in a["hints"]))

    def test_no_hints_empty_output(self):
        r = {"success": False, "exit_code": 0, "stdout": "", "stderr": ""}
        a = da._analyze_output(r)
        self.assertEqual(a, {})

    def test_multiple_patterns_all_hints_collected(self):
        a = da._analyze_output(_fail(
            stderr="SQLSTATE[HY000] [1045] Access denied\nPermission denied"
        ))
        self.assertEqual(a["error_type"], "db_auth")
        self.assertTrue(len(a["hints"]) >= 2)

    def test_error_in_stdout(self):
        a = da._analyze_output(_fail(stdout="npm ERR! code ENOENT", stderr=""))
        self.assertEqual(a["error_type"], "npm_error")

    def test_pattern_priority_specific_before_generic(self):
        a = da._analyze_output(_fail(
            stderr="SQLSTATE[HY000] [2002] Connection refused"
        ))
        self.assertEqual(a["error_type"], "db_conn")


# ===========================================================================
# _build_summary
# ===========================================================================
class TestBuildSummary(unittest.TestCase):

    def test_ok_summary(self):
        r = {"success": True, "command": "ls", "duration_sec": 0.1, "exit_code": 0}
        s = da._build_summary(r)
        self.assertIn("OK", s)
        self.assertIn("ls", s)

    def test_blocked_summary(self):
        r = {"blocked": True, "command": "rm -rf /", "success": False}
        s = da._build_summary(r)
        self.assertIn("DITOLAK", s)

    def test_blocked_by_mode(self):
        r = {"blocked_by_mode": True, "command": "apt install x", "mode": "production", "success": False}
        s = da._build_summary(r)
        self.assertIn("DIBLOKIR", s)
        self.assertIn("production", s)

    def test_timeout_summary(self):
        r = {"timeout": True, "command": "long_cmd", "duration_sec": 180, "success": False}
        s = da._build_summary(r)
        self.assertIn("TIMEOUT", s)

    def test_failure_with_analysis(self):
        r = {"success": False, "exit_code": 1, "command": "php x",
             "_analysis": {"error_type": "php_fatal"}}
        s = da._build_summary(r)
        self.assertIn("GAGAL", s)
        self.assertIn("php_fatal", s)


if __name__ == "__main__":
    unittest.main()
