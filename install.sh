#!/bin/bash
# Trident Protect Web UI Installer — Kubernetes Edition
# Installs Python deps, CLI binary, sets up systemd service.
# K8s-only: uses kubectl, kubeconfig-based cluster profiles (configured via UI).
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Run apt update once at start (Ubuntu 24.04 fresh install needs it)
if [ "$(id -u)" -eq 0 ]; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq 2>/dev/null || true
fi

# --- Colors for status output ---
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    RED='\033[0;31m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m' # No Color
else
    GREEN=''; YELLOW=''; RED=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; }
info() { echo -e "  ${CYAN}[INFO]${NC} $1"; }
ask()  { echo -ne "  ${BLUE}[?]${NC} $1"; }

step() {
    echo ""
    echo -e "${BOLD}${BLUE}[$1/9]${NC} ${BOLD}$2${NC}"
}

# --- Header ---
clear
echo -e "${BOLD}${CYAN}========================================${NC}"
echo -e "${BOLD}${CYAN} Trident Protect Web UI Installer${NC}"
echo -e "${BOLD}${CYAN} Kubernetes Edition · v1.4.3${NC}"
echo -e "${BOLD}${CYAN}========================================${NC}"
echo ""
echo "  This installer will:"
echo "    1. Check prerequisites (Python, curl, kubectl)"
echo "    2. Set up installation directories"
echo "    3. Create a Python virtual environment"
echo "    4. Install Python dependencies (Flask, PyYAML, ldap3)"
echo "    5. Download tridentprotect-ctl CLI from GitHub"
echo "    6. Generate config.yaml (cluster profiles added later via UI)"
echo "    7. Install systemd service"
echo "    8. Configure firewall (open port 8080)"
echo "    9. Verify installation"
echo ""
echo "  Press Ctrl+C to abort at any time."
echo ""

CLI_VERSION_DEFAULT="26.06.0"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=9
REQUIRED_DISK_MB=500

apt_install() {
    local MAX_WAIT=120
    local waited=0
    while fuser /var/lib/dpkg/lock-frontend > /dev/null 2>&1; do
        if [ "$waited" -ge "$MAX_WAIT" ]; then
            warn "dpkg lock held >${MAX_WAIT}s — aborting"
            return 1
        fi
        sleep 5
        waited=$((waited + 5))
    done
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
}

CLI_VERSION=$(curl -fsSL --max-time 10 "https://api.github.com/repos/NetApp/tridentctl-protect/releases/latest" 2>/dev/null \
    | grep -oP '"tag_name":\s*"\K[^"]+' | sed 's/^v//' || echo "$CLI_VERSION_DEFAULT")
CLI_VERSION=${CLI_VERSION:-$CLI_VERSION_DEFAULT}
CLI_URL="https://github.com/NetApp/tridentctl-protect/releases/download/v${CLI_VERSION}/tridentctl-protect-linux-amd64"

# ============================================================================
step "1/9" "Checking prerequisites"
# ============================================================================

if [ -f /etc/os-release ]; then
    . /etc/os-release
    info "OS: $PRETTY_NAME"
    IS_UBUNTU=0
    case "$ID" in
        ubuntu|debian) IS_UBUNTU=1 ;;
    esac
else
    warn "Cannot detect OS (no /etc/os-release)"
    IS_UBUNTU=0
fi

PYTHON=$(which python3 2>/dev/null || true)
if [ -z "$PYTHON" ]; then
    if [ "$IS_UBUNTU" -eq 1 ] && [ "$(id -u)" -eq 0 ]; then
        warn "python3 not found, installing..."
        apt_install python3 > /dev/null 2>&1 || { fail "Cannot install python3"; exit 1; }
        ok "python3 installed"
        PYTHON=$(which python3)
    else
        fail "python3 not found"
        echo "       Install with: sudo apt-get install -y python3 python3-venv python3-pip"
        exit 1
    fi
fi
PY_VERSION=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt "$PYTHON_MIN_MAJOR" ] || { [ "$PY_MAJOR" -eq "$PYTHON_MIN_MAJOR" ] && [ "$PY_MINOR" -lt "$PYTHON_MIN_MINOR" ]; }; then
    fail "Python $PY_VERSION found but >= $PYTHON_MIN_MAJOR.$PYTHON_MIN_MINOR required"
    exit 1
fi
ok "Python $PY_VERSION"

