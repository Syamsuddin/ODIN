#!/usr/bin/env bash
export DEPLOY_MODE=local
export PROJECT_ROOT=/var/www/simuru
export ALLOWED_LOG_DIRS=/var/log,/var/www
# Memory ODIN: SENGAJA di luar PROJECT_ROOT (tak kena sandbox run_command,
# tak ikut `git reset --hard` saat deploy). Folder dibuat otomatis (perm 700).
export MEMORY_DIR=/home/deploy/agent/memory
cd "$PROJECT_ROOT" || { echo "FATAL: $PROJECT_ROOT tidak bisa diakses" >&2; exit 1; }
exec /home/deploy/agent/.venv/bin/python /home/deploy/agent/deploy_agent.py
