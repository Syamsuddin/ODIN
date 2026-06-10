#!/usr/bin/env bash
# ODIN v1.3.0 — MCP Server launcher (lihat /home/odin/odin_agent.py)
export DEPLOY_MODE=local
export PROJECT_ROOT=/var/www/simuru
export ALLOWED_LOG_DIRS=/var/log,/var/www/simuru
export MEMORY_DIR=/home/odin/memory
cd "$PROJECT_ROOT" || { echo "FATAL: $PROJECT_ROOT tidak bisa diakses" >&2; exit 1; }
exec /home/odin/.venv/bin/python /home/odin/odin_agent.py
