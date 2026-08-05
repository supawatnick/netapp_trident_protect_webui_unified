#!/bin/bash
# Uninstall Trident Protect Web UI (v1.4.14)
# - Stops + disables the systemd service
# - Removes the systemd unit
# - Backs up config.yaml (with your SA tokens) to /tmp
# - Removes the install directory (the directory this script lives in)
# - Removes firewall rules for the listen port (ufw / firewalld)
#
# Run:  sudo ./uninstall.sh
# (You'll be asked to confirm with [y/N] before any destructive step)

set -e

SERVICE="trident-protect-webui.service"
# The installer (install.sh) writes the unit with WorkingDirectory=SCRIPT_DIR
# and installs this script next to the app, so the install dir == script dir.
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_BACKUP_DIR="/tmp"

# --- Colour helpers ---
if [ -t 1 ]; then
  RED=$'\033[0;31m'; YELLOW=$'\033[1;33m'; GREEN=$'\033[0;32m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
else
  RED=''; YELLOW=''; GREEN=''; CYAN=''; NC=''
fi
ok()   { printf "  ${GREEN}[OK]${NC} %s\n" "$1"; }
warn() { printf "  ${YELLOW}[WARN]${NC} %s\n" "$1"; }
fail() { printf "  ${RED}[FAIL]${NC} %s\n" "$1"; }
info() { printf "  ${CYAN}[INFO]${NC} %s\n" "$1"; }

# --- Detect listen port from config.yaml if present, else default 8080 ---
LISTEN_PORT=8080
if [ -f "$INSTALL_DIR/config.yaml" ]; then
  PORT=$("$INSTALL_DIR/.venv/bin/python3" - <<PY 2>/dev/null || true
import yaml
try:
    c = yaml.safe_load(open("$INSTALL_DIR/config.yaml"))
    print(c.get("app", {}).get("port", 8080))
except Exception:
    print(8080)
PY
)
  case "$PORT" in
    ''|*[!0-9]*) PORT=8080 ;;
  esac
  [ "$PORT" -gt 0 ] 2>/dev/null && LISTEN_PORT="$PORT"
fi

# --- Pre-flight ---
echo ""
echo -e "${CYAN}=============================================${NC}"
echo -e "${CYAN} Trident Protect Web UI — Uninstaller${NC}"
echo -e "${CYAN}=============================================${NC}"
echo ""
echo "  This script will:"
echo "    1. Stop + disable ${SERVICE}"
echo "    2. Remove the systemd unit (/etc/systemd/system/${SERVICE})"
echo "    3. Back up config.yaml (with your tokens) → ${CONFIG_BACKUP_DIR}/"
echo "    4. Remove ${INSTALL_DIR}"
echo "    5. Remove firewall rule for port ${LISTEN_PORT} (ufw / firewalld)"
echo ""
echo "  Confirmation required for destructive steps."
echo ""

# Check we're root
if [ "$(id -u)" -ne 0 ]; then
  fail "This script must be run as root (use sudo)"
  exit 1
fi