if [ "$IS_UBUNTU" -eq 1 ] && [ "$(id -u)" -eq 0 ]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip 2>/dev/null || true
fi

if $PYTHON -c 'import venv' > /dev/null 2>&1 && $PYTHON -c 'import ensurepip' > /dev/null 2>&1; then
    ok "python3-venv available"
else
    fail "python3-venv still missing after install attempt"
    echo "       Install with: sudo apt-get install -y python3-venv python3-pip"
    exit 1
fi

if $PYTHON -m pip --version > /dev/null 2>&1; then
    PIP_VERSION=$($PYTHON -m pip --version | awk '{print $2}')
    ok "pip $PIP_VERSION"
else
    fail "pip not found. Install with: sudo apt-get install -y python3-pip"
    exit 1
fi

if ! which curl > /dev/null 2>&1; then
    if [ "$IS_UBUNTU" -eq 1 ] && [ "$(id -u)" -eq 0 ]; then
        warn "curl not found, installing..."
        apt_install curl > /dev/null 2>&1 || { fail "Cannot install curl"; exit 1; }
        ok "curl installed"
    else
        fail "curl not found. Install with: sudo apt-get install -y curl"
        exit 1
    fi
else
    ok "curl available"
fi

# Check kubectl (k8s edition: only kubectl, no oc)
if which kubectl > /dev/null 2>&1; then
    KUBE_VERSION=$(kubectl version --client -o json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('gitVersion','?'))" 2>/dev/null || echo "?")
    ok "kubectl client found ($KUBE_VERSION)"
