#!/bin/bash
# Build the Trident Protect Web UI installation package (tar.gz)
# Kubernetes Edition · v1.4.4
# Excludes: the large CLI binary, runtime artifacts, secrets
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

UI_VERSION="1.4.18"
PACKAGE_NAME="trident-protect-webui"
OUTPUT_DIR="/root/package"
OUTPUT_FILE="${OUTPUT_DIR}/${PACKAGE_NAME}.tar.gz"

# --- Pre-flight checks ---
echo "=== Trident Protect Web UI — Package Builder (Kubernetes Edition · v${UI_VERSION}) ==="
echo ""

if [ ! -d app ] || [ ! -f install.sh ] || [ ! -f requirements.txt ]; then
    echo "ERROR: Must be run from the repo root (containing app/, install.sh, requirements.txt)"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

# --- Clean previous package ---
rm -rf "${OUTPUT_DIR:?}/${PACKAGE_NAME}"
rm -f "$OUTPUT_FILE"

# --- Build staging directory ---
STAGING="/tmp/${PACKAGE_NAME}"
rm -rf "$STAGING"
mkdir -p "$STAGING"

echo "Staging files..."

# Application source (Python + templates + static)
rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' app "$STAGING/"
echo "  ✓ app/"

# Scripts
cp install.sh "$STAGING/"
cp uninstall.sh "$STAGING/"
cp run.sh "$STAGING/"
cp stop.sh "$STAGING/"
echo "  ✓ install.sh, uninstall.sh, run.sh, stop.sh"

# Config template
cp config.example.yaml "$STAGING/"
echo "  ✓ config.example.yaml"

# Requirements
cp requirements.txt "$STAGING/"
echo "  ✓ requirements.txt"

# systemd unit
mkdir -p "$STAGING/systemd"
cp systemd/trident-protect-webui.service "$STAGING/systemd/"
echo "  ✓ systemd/trident-protect-webui.service"

# Release notes + changelog
[ -f CHANGELOG.md ] && cp CHANGELOG.md "$STAGING/" && echo "  ✓ CHANGELOG.md"

# Make scripts executable
chmod +x "$STAGING/install.sh" "$STAGING/run.sh" "$STAGING/stop.sh"

# --- Create README for the package ---
cat > "$STAGING/README.md" <<'EOF'
# Trident Protect Web UI (Kubernetes Edition)

Web UI for managing **NetApp Trident Protect** resources (Applications, Backups, Snapshots, Schedules, Restores, AppVaults, Disaster Recovery) on **vanilla Kubernetes** clusters.

> **This is the Kubernetes-only edition.** For OpenShift, see the separate `netapp_trident_protect_webui_ocp` repository.

## Quick Install

```bash
tar xzf trident-protect-webui-v*.tar.gz
cd trident-protect-webui
sudo ./install.sh
```

The installer will:
1. Check prerequisites (Python 3.9+, pip, curl, kubectl)
2. Set up directories
3. Create a Python virtual environment
4. Install Python dependencies (Flask, PyYAML, ldap3)
5. Download `tridentprotect-ctl` CLI from GitHub
6. Generate `config.yaml` (cluster profiles added later via the UI)
7. Install systemd service
8. Configure firewall (open port 8080)
9. Verify installation

## After Install

| Action | Command |
|---|---|
| Open UI | `http://<server-ip>:8080` |
| Service status | `systemctl status trident-protect-webui` |
| View logs | `journalctl -u trident-protect-webui -f` |
| Restart | `systemctl restart trident-protect-webui` |
| Stop | `systemctl stop trident-protect-webui` |
| Manual start | `./run.sh` |
| Manual stop | `./stop.sh` |

## First Login

The dashboard auth is enabled by default and seeds an `admin` user with password `admin123` on first run. **Change this immediately** via Settings → Credentials after logging in.

Manage additional local users and LDAP under **Settings → Credentials** (admin only).

## Register Your First Kubernetes Cluster

1. Sign in as `admin`.
2. Open **Settings → Clusters**.
3. Click **+ Add Profile**, enter Profile Name + API URL.
4. Click **Save Profile**.
5. Click **Login & Switch** on the new profile row.
6. Paste a bearer token (e.g. from `kubectl -n kube-system create token k8s-ui-sa --duration=87600h`).
7. Click **Login & Switch** again — the token is stored, the active profile switches automatically.

**Alternative:** use **Import Kubeconfig** to paste/upload a kubeconfig file. A profile is auto-created from `current-context`.

## Requirements

- **OS:** Ubuntu 20.04+ / Debian 11+ (other Linux distros work with minor tweaks)
- **Python:** 3.9 or higher
- **Internet:** Required during install (to download CLI binary + Python deps)
- **kubectl:** Required for cluster queries + kubeconfig management
  - Install: https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/
- **Trident Protect:** Must be installed on the target cluster (operator + CRDs)
- **AppVault:** Object storage target (ONTAP S3, AWS S3, MinIO, etc.)

## Configuration

Edit `config.yaml` after install to customize:

- **app:** host/port/secret_key
- **cli:** path to `tridentprotect-ctl`
- **appvault:** default backup/snapshot storage
- **clusters:** profiles (API URL, AppVault, TLS settings, token, kubeconfig_context)
- **auth:** enable/disable dashboard login, default role, LDAP config
- **logs:** audit log enable/max_rows

You can manage multiple clusters via the **Settings → Clusters** page in the UI. The DR page requires exactly 2 profiles (source + destination).

## License

Internal NetApp tool.
EOF

echo "  ✓ README.md"

# --- Create .gitignore for the package ---
cat > "$STAGING/.gitignore" <<'EOF'
.venv/
bin/
config.yaml
auth_users.json
logs/
*.log
.web.pid
__pycache__/
*.pyc
EOF

echo "  ✓ .gitignore"

# --- Compute package size ---
SIZE_KB=$(du -sk "$STAGING" | awk '{print $1}')
echo ""
echo "Staged package size: ${SIZE_KB}KB ($(($SIZE_KB / 1024))MB)"

# --- Create tarball ---
echo ""
echo "Creating ${OUTPUT_FILE}..."
tar czf "$OUTPUT_FILE" -C /tmp "$PACKAGE_NAME"

PACKAGE_SIZE=$(du -h "$OUTPUT_FILE" | awk '{print $1}')
echo ""
echo "=== Package built successfully ==="
echo ""
echo "  File:     $OUTPUT_FILE"
echo "  Size:     $PACKAGE_SIZE"
echo ""
echo "To install on target server:"
echo ""
echo "  scp ${OUTPUT_FILE} user@target:"
echo "  ssh user@target 'tar xzf ${PACKAGE_NAME}.tar.gz && cd ${PACKAGE_NAME} && sudo ./install.sh'"
echo ""

# --- Clean up staging ---
rm -rf "$STAGING"
