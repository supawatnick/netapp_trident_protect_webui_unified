# Trident Protect Web UI — Unified Edition

A single web UI that manages NetApp **Trident Protect** resources on both
**OpenShift Container Platform (OCP)** and **vanilla Kubernetes** clusters.

This is the **v2.0.0 unified edition** — merged from the OCP-only (v1.4.18) and
Kubernetes-only (v1.4.18) sister repositories, with cluster authentication
hardened to **bearer-token-only** (no username/password).

> Open source project — use, modify, and distribute freely.

---

## Features

- **Dashboard** — aggregate stats across Applications, Backups, Snapshots, Schedules
- **Application Management** — list, validate, apply (`oc apply -f -`), delete
- **Backup / Snapshot / Schedule Management** — full CRUD, enable/disable schedules
- **Restore Wizard** — cross-namespace backup/snapshot restore with source→destination storage class mapping
- **AppVault Management** — object-storage backends (ONTAP S3, AWS S3, Azure Blob, GCP GCS, StorageGrid, MinIO)
- **Disaster Recovery** — AppMirrorRelationship (AMR) with Reverse Resync wizard, failover, resync, bootstrap snapshots
- **Multi-cluster** — up to 2 profiles (source + destination for DR)
- **Live Trident version banner** — detects `netapp/controller:<tag>` per cluster
- **Auth** — local user store (`auth_users.json`, bcrypt-hashed) + optional LDAP
- **Audit log** — JSON-lines per mutating request, viewable at `/logs` (admin)

---

## Architecture

```
Form/YAML → validate → `oc apply -f -` (or single `oc patch`/`delete`)
```

The dashboard **never orchestrates**. Every action is form → YAML → applied via
the standard Kubernetes/OCP tool. The Trident controller reconciles.

