# ODIN — Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

---

## [0.9.0] — 2026-06-06

Rilis pertama dengan nama ODIN. Seluruh fitur inti lengkap dan teruji (80 test).

### Added

- **Server Profile & Auto-Mode** (Fase 4)
  - `inspect_server` tool — full inspection on-demand
  - Startup auto-inspection: OS, kernel, disk, memory, firewall, SSH, cron, users
  - Type detection: `web-app`, `database`, `container`, `general`
  - Stack-specific deep scan: web (nginx/apache/PHP/FPM/composer/node/DB/Redis/SSL), database (MySQL/PG/Mongo/backups), container (Docker/compose)
  - App inspection: .env, vendor, framework detection (Laravel/Django/Node), git state
  - Operation mode derivation: `setup` / `deploy` / `production` — dari data, bukan manual
  - Mode override via memory (`server:mode-override`)
  - Dual enforcement: server `_mode_gate` blocks + guard `_shift_tier` risk escalation
  - Profile summary persisted to memory (`server:stack-profile`, pinned)
  - `ODIN_SKIP_INSPECT=1` env var for testing

- **Runbook Engine** (Fase 3)
  - `runbook` tool — multi-step workflow execution (max 20 steps)
  - Per-step error analysis and rollback tracking
  - `continue_on_fail` per step
  - Guard: risk tier = max write step; all-read runbooks auto-allow

- **Rollback Tracking** (Fase 3)
  - `_capture_pre_state` before destructive commands (git, migrate, service)
  - `_suggest_rollback` generates undo commands from captured state
  - `rollback_plan` tool — actionable rollback suggestions from session history
  - `_rollback_hint` attached to tool results

- **Output Intelligence** (Fase 2)
  - 22 error patterns: DB (SQLSTATE, deadlock, max conn), PHP/Laravel (fatal, OOM, timeout), system (disk full, OOM kill, permission denied), tools (nginx, SSL, npm)
  - `_analyze_output()` attaches `_analysis` to failed commands

- **Session History** (Fase 2)
  - `session_history` tool — in-memory log of all tool executions per session

- **Pre-flight Checks** (Fase 2)
  - `_preflight_deploy` — disk, git dirty, commit, PHP version check before deploy
  - Blockers abort deploy with report

- **Audit Log** (Fase 2)
  - Append-only `audit.jsonl` — every tool execution recorded
  - Disable with `AUDIT_ENABLED=0`

- **Memory System** (Fase 0)
  - 3 namespaces: `server`, `instruction`, `profile`
  - 4 tools: `memory_write`, `memory_recall`, `memory_forget`, `memory_digest`
  - Append-only JSONL + fold + TTL + tombstone + compaction
  - Secret detection (password, token, private key, JWT, AWS key)
  - Auto-inject digest to FastMCP instructions on startup

- **Core Tools** (Fase 0)
  - `run_command` — shell execution with READ/WRITE classification
  - `tail_log` — log file reader with allowed dirs
  - `service_action` — systemd management (status/restart/reload/start/stop)
  - `laravel_deploy` — one-button Laravel deploy with pre-flight
  - `run_tests` — PHPUnit/Pest test runner
  - `http_health_check` — HTTP status verification
  - `server_info` — server summary

- **Security Model** (Fase 0-1)
  - PreToolUse guard with READ/WRITE classifier (`seg_is_read`)
  - 20+ sub-command classifiers (git, docker, mysql, npm, curl, ufw, nginx, etc.)
  - Risk engine: 5-tier cards (AMAN/RENDAH/SEDANG/TINGGI/KRITIS) + 26 shell rules
  - `_DANGER_RE` hard-block for catastrophic commands (server-side)
  - Command substitution (`$()`, backticks) detection — forces "ask"
  - DB read/write classification (SELECT/SHOW → allow, DML/DDL → ask)
  - Production mode: tier shift +1, `MODE PRODUCTION` warning on risk cards

- **Versioning**
  - `__version__` constant in both Python files
  - `CHANGELOG.md` for tracking changes

### Changed

- MCP server name: `deploy-agent` → `odin` (tool prefix: `mcp__odin__`)
- Logger name: `deploy-agent` → `odin`
- Memory digest header: `MEMORY deploy-agent` → `MEMORY ODIN`
- All documentation updated with ODIN branding

---

## [0.0.0] — 2026-05-xx

Initial commit. Bare MCP server skeleton.
