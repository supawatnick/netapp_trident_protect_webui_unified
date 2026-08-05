# NetApp Trident Protect Web UI — Documentation (v1.3.0)

> **Comprehensive guide** for the **Trident Protect Web UI (OCP Edition)** — covering every feature, the architecture, the disaster recovery workflows (Reverse (YAML) wizard), the API, troubleshooting, and the v1.3.0 release notes.

> 🧹 **Architectural principle (v1.3.0+):** *Every dashboard action is form → YAML → `oc apply -f -` or a single `oc` command. The dashboard never orchestrates.*

---

## Table of Contents

1. [Overview & Architecture](#1-overview--architecture)
2. [Features](#2-features)
   - 2.1 [Dashboard](#21-dashboard)
   - 2.2 [Application Management](#22-application-management)
   - 2.3 [Backup Management](#23-backup-management)
   - 2.4 [Snapshot Management](#24-snapshot-management)
   - 2.5 [Schedule Management](#25-schedule-management)
   - 2.6 [Restore Wizard](#26-restore-wizard)
   - 2.7 [AppVault Management](#27-appvault-management)
   - 2.8 [Multi-Cluster Support](#28-multi-cluster-support)
   - 2.9 [Disaster Recovery](#29-disaster-recovery)
   - 2.10 [Settings](#210-settings)
   - 2.11 [Dashboard Login & RBAC](#211-dashboard-login--rbac-v140)
   - 2.12 [Audit Log (Logs menu)](#212-audit-log-logs-menu-v143)
3. [Disaster Recovery Workflows](#3-disaster-recovery-workflows)
4. [Storage Class Mapping (v1.2.0)](#4-storage-class-mapping-v110)
5. [Bootstrap Snapshot Pattern (v1.2.0)](#5-bootstrap-snapshot-pattern-v110)
6. [API Reference](#6-api-reference)
7. [Configuration Reference](#7-configuration-reference)
8. [Troubleshooting](#8-troubleshooting)
9. [Changelog](#9-changelog)

---

## 1. Overview & Architecture

### 1.1 What is Trident Protect Web UI?

A **web-based management interface** for **NetApp Trident Protect** resources on **Red Hat OpenShift Container Platform (OCP)** clusters. It provides an intuitive GUI for managing **Applications**, **Backups**, **Snapshots**, **Schedules**, **AppVaults**, and **Disaster Recovery (AppMirrorRelationships)** — designed for SREs, platform engineers, and storage administrators.

### 1.2 High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                    Browser (Web UI)                                │
│              HTML + Vanilla JS + CSS                              │
└──────────────────────────┬─────────────────────────────────────────┘
                           │ HTTP (JSON REST API)
┌──────────────────────────▼─────────────────────────────────────────┐
│                  Flask Backend (Python 3.12)                       │
│   ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐   │
│   │  app/main.py    │  │ app/config.py    │  │ app/templates  │   │
│   │ (REST routes)   │  │ (YAML config)    │  │ (Jinja2 HTML)  │   │
│   └────────┬────────┘  └──────────────────┘  └────────────────┘   │
│            │                                                       │
│   ┌────────▼────────┐                                              │
│   │ trident_protect │                                              │
│   │      .py        │  ← Business logic, oc command wrappers     │
│   └────────┬────────┘                                              │
└────────────┼────────────────────────────────────────────────────────┘
             │
   ┌─────────▼──────────┐
   │   tridentprotect-ctl│  ← Official CLI (bundled)
   └─────────┬──────────┘
             │
   ┌─────────▼──────────┐
   │   OpenShift API    │  ← kubernetes resources (CRDs)
   │  (Application,    │
   │   Schedule, AMR,  │
   │   AppVault, etc.) │
   └────────────────────┘
```

### 1.3 Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, Flask, PyYAML, subprocess |
| Frontend | HTML5, Vanilla JavaScript (no framework), CSS3 |
| CLI Tool | NetApp `tridentprotect-ctl` v26.02.0 (bundled in `bin/`) |
| Target Platform | Red Hat OpenShift Container Platform (OCP) |
| Process Manager | systemd (production) / direct `python3 -m app.main` (dev) |
| Port | 8080 (HTTP) |

### 1.4 OCP-Only Edition

This edition **only supports OpenShift Container Platform**. For vanilla Kubernetes, see the separate `netapp_trident_protect_webui_k8s` repository (planned for a future release).

The OCP-only mode means:
- Uses `oc` CLI (not `kubectl`) for cluster operations
- `kubecontext` follows OCP convention: `default/<api>/<user>`
- StorageClass detection uses OCP's `storageclass.kubernetes.io/is-default-class` annotation
- `tridentprotect-ctl` is the v26.02.0 OCP-compatible binary

---

## 2. Features

### 2.1 Dashboard

**URL**: `/` (root)

The Dashboard provides a **real-time overview** of protected applications, backup/snapshot counts, and cluster health.

#### What you see:

| Metric | Source | Example |
|--------|--------|---------|
| Active profile | `config.yaml` → `clusters.active` | `clus2-ocp` |
| Server | `oc whoami` | `https://api.clus2.ntapth.com:6443` |
| User | `oc whoami` | `kube:admin` |
| OCP Cluster | `oc version -o json` | `production-cluster (openshift)` |
| Applications | `tridentprotect-ctl get application` | 6 apps |
| Backups | `oc get backups --all-namespaces` | 24 backups |
| Snapshots | `oc get snapshots --all-namespaces` | 12 snapshots |
| Schedules | `oc get schedules --all-namespaces` | 4 schedules |

#### How it works:

```python
# trident_protect.py
def list_dashboard() -> dict:
    return {
        "apps": list_applications(),         # via tridentprotect-ctl get application
        "backups": list_backups(),           # via oc get backups
        "snapshots": list_snapshots(),       # via oc get snapshots
        "schedules": list_schedules(),       # via oc get schedules
    }
```

The Dashboard polls `/api/dashboard` every **30 seconds** (configurable via `config.yaml` → `refresh.interval_sec`).

---

### 2.2 Application Management

**URL**: `/applications`

Manage **Application CRs** (Trident Protect's logical grouping of Kubernetes resources).

#### Operations

| Action | UI Element | Backend API | CLI Command |
|--------|------------|-------------|-------------|
| List apps | Table on page load | `GET /api/applications` | `tridentprotect-ctl get application` |
| Create app | Form or YAML editor | `POST /api/applications/create` | `tridentprotect-ctl create application` |
| View YAML | "View" button | `GET /api/applications/<ns>/<name>` | `tridentprotect-ctl get application <name> -o yaml` |
| Validate YAML | "Validate" button | `POST /api/applications/validate` | n/a (client-side validation) |
| Delete app | "Delete" button | `DELETE /api/applications/<ns>/<name>` | `tridentprotect-ctl delete application <name>` |

#### Application CR structure:

```yaml
apiVersion: protect.trident.netapp.io/v1
kind: Application
metadata:
  name: my-app
  namespace: my-namespace
spec:
  includedNamespaces:
    - namespace: my-namespace
      labelSelector: {}    # Optional: filter by labels
```

#### Validation rules:

- `metadata.name` required (DNS-compliant)
- `metadata.namespace` required (must exist)
- `spec.includedNamespaces` required (at least one)
- `apiVersion` must start with `protect.trident.netapp.io`

---

### 2.3 Backup Management

**URL**: `/backups`

Trigger **ad-hoc backups** and browse **backup history** across all namespaces.

#### Operations

| Action | UI Element | Backend API |
|--------|------------|-------------|
| List backups | Table on page load | `GET /api/backups` |
| Create backup | Form (name + namespace + app + vault) | `POST /api/backups/create` |
| View YAML | "View" button | `GET /api/backup/<ns>/<name>` |
| Restore backup | "Restore" button → redirects to Restore page | (See §2.6) |
| Delete backup | "Delete" button | `DELETE /api/backup/<ns>/<name>` |

#### Backup CR structure (managed by Trident Protect):

```yaml
apiVersion: protect.trident.netapp.io/v1
kind: Backup
metadata:
  name: backup-20260728-120000
  namespace: my-namespace
spec:
  appVaultRef: ontap-s3-appvault
  applicationRef: my-app
```

---

### 2.4 Snapshot Management

**URL**: `/snapshots`

Create **on-demand snapshots** of running applications.

#### Operations

| Action | UI Element | Backend API |
|--------|------------|-------------|
| List snapshots | Table on page load | `GET /api/snapshots` |
| Create snapshot | Form | `POST /api/snapshots/create` |
| View YAML | "View" button | `GET /api/snapshot/<ns>/<name>` |
| Restore snapshot | "Restore" button → redirects to Restore page | (See §2.6) |
| Delete snapshot | "Delete" button | `DELETE /api/snapshot/<ns>/<name>` |

#### Snapshot CR structure:

```yaml
apiVersion: protect.trident.netapp.io/v1
kind: Snapshot
metadata:
  name: snap-20260728-130000
  namespace: my-namespace
spec:
  appVaultRef: ontap-s3-appvault
  applicationRef: my-app
```

---

### 2.5 Schedule Management

**URL**: `/schedules`

Manage **recurring backup/snapshot schedules** with cron-like granularity.

#### Operations (v1.2.0 Enhanced)

| Action | UI Element | Backend API |
|--------|------------|-------------|
| List schedules | Table on page load | `GET /api/schedules` |
| View schedule | "View" button | `GET /api/schedules/<ns>/<name>` |
| **Enable schedule** (v1.2.0) | Green "Enable" button | `POST /api/schedules/<ns>/<name>/enable` |
| **Disable schedule** (v1.2.0) | Red "Disable" button | `POST /api/schedules/<ns>/<name>/disable` |
| Create schedule | "+ Add Schedule" button | `POST /api/schedules/create` |
| Delete schedule | "Delete" button | `DELETE /api/schedules/<ns>/<name>` |

#### Schedule CR structure:

```yaml
apiVersion: protect.trident.netapp.io/v1
kind: Schedule
metadata:
  name: amr-schedule-my-app-rep
  namespace: my-namespace
spec:
  appVaultRef: ontap-s3-appvault
  applicationRef: my-app
  granularity: Custom          # Hourly / Daily / Weekly / Monthly / Custom
  enabled: true
  recurrenceRule: |-
    DTSTART:20260728T130000Z
    RRULE:FREQ=HOURLY;INTERVAL=1
  snapshotRetention: "2"
  backupRetention: "0"
  dataMover: Kopia
```

#### v1.2.0 Enhancement: Enable/Disable GUI

**Before v1.2.0**: Schedules could only be enabled/disabled via CLI:
```bash
oc patch schedule my-schedule -n my-ns --type merge -p '{"spec":{"enabled":false}}'
```

**v1.2.0**: Added **Enable/Disable buttons** in the Schedules page. The button auto-detects the cluster context from the namespace prefix (`clus1-*` → `nail-ocp`, `clus2-*` → `clus2-ocp`).

**Implementation** (`main.py`):

```python
def _resolve_schedule_context(namespace: str) -> str:
    """Auto-detect cluster context from namespace prefix."""
    if namespace.startswith("clus1-"):
        return cfg.profiles.get("nail-ocp", {}).get("kubecontext", "")
    if namespace.startswith("clus2-"):
        return cfg.profiles.get("clus2-ocp", {}).get("kubecontext", "")
    return ""

@app.route("/api/schedules/<namespace>/<name>/enable", methods=["POST"])
def api_enable_schedule(namespace, name):
    ctx = request.args.get("context") or _resolve_schedule_context(namespace)
    rc, out, err = trident_protect.oc_run(
        ["patch", "schedule", name, "-n", namespace,
         "--type", "merge", "-p", '{"spec":{"enabled":true}}'],
        context=ctx
    )
    # ...
```

**UI** (`schedules.html`):

```js
async function toggleSchedule(namespace, name, enable) {
  const action = enable ? 'enable' : 'disable';
  if (!confirm(`${verb} schedule "${name}"?`)) return;
  // Fetch profiles for context resolution
  const settings = await fetch('/api/settings').then(r => r.json());
  const profiles = settings.profiles || {};
  let context = '';
  if (namespace.startsWith('clus1-')) context = profiles['nail-ocp']?.kubecontext || '';
  else if (namespace.startsWith('clus2-')) context = profiles['clus2-ocp']?.kubecontext || '';
  // Call API
  const res = await fetch(
    `/api/schedules/${namespace}/${name}/${action}?context=${context}`,
    { method: 'POST' }
  );
  // ...
}
```

---

### 2.6 Restore Wizard

**URL**: `/restore`

Restore a backup or snapshot to a destination namespace with **StorageClass mapping**.

#### Restore flow:

1. Select source: namespace + backup/snapshot
2. Select destination: namespace + storage class
3. Map storage classes (source → destination)
4. Trigger restore

#### BackupRestore CR (managed by Trident Protect):

```yaml
apiVersion: protect.trident.netapp.io/v1
kind: BackupRestore
metadata:
  name: restore-my-app-20260728
  namespace: destination-namespace
spec:
  appVaultRef: destination-appvault
  appArchivePath: <path from backup CR>
  namespaceMapping:
    - destination: destination-namespace
      source: source-namespace
  storageClassMapping:
    - destination: a400-nfs-v2
      source: a400-nfs
```

---

### 2.7 AppVault Management

**URL**: `/settings/appvaults`

AppVault is the **object storage backend** for backups/snapshots. Supported providers:
- **NetApp ONTAP S3** (`ontap-s3`)
- **AWS S3** (`aws`)
- **Azure Blob** (`azure`)
- **Google Cloud Storage** (`gcp`)
- **StorageGRID S3** (`storagegrid-s3`)
- **Generic S3** (`generic-s3`)

#### AppVault CR structure:

```yaml
apiVersion: protect.trident.netapp.io/v1
kind: AppVault
metadata:
  name: ontap-s3-appvault
  namespace: trident-protect
spec:
  providerType: ontap-s3
  providerConfig:
    bucketName: my-trident-protect-bucket
    s3ForcePathStyle: false
  providerCredentials:
    valueFromSecret:
      name: appvault-secret
    secretAccessKey: "<redacted>"
```

---

### 2.8 Multi-Cluster Support

**URL**: `/settings/clusters`

Manage **multiple OpenShift clusters** from a single Web UI instance. Supports up to **2 profiles** (DR source + destination).

#### v1.2.0 Enhancements

1. **Auto-login on Switch**: When switching profiles with a saved token, the Web UI automatically calls `oc login --token <token>` to re-authenticate.

2. **Login Button for Expired Tokens**: If the saved token fails (e.g., expired), the UI shows a **Login** button next to **Switch** so users can re-authenticate.

3. **Kubecontext Auto-Discovery**: After successful login, the Web UI scans `~/.kube/config` for the new context (e.g., `default/api-clus1-ntapth.com:6443/kube:admin`) and saves it in the profile. This enables DR operations that need `--context <name>`.

#### Profile structure (`config.yaml`):

```yaml
clusters:
  active: clus2-ocp
  profiles:
    nail-ocp:
      api_url: https://api.clus1.ntapth.com:6443
      kubecontext: default/api-clus1-ntapth-com:6443/kube:admin   # auto-discovered
      token: sha256~MeGsWD2edv8C7L7MmwTVCXBJVVVgUyb-MpGlLDllalw
      insecure_skip_tls: true
      appvault: ontap-s3-appvault
      appvault_namespace: trident-protect
      description: CLUS1 OCP cluster
    clus2-ocp:
      api_url: https://api.clus2.ntapth.com:6443
      kubecontext: default/api-clus2-ntapth-com:6443/kube:admin   # auto-discovered
      token: sha256~9J9Jz8YJERssep4Fj2b-RwddlHyyPPGOoAPGCRSwzqo
      insecure_skip_tls: true
      appvault: ontap-s3-appvault
      appvault_namespace: trident-protect
      description: CLUS2 OCP cluster
```

#### Implementation (`trident_protect.py`):

```python
def find_kubecontext_for_api(api_url: str) -> str:
    """Find the kubeconfig context name that points to a given API URL.

    After `oc login`, a context is created in ~/.kube/config
    (named like 'default/<api>/kube:admin').
    """
    from pathlib import Path
    import yaml
    kc = yaml.safe_load((Path.home() / ".kube" / "config").read_text())

    target = api_url.strip().rstrip("/")
    matching_clusters = {
        c.get("name", "")
        for c in (kc.get("clusters") or [])
        if ((c.get("cluster") or {}).get("server") or "").strip().rstrip("/") == target
    }

    for ctx in (kc.get("contexts") or []):
        if (ctx.get("context") or {}).get("cluster") in matching_clusters:
            return ctx.get("name", "")
    return ""
```

---

### 2.9 Disaster Recovery

**URL**: `/disaster-recovery`

Manage cross-cluster **AppMirrorRelationships (AMR)** for disaster recovery. AMR replicates app data from source cluster to destination cluster via **NetApp SnapMirror**.

#### Operations

| Action | UI Element | Backend API |
|--------|------------|-------------|
| View overview | Page load | `GET /api/dr/overview` |
| Create AMR (form) | Form | `POST /api/dr/relationships/create` |
| Create AMR (YAML) | YAML editor | `POST /api/dr/relationships/create` |
| Validate AMR | "Validate" button | `POST /api/dr/relationships/validate` |
| Failover | "Failover" button | `POST /api/dr/relationships/<ns>/<name>/failover` |
| Resync | "Resync" button | `POST /api/dr/relationships/<ns>/<name>/resync` |
| Reverse (YAML) | "Reverse (YAML)" button (6-step wizard) | `POST /api/dr/relationships/create` (after wizard gate) |
| Resume | "Resume" button | `POST /api/dr/relationships/<ns>/<name>/resume` |

#### AMR CR structure (Trident Protect v26.02.0):

```yaml
apiVersion: protect.trident.netapp.io/v1
kind: AppMirrorRelationship
metadata:
  name: amr-clus1-clus2-rep
  namespace: clus1-trident-protect-1
  annotations:
    trident.netapp.io/source-cluster: nail-ocp    # UI custom annotation
spec:
  desiredState: Established
  sourceAppVaultRef: ontap-s3-appvault            # Cluster where source app lives
  destinationAppVaultRef: ocp-bucket-02           # Cluster where dest app lives
  sourceApplicationName: stateless-protect-app
  sourceApplicationUID: 7e667d7e-8c40-4a90-994c-e0613e4be17e
  namespaceMapping:
    - destination: clus1-trident-protect-1
      source: clus2-trident-protect-1
  recurrenceRule: |-
    DTSTART:20260728T130000Z
    RRULE:FREQ=HOURLY;INTERVAL=1
  storageClassMapping:                              # v1.2.0 enhancement
    - source: a400-nfs-02
      destination: a400-nfs-v2
```

#### AMR Lifecycle States

| State | Meaning | Next Action |
|-------|---------|-------------|
| `Establishing` | Initial replication in progress | Wait for `Established` |
| `Established` | Active replication, source = running app | Can `Failover` |
| `Promoting` | Failover in progress | Wait for `Promoted` |
| `Promoted` | Failover complete, destination = running app | Can `Resync` or `Reverse (YAML)` |
| `Failed` | Replication failed | Check controller logs |

#### Auto-detected cluster labels

The UI shows **source** and **destination cluster** labels next to each AMR by deriving them from the **namespace prefixes**:
- `clus1-*` → `nail-ocp`
- `clus2-*` → `clus2-ocp`

This is done via `_derive_cluster_from_namespace()` in `trident_protect.py`.

---

### 2.10 Settings

**URL**: `/settings`

Four sub-pages:

1. **Settings → Overview** — Active profile, server, user info
2. **Settings → Clusters** — Profile management, switching, login
3. **Settings → AppVaults** — AppVault configuration
4. **Settings → Credentials** *(admin only)* — Dashboard login users + LDAP config (see §2.11)

#### Cluster Profile Management

| Action | UI Element | Backend API |
|--------|------------|-------------|
| List profiles | Table on page load | `GET /api/settings` |
| Add profile | "+ Add Profile" | `POST /api/settings/profile` |
| Edit profile | "Edit" button | `POST /api/settings/profile` |
| Switch profile | "Switch" button | `POST /api/settings/switch` |
| Login & Switch | "Login & Switch" or "Login" | `POST /api/settings/login` |
| Import Kubeconfig | "Import Kubeconfig" | `POST /api/settings/kubeconfig` |
| Delete profile | "Delete" button | `DELETE /api/settings/profile/<name>` |
| Test connection | "Test" button | `POST /api/settings/test` |
| Show token | "Show full" | `GET /api/settings/token` |

### 2.11 Dashboard Login & RBAC *(v1.4.0+)*

The dashboard has its **own login layer** that is **completely separate** from the
OCP cluster profile login. The OCP profile login is still used to talk to the
Kubernetes API; the dashboard login gates who can use the UI.

**URL:** `/login` · **Session:** Flask cookie (`SECRET_KEY` from `config.yaml`)

#### Two roles

| Role     | Can do                                                                 |
|----------|------------------------------------------------------------------------|
| `admin`  | Everything — all CRUD endpoints, profile switch, manage Credentials.  |
| `readonly` | Read-only across every page. May still **switch profile** and re-authenticate a cluster profile (the only writes allowed). |

`readonly` POST/DELETE/PUT/PATCH calls are blocked at the backend
(`before_request` guard) **except** the explicit allowlist:

- `POST /api/settings/switch`
- `POST /api/settings/login`
- `POST /api/settings/test`

Admin-only endpoints (return 403 for non-admin):
- All `/api/credentials/*` (user + LDAP management)
- `GET /api/settings/token` — full bearer token; `readonly` gets only the masked preview
- `GET /settings/credentials` (page route) — `readonly` is redirected to `/settings`

#### Local users

Stored in `auth_users.json` at the project root (gitignored, mode `0600`).
Passwords are hashed via `werkzeug.security` (scrypt). Add/edit/delete from
**Settings → Credentials** (the **Local Users** panel).

A default user is **seeded on first run** when `auth.enabled: true` and no
users exist:

```
admin / admin123
```

A prominent warning is logged. **Change the password immediately** under
Settings → Credentials.

#### Login username format

Login attempts are routed **strictly** by format — there is no implicit
fallback from local to LDAP:

| Input format | Auth path | LDAP records stored as |
|---|---|---|
| `nick` (bare) | **local only** | — |
| `DOMAIN\nick` (netbios) | **LDAP only** | `domain\nick` |
| `nick@domain` (UPN) | **LDAP only** | `nick@domain` |

The bare-name LDAP fallback has been removed so that a same-named local
user and a same-named domain user can co-exist without collision. To sign
in as a domain user, type the qualified form; to sign in as a local user,
type the bare username. If LDAP is disabled and a qualified login is
attempted, authentication fails with `401 Invalid username or password`.

#### LDAP / domain users (optional)

Configure under Settings → Credentials. When enabled, a successful LDAP
login performs:

1. **Service bind** to LDAP using `bind_dn` / `bind_password`
2. **User search** under `base_dn` with the configured `user_filter` (with
   `{username}` placeholder, RFC 4515-escaped). The search uses the bare
   username extracted from the login input.
3. **User bind** with the user's DN + submitted password
4. **Role determination:** member of any `admin_group_dns` → `admin`; else
   `default_role` (typically `readonly`)

Domain users are auto-registered in `auth_users.json` under the qualified
name (e.g. `ntapth\nick`) with `source: "ldap"` (no password hash is
stored). They appear in the read-only **Domain Users (LDAP)** panel of
**Settings → Credentials** for audit purposes only — management
(add/edit/delete) is done in AD, not in the UI.

`ldap3` (`pip install ldap3`) is used; supports plain `ldap://`, `ldaps://`,
and `STARTTLS`. The `skip_cert_verify` toggle covers self-signed certificates.

#### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/login` | Authenticate (`username` may be bare or `DOMAIN\user` / `user@domain`) |
| `POST` | `/api/auth/logout` | Clear session |
| `GET`  | `/api/auth/me` | Current `{user, role, source}` |
| `GET`  | `/api/credentials/users?source=local` | List local users (admin only) |
| `GET`  | `/api/credentials/users?source=ldap` | List domain users (admin only) |
| `GET`  | `/api/credentials/users` | List all users (admin only) |
| `POST` | `/api/credentials/users` | Create local user (admin only) |
| `POST` | `/api/credentials/users/<name>` | Edit role/password of local user (admin only) |
| `DELETE` | `/api/credentials/users/<name>` | Delete local user (admin only) |
| `GET`  | `/api/credentials/ldap` | Get LDAP config (admin only) |
| `POST` | `/api/credentials/ldap` | Save LDAP config (admin only) |
| `POST` | `/api/credentials/ldap/test` | Test LDAP server (admin only) |

#### `auth:` config section

```yaml
auth:
  enabled: true
  default_role: "readonly"
  ldap:
    enabled: false
    server: "ldap.example.com"
    port: 389
    use_tls: false
    skip_cert_verify: true
    bind_dn: "cn=svc,ou=service,dc=example,dc=com"
    bind_password: ""
    base_dn: "ou=people,dc=example,dc=com"
    user_filter: "(&(objectClass=person)(sAMAccountName={username}))"
    username_attribute: "sAMAccountName"
    admin_group_dns:
      - "cn=tp-admins,ou=groups,dc=example,dc=com"
```

### 2.12 Audit Log (Logs menu) *(v1.4.3+)*

Every mutating action performed through the UI Dashboard is recorded so
you can answer "who did what, and when?". Records are appended to
`logs/audit.log` (JSON lines, gitignored via `*.log`).

**Menu:** **Logs** (sidebar, admin-only) · **API:** `GET /api/audit/logs` (admin-only)

#### What is recorded

| Action verbs | Endpoints |
|---|---|
| `schedule create / delete / enable / disable` | `apply`, `delete`, `enable`, `disable` |
| `backup create / delete`, `backuprestore trigger` | `apply`, `delete`, `/backuprestores` |
| `snapshot create / delete`, `snapshotrestore trigger` | `apply`, `delete`, `/snapshotrestores` |
| `application create / delete` | `apply`, `delete` |
| `appvault create / delete` | `POST /api/appvaults`, `delete` |
| `restore delete` | delete restore |
| `dr_relationship create / delete / failover / resync / bootstrap_snapshot` | DR routes |
| `cluster_profile save / delete / switch`, `cluster login` | `/api/settings/profile*`, `switch`, `login` |
| `local_user create / update / delete` | `/api/credentials/users*` |
| `ldap update` | `/api/credentials/ldap` |
| `auth login / logout` | `/api/auth/login`, `/logout` |

Validate and test endpoints (`*validate`, `test`, `healthz`, static) are
intentionally **excluded** to keep the trail focused on real actions.
**Blocked attempts** (401 unauthenticated, 403 readonly) are also recorded
so you can see failed/abusive activity.

#### Record fields

```
ts (UTC)   user   role   source   action   kind
namespace  name   detail  ip   method   path   status   ok
```

`detail` is the response `message` (e.g. `"Schedule ns1/my-sched enabled"`)
truncated to 500 chars. The raw request YAML/payload is **not** logged.

#### `logs:` config section

```yaml
logs:
  enabled: true        # default ON when the section is missing
  max_rows: 10000      # auto-prune the file past this many lines (0 = unlimited)
```

The file is auto-pruned at startup and every 200 writes; only the last
`max_rows` lines are kept. The file is `chmod 0640` and lives outside git.

#### Querying

```bash
# tail the raw JSON-lines log
tail -f logs/audit.log | jq .

# or use the API (admin session cookie required)
curl -s 'http://<host>:8080/api/audit/logs?user=admin&result=fail&limit=50' | jq
```

The Logs page supports filter by user, action, result (ok/fail), and free-text
search across name/namespace/detail. Auto-refresh and pagination are built in.

---

## 3. Disaster Recovery Workflows

### 3.1 Initial Setup (Create AMR)

```
Source cluster: CLUS1 (nail-ocp)
Destination cluster: CLUS2 (clus2-ocp)
App: stateless-protect-app (1Gi PVC)
Storage:
  CLUS1 → a400-nfs
  CLUS2 → a400-nfs-02
```

**Steps:**

1. Ensure both clusters have AppVault CRs (e.g., `ontap-s3-appvault` on both)
2. Ensure source app `stateless-protect-app` exists on CLUS1
3. Create the AppMirrorRelationship on CLUS2 (destination, where AMR lives) via the AMR form → `POST /api/dr/relationships/create` (apply-YAML only). Trident Protect pulls the source Application manifest from the source AppVault and creates the destination Application CR + first transfer automatically — no other CRs are created by the dashboard.

### 3.2 Failover (CLUS1 → CLUS2)

```
Current state:
  - AMR src=CLUS1, dst=CLUS2, state=Established
  - CLUS1: pod Running (source)
  - CLUS2: no pod (destination standby)
```

**Steps:**

1. User clicks "Failover" in UI
2. Backend patches AMR `desiredState=Promoted`
3. Wait for AMR state=Promoted (~30-60s)
4. CLUS2 pod comes up (data replicated from CLUS1)
5. CLUS1 pod still running (per docs, source not auto-stopped)

### 3.3 Reverse Direction — Apply-YAML (6-step wizard)

Dashboard **does not orchestrate** reverse direction. The user drives it via a 6-step gated wizard that **only produces an AMR YAML**; the dashboard **applies it via a single `oc apply -f -`** — no orchestration logic on the server side (consistent with the project rule of "every action = apply-YAML-only or single oc command").

#### Prerequisites (must be true before clicking Reverse (YAML))

1. AMR is in `Promoted` state
2. Source cluster has at least one Schedule + Snapshot for the source application
3. All schedules on the **source cluster** have been disabled
4. The **destination (promoted) cluster** has at least one Schedule + Snapshot to bootstrap reverse replication
5. Old AMR is deleted

#### Wizard steps (all are gates; no manual code runs)

| # | Step | Logic |
|---|---|---|
| 1 | Verify AMR is in `Promoted` state | reject if not (button to call Failover offered) |
| 2 | Verify source cluster has Schedule + Snapshot | block + message; user creates them via Schedules/Snapshots page, then Re-check |
| 3 | Disable ALL source-cluster schedules | batch POST `/api/schedules/<ns>/<name>/disable`; gate when none enabled |
| 4 | Verify destination (promoted) cluster has Schedule + Snapshot + choose baseline snapshot | radio A = use latest existing / radio B = create new; gate: A needs snapshot present, B needs POST `/reverse-wizard-create-bootstrap-snapshot` succeeded |
| 5 | Delete old AMR | `DELETE /api/dr/relationships/<ns>/<name>`; gate when oc returns NotFound |
| 6 | Finish | close wizard; user opens `Create AMR` form manually and fills in reversed values |

#### What the wizard produces for the user (Step 6 summary)

When the 6 gates are cleared, the wizard renders the reversed AMR values to fill into the `Create AMR` form:

- **Name:** same as original (e.g., `amr-stateless`)
- **Source Cluster:** the OTHER profile (the one that is NOT the active profile)
- **Source Namespace:** original destination namespace (where the promoted app lives)
- **Source App:** original `sourceApplicationName` + its current UID (collected from Application CR on promoted side)
- **Source AppVault:** original **destination** AppVaultRef (was `destinationAppVaultRef`; swap for balance)
- **Destination Cluster:** the **active** profile (dashboard only applies to active cluster)
- **Destination Namespace:** dropdown (default = original source namespace; user can override)
- **Destination AppVault:** original **source** AppVaultRef (swap)
- **Storage Class:** default of new destination cluster
- **Snapshot Frequency:** Every 1 hour

The user clicks `Apply` on the Create form → dashboard applies the YAML via `oc apply -f -` on the active cluster. No orchestration.

### 3.4 Failback (Use the Create AMR wizard to flip again)

After Reverse (YAML) makes CLUS1 the new source, the user can re-run the wizard to flip back:

1. Open `Reverse (YAML)` again on the new Promoted AMR
2. Wizard Step 6 produces another set of reversed values
3. Apply → cluster flip is the inverse of the previous one

### 3.5 Resync (Simple)

For situations where you want to discard data on the destination:

1. Click "Resync" on AMR (state=Promoted)
2. Backend patches `desiredState=Established`
3. Replication resumes (destination data will be overwritten by source)

### 3.6 Cleanup (Delete AMR)

1. Click "Delete" on AMR row
2. Confirm dialog
3. Backend deletes AMR via `tridentprotect-ctl delete appmirrorrelationship`

---

## 4. Storage Class Mapping (v1.2.0)

### 4.1 The Problem

When source and destination clusters have **different storage classes**, the AMR fails because:

```
ERROR: Waiting for destination PVC 'clus2-trident-protect-1/nginx-pvc' to be 'Bound'
```

The destination cluster doesn't have the source cluster's storage class, so the PVC stays in `Pending` state.

### 4.2 The Solution (v1.2.0)

The `storageClassMapping` field in AMR spec tells Trident Protect to **map source SCs to destination SCs**:

```yaml
storageClassMapping:
  - source: a400-nfs-02        # Source cluster's SC
    destination: a400-nfs-v2   # Destination cluster's SC
```

This causes the controller to create PVCs on the destination using the mapped SC.

### 4.3 Auto-Detection + Manual Selection

**v1.2.0** combines:

1. **Auto-detect source SC** from current destination's PVC (via `get_pvc_storageclass()`)
2. **User selects destination SC** from a dropdown of destination cluster's available SCs (via `list_storage_classes()`)

#### Backend (`trident_protect.py`):

```python
def get_pvc_storageclass(namespace: str, context: str | None = None) -> str:
    """Auto-detect source SC from first PVC in namespace."""
    rc, out, err = oc_run(
        ["get", "pvc", "-n", namespace,
         "-o", "jsonpath={.items[0].spec.storageClassName}"],
        context=context,
    )
    if rc != 0:
        log.warning(f"Failed to get PVC storage class in {namespace}")
        return ""
    return (out or "").strip()


def list_storage_classes(context: str | None = None) -> list[dict]:
    """List storage classes with default detection."""
    rc, out, err = oc_run(["get", "storageclass", "-o", "json"], context=context)
    if rc != 0:
        return []
    items = json.loads(out).get("items", [])
    result = []
    for sc in items:
        annotations = sc.get("metadata", {}).get("annotations", {}) or {}
        is_default = (
            annotations.get("storageclass.kubernetes.io/is-default-class") == "true"
            or annotations.get("storageclass.beta.kubernetes.io/is-default-class") == "true"
        )
        result.append({"name": sc.get("metadata", {}).get("name", ""), "isDefault": is_default})
    return result
```

#### Updated spec builder:

```python
if source_storage_class and dest_storage_class:
    reversed_spec["storageClassMapping"] = [
        {"source": source_storage_class, "destination": dest_storage_class}
    ]
else:
    # Fallback for legacy reverse direction
    sc = source_storage_class or dest_storage_class or "a400-nfs"
    reversed_spec["storageClassName"] = sc
```

---

## 5. Bootstrap Snapshot Pattern

### 5.1 The Problem

When the user creates a new (reversed) AMR via the wizard, the new AMR waits for a snapshot from the new source cluster. If the source's schedule hasn't triggered yet:

```
ERROR: Failed to get most recent snapshot: No snapshots found for source app
```

### 5.2 The Solution (in the Reverse (YAML) wizard Step 4)

Wizard Step 4 detects whether the destination (promoted) cluster already has a snapshot + schedule:

- **Option A (default):** Use the latest completed snapshot on the destination cluster
- **Option B:** Trigger a new on-demand snapshot via `POST /api/dr/relationships/<ns>/<name>/reverse-wizard-create-bootstrap-snapshot` (which calls `trigger_bootstrap_snapshot()` — apply-YAML + `wait_for_snapshot`)

The backend function:

```python
def trigger_bootstrap_snapshot(snapshot_name, application_ref, namespace, appvault_ref, context=None):
    manifest = {
        "apiVersion": "protect.trident.netapp.io/v1",
        "kind": "Snapshot",
        "metadata": {"name": snapshot_name, "namespace": namespace},
        "spec": {"applicationRef": application_ref, "appVaultRef": appvault_ref},
    }
    yaml = _yaml.safe_dump(manifest, ...)
    # oc apply -f - (single command, no orchestration)
    # wait_for_snapshot(...) — polls until Completed
```

### 5.3 Impact

The new wizard Step 4 eliminates the "no snapshots found" bootstrap failure — user either picks Option A (existing latest) or B (creates one inline) before proceeding past the gate.

---

## 6. API Reference

### 6.1 Applications

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/applications` | List all applications |
| POST | `/api/applications/create` | Create application (YAML) |
| POST | `/api/applications/validate` | Validate application YAML |
| GET | `/api/applications/<ns>/<name>` | Get application YAML |
| DELETE | `/api/applications/<ns>/<name>` | Delete application |

### 6.2 Backups

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/backups` | List all backups |
| POST | `/api/backups/create` | Trigger backup |
| GET | `/api/backup/<ns>/<name>` | Get backup YAML |
| DELETE | `/api/backup/<ns>/<name>` | Delete backup |

### 6.3 Snapshots

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/snapshots` | List all snapshots |
| POST | `/api/snapshots/create` | Create snapshot |
| GET | `/api/snapshot/<ns>/<name>` | Get snapshot YAML |
| DELETE | `/api/snapshot/<ns>/<name>` | Delete snapshot |

### 6.4 Schedules

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/schedules` | List all schedules |
| POST | `/api/schedules/create` | Create schedule |
| GET | `/api/schedules/<ns>/<name>` | Get schedule YAML |
| DELETE | `/api/schedules/<ns>/<name>` | Delete schedule |
| **POST** | **`/api/schedules/<ns>/<name>/enable`** | **Enable schedule (v1.2.0)** |
| **POST** | **`/api/schedules/<ns>/<name>/disable`** | **Disable schedule (v1.2.0)** |

### 6.5 Disaster Recovery

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/dr/overview` | DR overview (AMRs + clusters) |
| POST | `/api/dr/relationships/create` | Create AMR (apply-YAML only) |
| POST | `/api/dr/relationships/validate` | Validate AMR YAML |
| POST | `/api/dr/relationships/<ns>/<name>/failover` | Failover |
| POST | `/api/dr/relationships/<ns>/<name>/resync` | Resync |
| GET | `/api/dr/relationships/<ns>/<name>/reverse-wizard-state` | Reverse (YAML) wizard state snapshot (6-step) |
| POST | `/api/dr/relationships/<ns>/<name>/reverse-wizard-create-bootstrap-snapshot` | Create bootstrap snapshot on promoted side (wizard Step 4 Option B) |
| POST | `/api/dr/relationships/<ns>/<name>/resume` | Resume AMR |

### 6.6 Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/settings` | Get all settings |
| POST | `/api/settings/switch` | Switch active profile |
| POST | `/api/settings/login` | Login with token/password |
| POST | `/api/settings/profile` | Add/update profile |
| DELETE | `/api/settings/profile/<name>` | Delete profile |
| POST | `/api/settings/test` | Test connection |
| GET | `/api/settings/token` | Get current token |
| POST | `/api/settings/kubeconfig` | Import kubeconfig |

---

## 7. Configuration Reference

### 7.1 `config.yaml` Structure

```yaml
app:
  host: 0.0.0.0
  port: 8080
  debug: false
  secret_key: trident-protect-ui-demo-key-2026

cli:
  path: bin/tridentprotect-ctl

refresh:
  interval_sec: 30

appvault: {}

clusters:
  active: clus2-ocp
  profiles:
    <profile_name>:
      api_url: https://api.cluster.example.com:6443
      token: sha256~xxxxx...         # auto-saved after login
      kubecontext: default/...        # auto-discovered after login
      insecure_skip_tls: true
      appvault: ontap-s3-appvault
      appvault_namespace: trident-protect
      description: "Cluster description"
```

### 7.2 Profile Conventions

| Convention | Value |
|-----------|-------|
| Profile names | `nail-ocp`, `clus2-ocp` (or any) |
| Max profiles | 2 (DR source + destination) |
| Namespace convention | `clus<N>-<app>` (e.g., `clus1-trident-protect-1`) |
| Cluster from namespace | `clus1-*` → `nail-ocp`, `clus2-*` → `clus2-ocp` |
| Kubecontext format | `default/<api-url>/<user>` |

### 7.3 Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TRIDENT_PROTECT_CLI` | `bin/tridentprotect-ctl` | Override CLI path |
| `TRIDENT_PROTECT_CONFIG` | `config.yaml` | Override config path |
| `TRIDENT_PROTECT_PORT` | `8080` | Override HTTP port |

---

## 8. Troubleshooting

### 8.1 Common Errors

#### "Active profile 'X' has no kubecontext configured"

**Cause**: Profile was created via token login but `oc login` was never called.

**Fix**:
- Switch to profile via UI (auto-runs `oc login`)
- OR run `oc login <api> --token <token>` manually

#### "Failed to get most recent snapshot: No snapshots found for source app"

**Cause**: AMR's new source cluster doesn't have any snapshots yet.

**Fix**:
- v1.2.0+: This should be handled by Step 0 (bootstrap snapshot)
- v1.0.0: Manually create snapshot on source cluster

#### "Waiting for destination PVC to be 'Bound'" (Stuck)

**Cause**: Storage class mismatch — destination cluster doesn't have source's SC.

**Fix**:
- v1.2.0+: Use a single `storageClassName` field in the AMR spec (set when creating via Create form) — the reversed AMR's storage class matches the destination cluster's available SCs
- v1.0.0: Manually edit AMR spec to add `storageClassMapping`

#### "Saved token is invalid or expired"

**Cause**: The bearer token in `config.yaml` has expired.

**Fix**: Click "Login" button in Settings → Clusters → re-authenticate with new token.

### 8.2 Debug Commands

```bash
# Check AMR state
oc get amr -A
oc get amr <name> -n <ns> -o yaml

# Check conditions
oc get amr <name> -n <ns> -o jsonpath='{.status.conditions}'

# Check events
oc get events -n <ns> --sort-by=.lastTimestamp

# Check Trident Protect controller
oc logs -n trident-protect -l app=trident-protect-controller

# Check AppMirrorUpdate
oc get appmirrorupdate -A

# Check schedules
oc get schedule -A
oc get schedule <name> -n <ns> -o yaml
```

### 8.3 FAQ

**Q: How many clusters can I manage?**
A: Maximum 2 profiles (DR source + destination).

**Q: Where is my kubeconfig stored?**
A: `~/.kube/config` (standard Kubernetes location).

**Q: How does auto-login work?**
A: When switching profiles with a saved token, the Web UI calls `oc login <api> --token <token>` to authenticate.

**Q: What's the difference between Reverse (YAML) and Reverse Resync?**
A: See [§3.3](#33-reverse-direction--apply-yaml-6-step-wizard). Reverse (YAML) is the dashboard's apply-YAML-only 6-step gated wizard — no orchestration. Reverse Resync is a single oc patch back to `desiredState=Established`.

**Q: How long does initial replication take?**
A: Depends on PVC size. ~30 seconds for 1Gi PVC, longer for larger volumes.

---

## 9. Changelog

### 9.1 v1.3.0 (Current — 2026-07-31)

**Release model:** *Every dashboard action is form → YAML → `oc apply -f -` or a single `oc` command. No orchestration.*

**Major Features:**

1. **Reverse (YAML) Wizard (6-step gated)** — Replaces the 12-step orchestrator reverse-direction. The wizard gates each prerequisite, then the user applies the resulting AMR via the existing Create form (`oc apply -f -` on the active cluster).
   - Wizard gates: Promoted → Source snapshot+schedule → Disable source schedules → **Destination baseline + snapshot choice** → Delete old AMR → Finish
   - **Step 4 snapshot choice (NEW):** Option A "use latest existing" (default) or Option B "create new snapshot now" (apply-YAML + `wait_for_snapshot`). Editable default name `reverse-bootstrap-<amr>-<unix-ts>`. Option B is forced when no snapshot exists.
   - Pre-wizard warning modal: "Resources on destination cluster must be down before reverse replication can proceed."
   - Step 6 (Finish) replaces the old auto-open-form behavior — wizard closes and instructs the user to open the Create AMR form with reversed values.
2. **Removed (orchestrator deleted):**
   - **Row buttons:** "Sync", "Reverse Direction" (orchestrator).
   - **Backend routes:** `POST /api/dr/relationships/<ns>/<name>/sync`, `GET /api/dr/relationships/<ns>/<name>/pre-reverse-direction`, `POST /api/dr/relationships/<ns>/<name>/reverse-direction`.
   - **Backend functions:** `trigger_amr_sync()`, `reverse_application_direction()`.
3. **UI rename + color:** Row button "Reverse (YAML)" → **"Reverse Resync"** + `btn-warning` (orange).
4. **NEW `uninstall.sh`** — thorough uninstall (stop+disable service, remove `/opt/trident-protect-webui`, backup `config.yaml` to `/tmp/trident-webui-config-backup-*.yaml`, remove firewall rule).
5. **Sidebar version:** `v1.3.0 (Trident v26.6)`.
6. **Trident Protect CLI:** v26.06.0.

**Bug Fixes:**

- `get_reverse_wizard_state()` no longer early-returns when AMR is missing → sets `oldAmrDeleted: True` so Step 4 (Step 5 in new numbering) of the wizard advances instead of getting stuck.
- Frontend now caches wizard spec before destructive actions and falls back to the cached values when the live state API returns empties (handles the refresh-after-delete race).
- `wizardDeleteOldAmr()` polls `refreshWizardState()` every 1.5s up to 5 times until `oldAmrDeleted` flips to True.
- `populateNsDropdown` / `loadSourceClusterData` / `loadDestClusterData` are `await`-ed before reading their `<select>` options in `populateReversedAmrForm` (precise load ordering — no more `ERR: undefined` from a half-loaded select).

**Files Changed:**

- `app/main.py` — added `GET .../reverse-wizard-state`, `POST .../reverse-wizard-create-bootstrap-snapshot`; removed `.../sync`, `.../pre-reverse-direction`, `.../reverse-direction`.
- `app/trident_protect.py` — added `get_reverse_wizard_state()`, `trigger_bootstrap_snapshot()`; removed `trigger_amr_sync()`, `reverse_application_direction()`.
- `app/templates/disaster_recovery.html` — `Reverse Resync` row button (warning), pre-wizard warning modal, 6-step wizard, snapshots list + Step 4 choice UX, Finish (no auto-open-form).
- `app/templates/base.html` — version label.
- **NEW** `uninstall.sh`.
- `README.md`, `DOCS.md`, `CHANGELOG.md`, `RELEASE.md` — refreshed.
- `make-package.sh` — `CLI_VERSION="26.06.0"`.

### 9.2 v1.2.0 — 2026-07-29

Restore workflow overhaul, Reclaim Policy UI, profile-name bug fix, restore-page performance. See CHANGELOG.md.

### 9.3 v1.0.0

- Initial release: Trident Protect Web UI v26.02.0
- OpenShift-only edition
- Multi-cluster support (up to 2 profiles)
- Disaster Recovery: Failover, Resync, AMR CRUD
- Backup/Snapshot/Schedule management
- AppVault management
- Import Kubeconfig feature