else
    warn "kubectl not found, downloading from dl.k8s.io..."
    KUBE_TMP=$(mktemp -d)
    KUBE_STABLE=$(curl -fsSL --max-time 10 https://dl.k8s.io/release/stable.txt 2>/dev/null || echo "v1.30.0")
    if curl -fsSL -o "$KUBE_TMP/kubectl" "https://dl.k8s.io/release/${KUBE_STABLE}/bin/linux/amd64/kubectl" 2>/dev/null; then
        if [ "$(id -u)" -eq 0 ]; then
            mv "$KUBE_TMP/kubectl" /usr/local/bin/kubectl
            chmod +x /usr/local/bin/kubectl
            ok "kubectl installed to /usr/local/bin/kubectl ($KUBE_STABLE)"
        else
            mv "$KUBE_TMP/kubectl" "$SCRIPT_DIR/bin/kubectl"
            chmod +x "$SCRIPT_DIR/bin/kubectl"
            ok "kubectl installed to $SCRIPT_DIR/bin/kubectl ($KUBE_STABLE)"
        fi
        rm -rf "$KUBE_TMP"
    else
        fail "Cannot download kubectl"
        echo "       Install kubectl manually: https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/"
        exit 1
    fi
fi

AVAIL_MB=$(df -m "$SCRIPT_DIR" | awk 'NR==2 {print $4}')
if [ "$AVAIL_MB" -lt "$REQUIRED_DISK_MB" ]; then
    fail "Insufficient disk space: ${AVAIL_MB}MB available, ${REQUIRED_DISK_MB}MB required"
    exit 1
fi
ok "Disk space: ${AVAIL_MB}MB available"

IS_ROOT=0
if [ "$(id -u)" -eq 0 ]; then
    IS_ROOT=1
    ok "Running as root (systemd + firewall available)"
else
    warn "Not running as root (systemd install + firewall will be skipped)"
fi

# ============================================================================
step "2/9" "Setting up directories"
# ============================================================================

mkdir -p bin
mkdir -p logs
ok "Created bin/ and logs/"

if [ ! -f "$SCRIPT_DIR/app/main.py" ]; then
    fail "app/main.py not found. Are you running this from the extracted package?"
    exit 1
fi
ok "Application source present"

# ============================================================================
step "3/9" "Creating Python virtual environment"
# ============================================================================

if [ -d ".venv" ]; then
    warn "Existing .venv found, recreating..."
    rm -rf .venv
fi

$PYTHON -m venv .venv
ok "Created .venv/"

# ============================================================================
step "4/9" "Installing Python dependencies"
# ============================================================================

.venv/bin/pip install --upgrade pip > /dev/null 2>&1
ok "pip upgraded"

.venv/bin/pip install -r requirements.txt 2>&1 | tail -5

if .venv/bin/python -c "import flask, yaml; print(f'flask {flask.__version__}, PyYAML {yaml.__version__}')"; then
    ok "Python dependencies installed"
else
    fail "Failed to install Python dependencies"
    exit 1
fi

# ============================================================================
step "5/9" "Downloading tridentprotect-ctl CLI"
# ============================================================================

if [ -x bin/tridentprotect-ctl ]; then
    CURRENT_VER=$(./bin/tridentprotect-ctl version 2>/dev/null || echo "unknown")
    ask "Existing CLI found: $CURRENT_VER. Re-download? [y/N]: "
    read -r REPLY
    if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
        info "Keeping existing CLI"
    else
        rm -f bin/tridentprotect-ctl
    fi
fi

if [ ! -x bin/tridentprotect-ctl ]; then
    info "Downloading tridentprotect-ctl v${CLI_VERSION} from GitHub..."
    if curl -fsSL -o bin/tridentprotect-ctl "$CLI_URL"; then
        chmod +x bin/tridentprotect-ctl
        ok "CLI downloaded"
    else
        fail "Failed to download CLI"
        echo "       URL: $CLI_URL"
        echo "       Manual fix: place tridentprotect-ctl at bin/tridentprotect-ctl"
        exit 1
    fi
fi

CLI_VER=$(./bin/tridentprotect-ctl version 2>/dev/null | head -1 || echo "unknown")
ok "CLI ready: $CLI_VER"

# ============================================================================
step "6/9" "Generating config.yaml"
# ============================================================================

if [ -f config.yaml ]; then
    warn "Existing config.yaml found"
    ask "Overwrite with new (empty) config? [y/N]: "
    read -r REPLY
    if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
        info "Keeping existing config.yaml"
        SKIP_CONFIG=1
    fi
fi

if [ -z "$SKIP_CONFIG" ]; then
    SECRET_KEY=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')

    ask "Listen port [default: 8080]: "
    read -r LISTEN_PORT
    if [ -z "$LISTEN_PORT" ] || ! [[ "$LISTEN_PORT" =~ ^[0-9]+$ ]]; then
        LISTEN_PORT=8080
    fi

    cat > config.yaml <<EOF
app:
  host: "0.0.0.0"
  port: ${LISTEN_PORT}
  debug: false
  secret_key: "${SECRET_KEY}"

cli:
  path: "bin/tridentprotect-ctl"

refresh:
  interval_sec: 30

appvault:
  default: ""
  namespace: "trident-protect"

clusters:
  active: ""
  profiles: {}

# Audit log (who did what in the UI) — every mutating action is appended
# to logs/audit.log (gitignored). View via the "Logs" menu (admin only).
logs:
  enabled: true
  max_rows: 10000

# Dashboard auth — enable by default so the login page appears and
# admin/admin123 is seeded on first run. Configure LDAP under
# Settings → Credentials after first login.
auth:
  enabled: true
  default_role: "readonly"
  ldap:
    enabled: false
    server: ""
    port: 389
    use_tls: false
    skip_cert_verify: true
    bind_dn: ""
    bind_password: ""
    base_dn: ""
    user_filter: "(&(objectClass=person)(sAMAccountName={username}))"
    username_attribute: "sAMAccountName"
    netbios_domain: ""
    admin_group_dns: []
EOF
    ok "config.yaml created (empty cluster profiles)"
    info "Secret key generated (32-byte random)"
    info "Add cluster profiles via Settings → Clusters after first login"
fi

# ============================================================================
step "7/9" "Installing systemd service"
# ============================================================================

if [ "$IS_ROOT" -eq 0 ]; then
    warn "Not running as root, skipping systemd install"
    info "Use 'nohup ./run.sh' to start manually"
else
    SERVICE_FILE="/etc/systemd/system/trident-protect-webui.service"
    USER_NAME="${SUDO_USER:-root}"
    INSTALL_PATH="$SCRIPT_DIR"

    sed -e "s|WorkingDirectory=.*|WorkingDirectory=${INSTALL_PATH}|" \
        -e "s|ExecStart=.*|ExecStart=${INSTALL_PATH}/.venv/bin/python3 -m app.main|" \
        -e "s|User=.*|User=${USER_NAME}|" \
        systemd/trident-protect-webui.service > "$SERVICE_FILE"

    ok "Service file installed: $SERVICE_FILE"

    systemctl daemon-reload
    ok "systemd daemon reloaded"

    systemctl enable trident-protect-webui > /dev/null 2>&1
    ok "Service enabled on boot"

    systemctl restart trident-protect-webui
    sleep 2

    if systemctl is-active --quiet trident-protect-webui; then
        ok "Service started"
        info "Status: systemctl status trident-protect-webui"
        info "Logs:   journalctl -u trident-protect-webui -f"
    else
        warn "Service failed to start, check logs:"
        systemctl status trident-protect-webui --no-pager || true
    fi
fi

# ============================================================================
step "8/9" "Configuring firewall"
# ============================================================================

if [ "$IS_ROOT" -eq 0 ]; then
    warn "Not running as root, skipping firewall config"
elif which ufw > /dev/null 2>&1; then
    if ufw status | grep -q "Status: active"; then
        ufw allow "${LISTEN_PORT}/tcp" > /dev/null 2>&1
        ok "ufw: opened port ${LISTEN_PORT}/tcp"
    else
        info "ufw not active, skipping"
    fi
elif which firewall-cmd > /dev/null 2>&1; then
    firewall-cmd --permanent --add-port="${LISTEN_PORT}/tcp" > /dev/null 2>&1
    firewall-cmd --reload > /dev/null 2>&1
    ok "firewalld: opened port ${LISTEN_PORT}/tcp"
else
    info "No firewall detected, manual config may be needed"
    info "Open port ${LISTEN_PORT}/tcp in your firewall"
fi

# ============================================================================
step "9/9" "Verifying installation"
# ============================================================================

if .venv/bin/python -c "from app.config import Config; Config.instance()"; then
    ok "Config valid"
else
    fail "Config validation failed"
    exit 1
fi

if ./bin/tridentprotect-ctl version > /dev/null 2>&1; then
    ok "CLI binary works"
else
    fail "CLI binary not executable"
    exit 1
fi

sleep 1
if curl -fsS --max-time 5 "http://127.0.0.1:${LISTEN_PORT}/healthz" > /dev/null 2>&1; then
    HEALTH=$(curl -fsS --max-time 5 "http://127.0.0.1:${LISTEN_PORT}/healthz")
    ok "Health check: $HEALTH"
else
    warn "Health check failed (service may still be starting up)"
    info "Wait a few seconds and check: curl http://localhost:${LISTEN_PORT}/healthz"
fi

# ============================================================================
# Summary
# ============================================================================

SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo ""
echo -e "${BOLD}${GREEN}========================================${NC}"
echo -e "${BOLD}${GREEN} Installation Complete!${NC}"
echo -e "${BOLD}${GREEN}========================================${NC}"
echo ""
echo -e "  ${BOLD}Web UI URL:${NC}     http://${SERVER_IP}:${LISTEN_PORT}"
echo -e "  ${BOLD}Local URL:${NC}      http://localhost:${LISTEN_PORT}"
echo ""
echo -e "  ${BOLD}Dashboard login:${NC}"
echo -e "    Open the URL above. If auth is enabled, sign in with:"
echo -e "    ${BOLD}admin / admin123${NC}   ← seeded on first run, CHANGE IMMEDIATELY"
echo -e "    Manage users + LDAP under ${BOLD}Settings → Credentials${NC}"
echo ""
echo -e "  ${BOLD}Next — register a cluster:${NC}"
echo -e "    1. Sign in as admin."
echo -e "    2. Open ${BOLD}Settings → Clusters${NC}."
echo -e "    3. Either paste a bearer token (api_url + token) and click"
echo -e "       ${BOLD}Login & Switch${NC}, or import a kubeconfig YAML."
echo ""
echo -e "  ${BOLD}Service:${NC}        systemctl status trident-protect-webui"
echo -e "  ${BOLD}Logs:${NC}           journalctl -u trident-protect-webui -f"
echo -e "  ${BOLD}Restart:${NC}        systemctl restart trident-protect-webui"
echo -e "  ${BOLD}Stop:${NC}           systemctl stop trident-protect-webui"
echo -e "  ${BOLD}Start:${NC}          systemctl start trident-protect-webui"
echo ""
echo -e "  ${BOLD}Manual start:${NC}   ./run.sh"
echo -e "  ${BOLD}Manual stop:${NC}    ./stop.sh"
echo ""
echo -e "  ${BOLD}Install path:${NC}   ${SCRIPT_DIR}"
echo ""