# --- 1. Confirm overall ---
read -rp "Proceed with uninstall? [y/N] " ans
[[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
echo ""

# --- 2. Stop + disable service ---
if systemctl list-unit-files "$SERVICE" 2>/dev/null | grep -q "$SERVICE"; then
  info "Step 1: Stop + disable ${SERVICE}"
  systemctl stop "$SERVICE" 2>/dev/null || warn "stop failed (may not be running)"
  systemctl disable "$SERVICE" 2>/dev/null || warn "disable failed (may not be enabled)"
  ok "${SERVICE} stopped + disabled"
else
  info "Step 1: ${SERVICE} not installed, skipping"
fi

# --- 3. Remove systemd unit ---
if [ -f "/etc/systemd/system/$SERVICE" ]; then
  info "Step 2: Remove /etc/systemd/system/$SERVICE"
  rm -f "/etc/systemd/system/$SERVICE"
  systemctl daemon-reload 2>/dev/null || true
  systemctl reset-failed "$SERVICE" 2>/dev/null || true
  ok "systemd unit removed"
else
  info "Step 2: systemd unit not found, skipping"
fi

# --- 4. Back up config.yaml ---
TS=$(date +%Y%m%d-%H%M%S)
if [ -f "$INSTALL_DIR/config.yaml" ]; then
  BACKUP_FILE="${CONFIG_BACKUP_DIR}/trident-webui-config-backup-${TS}.yaml"
  cp "$INSTALL_DIR/config.yaml" "$BACKUP_FILE" && chmod 600 "$BACKUP_FILE"
  ok "Backed up config.yaml → ${BACKUP_FILE}"
  warn "Restore with:  sudo cp ${BACKUP_FILE} ${INSTALL_DIR}/config.yaml"
elif [ ! -d "$INSTALL_DIR" ]; then
  info "Step 3: ${INSTALL_DIR} not present, skipping config backup"
fi

# --- 5. Remove install dir (with tarball fallback) ---
if [ -d "$INSTALL_DIR" ]; then
  # Offer to make a tarball safety net if /root/tarball/ is writable
  SAFETY_DIR="/root/tarball"
  PRE_TARBALL=""
  if [ -w "$SAFETY_DIR" ] || mkdir -p "$SAFETY_DIR" 2>/dev/null; then
    read -rp "Also create a tarball backup before removal? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
      PRE_TARBALL="${SAFETY_DIR}/trident-protect-webui-uninstall-${TS}.tgz"
      tar czf "$PRE_TARBALL" -C "$INSTALL_DIR" . 2>/dev/null \
        && ok "Tarball snapshot → ${PRE_TARBALL}"
    fi
  fi

  info "Step 4: Remove ${INSTALL_DIR}"
  rm -rf "$INSTALL_DIR"
  ok "Install directory removed"
  if [ -n "$PRE_TARBALL" ]; then
    info "  Tarball still at: $PRE_TARBALL"
  fi
else
  info "Step 4: ${INSTALL_DIR} not present, skipping"
fi

# --- 6. Firewall ---
if command -v ufw >/dev/null 2>&1; then
  info "Step 5: Remove firewall rule (ufw)"
  if ufw status 2>/dev/null | grep -qw "$LISTEN_PORT"; then
    ufw delete allow "${LISTEN_PORT}/tcp" 2>/dev/null && ok "ufw rule for ${LISTEN_PORT}/tcp removed"
  else
    info "  (no ufw rule for ${LISTEN_PORT})"
  fi
elif command -v firewall-cmd >/dev/null 2>&1; then
  info "Step 5: Remove firewall rule (firewalld)"
  if firewall-cmd --query-port="${LISTEN_PORT}/tcp" 2>/dev/null; then
    firewall-cmd --remove-port="${LISTEN_PORT}/tcp" 2>/dev/null \
      && firewall-cmd --runtime-to-permanent 2>/dev/null \
      && ok "firewalld rule for ${LISTEN_PORT}/tcp removed"
  else
    info "  (no firewalld rule for ${LISTEN_PORT})"
  fi
else
  info "Step 5: no firewall tooling detected, skipping"
fi

# --- Summary ---
echo ""
echo -e "${CYAN}=============================================${NC}"
echo -e "${GREEN} Uninstalled successfully ${NC}"
echo -e "${CYAN}=============================================${NC}"
echo ""
[ -f "${CONFIG_BACKUP_DIR}/trident-webui-config-backup-${TS}.yaml" ] && \
  echo "  config.yaml backup:  ${CONFIG_BACKUP_DIR}/trident-webui-config-backup-${TS}.yaml"
[ -n "$PRE_TARBALL" ] && [ -f "$PRE_TARBALL" ] && \
  echo "  full tarball:        $PRE_TARBALL"
echo ""
echo "To restore: re-run the installer with the same config.yaml"
echo "  sudo cp ${CONFIG_BACKUP_DIR}/trident-webui-config-backup-${TS}.yaml ${INSTALL_DIR}/config.yaml"
echo "  sudo ./install.sh"
echo ""
