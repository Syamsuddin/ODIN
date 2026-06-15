#!/usr/bin/env bash
# ODIN v2.0 — Multi-project MCP launcher
# Usage: run.sh [--project <name>]
#
# Mode:
#   --project <name>  →  source projects/<name>.conf, memory di memory/<name>/
#   (tanpa flag)      →  backward-compatible: single .conf atau env vars lama

ODIN_HOME="$(cd "$(dirname "$0")" && pwd)"
PROJECTS_DIR="$ODIN_HOME/projects"

# --- Parse --project ---
PROJECT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# --- Resolve config ---
if [[ -n "$PROJECT" ]]; then
    CONF="$PROJECTS_DIR/$PROJECT.conf"
    if [[ ! -f "$CONF" ]]; then
        echo "FATAL: project '$PROJECT' tidak ditemukan ($CONF)" >&2
        if [[ -d "$PROJECTS_DIR" ]]; then
            echo "  Projects tersedia:" >&2
            ls "$PROJECTS_DIR"/*.conf 2>/dev/null | xargs -I{} basename {} .conf | sed 's/^/    /' >&2
        fi
        exit 1
    fi
    source "$CONF"
    export MEMORY_DIR="${MEMORY_DIR:-$ODIN_HOME/memory/$PROJECT}"
else
    CONF_COUNT=0
    if [[ -d "$PROJECTS_DIR" ]]; then
        CONF_COUNT=$(find "$PROJECTS_DIR" -maxdepth 1 -name "*.conf" 2>/dev/null | wc -l | tr -d ' ')
    fi
    if [[ "$CONF_COUNT" -eq 1 ]]; then
        source "$PROJECTS_DIR"/*.conf
        PROJECT="${PROJECT_NAME:-legacy}"
        export MEMORY_DIR="${MEMORY_DIR:-$ODIN_HOME/memory/$PROJECT}"
    else
        : "${PROJECT_ROOT:=/var/www/html}"
        : "${ALLOWED_LOG_DIRS:=/var/log}"
        : "${MEMORY_DIR:=$ODIN_HOME/memory}"
    fi
fi

export DEPLOY_MODE="${DEPLOY_MODE:-local}"
export PROJECT_NAME="${PROJECT_NAME:-}"
export PROJECT_ROOT
export ALLOWED_LOG_DIRS
export MEMORY_DIR
export GLOBAL_MEMORY_DIR="${GLOBAL_MEMORY_DIR:-$ODIN_HOME/memory/_cortex}"

cd "$PROJECT_ROOT" || { echo "FATAL: $PROJECT_ROOT tidak bisa diakses" >&2; exit 1; }
exec "$ODIN_HOME/.venv/bin/python" "$ODIN_HOME/odin_agent.py"
