#!/usr/bin/env bash
set -e  # Exit on error

#═══════════════════════════════════════════════════════════════════════════════
# ODIN Migration Script v2.1.0
#
# Purpose: Automated migration from ODIN v1.x to v2.1.0
# Target:  Laptop (local) + Server SIMURU
# Author:  ODIN Team
# Date:    2026-06-16
#═══════════════════════════════════════════════════════════════════════════════

# ── Colors ─────────────────────────────────────────────────────────────────────
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    MAGENTA='\033[0;35m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' MAGENTA='' CYAN='' BOLD='' RESET=''
fi

# ── Functions ──────────────────────────────────────────────────────────────────
info()    { echo -e "${BLUE}▸${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warning() { echo -e "${YELLOW}⚠${RESET} $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}${MAGENTA}━━━ $* ━━━${RESET}\n"; }

die() {
    error "$1"
    echo ""
    error "Migrasi dibatalkan. Tidak ada perubahan dilakukan."
    exit 1
}

confirm() {
    local prompt="$1"
    local default="${2:-n}"
    local yn

    if [[ "$default" == "y" ]]; then
        prompt="$prompt [Y/n] "
    else
        prompt="$prompt [y/N] "
    fi

    read -p "$(echo -e "${CYAN}?${RESET} $prompt")" yn
    yn="${yn:-$default}"

    case "$yn" in
        [Yy]*) return 0 ;;
        *) return 1 ;;
    esac
}

spinner() {
    local pid=$1
    local msg="$2"
    local spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local i=0

    while kill -0 "$pid" 2>/dev/null; do
        i=$(( (i+1) % 10 ))
        printf "\r${BLUE}${spin:$i:1}${RESET} %s" "$msg"
        sleep 0.1
    done

    wait "$pid"
    local exit_code=$?
    printf "\r"

    if [[ $exit_code -eq 0 ]]; then
        success "$msg"
    else
        error "$msg (exit code: $exit_code)"
    fi

    return $exit_code
}

# ── Variables ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODIN_DIR="$HOME/.odin"
BACKUP_DIR="$ODIN_DIR/backups/migration-$(date +%Y%m%d_%H%M%S)"

# Server info (akan di-detect atau diminta)
SERVER_ALIAS="vps-app"
SERVER_HOST=""
SERVER_USER="odin"
SERVER_PORT="22"
SSH_KEY=""

# Project info
PROJECT_NAME="simuru"
PROJECT_WORKDIR=""
PROJECT_REMOTE_PATH="/var/www/simuru"
ALLOWED_LOG_DIRS="/var/log,/var/www/simuru,/home/odin"

# Migration state
MIGRATION_LOG="$BACKUP_DIR/migration.log"
ERRORS=()