- **Backend:** Python 3.9+, Flask 3.1
- **Frontend:** Vanilla JS + Jinja2 templates (no React/Vue/build pipeline)
- **CSS:** Custom NetApp-branded stylesheet
- **Cluster I/O:** `subprocess` → `tridentprotect-ctl` (NetApp's CLI) + `oc` / `kubectl`
- **No Kubernetes Python client** — everything goes through CLI tools
- **Persistence:** flat files only (`config.yaml`, `auth_users.json`, `logs/audit.log`)
- **Service:** systemd (runs as root, default port 8080)

---

## Cluster Authentication — TOKEN ONLY

Starting with v2.0.0, the dashboard accepts **only bearer tokens** for cluster
authentication. Username/password login is **removed**.

Two ways to onboard a cluster:

1. **Paste a bearer token** — works on both OCP and vanilla Kubernetes.
   For OpenShift: copy from the web console (top-right → *Copy login command*
   → *Display Token*). For Kubernetes: create a ServiceAccount token or use
   your existing kubeconfig token.

2. **Import a kubeconfig** — paste the entire kubeconfig YAML and the dashboard
   will extract the cluster, credentials, and context automatically.

Both paths are available under **Settings → Clusters → Add / Import**.

### Why token-only?

- ServiceAccount tokens are the only authentication primitive that works
  identically on OCP and vanilla Kubernetes.
- Long-lived bearer tokens are the most operationally simple credential type
  for a dashboard that lives outside the cluster.
- OIDC/OAuth flows require in-cluster components that don't fit a Linux
  VM-hosted dashboard.

---

## Quick start

### Prerequisites

- Ubuntu 20.04 / 22.04 / 24.04 or Debian 11 / 12 (other Linux may work)
- Python 3.9+ (3.11 / 3.12 recommended)
- `tridentprotect-ctl` binary (downloaded automatically by `install.sh`)
- `kubectl` (or `oc`) — installed automatically by `install.sh`

### Install

```bash
git clone https://github.com/supawatnick/netapp_trident_protect_webui_unified.git
cd netapp_trident_protect_webui_unified
sudo ./install.sh
```

The installer:
1. Installs Python + `pip` + `venv` + `curl` (via `apt`)
2. Downloads `tridentprotect-ctl` from NetApp's GitHub releases
3. Downloads `kubectl` (primary) or `oc` (fallback) if missing
4. Creates `/opt/trident-protect-webui` and a Python venv
5. Installs `requirements.txt`
6. Generates `config.yaml` with a random `secret_key`
7. Installs + enables the `trident-protect-webui.service` systemd unit
8. Opens firewall port (8080 via `ufw` or `firewalld`)
9. Verifies with `curl http://127.0.0.1:8080/healthz`

### First login

Default dashboard credentials:

```
admin / admin123
```

**⚠️ Change immediately** via Settings → Credentials.

### Onboard a cluster

1. Open **Settings → Clusters**
2. Click **Add Profile**, paste your bearer token (or use **Import Kubeconfig**)
3. Click **Save** → the dashboard validates connectivity, then auto-activates the profile
4. **Dashboard** now shows resources from this cluster

For DR, add a second profile (max 2). The DR pages will let you pick
source + destination profiles.

### Uninstall

```bash
sudo ./uninstall.sh
```

Keeps a backup of `config.yaml` at `/tmp/trident-webui-config-backup-<ts>.yaml`.

---

## Compatibility

| Component | Tested versions |
|---|---|
| OpenShift | 4.16 / 4.17 / 4.18 / 4.21 |
| Kubernetes | 1.28 / 1.29 / 1.30 / 1.31 |
| Trident Protect | v26.02.0 / v26.06.0 |
| OS | Ubuntu 20.04 / 22.04 / 24.04, Debian 11 / 12 |
| Python | 3.9 / 3.10 / 3.11 / 3.12 |
| Backend storage | ONTAP S3 (primary), AWS S3, MinIO, Azure Blob, GCP GCS, StorageGrid |

---

## API reference (selected)

| Endpoint | Methods | Purpose |
|---|---|---|
| `/healthz` | GET | health probe |
| `/api/auth/login` | POST | dashboard login (`{username, password}`) |
| `/api/auth/logout` | POST | clear session |
| `/api/dashboard` | GET | aggregate stats |
| `/api/applications` `/api/application/{ns}/{name}` | GET / DELETE | list / detail / delete |
| `/api/application/validate` `/apply` | POST | YAML flow |
| `/api/backups` `/api/backup/{ns}/{name}` | GET / DELETE | same pattern |
| `/api/backup` | POST | trigger on-demand |
| `/api/backup/{ns}/{name}/backuprestores` `/inplacerestore` | POST | restore triggers |
| `/api/snapshots` `/api/snapshot/{ns}/{name}` `/inplacerestore` | GET / DELETE / POST | same pattern |
| `/api/schedules` `/api/schedule/{ns}/{name}` `/enable` `/disable` | GET / DELETE / POST | same pattern |
| `/api/namespaces` | GET | cluster namespaces (excludes `kube-*`, `openshift-*`) |
| `/api/restore/sources` `/destinations` | GET | restore wizard |
| `/api/restore/validate` `/apply` | POST | YAML restore |
| `/api/appvaults` `/api/appvaults/{ns}/{name}` | GET / POST / DELETE | AppVault CRUD |
| `/api/appvaults/{ns}/{name}/test` | POST | test AppVault state |
| `/api/cluster-info` | GET | `{name, platform, api_url, version}` |
| `/api/platform` | GET | `ocp` / `k8s` |
| `/api/trident-version` | GET | per-cluster Trident Protect version banner |
| `/api/settings/profile` | POST | add / update profile (max 2) |
| `/api/settings/profile/{name}` | DELETE | delete profile |
| `/api/settings/switch` | POST | switch active cluster (auto-login via stored token) |
| `/api/settings/login` | POST | **token-only** — set bearer token for a profile |
| `/api/settings/kubeconfig` | POST | import kubeconfig YAML |
| `/api/settings/test` | POST | health probe |
| `/api/settings/token` | GET | reveal bearer token (admin only) |
| `/api/settings/restart` | POST | restart Flask process |
| `/api/dr/overview` `/api/dr/apps` `/api/dr/appvaults` `/api/dr/storageclasses` `/api/dr/namespaces` | GET | DR discovery |
| `/api/dr/relationships/create` `/validate` | POST | create AMR |
| `/api/dr/relationships/{ns}/{name}` | GET / DELETE | detail / delete |
| `/api/dr/relationships/{ns}/{name}/failover` `/resync` | POST | DR actions |
| `/api/dr/relationships/{ns}/{name}/reverse-wizard-state` | GET | reverse resync state |
| `/api/dr/relationships/{ns}/{name}/reverse-wizard-create-bootstrap-snapshot` | POST | create bootstrap snapshot |
| `/api/credentials/users` | GET / POST | local user CRUD |
| `/api/credentials/ldap` `/test` | GET / POST | LDAP config |
| `/api/audit/logs` | GET | admin-only audit log query |

Total: **~70 routes**. See `app/main.py` for the definitive list.

---

## Repository layout

```
.
├── app/                      # Flask backend
│   ├── __init__.py
│   ├── main.py               # all routes
│   ├── trident_protect.py    # tridentprotect-ctl / oc / kubectl wrapper
│   ├── auth.py               # local users + LDAP
│   ├── audit.py              # JSON-line audit log
│   ├── config.py             # YAML config singleton
│   ├── api/__init__.py
│   ├── templates/            # 15 Jinja2 templates
│   └── static/css,js/        # NetApp CSS + 5 vanilla JS files
├── yaml/                     # 4 sample CRs (Application, Backup full, Schedule, Backup incremental)
├── restore/                  # Operator walkthroughs for Trident Protect v1, v2, v4
│   ├── trident-protect-1/    # v1.x restore walkthrough
│   ├── trident-protect-2/    # v2.x restore walkthrough
│   └── trident-protect-4/    # v4.x restore + snapshot-restore walkthroughs
├── systemd/
│   └── trident-protect-webui.service
├── DOCS.md                   # Detailed architecture + API reference
├── RELEASE.md                # Release procedure template
├── README.md                 # (this file)
├── CHANGELOG.md              # Version history
├── config.example.yaml       # Config template
├── requirements.txt          # flask==3.1.3, PyYAML==6.0.3, ldap3==2.9.1
├── install.sh                # 9-step installer
├── uninstall.sh              # 7-step uninstaller (backs up config.yaml)
├── run.sh                    # foreground runner
├── stop.sh                   # PID-based stop
├── make-package.sh           # builds dist/trident-protect-webui.tar.gz
└── netapp.design.md          # design tokens (cosmetic)
```

---

## Versioning

| Version | Notes |
|---|---|
| v2.0.0 (this) | Unified OCP + Kubernetes. Token-only cluster auth. |
| v1.4.18 | Last OCP-only / k8s-only split releases |

This unified edition supersedes both prior releases. Future minor versions
(v2.0.x, v2.1.x) will continue on this codebase.

---

## Credits

Powered by **NetApp Professional Service Asean Team & Nick R.**
Trident Protect is a NetApp product — see https://www.netapp.com.

---

## License

Open source project — use, modify, and distribute freely.