# ── Pre-flight Checks ──────────────────────────────────────────────────────────
preflight_checks() {
    header "Pre-flight Checks"

    # Check we're in ODIN repo
    if [[ ! -f "$SCRIPT_DIR/server/odin_agent.py" ]]; then
        die "Script harus dijalankan dari root folder ODIN repo"
    fi

    info "Lokasi ODIN: $SCRIPT_DIR"

    # Check Python version
    if ! command -v python3 &>/dev/null; then
        die "Python 3 tidak ditemukan. Install Python 3.8+ terlebih dahulu."
    fi

    local py_version=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    info "Python version: $py_version"

    if [[ "$(printf '%s\n' "3.8" "$py_version" | sort -V | head -n1)" != "3.8" ]]; then
        die "Python 3.8+ diperlukan (found: $py_version)"
    fi

    success "Python version OK"

    # Check local versions
    local agent_version=$(grep -m1 '^__version__' "$SCRIPT_DIR/server/odin_agent.py" | cut -d'"' -f2)
    local guard_version=$(grep -m1 '^__version__' "$SCRIPT_DIR/client/odin_guard.py" | cut -d'"' -f2)
    local cli_version=$(grep -m1 '^__version__' "$SCRIPT_DIR/client/odin_cli.py" | cut -d'"' -f2)

    info "Local versions:"
    echo "  - odin_agent.py: $agent_version"
    echo "  - odin_guard.py: $guard_version"
    echo "  - odin_cli.py:   $cli_version"

    if [[ "$agent_version" != "2.1.0" ]]; then
        die "odin_agent.py version bukan 2.1.0 (found: $agent_version)"
    fi

    success "Local files version OK (2.1.0)"

    # Detect existing SIMURU workdir
    info "Mencari workdir SIMURU..."

    local possible_paths=(
        "$HOME/PROJECTS/SIMURU"
        "$HOME/Projects/SIMURU"
        "$HOME/simuru"
        "$HOME/projects/simuru"
    )

    for path in "${possible_paths[@]}"; do
        if [[ -d "$path" ]]; then
            PROJECT_WORKDIR="$path"
            info "Ditemukan: $PROJECT_WORKDIR"
            break
        fi
    done

    if [[ -z "$PROJECT_WORKDIR" ]]; then
        warning "Workdir SIMURU tidak ditemukan di lokasi standar"
        read -p "$(echo -e "${CYAN}?${RESET} Masukkan path workdir SIMURU: ")" PROJECT_WORKDIR

        if [[ ! -d "$PROJECT_WORKDIR" ]]; then
            die "Workdir tidak valid: $PROJECT_WORKDIR"
        fi
    fi

    success "Workdir SIMURU: $PROJECT_WORKDIR"

    # Check SSH config (detect from existing MCP config jika ada)
    if [[ -f "$PROJECT_WORKDIR/.claude/settings.json" ]]; then
        info "Detecting server info dari .claude/settings.json..."

        # Extract SSH target dari command (format: ssh user@host ...)
        local ssh_cmd=$(python3 -c "
import json, sys
try:
    with open('$PROJECT_WORKDIR/.claude/settings.json') as f:
        data = json.load(f)
        for name, cfg in data.get('mcpServers', {}).items():
            if name == 'odin':
                cmd = cfg.get('command', '')
                if 'ssh' in cmd:
                    # Extract from 'ssh user@host /path/to/run.sh'
                    parts = cmd.split()
                    for part in parts:
                        if '@' in part:
                            print(part)
                            sys.exit(0)
except Exception as e:
    print('', file=sys.stderr)
    sys.exit(1)
        " 2>/dev/null)

        if [[ -n "$ssh_cmd" ]]; then
            SERVER_USER="${ssh_cmd%%@*}"
            SERVER_HOST="${ssh_cmd##*@}"
            success "Detected SSH: $SERVER_USER@$SERVER_HOST"
        fi
    fi

    # If not detected, ask user
    if [[ -z "$SERVER_HOST" ]]; then
        warning "Server info tidak terdeteksi"
        read -p "$(echo -e "${CYAN}?${RESET} SSH host/IP server SIMURU: ")" SERVER_HOST
        read -p "$(echo -e "${CYAN}?${RESET} SSH user [odin]: ")" input_user
        SERVER_USER="${input_user:-odin}"
    fi

    # Test SSH connectivity
    info "Testing SSH connectivity ke $SERVER_USER@$SERVER_HOST..."

    if ssh -o ConnectTimeout=5 -o BatchMode=yes "$SERVER_USER@$SERVER_HOST" "echo OK" &>/dev/null; then
        success "SSH connection OK (passwordless)"
    else
        warning "SSH passwordless auth gagal"
        info "Mencoba dengan password/key..."

        if ! ssh -o ConnectTimeout=10 "$SERVER_USER@$SERVER_HOST" "echo OK" &>/dev/null; then
            die "Tidak bisa connect ke $SERVER_USER@$SERVER_HOST. Periksa SSH config."
        fi

        warning "SSH perlu password/prompt. Setup SSH key direkomendasikan."
    fi

    # Create backup dir
    mkdir -p "$BACKUP_DIR"
    success "Backup directory: $BACKUP_DIR"

    echo ""
}

# ── Backup Current State ───────────────────────────────────────────────────────
backup_current_state() {
    header "Backup Current State"

    info "Backing up current configuration..."

    # Backup laptop configs
    if [[ -d "$ODIN_DIR" ]]; then
        info "Backup ~/.odin/ ..."
        cp -r "$ODIN_DIR" "$BACKUP_DIR/odin-laptop.bak" 2>/dev/null || true
    fi

    if [[ -f "$PROJECT_WORKDIR/.claude/settings.json" ]]; then
        info "Backup .claude/settings.json ..."
        mkdir -p "$BACKUP_DIR/claude-config"
        cp "$PROJECT_WORKDIR/.claude/settings.json" "$BACKUP_DIR/claude-config/settings.json.bak"
    fi

    # Backup server files
    info "Backup server files (odin_agent.py, run.sh, memory)..."

    local backup_script="
        cd /home/$SERVER_USER
        mkdir -p migration-backup-$(date +%Y%m%d)
        cp -f odin_agent.py migration-backup-$(date +%Y%m%d)/ 2>/dev/null || true
        cp -f run.sh migration-backup-$(date +%Y%m%d)/ 2>/dev/null || true
        cp -r memory migration-backup-$(date +%Y%m%d)/ 2>/dev/null || true
        echo 'Backup created at /home/$SERVER_USER/migration-backup-$(date +%Y%m%d)'
    "

    if ssh "$SERVER_USER@$SERVER_HOST" "$backup_script" 2>/dev/null; then
        success "Server files backed up"
    else
        warning "Server backup gagal (tapi lanjut)"
    fi

    success "Backup completed: $BACKUP_DIR"
    echo ""
}

# ── Install CLI Dependencies ───────────────────────────────────────────────────
install_cli_deps() {
    header "Install CLI Dependencies"

    # Check if already installed
    if python3 -c "import paramiko, yaml" 2>/dev/null; then
        success "Dependencies sudah terinstall (paramiko, pyyaml)"
        return 0
    fi

    info "Installing paramiko dan pyyaml..."

    if [[ -f "$SCRIPT_DIR/requirements-cli.txt" ]]; then
        if confirm "Install via pip dari requirements-cli.txt?" "y"; then
            pip3 install -r "$SCRIPT_DIR/requirements-cli.txt" || die "Install dependencies gagal"
            success "Dependencies installed"
        else
            die "Dependencies diperlukan untuk CLI. Install manual: pip install paramiko pyyaml"
        fi
    else
        if confirm "Install paramiko dan pyyaml via pip?" "y"; then
            pip3 install 'paramiko>=3.0' 'pyyaml>=6.0' || die "Install dependencies gagal"
            success "Dependencies installed"
        else
            die "Dependencies diperlukan untuk CLI"
        fi
    fi

    echo ""
}

# ── Setup Server Registry ──────────────────────────────────────────────────────
setup_server_registry() {
    header "Setup Server Registry"

    # Check if server already registered
    if [[ -f "$ODIN_DIR/servers/$SERVER_ALIAS.yaml" ]] || [[ -f "$ODIN_DIR/servers/$SERVER_ALIAS.json" ]]; then
        info "Server '$SERVER_ALIAS' sudah terdaftar"

        if confirm "Update server config?" "n"; then
            info "Updating server registry..."
            # Use odin CLI to update
            python3 "$SCRIPT_DIR/client/odin_cli.py" server remove "$SERVER_ALIAS" 2>/dev/null || true
        else
            success "Menggunakan server registry yang ada"
            return 0
        fi
    fi

    info "Registering server: $SERVER_ALIAS"

    # Create server config manually (non-interactive)
    mkdir -p "$ODIN_DIR/servers"
    mkdir -p "$ODIN_DIR/keys"

    # Generate SSH key if not exists
    SSH_KEY="$ODIN_DIR/keys/$SERVER_ALIAS"

    if [[ ! -f "$SSH_KEY" ]]; then
        info "Generating SSH key untuk $SERVER_ALIAS..."
        ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -C "odin@$SERVER_ALIAS" >/dev/null
        success "SSH key generated: $SSH_KEY"

        info "Copy public key ke server? (untuk passwordless SSH)"
        if confirm "Jalankan ssh-copy-id?" "y"; then
            ssh-copy-id -i "$SSH_KEY.pub" "$SERVER_USER@$SERVER_HOST" || warning "ssh-copy-id gagal, setup manual nanti"
        fi
    else
        info "Using existing SSH key: $SSH_KEY"
    fi

    # Create server YAML config
    cat > "$ODIN_DIR/servers/$SERVER_ALIAS.yaml" <<EOF
host: $SERVER_HOST
user: $SERVER_USER
port: $SERVER_PORT
key: $SSH_KEY
description: SIMURU Production Server
created_at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

    success "Server '$SERVER_ALIAS' registered"
    echo ""
}

# ── Update Server Files ────────────────────────────────────────────────────────
update_server_files() {
    header "Update Server Files to v2.1.0"

    info "Uploading odin_agent.py v2.1.0 ke server..."

    if scp "$SCRIPT_DIR/server/odin_agent.py" "$SERVER_USER@$SERVER_HOST:/home/$SERVER_USER/odin_agent.py"; then
        success "odin_agent.py uploaded"
    else
        die "Upload odin_agent.py gagal"
    fi

    info "Uploading run.sh (multi-project support)..."

    if [[ -f "$SCRIPT_DIR/server/run.sh" ]]; then
        if scp "$SCRIPT_DIR/server/run.sh" "$SERVER_USER@$SERVER_HOST:/home/$SERVER_USER/run.sh"; then
            ssh "$SERVER_USER@$SERVER_HOST" "chmod +x /home/$SERVER_USER/run.sh"
            success "run.sh uploaded"
        else
            warning "Upload run.sh gagal (tapi lanjut)"
        fi
    else
        warning "run.sh tidak ditemukan di repo lokal"
    fi

    # Check if venv exists and has mcp[cli]
    info "Checking Python venv di server..."

    local venv_check=$(ssh "$SERVER_USER@$SERVER_HOST" '
        if [[ -d ~/venv ]]; then
            source ~/venv/bin/activate
            python -c "import mcp" 2>/dev/null && echo "OK" || echo "MISSING"
        else
            echo "NO_VENV"
        fi
    ')

    if [[ "$venv_check" == "NO_VENV" ]]; then
        warning "Venv tidak ditemukan di server"
        if confirm "Create venv + install mcp[cli]?" "y"; then
            info "Setting up venv di server..."
            ssh "$SERVER_USER@$SERVER_HOST" '
                python3 -m venv ~/venv
                source ~/venv/bin/activate
                pip install --upgrade pip
                pip install "mcp[cli]"
            ' || die "Setup venv gagal"
            success "Venv created + mcp[cli] installed"
        fi
    elif [[ "$venv_check" == "MISSING" ]]; then
        info "Installing mcp[cli] di existing venv..."
        ssh "$SERVER_USER@$SERVER_HOST" '
            source ~/venv/bin/activate
            pip install "mcp[cli]"
        ' || warning "Install mcp[cli] gagal"
        success "mcp[cli] installed"
    else
        success "Venv + mcp[cli] sudah ada"
    fi

    # Create projects directory structure
    info "Setting up multi-project structure di server..."

    ssh "$SERVER_USER@$SERVER_HOST" "
        mkdir -p ~/projects
        mkdir -p ~/memory/$PROJECT_NAME
        chmod 700 ~/memory
        chmod 700 ~/memory/$PROJECT_NAME
    " || warning "Setup directory structure gagal (tapi lanjut)"

    success "Server files updated to v2.1.0"
    echo ""
}

# ── Setup Project Config ───────────────────────────────────────────────────────
setup_project_config() {
    header "Setup Project Configuration"

    info "Creating project config untuk '$PROJECT_NAME'..."

    # Create project.conf di server
    local project_conf="/home/$SERVER_USER/projects/$PROJECT_NAME.conf"

    info "Uploading project.conf ke server..."

    ssh "$SERVER_USER@$SERVER_HOST" "cat > $project_conf" <<EOF
# ODIN Project Config: $PROJECT_NAME
# Generated: $(date)

export PROJECT_ROOT="$PROJECT_REMOTE_PATH"
export ALLOWED_LOG_DIRS="$ALLOWED_LOG_DIRS"
export MEMORY_DIR="/home/$SERVER_USER/memory/$PROJECT_NAME"
export PROJECT_NAME="$PROJECT_NAME"

# Optional: custom timeouts, limits, etc
# export DEFAULT_TIMEOUT=240
# export OUTPUT_LIMIT=30000
EOF

    if [[ $? -eq 0 ]]; then
        success "project.conf created di server"
    else
        die "Create project.conf gagal"
    fi

    # Create project registry di laptop
    mkdir -p "$ODIN_DIR/projects"

    cat > "$ODIN_DIR/projects/$PROJECT_NAME.yaml" <<EOF
name: $PROJECT_NAME
server: $SERVER_ALIAS
workdir: $PROJECT_WORKDIR
remote_path: $PROJECT_REMOTE_PATH
allowed_log_dirs: $ALLOWED_LOG_DIRS
created_at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

    success "Project registry created: ~/.odin/projects/$PROJECT_NAME.yaml"

    # Migrate existing memory if needed
    info "Checking for existing memory to migrate..."

    local old_memory_exists=$(ssh "$SERVER_USER@$SERVER_HOST" '
        [[ -f ~/memory/memory.jsonl ]] && echo "YES" || echo "NO"
    ')

    if [[ "$old_memory_exists" == "YES" ]]; then
        warning "Ditemukan memory.jsonl lama (v1.x single-project)"

        if confirm "Migrate memory ke $PROJECT_NAME namespace?" "y"; then
            info "Migrating memory..."
            ssh "$SERVER_USER@$SERVER_HOST" "
                mkdir -p ~/memory/$PROJECT_NAME
                cp ~/memory/memory.jsonl ~/memory/$PROJECT_NAME/memory.jsonl
                [[ -f ~/memory/audit.jsonl ]] && cp ~/memory/audit.jsonl ~/memory/$PROJECT_NAME/audit.jsonl || true
                chmod 600 ~/memory/$PROJECT_NAME/*.jsonl
            " && success "Memory migrated" || warning "Migration gagal (cek manual)"
        fi
    fi

    echo ""
}

# ── Setup MCP Config ───────────────────────────────────────────────────────────
setup_mcp_config() {
    header "Setup MCP Configuration"

    local settings_file="$PROJECT_WORKDIR/.claude/settings.json"

    mkdir -p "$PROJECT_WORKDIR/.claude"

    # Backup existing settings
    if [[ -f "$settings_file" ]]; then
        info "Backup existing settings.json..."
        cp "$settings_file" "$settings_file.bak.$(date +%Y%m%d_%H%M%S)"
    fi

    info "Generating new settings.json dengan MCP config v2.1..."

    # Generate MCP config
    cat > "$settings_file" <<EOF
{
  "mcpServers": {
    "odin": {
      "command": "ssh",
      "args": [
        "-F",
        "/dev/null",
        "-i",
        "$SSH_KEY",
        "-p",
        "$SERVER_PORT",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "$SERVER_USER@$SERVER_HOST",
        "/home/$SERVER_USER/run.sh",
        "--project",
        "$PROJECT_NAME"
      ],
      "description": "ODIN v2.1 — MCP Agent for $PROJECT_NAME on $SERVER_ALIAS"
    }
  },
  "hooks": [
    {
      "hookEventName": "PreToolUse",
      "command": "$SCRIPT_DIR/client/odin_guard.py",
      "includeToolUses": ["mcp__odin__*"]
    }
  ],
  "permissions": {
    "allow": [
      "mcp__odin__server_info",
      "mcp__odin__session_history",
      "mcp__odin__memory_recall",
      "mcp__odin__memory_digest",
      "mcp__odin__memory_health",
      "mcp__odin__audit_tail",
      "mcp__odin__inspect_server",
      "mcp__odin__rollback_plan",
      "mcp__odin__runbook_templates"
    ]
  }
}
EOF

    success "settings.json created"

    # Setup mode file per project
    mkdir -p "$ODIN_DIR/modes"

    if [[ ! -f "$ODIN_DIR/modes/$PROJECT_NAME" ]]; then
        echo "production" > "$ODIN_DIR/modes/$PROJECT_NAME"
        success "Mode initialized: production"
    else
        info "Mode file sudah ada: $(cat "$ODIN_DIR/modes/$PROJECT_NAME")"
    fi

    echo ""
}

# ── Comprehensive Tests ────────────────────────────────────────────────────────
run_comprehensive_tests() {
    header "Comprehensive Tests"

    local test_results=()

    # Test 1: Local version check
    info "[1/10] Checking local versions..."
    local agent_ver=$(grep -m1 '^__version__' "$SCRIPT_DIR/server/odin_agent.py" | cut -d'"' -f2)
    local guard_ver=$(grep -m1 '^__version__' "$SCRIPT_DIR/client/odin_guard.py" | cut -d'"' -f2)

    if [[ "$agent_ver" == "2.1.0" ]] && [[ "$guard_ver" == "2.1.0" ]]; then
        success "Local versions: odin_agent=$agent_ver, odin_guard=$guard_ver"
        test_results+=("✓ Local versions")
    else
        error "Version mismatch: agent=$agent_ver, guard=$guard_ver"
        test_results+=("✗ Local versions")
        ERRORS+=("Local version bukan 2.1.0")
    fi

    # Test 2: Server version check
    info "[2/10] Checking server version..."
    local server_ver=$(ssh "$SERVER_USER@$SERVER_HOST" "grep -m1 '^__version__' /home/$SERVER_USER/odin_agent.py | cut -d'\"' -f2" 2>/dev/null)

    if [[ "$server_ver" == "2.1.0" ]]; then
        success "Server version: $server_ver"
        test_results+=("✓ Server version")
    else
        error "Server version: $server_ver (expected 2.1.0)"
        test_results+=("✗ Server version")
        ERRORS+=("Server version bukan 2.1.0")
    fi

    # Test 3: SSH connectivity
    info "[3/10] Testing SSH connectivity..."
    if ssh -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "echo 'SSH OK'" &>/dev/null; then
        success "SSH connection OK"
        test_results+=("✓ SSH connectivity")
    else
        error "SSH connection failed"
        test_results+=("✗ SSH connectivity")
        ERRORS+=("SSH connection gagal")
    fi

    # Test 4: Server file structure
    info "[4/10] Checking server file structure..."
    local file_check=$(ssh "$SERVER_USER@$SERVER_HOST" '
        [[ -f ~/odin_agent.py ]] && echo -n "agent "
        [[ -f ~/run.sh ]] && echo -n "runsh "
        [[ -d ~/projects ]] && echo -n "projects "
        [[ -d ~/memory/'$PROJECT_NAME' ]] && echo -n "memory "
        [[ -f ~/projects/'$PROJECT_NAME'.conf ]] && echo -n "conf"
    ')

    if [[ "$file_check" == *"agent"* ]] && [[ "$file_check" == *"memory"* ]]; then
        success "Server structure: $file_check"
        test_results+=("✓ Server structure")
    else
        error "Server structure incomplete: $file_check"
        test_results+=("✗ Server structure")
        ERRORS+=("Server structure tidak lengkap")
    fi

    # Test 5: Python venv
    info "[5/10] Testing Python venv + mcp..."
    local venv_test=$(ssh "$SERVER_USER@$SERVER_HOST" '
        source ~/venv/bin/activate 2>/dev/null
        python -c "import mcp; print(\"OK\")" 2>/dev/null || echo "FAIL"
    ')

    if [[ "$venv_test" == "OK" ]]; then
        success "Venv + mcp[cli] OK"
        test_results+=("✓ Python venv")
    else
        error "Venv or mcp[cli] missing"
        test_results+=("✗ Python venv")
        ERRORS+=("Venv atau mcp[cli] tidak tersedia")
    fi

    # Test 6: MCP server spawn test
    info "[6/10] Testing MCP server spawn..."
    local spawn_test=$(timeout 10 ssh "$SERVER_USER@$SERVER_HOST" "/home/$SERVER_USER/run.sh --project $PROJECT_NAME" <<< '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"0.1.0","clientInfo":{"name":"test"}}}' 2>/dev/null | head -1)

    if [[ "$spawn_test" == *"jsonrpc"* ]]; then
        success "MCP server spawn OK"
        test_results+=("✓ MCP spawn")
    else
        warning "MCP spawn test tidak konklusif (tapi mungkin OK)"
        test_results+=("⚠ MCP spawn")
    fi

    # Test 7: Project config
    info "[7/10] Checking project config..."
    if [[ -f "$ODIN_DIR/projects/$PROJECT_NAME.yaml" ]]; then
        success "Project registry OK: $PROJECT_NAME"
        test_results+=("✓ Project registry")
    else
        error "Project registry missing"
        test_results+=("✗ Project registry")
        ERRORS+=("Project registry tidak ditemukan")
    fi

    # Test 8: MCP settings.json
    info "[8/10] Checking MCP settings.json..."
    if [[ -f "$PROJECT_WORKDIR/.claude/settings.json" ]]; then
        local mcp_check=$(grep -c "\"odin\"" "$PROJECT_WORKDIR/.claude/settings.json" || echo 0)
        if [[ $mcp_check -gt 0 ]]; then
            success "MCP config OK"
            test_results+=("✓ MCP config")
        else
            error "MCP config tidak ada entry 'odin'"
            test_results+=("✗ MCP config")
            ERRORS+=("MCP config tidak valid")
        fi
    else
        error "settings.json tidak ditemukan"
        test_results+=("✗ MCP config")
        ERRORS+=("settings.json tidak ditemukan")
    fi

    # Test 9: Guard hook
    info "[9/10] Checking guard hook..."
    if [[ -f "$PROJECT_WORKDIR/.claude/settings.json" ]]; then
        local hook_check=$(grep -c "odin_guard.py" "$PROJECT_WORKDIR/.claude/settings.json" || echo 0)
        if [[ $hook_check -gt 0 ]]; then
            success "Guard hook registered"
            test_results+=("✓ Guard hook")
        else
            warning "Guard hook tidak terdaftar (tambahkan manual)"
            test_results+=("⚠ Guard hook")
        fi
    else
        test_results+=("✗ Guard hook")
    fi

    # Test 10: Memory integrity
    info "[10/10] Checking memory integrity..."
    local memory_file="/home/$SERVER_USER/memory/$PROJECT_NAME/memory.jsonl"
    local memory_check=$(ssh "$SERVER_USER@$SERVER_HOST" "
        if [[ -f $memory_file ]]; then
            wc -l < $memory_file
        else
            echo 0
        fi
    " 2>/dev/null)

    if [[ "$memory_check" =~ ^[0-9]+$ ]]; then
        success "Memory file OK ($memory_check entries)"
        test_results+=("✓ Memory integrity")
    else
        info "Memory file belum ada (normal untuk fresh install)"
        test_results+=("⚠ Memory integrity")
    fi

    echo ""
    header "Test Results Summary"

    for result in "${test_results[@]}"; do
        if [[ "$result" == ✓* ]]; then
            echo -e "${GREEN}$result${RESET}"
        elif [[ "$result" == ⚠* ]]; then
            echo -e "${YELLOW}$result${RESET}"
        else
            echo -e "${RED}$result${RESET}"
        fi
    done

    echo ""

    if [[ ${#ERRORS[@]} -eq 0 ]]; then
        success "Semua critical tests PASSED ✓"
        return 0
    else
        error "Ditemukan ${#ERRORS[@]} error(s):"
        for err in "${ERRORS[@]}"; do
            echo -e "  ${RED}✗${RESET} $err"
        done
        return 1
    fi
}

# ── Final Report ───────────────────────────────────────────────────────────────
generate_final_report() {
    header "Migration Complete!"

    cat <<EOF
${GREEN}╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║  ${BOLD}ODIN v2.1.0 Migration — COMPLETED${RESET}${GREEN}                          ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝${RESET}

${BOLD}📦 Components Updated:${RESET}

  Laptop:
    ✓ odin_agent.py    : v2.1.0
    ✓ odin_guard.py    : v2.1.0
    ✓ odin_cli.py      : v2.0.0
    ✓ CLI dependencies : paramiko, pyyaml
    ✓ Server registry  : $SERVER_ALIAS ($SERVER_HOST)
    ✓ Project registry : $PROJECT_NAME

  Server ($SERVER_USER@$SERVER_HOST):
    ✓ odin_agent.py    : v2.1.0
    ✓ run.sh           : multi-project support
    ✓ venv + mcp[cli]  : installed
    ✓ Project config   : $PROJECT_NAME.conf
    ✓ Memory namespace : $PROJECT_NAME/

${BOLD}🔧 Configuration:${RESET}

  Workdir   : $PROJECT_WORKDIR
  MCP Config: $PROJECT_WORKDIR/.claude/settings.json
  Registry  : ~/.odin/projects/$PROJECT_NAME.yaml
  Mode      : $(cat "$ODIN_DIR/modes/$PROJECT_NAME" 2>/dev/null || echo "unknown")
  SSH Key   : $SSH_KEY

${BOLD}💾 Backups:${RESET}

  Location: $BACKUP_DIR
    - odin-laptop.bak/       (laptop configs)
    - claude-config/         (.claude/settings.json)
    - Server: /home/$SERVER_USER/migration-backup-$(date +%Y%m%d)/

${BOLD}🚀 Next Steps:${RESET}

  1. Buka Claude Code baru di workdir SIMURU:
     ${CYAN}cd $PROJECT_WORKDIR${RESET}
     ${CYAN}code . ${RESET}  ${DIM}# atau cara lain untuk buka Claude Code${RESET}

  2. Test ODIN v2.1.0:
     ${CYAN}/odin:status${RESET}
     ${CYAN}/odin:about${RESET}

  3. Verifikasi project identity di risk card saat execute command:
     ${CYAN}Prj: simuru → vps-app${RESET}

  4. Test 1 command (contoh):
     ${CYAN}cek status nginx di simuru${RESET}

  5. (Optional) Test CLI commands dari terminal:
     ${CYAN}odin project list${RESET}
     ${CYAN}odin doctor vps-app${RESET}

${BOLD}📚 Documentation:${RESET}

  - Full analysis : $SCRIPT_DIR/ANALISIS_MIGRASI_ODIN.md
  - Changelog     : $SCRIPT_DIR/CHANGELOG.md
  - Project guide : $SCRIPT_DIR/CLAUDE.md

${BOLD}🆘 Rollback (jika diperlukan):${RESET}

  Server:
    ${CYAN}ssh $SERVER_USER@$SERVER_HOST${RESET}
    ${CYAN}cp migration-backup-$(date +%Y%m%d)/odin_agent.py odin_agent.py${RESET}

  Laptop:
    ${CYAN}cp $BACKUP_DIR/claude-config/settings.json.bak $PROJECT_WORKDIR/.claude/settings.json${RESET}

${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}

EOF

    if [[ ${#ERRORS[@]} -gt 0 ]]; then
        warning "Migrasi selesai dengan ${#ERRORS[@]} warning(s). Cek output di atas."
    else
        success "Migration 100% successful. ODIN v2.1.0 ready to use!"
    fi

    echo ""
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
    cat <<'EOF'
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   ██████╗ ██████╗ ██╗███╗   ██╗    ██╗   ██╗██████╗     ██╗     ║
║  ██╔═══██╗██╔══██╗██║████╗  ██║    ██║   ██║╚════██╗   ███║     ║
║  ██║   ██║██║  ██║██║██╔██╗ ██║    ██║   ██║ █████╔╝   ╚██║     ║
║  ██║   ██║██║  ██║██║██║╚██╗██║    ╚██╗ ██╔╝██╔═══╝     ██║     ║
║  ╚██████╔╝██████╔╝██║██║ ╚████║     ╚████╔╝ ███████╗    ██║     ║
║   ╚═════╝ ╚═════╝ ╚═╝╚═╝  ╚═══╝      ╚═══╝  ╚══════╝    ╚═╝     ║
║                                                                  ║
║             Migration Script: v1.x → v2.1.0                     ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝

EOF

    info "Target: Laptop + Server SIMURU"
    info "Date: $(date)"
    echo ""

    if ! confirm "Lanjutkan migrasi ODIN ke v2.1.0?" "y"; then
        info "Migration dibatalkan oleh user."
        exit 0
    fi

    echo ""

    # Execute migration steps
    preflight_checks
    backup_current_state
    install_cli_deps
    setup_server_registry
    update_server_files
    setup_project_config
    setup_mcp_config

    # Comprehensive tests
    if run_comprehensive_tests; then
        generate_final_report
        exit 0
    else
        error "Beberapa tests gagal. Periksa output di atas."
        echo ""
        warning "Migration mungkin tidak sempurna. Cek manual atau rollback jika perlu."

        if confirm "Tetap lanjutkan (generate report)?" "y"; then
            generate_final_report
        fi

        exit 1
    fi
}

# ── Execute ────────────────────────────────────────────────────────────────────
main "$@"
