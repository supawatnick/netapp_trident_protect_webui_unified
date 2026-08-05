"""High-level Trident Protect operations via tridentprotect-ctl CLI.

Supports both OpenShift (oc) and vanilla Kubernetes (kubectl) clusters.
Platform detection happens once at startup.
"""
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger("tp-protect")

CLI_PATH = str(Path(__file__).parent.parent / "bin" / "tridentprotect-ctl")
TIMEOUT_SEC = 60

# Platform detection — active profile's `platform` field takes priority,
# falls back to auto-detect from binaries on disk.
_PLATFORM_AUTODETECTED = None  # cache for auto-detected value (binary check)


def _autodetect_platform() -> str:
    """Auto-detect platform from available binaries.

    Detection strategy:
      1. If 'oc' binary exists → 'ocp'
      2. Else if 'kubectl' binary exists → 'k8s'
      3. Else → 'unknown'
    """
    global _PLATFORM_AUTODETECTED
    if _PLATFORM_AUTODETECTED is not None:
        return _PLATFORM_AUTODETECTED
    if shutil.which("oc"):
        _PLATFORM_AUTODETECTED = "ocp"
    elif shutil.which("kubectl"):
        _PLATFORM_AUTODETECTED = "k8s"
    else:
        _PLATFORM_AUTODETECTED = "unknown"
    log.info(f"Auto-detected platform (from binaries): {_PLATFORM_AUTODETECTED}")
    return _PLATFORM_AUTODETECTED


def _get_active_profile() -> dict:
    """Read the currently active profile from config.yaml.

    Returns {} if no active profile or config unavailable.
    """
    try:
        from app.config import Config
        cfg = Config.instance()
        return cfg.active_profile or {}
    except Exception:
        return {}


def detect_platform() -> str:
    """Detect cluster platform: 'ocp' (OpenShift) or 'k8s' (vanilla Kubernetes).

    Resolution order:
      1. Active profile's `platform` field (manual override) — 'ocp' or 'k8s'
      2. Auto-detect from binaries (oc vs kubectl)
      3. 'unknown'
    """
    profile = _get_active_profile()
    explicit = profile.get("platform", "").strip().lower()
    if explicit in ("ocp", "k8s"):
        return explicit
    return _autodetect_platform()


def get_platform_cli() -> str:
    """Return the CLI binary to use for cluster operations: 'oc' or 'kubectl'."""
    platform = detect_platform()
    if platform == "ocp":
        return "oc"
    elif platform == "k8s":
        return "kubectl"
    return "oc"  # fallback; will fail if not installed


def _cli_path() -> str:
    """Resolve CLI path; allow override via env var."""
    p = os.environ.get("TRIDENTPROTECT_CTL", get_cli_path())
    return p


def _run(args, timeout=TIMEOUT_SEC, context: str | None = None) -> tuple[int, str, str]:
    """Run tridentprotect-ctl and return (rc, stdout, stderr).

    If context is provided, pass --context after the subcommand (get/create/delete).
    The tridentprotect-ctl Go binary accepts -context as a subcommand flag only;
    placing it before the subcommand fails with "flag provided but not defined: -context".
    """
    cmd = [get_cli_path()] + args[:1]
    if context:
        cmd += ["--context", context]
    cmd += args[1:]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _list(resource: str, namespace: str = "", context: str | None = None) -> list[dict]:
    """List resources cluster-wide or in a namespace. Returns parsed JSON items."""
    args = ["get", resource, "-o", "json"]
    if namespace:
        args += ["-n", namespace]
    else:
        args += ["-A"]
    rc, out, err = _run(args, context=context)
    if rc != 0:
        return []
    try:
        data = json.loads(out)
        return data.get("items", []) or []
    except json.JSONDecodeError:
        return []


def _get(resource: str, name: str, namespace: str, context: str | None = None) -> dict | None:
    """Get a single resource."""
    rc, out, err = _run(["get", resource, name, "-n", namespace, "-o", "json"], context=context)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# --- Time formatting helpers ---

def _fmt_time(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


def _age(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        diff = now - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ts


def _fmt_bytes(b: int | None) -> str:
    if b is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(b)
    for u in units:
        if size < 1024:
            return f"{size:.1f} {u}"
        size /= 1024
    return f"{size:.1f} PB"


def _state_class(state: str) -> str:
    s = (state or "").lower()
    if s in ("completed", "success"):
        return "success"
    if s in ("failed", "error"):
        return "error"
    if s in ("running", "inprogress", "pending", ""):
        return "pending"
    return "unknown"


# --- Serializers ---

def serialize_application(app: dict) -> dict:
    md = app.get("metadata", {})
    st = app.get("status", {})
    return {
        "name": md.get("name"),
        "namespace": md.get("namespace"),
        "created": _fmt_time(md.get("creationTimestamp")),
        "age": _age(md.get("creationTimestamp")),
        "uid": md.get("uid", ""),
        "protectionState": st.get("protectionState", "Unknown"),
        "protectionHealthState": st.get("protectionHealthState", "Unknown"),
        "details": st.get("protectionStateDetails", []),
        "resourceCount": st.get("resourceCount", 0),
        "storageBytes": st.get("storageCapacityBytes"),
        "storageHuman": _fmt_bytes(st.get("storageCapacityBytes")),
    }




def _resolve_schedule_source(
    name: str,
    labels: dict,
    schedule_map: dict | None,
) -> str:
    """Classify how a Backup/Snapshot was created.

    Returns:
      - schedule name (e.g. "schedule-amr-stateless") if created by a known Schedule
      - "reverse-bootstrap" if the resource is a reverse-direction bootstrap snapshot
      - "on-demand" if created manually
      - "" (empty) if created by a Schedule that no longer exists (UID not resolvable)
    """
    if name.startswith("reverse-bootstrap-"):
        return "reverse-bootstrap"
    schedule_uid = (labels or {}).get("created-by-trident-protect-schedule-uid", "")
    if schedule_uid:
        if schedule_map and schedule_uid in schedule_map:
            return schedule_map[schedule_uid]
        return ""
    return "on-demand"


def _derive_schedule_map(schedules: list[dict] | None = None) -> dict[str, str]:
    """Build a UID -> schedule-name map for use by _resolve_schedule_source.

    Fetches schedules via _list("schedule", context=None) if not provided.
    """
    if schedules is None:
        try:
            schedules = _list("schedule")
        except Exception:
            return {}
    out: dict[str, str] = {}
    for s in schedules or []:
        uid = (s.get("metadata", {}) or {}).get("uid", "")
        name = (s.get("metadata", {}) or {}).get("name", "")
        if uid and name:
            out[uid] = name
    return out
def serialize_backup(b: dict, context: str | None = None, schedule_map: dict | None = None) -> dict:
    md = b.get("metadata", {})
    sp = b.get("spec", {})
    st = b.get("status", {})
    return {
        "name": md.get("name"),
        "namespace": md.get("namespace"),
        "created": _fmt_time(md.get("creationTimestamp")),
        "age": _age(md.get("creationTimestamp")),
        "applicationRef": sp.get("applicationRef"),
        "appVaultRef": sp.get("appVaultRef"),
        "reclaimPolicy": sp.get("reclaimPolicy"),
        "state": st.get("state", "Unknown"),
        "stateClass": _state_class(st.get("state")),
        "appArchivePath": st.get("appArchivePath"),
        "error": st.get("error"),
        "completionTime": _fmt_time(st.get("completionTimestamp")),
        "scheduleSource": _resolve_schedule_source(
            md.get("name", ""), md.get("labels", {}) or {}, schedule_map
        ),
        # sourceStorageClasses removed (v1.4.8) — restore flow no longer uses SC
    }


def serialize_snapshot(s: dict, context: str | None = None, schedule_map: dict | None = None) -> dict:
    md = s.get("metadata", {})
    sp = s.get("spec", {})
    st = s.get("status", {})
    return {
        "name": md.get("name"),
        "namespace": md.get("namespace"),
        "created": _fmt_time(md.get("creationTimestamp")),
        "age": _age(md.get("creationTimestamp")),
        "applicationRef": sp.get("applicationRef"),
        "appVaultRef": sp.get("appVaultRef"),
        "reclaimPolicy": sp.get("reclaimPolicy"),
        "state": st.get("state", "Unknown"),
        "stateClass": _state_class(st.get("state")),
        "appArchivePath": st.get("appArchivePath"),
        "error": st.get("error"),
        "scheduleSource": _resolve_schedule_source(
            md.get("name", ""), md.get("labels", {}) or {}, schedule_map
        ),
        # sourceStorageClasses removed (v1.4.8) — restore flow no longer uses SC
    }


def serialize_schedule(s: dict) -> dict:
    md = s.get("metadata", {})
    sp = s.get("spec", {})
    st = s.get("status", {})
    lrp = st.get("lastRecoveryPoints", {}) or {}
    return {
        "name": md.get("name"),
        "namespace": md.get("namespace"),
        "created": _fmt_time(md.get("creationTimestamp")),
        "age": _age(md.get("creationTimestamp")),
        "applicationRef": sp.get("applicationRef"),
        "granularity": sp.get("granularity", "Unknown"),
        "enabled": sp.get("enabled", False),
        "backupRetention": sp.get("backupRetention"),
        "snapshotRetention": sp.get("snapshotRetention"),
        "lastScheduleTime": _fmt_time(st.get("lastScheduleTime")),
        "lastScheduleTimeAgo": _age(st.get("lastScheduleTime")),
        "lastBackup": (lrp.get("backup") or {}).get("name"),
        "lastSnapshot": (lrp.get("snapshot") or {}).get("name"),
    }


# --- List API ---



def get_source_storageclasses(kind: str, namespace: str, name: str, context: str | None = None) -> list[str]:
    """Discover StorageClass(es) used when the named Backup/Snapshot was created.

    Walks: CR.status → VolumeSnapshot.spec.source.persistentVolumeClaimName
        → live PVC.spec.storageClassName.

    Returns sorted unique list. Empty list if lookup fails.
    """
    if context is None or not namespace or not name or kind not in ("Backup", "Snapshot"):
        return []
    resource = "backup" if kind == "Backup" else "snapshot"
    cr = _get(resource, name, namespace, context=context)
    if not cr:
        return []
    sc_set: set[str] = set()
    # Walk status for VolumeSnapshot / PVC references
    for ref in (cr.get("status", {}) or {}).get("volumeSnapshots", []) or []:
        vs_name = ref.get("name") or ref.get("volumeSnapshotName")
        if not vs_name:
            continue
        vs_obj = _get("volumesnapshot", vs_name, namespace, context=context)
        if not vs_obj:
            continue
        pvc_name = ((vs_obj.get("spec") or {}).get("source") or {}).get("persistentVolumeClaimName")
        if not pvc_name:
            continue
        pvc_obj = _get("pvc", pvc_name, namespace, context=context)
        if pvc_obj:
            sc = (pvc_obj.get("spec", {}) or {}).get("storageClassName")
            if sc:
                sc_set.add(sc)
    # Also try volumesnapshotcontent (if direct bind)
    if not sc_set:
        for vsc in _list("volumesnapshotcontent", context=context):
            spec = vsc.get("spec", {}) or {}
            src = spec.get("source", {}) or {}
            if src.get("persistentVolumeClaimName") and not src.get("volumeHandle"):
                # skip direct-bind
                pass
    return sorted(sc_set)
def get_active_appvault() -> tuple[str, str]:
    """Return (appvault_name, appvault_namespace) from active profile or legacy config."""
    from .config import Config
    cfg = Config.instance()
    if cfg.active_profile:
        return (
            cfg.active_profile.get("appvault", "ontap-s3-appvault"),
            cfg.active_profile.get("appvault_namespace", "trident-protect"),
        )
    return (
        cfg.appvault.get("name", "ontap-s3-appvault"),
        cfg.appvault.get("namespace", "trident-protect"),
    )


def get_cli_path() -> str:
    """Return path to tridentprotect-ctl CLI."""
    from .config import Config
    cfg = Config.instance()
    p = cfg.cli.get("path", "bin/tridentprotect-ctl")
    if not os.path.isabs(p):
        # Resolve relative to project root
        return str(Path(__file__).parent.parent / p)
    return p


def _get_active_kubeconfig_context() -> str:
    """Return the kubeconfig context name from the active profile's kubeconfig_context field.

    This is used to pass --context to tridentprotect-ctl so it queries
    the correct cluster when switching between multiple k8s profiles.
    Returns empty string if no context is set (use kubeconfig current-context).
    """
    try:
        profile = _get_active_profile()
        return profile.get("kubeconfig_context", "")
    except Exception:
        return ""


def oc_run(args: list[str], timeout: int = 30, context: str | None = None) -> tuple[int, str, str]:
    """Run platform CLI (oc or kubectl) and return (rc, stdout, stderr).

    The CLI binary is determined per-call:
    - Active profile's `platform` field (manual override) — 'ocp' or 'k8s'
    - Auto-detect: 'oc' if available, else 'kubectl'

    For tridentprotect-ctl calls that need a specific cluster context,
    pass --context=<context_name> based on the active profile.

    Returns (127, "", "kubectl not found") if the binary doesn't exist.
    """
    cli = get_platform_cli()
    if not shutil.which(cli):
        return 127, "", f"{cli} not found in PATH"
    cmd = [cli]
    if context:
        cmd += ["--context", context]
    proc = subprocess.run(
        cmd + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def oc_login_with_token(api_url: str, token: str, insecure: bool = True) -> tuple[bool, str]:
    """Login to cluster using bearer token (updates kubeconfig).

    For OpenShift: uses `oc login --token=...`
    For vanilla Kubernetes: writes kubeconfig directly via `kubectl config`.
    """
    platform = detect_platform()
    if platform == "ocp":
        args = ["login", api_url, "--token", token]
        if insecure:
            args.append("--insecure-skip-tls-verify")
        rc, out, err = oc_run(args, timeout=30)
        if rc == 0:
            return True, (out or "Login successful").strip()
        return False, (err or out).strip()
    elif platform == "k8s":
        # Manually write kubeconfig via kubectl config subcommands.
        # Use a STABLE cluster/user/context name derived from the api_url host
        # (e.g. "webui@webui-clus1-10-10-10-41") so subsequent logins for the
        # same cluster overwrite the same entry instead of accumulating
        # stale "webui-cluster-<random>" entries. find_kubecontext_for_api()
        # can then resolve the context reliably.
        from urllib.parse import urlparse
        host = (urlparse(api_url).hostname or "cluster").replace(".", "-")
        cluster_name = f"webui-{host}"
        user_name = f"webui-user-{host}"
        context_name = f"webui@{cluster_name}"
        steps = [
            ["config", "set-cluster", cluster_name, f"--server={api_url}"]
            + (["--insecure-skip-tls-verify=true"] if insecure else []),
            ["config", "set-credentials", user_name, f"--token={token}"],
            ["config", "set-context", context_name, f"--cluster={cluster_name}", f"--user={user_name}"],
            ["config", "use-context", context_name],
        ]
        for step in steps:
            rc, out, err = oc_run(step, timeout=15)
            if rc != 0:
                return False, (err or out or f"kubectl {step[0]} failed").strip()
        return True, f"Logged in to {api_url} (context: {context_name})"
    else:
        return False, "No oc or kubectl binary available"


def oc_login_with_password(api_url: str, username: str, password: str, insecure: bool = True) -> tuple[bool, str]:
    """Login to cluster using username/password (updates kubeconfig).

    Only available on OpenShift. Vanilla Kubernetes clusters use
    OIDC tokens, not username/password, so this returns an error
    on k8s. Users should use a kubeconfig file or token auth.
    """
    platform = detect_platform()
    if platform != "ocp":
        return False, (
            "Username/password login is only supported on OpenShift. "
            "For vanilla Kubernetes, use a bearer token via 'Login with Token' or "
            "manually configure your kubeconfig."
        )
    args = ["login", api_url, "-u", username, "-p", password]
    if insecure:
        args.append("--insecure-skip-tls-verify")
    rc, out, err = oc_run(args, timeout=30)
    if rc == 0:
        return True, (out or "Login successful").strip()
    return False, (err or out).strip()


def oc_whoami() -> dict:
    """Return current user/server info from kubeconfig.

    For OpenShift: uses 'oc user' and 'oc server' for richer info.
    For vanilla Kubernetes: parses 'kubectl config view --minify' output.
    """
    info = {"user": "", "server": "", "context": "", "token_expires": ""}
    platform = detect_platform()

    if platform == "ocp":
        for field, key in [("user", "user"), ("server", "server")]:
            rc, out, err = oc_run([field, "-o", "json"], timeout=10)
            if rc == 0:
                try:
                    data = json.loads(out)
                    info[key] = data.get(key, "")
                except Exception:
                    info[key] = out.strip()
    elif platform == "k8s":
        # Use kubectl config view to get user, server, context
        rc, out, err = oc_run(["config", "view", "--minify", "-o", "json"], timeout=10)
        if rc == 0:
            try:
                data = json.loads(out)
                # Extract user
                users = data.get("users", [])
                if users:
                    info["user"] = users[0].get("name", "")
                # Extract server
                clusters = data.get("clusters", [])
                if clusters:
                    info["server"] = clusters[0].get("cluster", {}).get("server", "")
            except Exception:
                pass

    rc, out, _ = oc_run(["config", "current-context"], timeout=10)
    if rc == 0:
        info["context"] = out.strip()
    return info


def oc_get_token() -> str:
    """Get the current bearer token.

    For OpenShift: uses 'oc whoami -t'.
    For vanilla Kubernetes: parses the token from kubeconfig
    (only works if the user authenticated with a bearer token;
    x509 cert-based auth returns empty).
    """
    platform = detect_platform()
    if platform == "ocp":
        rc, out, _ = oc_run(["whoami", "-t"], timeout=10)
        if rc == 0:
            return out.strip()
        return ""
    elif platform == "k8s":
        # Try to extract token from kubeconfig
        rc, out, _ = oc_run(["config", "view", "--minify", "-o", "json"], timeout=10)
        if rc == 0:
            try:
                data = json.loads(out)
                users = data.get("users", [])
                if users:
                    token = users[0].get("user", {}).get("token", "")
                    if token:
                        return token
            except Exception:
                pass
        return ""
    return ""


def get_cluster_info() -> dict:
    """Get current cluster info (works for both OCP and k8s).

    Returns: {name, platform, api_url, version}

    For OpenShift:
      - name: from 'oc get infrastructure cluster'
      - platform: OCP platform (BareMetal, AWS, etc.)
      - version: openshiftVersion from 'oc version'
    For vanilla Kubernetes:
      - name: from 'kubectl config current-context' context name
      - platform: 'kubernetes'
      - version: gitVersion from 'kubectl version'
    """
    info = {"name": "", "platform": "", "api_url": "", "version": ""}
    platform = detect_platform()

    if platform == "ocp":
        try:
            rc, out, _ = oc_run(
                ["get", "infrastructure", "cluster", "-o", "json"],
                timeout=10,
            )
            if rc == 0:
                data = json.loads(out)
                st = data.get("status", {})
                info["name"] = st.get("infrastructureName", "")
                info["platform"] = st.get("platform", "")
                info["api_url"] = st.get("apiServerURL", "")
        except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.warning(f"get_cluster_info (OCP) failed: {e}")

        rc, out, _ = oc_run(["version", "-o", "json"], timeout=10)
        if rc == 0:
            try:
                v = json.loads(out)
                info["version"] = v.get("openshiftVersion", "")
            except Exception:
                pass
    elif platform == "k8s":
        info["platform"] = "kubernetes"
        # Get cluster name from current-context
        rc, out, _ = oc_run(["config", "current-context"], timeout=10)
        if rc == 0:
            info["name"] = out.strip()
        # Get API server URL
        rc, out, _ = oc_run(["config", "view", "--minify", "-o", "json"], timeout=10)
        if rc == 0:
            try:
                data = json.loads(out)
                clusters = data.get("clusters", [])
                if clusters:
                    info["api_url"] = clusters[0].get("cluster", {}).get("server", "")
            except Exception:
                pass
        # Get version
        rc, out, _ = oc_run(["version", "-o", "json"], timeout=10)
        if rc == 0:
            try:
                v = json.loads(out)
                info["version"] = v.get("serverVersion", {}).get("gitVersion", "")
            except Exception:
                pass

    return info


# Backward-compat alias (legacy name used in main.py)
get_ocp_cluster_info = get_cluster_info


def list_namespaces() -> list[dict]:
    """List all Active namespaces in the cluster via oc/kubectl CLI.

    tridentprotect-ctl does not support core v1 namespace resource,
    so we shell out to the platform CLI.
    """
    try:
        proc = subprocess.run(
            [get_platform_cli(), "get", "ns", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return []
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.error(f"list_namespaces failed: {e}")
        return []

    items = []
    for ns in data.get("items", []):
        phase = ns.get("status", {}).get("phase", "Unknown")
        if phase != "Active":
            continue
        items.append({
            "name": ns["metadata"]["name"],
            "status": phase,
        })
    items.sort(key=lambda x: x["name"])
    return items


def list_applications(namespace: str | None = None, context: str | None = None) -> list[dict]:
    items = [serialize_application(a) for a in _list("application", namespace or "", context=context)]
    if namespace:
        items = [i for i in items if i.get("namespace") == namespace]
    return items


def list_backups(namespace: str | None = None, context: str | None = None) -> list[dict]:
    items = _list("backup", namespace or "", context=context)
    sched_map = _derive_schedule_map() if items else {}
    return [serialize_backup(b, context=context, schedule_map=sched_map) for b in items]


def list_snapshots(namespace: str | None = None, context: str | None = None) -> list[dict]:
    items = _list("snapshot", namespace or "", context=context)
    sched_map = _derive_schedule_map() if items else {}
    return [serialize_snapshot(s, context=context, schedule_map=sched_map) for s in items]


def list_schedules(context: str | None = None) -> list[dict]:
    return [serialize_schedule(s) for s in _list("schedule", context=context)]


# --- AppVault management ---

def _get_appvault_error(av: dict) -> str:
    """Extract first error message from AppVault status.conditions."""
    conds = av.get("status", {}).get("conditions", []) or []
    for c in conds:
        if c.get("status", "").lower() == "false" or (c.get("type", "").startswith("error")):
            return c.get("message", c.get("reason", "Unknown error"))
    return ""


def serialize_appvault(av: dict) -> dict:
    md = av.get("metadata", {})
    sp = av.get("spec", {})
    st = av.get("status", {})
    pc = sp.get("providerConfig", {}).get("s3", {})
    return {
        "name": md.get("name"),
        "namespace": md.get("namespace"),
        "created": _fmt_time(md.get("creationTimestamp")),
        "age": _age(md.get("creationTimestamp")),
        "provider": sp.get("providerType", "Unknown"),
        "bucket": pc.get("bucketName", "—"),
        "endpoint": pc.get("endpoint", "—"),
        "state": st.get("state", "Unknown"),
        "stateClass": _state_class(st.get("state")),
        "error": _get_appvault_error(av),
    }


def get_appvault(namespace: str, name: str) -> dict | None:
    return _get("appvault", name, namespace)


def serialize_appvault_to_yaml(av: dict) -> str:
    """Convert AppVault CR dict to valid YAML string."""
    clean = {**av}
    if "apiVersion" not in clean:
        clean["apiVersion"] = "protect.trident.netapp.io/v1"
    if "kind" not in clean:
        clean["kind"] = "AppVault"
    ordered = {
        "apiVersion": clean.pop("apiVersion"),
        "kind": clean.pop("kind"),
        "metadata": {},
        "spec": {},
        "status": {},
    }
    if isinstance(clean.get("metadata"), dict):
        ordered["metadata"] = {
            k: v for k, v in clean["metadata"].items()
            if k not in ("managedFields",)
        }
        del clean["metadata"]
    ordered["spec"] = clean.pop("spec", {})
    ordered["status"] = clean.pop("status", {})
    for k, v in clean.items():
        ordered[k] = v
    return _yaml.dump(
        ordered,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        allow_unicode=True,
        width=4096,
    )


def list_appvaults(namespace: str = "", context: str | None = None) -> list[dict]:
    items = _list("appvault", namespace, context=context) if namespace else _list("appvault", context=context)
    items = [av for av in items if av.get("metadata", {}).get("namespace", "").startswith("trident")]
    return [serialize_appvault(av) for av in items]


# --- Application management ---

import yaml as _yaml

def validate_application_yaml(yaml_str: str) -> tuple[bool, str]:
    """Parse + validate Application YAML without applying."""
    try:
        doc = _yaml.safe_load(yaml_str)
    except _yaml.YAMLError as e:
        return False, f"YAML syntax error: {e}"
    if not isinstance(doc, dict):
        return False, "YAML must be a single document (mapping)"

    kind = doc.get("kind")
    if kind != "Application":
        return False, f"kind must be 'Application' (got '{kind}')"

    api_version = doc.get("apiVersion", "")
    if not api_version.startswith("protect.trident.netapp.io"):
        return False, f"apiVersion must start with 'protect.trident.netapp.io' (got '{api_version}')"

    metadata = doc.get("metadata", {}) or {}
    name = metadata.get("name")
    ns = metadata.get("namespace")
    if not name:
        return False, "metadata.name required"
    if not ns:
        return False, "metadata.namespace required"

    spec = doc.get("spec", {}) or {}
    if not spec.get("includedNamespaces"):
        return False, "spec.includedNamespaces required (list of namespace selectors)"

    return True, f"Valid — will create Application '{name}' in namespace '{ns}'"


def apply_application_yaml(yaml_str: str) -> tuple[bool, str]:
    """Apply Application YAML via `oc apply -f -` (stdin)."""
    proc = subprocess.run(
        [get_platform_cli(), "apply", "-f", "-"],
        input=yaml_str,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        return True, (proc.stdout or "Application applied").strip()
    return False, (proc.stderr or proc.stdout).strip()


def delete_application(namespace: str, name: str) -> tuple[bool, str]:
    rc, out, err = _run(["delete", "application", name, "-n", namespace])
    if rc == 0:
        return True, out.strip() or f"Deleted {namespace}/{name}"
    return False, (err or out).strip()


def create_appvault_secret(namespace: str, name: str, access_key: str, secret_key: str) -> tuple[bool, str]:
    """Create a Kubernetes Secret for AppVault credentials."""
    proc = subprocess.run(
        [
            get_platform_cli(), "create", "secret", "generic", name,
            f"--namespace={namespace}",
            f"--from-literal=accessKeyID={access_key}",
            f"--from-literal=secretAccessKey={secret_key}",
        ],
        capture_output=True, text=True, timeout=15,
    )
    if proc.returncode == 0:
        return True, f"Secret {namespace}/{name} created"
    err = (proc.stderr or proc.stdout).strip()
    if "already exists" in err.lower():
        return True, f"Secret {namespace}/{name} already exists"
    return False, err


def create_appvault(
    name: str,
    namespace: str,
    provider: str,
    endpoint: str,
    bucket: str,
    skip_cert: bool,
    secret_name: str,
) -> tuple[bool, str]:
    """Create AppVault via CLI."""
    args = [
        "create", "appvault", provider, name,
        "-n", namespace,
        "--bucket", bucket,
        "--endpoint", endpoint,
        "--skip-cert-validation" if skip_cert else "",
        "-s", secret_name,
    ]
    args = [a for a in args if a]  # remove empty strings
    rc, out, err = _run(args)
    if rc == 0:
        return True, (out or f"AppVault {namespace}/{name} created").strip()
    return False, (err or out).strip()


def delete_appvault(namespace: str, name: str) -> tuple[bool, str]:
    rc, out, err = _run(["delete", "appvault", name, "-n", namespace])
    if rc == 0:
        return True, (out or f"Deleted AppVault {namespace}/{name}").strip()
    return False, (err or out).strip()


def list_dashboard() -> dict:
    apps = list_applications()
    backups = list_backups()
    snapshots = list_snapshots()
    schedules = list_schedules()

    healthy = sum(1 for a in apps if a["protectionState"] == "Protected")
    partial = sum(1 for a in apps if a["protectionState"] == "Partial")
    unprotected = sum(1 for a in apps if a["protectionState"] in ("Not Protected", "None"))
    completed_backups = sum(1 for b in backups if b["stateClass"] == "success")
    failed_backups = sum(1 for b in backups if b["stateClass"] == "error")
    completed_snapshots = sum(1 for s in snapshots if s["stateClass"] == "success")
    enabled_schedules = sum(1 for s in schedules if s["enabled"])

    return {
        "totals": {
            "applications": len(apps),
            "backups": len(backups),
            "snapshots": len(snapshots),
            "schedules": len(schedules),
        },
        "health": {
            "applicationsProtected": healthy,
            "applicationsPartial": partial,
            "applicationsUnprotected": unprotected,
            "backupsCompleted": completed_backups,
            "backupsFailed": failed_backups,
            "snapshotsCompleted": completed_snapshots,
            "schedulesEnabled": enabled_schedules,
        },
    }


def get_backup(namespace: str, name: str) -> dict | None:
    return _get("backup", name, namespace)


def get_snapshot(namespace: str, name: str) -> dict | None:
    return _get("snapshot", name, namespace)


def get_application(namespace: str, name: str) -> dict | None:
    return _get("application", name, namespace)


def get_backuprestore(namespace: str, name: str) -> dict | None:
    return _get("backuprestore", name, namespace)


def get_snapshotrestore(namespace: str, name: str) -> dict | None:
    return _get("snapshotrestore", name, namespace)


def serialize_app_to_yaml(app: dict) -> str:
    """Convert Application CR dict to valid YAML string.

    Removes noisy metadata (managedFields, annotations) and dumps
    with PyYAML so the output can be copied directly into `oc apply -f`.
    Adds apiVersion/kind if missing (CLI output omits them).
    """
    clean = {**app}
    # Ensure apiVersion and kind are present (CLI output may omit them)
    if "apiVersion" not in clean:
        clean["apiVersion"] = "protect.trident.netapp.io/v1"
    if "kind" not in clean:
        clean["kind"] = "Application"
    # Move apiVersion/kind to top for canonical order
    ordered = {
        "apiVersion": clean.pop("apiVersion"),
        "kind": clean.pop("kind"),
        "metadata": {},
        "spec": {},
        "status": {},
    }
    if isinstance(clean.get("metadata"), dict):
        ordered["metadata"] = {
            k: v for k, v in clean["metadata"].items()
            if k not in ("managedFields", "annotations")
        }
        del clean["metadata"]
    ordered["spec"] = clean.pop("spec", {})
    ordered["status"] = clean.pop("status", {})
    # Re-insert any extra top-level keys (shouldn't be any, but be safe)
    for k, v in clean.items():
        ordered[k] = v
    return _yaml.dump(
        ordered,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        allow_unicode=True,
        width=4096,
    )


# --- Backup management ---

def serialize_backup_to_yaml(b: dict) -> str:
    """Convert Backup CR dict to valid YAML string."""
    clean = {**b}
    if "apiVersion" not in clean:
        clean["apiVersion"] = "protect.trident.netapp.io/v1"
    if "kind" not in clean:
        clean["kind"] = "Backup"
    ordered = {
        "apiVersion": clean.pop("apiVersion"),
        "kind": clean.pop("kind"),
        "metadata": {},
        "spec": {},
        "status": {},
    }
    if isinstance(clean.get("metadata"), dict):
        ordered["metadata"] = {
            k: v for k, v in clean["metadata"].items()
            if k not in ("managedFields", "annotations")
        }
        del clean["metadata"]
    ordered["spec"] = clean.pop("spec", {})
    ordered["status"] = clean.pop("status", {})
    for k, v in clean.items():
        ordered[k] = v
    return _yaml.dump(
        ordered,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        allow_unicode=True,
        width=4096,
    )


def validate_backup_yaml(yaml_str: str) -> tuple[bool, str, str]:
    """Parse + validate Backup YAML without applying.

    Returns (ok, message, modified_yaml_str) where modified_yaml_str contains
    any auto-generated values (e.g. metadata.name if not provided).
    """
    try:
        doc = _yaml.safe_load(yaml_str)
    except _yaml.YAMLError as e:
        return False, f"YAML syntax error: {e}", yaml_str
    if not isinstance(doc, dict):
        return False, "YAML must be a single document (mapping)", yaml_str

    kind = doc.get("kind")
    if kind != "Backup":
        return False, f"kind must be 'Backup' (got '{kind}')", yaml_str

    api_version = doc.get("apiVersion", "")
    if not api_version.startswith("protect.trident.netapp.io"):
        return False, f"apiVersion must start with 'protect.trident.netapp.io' (got '{api_version}')", yaml_str

    metadata = doc.get("metadata", {}) or {}
    name = metadata.get("name")
    ns = metadata.get("namespace")
    if not name:
        # Auto-generate name if not provided
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"manual-backup-{ts}"
        if "metadata" not in doc:
            doc["metadata"] = {}
        doc["metadata"]["name"] = name
        metadata = doc["metadata"]
        yaml_str = _yaml.dump(doc, default_flow_style=False, sort_keys=False, width=4096)
    if not ns:
        return False, "metadata.namespace required", yaml_str

    spec = doc.get("spec", {}) or {}
    if not spec.get("applicationRef"):
        return False, "spec.applicationRef required", yaml_str

    return True, f"Valid — will create Backup '{name}' in namespace '{ns}'", yaml_str


def apply_backup_yaml(yaml_str: str) -> tuple[bool, str]:
    """Apply Backup YAML via `oc apply -f -` (stdin)."""
    proc = subprocess.run(
        [get_platform_cli(), "apply", "-f", "-"],
        input=yaml_str,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        return True, (proc.stdout or "Backup applied").strip()
    return False, (proc.stderr or proc.stdout).strip()


# --- Snapshot management ---

def serialize_snapshot_to_yaml(s: dict) -> str:
    """Convert Snapshot CR dict to valid YAML string."""
    clean = {**s}
    if "apiVersion" not in clean:
        clean["apiVersion"] = "protect.trident.netapp.io/v1"
    if "kind" not in clean:
        clean["kind"] = "Snapshot"
    ordered = {
        "apiVersion": clean.pop("apiVersion"),
        "kind": clean.pop("kind"),
        "metadata": {},
        "spec": {},
        "status": {},
    }
    if isinstance(clean.get("metadata"), dict):
        ordered["metadata"] = {
            k: v for k, v in clean["metadata"].items()
            if k not in ("managedFields",)
        }
        del clean["metadata"]
    ordered["spec"] = clean.pop("spec", {})
    ordered["status"] = clean.pop("status", {})
    for k, v in clean.items():
        ordered[k] = v
    return _yaml.dump(
        ordered,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        allow_unicode=True,
        width=4096,
    )


def serialize_restore_to_yaml(r: dict, kind: str) -> str:
    """Convert BackupRestore/SnapshotRestore CR dict to valid YAML string."""
    clean = {**r}
    if "apiVersion" not in clean:
        clean["apiVersion"] = "protect.trident.netapp.io/v1"
    if "kind" not in clean:
        clean["kind"] = kind
    ordered = {
        "apiVersion": clean.pop("apiVersion"),
        "kind": clean.pop("kind"),
        "metadata": {},
        "spec": {},
        "status": {},
    }
    if isinstance(clean.get("metadata"), dict):
        ordered["metadata"] = {
            k: v for k, v in clean["metadata"].items()
            if k not in ("managedFields",)
        }
        del clean["metadata"]
    ordered["spec"] = clean.pop("spec", {})
    ordered["status"] = clean.pop("status", {})
    for k, v in clean.items():
        ordered[k] = v
    return _yaml.dump(
        ordered,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        allow_unicode=True,
        width=4096,
    )


def validate_snapshot_yaml(yaml_str: str) -> tuple[bool, str, str]:
    """Parse + validate Snapshot YAML without applying.

    Returns (ok, message, modified_yaml_str) where modified_yaml_str contains
    any auto-generated values (e.g. metadata.name if not provided).
    """
    try:
        doc = _yaml.safe_load(yaml_str)
    except _yaml.YAMLError as e:
        return False, f"YAML syntax error: {e}", yaml_str
    if not isinstance(doc, dict):
        return False, "YAML must be a single document (mapping)", yaml_str

    kind = doc.get("kind")
    if kind != "Snapshot":
        return False, f"kind must be 'Snapshot' (got '{kind}')", yaml_str

    api_version = doc.get("apiVersion", "")
    if not api_version.startswith("protect.trident.netapp.io"):
        return False, f"apiVersion must start with 'protect.trident.netapp.io' (got '{api_version}')", yaml_str

    metadata = doc.get("metadata", {}) or {}
    name = metadata.get("name")
    ns = metadata.get("namespace")
    if not name:
        # Auto-generate name if not provided
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"manual-snap-{ts}"
        if "metadata" not in doc:
            doc["metadata"] = {}
        doc["metadata"]["name"] = name
        metadata = doc["metadata"]
        yaml_str = _yaml.dump(doc, default_flow_style=False, sort_keys=False, width=4096)
    if not ns:
        return False, "metadata.namespace required", yaml_str

    spec = doc.get("spec", {}) or {}
    if not spec.get("applicationRef"):
        return False, "spec.applicationRef required", yaml_str

    return True, f"Valid — will create Snapshot '{name}' in namespace '{ns}'", yaml_str


def apply_snapshot_yaml(yaml_str: str) -> tuple[bool, str]:
    """Apply Snapshot YAML via `oc apply -f -` (stdin)."""
    proc = subprocess.run(
        [get_platform_cli(), "apply", "-f", "-"],
        input=yaml_str,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        return True, (proc.stdout or "Snapshot applied").strip()
    return False, (proc.stderr or proc.stdout).strip()


# --- Schedule management ---

def get_schedule(namespace: str, name: str) -> dict | None:
    return _get("schedule", name, namespace)


def serialize_schedule_to_yaml(sch: dict) -> str:
    """Convert Schedule CR dict to valid YAML string."""
    clean = {**sch}
    if "apiVersion" not in clean:
        clean["apiVersion"] = "protect.trident.netapp.io/v1"
    if "kind" not in clean:
        clean["kind"] = "Schedule"
    ordered = {
        "apiVersion": clean.pop("apiVersion"),
        "kind": clean.pop("kind"),
        "metadata": {},
        "spec": {},
        "status": {},
    }
    if isinstance(clean.get("metadata"), dict):
        ordered["metadata"] = {
            k: v for k, v in clean["metadata"].items()
            if k not in ("managedFields", "annotations")
        }
        del clean["metadata"]
    ordered["spec"] = clean.pop("spec", {})
    ordered["status"] = clean.pop("status", {})
    for k, v in clean.items():
        ordered[k] = v
    return _yaml.dump(
        ordered,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        allow_unicode=True,
        width=4096,
    )


def validate_schedule_yaml(yaml_str: str) -> tuple[bool, str]:
    """Parse + validate Schedule YAML without applying."""
    try:
        doc = _yaml.safe_load(yaml_str)
    except _yaml.YAMLError as e:
        return False, f"YAML syntax error: {e}"
    if not isinstance(doc, dict):
        return False, "YAML must be a single document (mapping)"

    kind = doc.get("kind")
    if kind != "Schedule":
        return False, f"kind must be 'Schedule' (got '{kind}')"

    api_version = doc.get("apiVersion", "")
    if not api_version.startswith("protect.trident.netapp.io"):
        return False, f"apiVersion must start with 'protect.trident.netapp.io' (got '{api_version}')"

    metadata = doc.get("metadata", {}) or {}
    name = metadata.get("name")
    ns = metadata.get("namespace")
    if not name:
        return False, "metadata.name required"
    if not ns:
        return False, "metadata.namespace required"

    spec = doc.get("spec", {}) or {}
    if not spec.get("applicationRef"):
        return False, "spec.applicationRef required"

    return True, f"Valid — will create Schedule '{name}' in namespace '{ns}'"


def apply_schedule_yaml(yaml_str: str) -> tuple[bool, str]:
    """Apply Schedule YAML via `oc apply -f -` (stdin)."""
    proc = subprocess.run(
        [get_platform_cli(), "apply", "-f", "-"],
        input=yaml_str,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        return True, (proc.stdout or "Schedule applied").strip()
    return False, (proc.stderr or proc.stdout).strip()


def delete_schedule(namespace: str, name: str) -> tuple[bool, str]:
    rc, out, err = _run(["delete", "schedule", name, "-n", namespace])
    if rc == 0:
        return True, out.strip() or f"Deleted {namespace}/{name}"
    return False, (err or out).strip()


# --- Trigger actions ---

def trigger_backup(namespace: str, application_ref: str, appvault: str, backup_name: str) -> tuple[bool, str]:
    """Trigger on-demand Backup via CLI."""
    rc, out, err = _run([
        "create", "backup", backup_name,
        "--app", application_ref,
        "--appvault", appvault,
        "-n", namespace,
        "--data-mover", "Kopia",
        "--reclaim-policy", "Retain",
    ])
    if rc == 0:
        return True, out.strip() or f"Backup {backup_name} triggered in {namespace}"
    return False, (err or out).strip()


def trigger_backup_restore(
    destination_namespace: str,
    backup_name: str,
    source_namespace: str,
    appvault: str,
    app_archive_path: str,
    destination_app_name: str | None = None,
) -> tuple[bool, str]:
    """Trigger BackupRestore via CLI.

    Uses --backup <ns>/<name> to let the CLI auto-resolve the AppVault +
    archive path. Explicit appvault/path are accepted for API symmetry but
    not needed when --backup is provided (mutually exclusive flags).
    """
    args = [
        "create", "backuprestore", f"restore-{backup_name}",
        "--backup", f"{source_namespace}/{backup_name}",
        "--namespace-mapping", f"{source_namespace}:{destination_namespace}",
        "-n", destination_namespace,
    ]
    if destination_app_name:
        args += ["--destination-app-name", destination_app_name]

    rc, out, err = _run(args)
    if rc == 0:
        return True, out.strip() or f"BackupRestore triggered to {destination_namespace}"
    return False, (err or out).strip()


def trigger_snapshot_restore(
    destination_namespace: str,
    snapshot_name: str,
    source_namespace: str,
    appvault: str,
    app_archive_path: str,
    destination_app_name: str | None = None,
) -> tuple[bool, str]:
    """Trigger SnapshotRestore via CLI.

    Uses --snapshot <ns>/<name> to let the CLI auto-resolve the AppVault +
    archive path. Explicit appvault/path are accepted for API symmetry but
    not needed when --snapshot is provided (mutually exclusive flags).
    """
    args = [
        "create", "snapshotrestore", f"snapshot-restore-{snapshot_name}",
        "--snapshot", f"{source_namespace}/{snapshot_name}",
        "--namespace-mapping", f"{source_namespace}:{destination_namespace}",
        "-n", destination_namespace,
    ]
    if destination_app_name:
        args += ["--destination-app-name", destination_app_name]

    rc, out, err = _run(args)
    if rc == 0:
        return True, out.strip() or f"SnapshotRestore triggered to {destination_namespace}"
    return False, (err or out).strip()




def trigger_backup_inplace(
    backup_name: str,
    source_namespace: str,
) -> tuple[bool, str]:
    """Trigger an in-place BackupRestore (BackupInplaceRestore CR).

    No destination namespace, no namespaceMapping, no storageclassMapping.
    The backup is restored back to the source namespace. Uses --backup to
    let the CLI auto-resolve the AppVault + archive path, and -n to pin
    the CR's metadata.namespace to the source namespace (otherwise the
    CLI falls back to the kubectl context default, e.g. 'default').
    """
    args = [
        "create", "backupinplacerestore", f"restore-inplace-{backup_name}",
        "--backup", f"{source_namespace}/{backup_name}",
        "-n", source_namespace,
    ]
    rc, out, err = _run(args, timeout=60)
    if rc == 0:
        return True, (out or f"BackupInplaceRestore triggered for {source_namespace}/{backup_name}").strip()
    return False, (err or out).strip()


def trigger_snapshot_inplace(
    snapshot_name: str,
    source_namespace: str,
) -> tuple[bool, str]:
    """Trigger an in-place SnapshotRestore (SnapshotInplaceRestore CR).

    No destination namespace, no namespaceMapping, no storageclassMapping.
    The snapshot is restored back to the source namespace. Uses --snapshot
    to let the CLI auto-resolve the AppVault + archive path, and -n to pin
    the CR's metadata.namespace to the source namespace (otherwise the
    CLI falls back to the kubectl context default, e.g. 'default').
    """
    args = [
        "create", "snapshotinplacerestore", f"snap-inplace-{snapshot_name}",
        "--snapshot", f"{source_namespace}/{snapshot_name}",
        "-n", source_namespace,
    ]
    rc, out, err = _run(args, timeout=60)
    if rc == 0:
        return True, (out or f"SnapshotInplaceRestore triggered for {source_namespace}/{snapshot_name}").strip()
    return False, (err or out).strip()


# Required-field map for each restore kind (used by validate_restore_yaml).
_RESTORE_REQUIRED_FIELDS: dict[str, list[str]] = {
    "BackupRestore":          ["appArchivePath", "appVaultRef", "namespaceMapping"],
    "BackupInplaceRestore":   ["appArchivePath", "appVaultRef"],
    "SnapshotRestore":        ["appArchivePath", "appVaultRef", "namespaceMapping"],
    "SnapshotInplaceRestore": ["appArchivePath", "appVaultRef"],
}


def validate_restore_yaml(yaml_str: str) -> tuple[bool, str]:
    """Parse + validate a user-edited restore YAML.

    Accepts any of the 4 supported restore CR kinds. Rejects unknown
    kinds, missing apiVersion prefix, missing required spec fields.
    """
    try:
        doc = _yaml.safe_load(yaml_str)
    except _yaml.YAMLError as e:
        return False, f"YAML syntax error: {e}"
    if not isinstance(doc, dict):
        return False, "YAML must be a single document (mapping)"

    kind = doc.get("kind")
    if kind not in _RESTORE_REQUIRED_FIELDS:
        return False, (
            f"kind must be one of {sorted(_RESTORE_REQUIRED_FIELDS.keys())} "
            f"(got '{kind}')"
        )

    api_version = doc.get("apiVersion", "")
    if not api_version.startswith("protect.trident.netapp.io"):
        return False, f"apiVersion must start with 'protect.trident.netapp.io' (got '{api_version}')"

    metadata = doc.get("metadata", {}) or {}
    name = metadata.get("name")
    ns = metadata.get("namespace")
    if not name:
        return False, "metadata.name required"
    if not ns:
        return False, "metadata.namespace required"

    spec = doc.get("spec", {}) or {}
    for field in _RESTORE_REQUIRED_FIELDS[kind]:
        if not spec.get(field):
            return False, f"spec.{field} required for {kind}"

    return True, f"Valid — will create {kind} '{name}' in namespace '{ns}'"


def apply_restore_yaml(yaml_str: str) -> tuple[bool, str]:
    """Apply a user-edited restore YAML via `kubectl apply -f -` (stdin).

    Used by the YAML tab in restore.html. Only invoked after a successful
    validate_restore_yaml() and admin role check. Restricted to admin.
    """
    cmd = [get_platform_cli(), "apply", "-f", "-"]
    if detect_platform() == "k8s":
        # For k8s, the active context will be used (set by /api/settings/login or /switch).
        pass
    proc = subprocess.run(cmd, input=yaml_str, capture_output=True, text=True, timeout=30)
    if proc.returncode == 0:
        return True, (proc.stdout or "Restore applied").strip()
    return False, (proc.stderr or proc.stdout).strip()
def delete_backup(namespace: str, name: str) -> tuple[bool, str]:
    rc, out, err = _run(["delete", "backup", name, "-n", namespace])
    if rc == 0:
        return True, out.strip() or f"Deleted {namespace}/{name}"
    return False, (err or out).strip()


def delete_snapshot(namespace: str, name: str) -> tuple[bool, str]:
    rc, out, err = _run(["delete", "snapshot", name, "-n", namespace])
    if rc == 0:
        return True, out.strip() or f"Deleted {namespace}/{name}"
    return False, (err or out).strip()


def get_restore_source_namespaces() -> dict:
    """Return namespaces that have completed backups or snapshots, plus the items."""
    backups = [b for b in _list("backup") if b.get("status", {}).get("state", "").lower() in ("completed", "success")]
    snapshots = [s for s in _list("snapshot") if s.get("status", {}).get("state", "").lower() in ("completed", "success")]
    ns_set = set()
    for b in backups:
        ns_set.add(b.get("metadata", {}).get("namespace", ""))
    for s in snapshots:
        ns_set.add(s.get("metadata", {}).get("namespace", ""))
    return {
        "namespaces": sorted(ns_set),
        "backups": [serialize_backup(b) for b in backups],
        "snapshots": [serialize_snapshot(s) for s in snapshots],
    }


# All 4 supported restore CR kinds (per Trident Protect v26.06.0+).
_RESTORE_KINDS: list[tuple[str, str]] = [
    ("BackupRestore",          "backuprestore"),
    ("BackupInplaceRestore",   "backupinplacerestore"),
    ("SnapshotRestore",        "snapshotrestore"),
    ("SnapshotInplaceRestore", "snapshotinplacerestore"),
]


def list_restores() -> list[dict]:
    """List all 4 restore CR kinds (cross-NS + inplace)."""
    results = []
    for kind, resource in _RESTORE_KINDS:
        for r in _list(resource):
            results.append(serialize_restore(r, kind))
    return results


def serialize_restore(r: dict, restore_type: str) -> dict:
    md = r.get("metadata", {})
    sp = r.get("spec", {})
    st = r.get("status", {})
    conds = st.get("conditions", []) or []
    error_msg = ""
    for c in conds:
        if c.get("status", "").lower() == "false" or "error" in (c.get("type", "")).lower():
            error_msg = c.get("message", c.get("reason", ""))
            break
    if not error_msg:
        error_msg = st.get("error", "")
    return {
        "name": md.get("name"),
        "namespace": md.get("namespace"),
        "type": restore_type,
        "created": _fmt_time(md.get("creationTimestamp")),
        "age": _age(md.get("creationTimestamp")),
        "state": st.get("state", "Unknown"),
        "stateClass": _state_class(st.get("state")),
        "sourceRef": sp.get("backupRef") or sp.get("snapshotRef") or "",
        "destinationNamespace": sp.get("destinationNamespace", ""),
        "error": error_msg,
        "conditions": conds,
    }


def delete_restore(restore_type: str, namespace: str, name: str) -> tuple[bool, str]:
    """Delete a restore CR by its kind (any of the 4 supported kinds)."""
    # reverse-lookup the resource name from the kind
    resource = None
    for kind, res in _RESTORE_KINDS:
        if kind == restore_type:
            resource = res
            break
    if not resource:
        # Fallback: try to infer from the type name
        if "Backup" in restore_type:
            resource = "backupinplacerestore" if "Inplace" in restore_type else "backuprestore"
        else:
            resource = "snapshotinplacerestore" if "Inplace" in restore_type else "snapshotrestore"
    rc, out, err = _run(["delete", resource, name, "-n", namespace])
    if rc == 0:
        return True, out.strip() or f"Deleted {namespace}/{name}"
    return False, (err or out).strip()


def get_storageclasses(context: str | None = None) -> list[str]:
    """Return list of StorageClass names via oc/kubectl."""
    cmd = [get_platform_cli()]
    if context:
        cmd += ["--context", context]
    cmd += ["get", "sc", "-o", "jsonpath={.items[*].metadata.name}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if proc.returncode == 0:
        return sorted(proc.stdout.strip().split())
    return []


def get_namespaces(context: str | None = None) -> list[str]:
    """Return list of non-system namespaces via oc/kubectl."""
    cmd = [get_platform_cli()]
    if context:
        cmd += ["--context", context]
    cmd += ["get", "ns", "-o",
            "jsonpath={.items[?(@.status.phase==\"Active\")].metadata.name}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if proc.returncode == 0:
        all_ns = proc.stdout.strip().split()
        return sorted([n for n in all_ns if not n.startswith("openshift-")
                       and not n.startswith("kube-") and n != "trident-protect"])
    return []


def test_connection() -> tuple[bool, str]:
    """Check CLI works."""
    cli_path = _cli_path()
    if not os.path.exists(cli_path):
        return False, f"tridentprotect-ctl not found at {cli_path}. Run install.sh or download manually."
    try:
        rc, out, err = _run(["version"], timeout=10)
    except FileNotFoundError as e:
        return False, f"CLI not executable: {e}"
    if rc == 0:
        return True, out.strip() or "Connected"
    return False, (err or out).strip() or "CLI failed"



# ---------------------------------------------------------------------------
# Disaster Recovery (AppMirrorRelationships)
# ---------------------------------------------------------------------------

def find_kubecontext_for_api(api_url: str) -> str:
    """Find a kubeconfig context whose cluster URL matches api_url.

    Used after `oc login` / `kubectl config set-cluster` to discover the
    context that was created so subsequent `kubectl --context` calls
    target the right cluster.
    """
    cli = get_platform_cli()
    try:
        proc = subprocess.run(
            [cli, "config", "view", "-o", "json"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    try:
        cfg = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return ""
    cluster_name = ""
    for c in cfg.get("clusters", []):
        if c.get("cluster", {}).get("server") == api_url:
            cluster_name = c.get("name", "")
            break
    if not cluster_name:
        return ""
    for c in cfg.get("contexts", []):
        if c.get("context", {}).get("cluster") == cluster_name:
            return c.get("name", "")
    return ""


def list_namespaces_for_context(context: str | None = None) -> list[dict]:
    """Return list of {name, ...} dicts for namespaces on the given context.

    Used by DR endpoints where we need cluster-aware namespace discovery.

    tridentprotect-ctl does not support core v1 namespaces, so we shell
    out to the platform CLI (kubectl on k8s, oc on OCP) via oc_run.
    """
    rc, out, err = oc_run(["get", "ns", "-o", "json"], context=context)
    if rc != 0:
        log.error(f"list_namespaces_for_context ({context}) failed: rc={rc} err={err.strip()}")
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        log.error(f"list_namespaces_for_context: JSON decode failed: {e}")
        return []

    items = []
    for ns in data.get("items", []):
        md = ns.get("metadata", {})
        st = ns.get("status", {})
        items.append({
            "name": md.get("name", ""),
            "status": st.get("phase", "Unknown"),
            "created": _fmt_time(md.get("creationTimestamp")),
        })
    # Hide system namespaces (mirror OCP DR behaviour: skip kube-* and openshift-*)
    items = [n for n in items
             if not n["name"].startswith("kube-")
             and not n["name"].startswith("openshift-")]
    return items


def list_storage_classes(context: str | None = None) -> list[dict]:
    """List storage classes (name + isDefault) on a given context.

    tridentprotect-ctl does not support storageclass, so we shell out to
    the platform CLI via oc_run (kubectl on k8s, oc on OCP).
    """
    rc, out, err = oc_run(["get", "storageclass", "-o", "json"], context=context)
    if rc != 0:
        log.error(f"list_storage_classes ({context}) failed: rc={rc} err={err.strip()}")
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        log.error(f"list_storage_classes: JSON decode failed: {e}")
        return []

    result = []
    for sc in data.get("items", []):
        name = sc.get("metadata", {}).get("name", "")
        ann = (sc.get("metadata", {}).get("annotations") or {})
        is_default = (
            ann.get("storageclass.kubernetes.io/is-default-class") == "true"
            or ann.get("storageclass.beta.kubernetes.io/is-default-class") == "true"
        )
        result.append({"name": name, "isDefault": is_default})
    return result


def _derive_cluster_from_namespace(namespace: str) -> str:
    """Best-effort cluster name from a namespace prefix.

    For the k8s edition, the source/destination cluster is not derivable
    purely from the namespace name (no clus1-/clus2- convention unless
    the user follows one). Returns empty string when no pattern matches.
    """
    if not namespace:
        return ""
    # Reserved: trident-protect namespace alone doesn't identify a cluster
    if namespace == "trident-protect":
        return ""
    return ""


def serialize_amr(amr: dict) -> dict:
    """Serialize an AppMirrorRelationship CR to a flat dict."""
    md = amr.get("metadata", {}) or {}
    sp = amr.get("spec", {}) or {}
    st = amr.get("status", {}) or {}
    conds = st.get("conditions", []) or []
    error_msg = ""
    for c in conds:
        if c.get("status", "").lower() == "false" or "error" in (c.get("type", "")).lower():
            error_msg = c.get("message", c.get("reason", ""))
            break
    if not error_msg:
        error_msg = st.get("error", "")

    ns_mapping = sp.get("namespaceMapping", []) or []
    src_ns = ""
    dest_ns = ""
    if ns_mapping and isinstance(ns_mapping[0], dict):
        src_ns = ns_mapping[0].get("source", "")
        dest_ns = ns_mapping[0].get("destination", "")
    if not dest_ns:
        dest_namespaces = st.get("destinationNamespaces", []) or []
        if dest_namespaces:
            dest_ns = dest_namespaces[0]

    derived_source_cluster = _derive_cluster_from_namespace(src_ns)
    derived_dest_cluster = _derive_cluster_from_namespace(dest_ns)
    annotations = md.get("annotations", {}) or {}
    annotation_source_cluster = annotations.get("trident.netapp.io/source-cluster", "")
    amr_lives_on_cluster = amr.get("_cluster", "")
    source_cluster = derived_source_cluster or annotation_source_cluster
    dest_cluster = derived_dest_cluster or amr_lives_on_cluster
    if (derived_source_cluster and derived_dest_cluster
            and derived_source_cluster == derived_dest_cluster
            and amr_lives_on_cluster
            and amr_lives_on_cluster != derived_source_cluster):
        dest_cluster = amr_lives_on_cluster

    return {
        "name": md.get("name"),
        "namespace": md.get("namespace"),
        "created": _fmt_time(md.get("creationTimestamp")),
        "age": _age(md.get("creationTimestamp")),
        "desiredState": sp.get("desiredState", ""),
        "sourceCluster": source_cluster or "—",
        "sourceNamespace": src_ns,
        "sourceAppVault": sp.get("sourceAppVaultRef", "—"),
        "sourceApplication": sp.get("sourceApplicationName", "—"),
        "sourceApplicationUID": sp.get("sourceApplicationUID", ""),
        "destinationAppVault": sp.get("destinationAppVaultRef", "—"),
        "destinationApplication": st.get("destinationApplicationRef", "—"),
        "destinationNamespace": dest_ns,
        "destinationCluster": dest_cluster or amr.get("_cluster", "—"),
        "recurrenceRule": sp.get("recurrenceRule", ""),
        "storageClassName": sp.get("storageClassName", ""),
        "state": st.get("state", "Unknown"),
        "stateClass": _state_class(st.get("state")),
        "lastTransferTime": _fmt_time((st.get("lastTransfer") or {}).get("completionTimestamp")),
        "lastTransferTimeAge": _age((st.get("lastTransfer") or {}).get("completionTimestamp")),
        "lastTransferSize": (st.get("lastTransfer") or {}).get("transferSizeBytes"),
        "lastTransferSizeHuman": _fmt_bytes((st.get("lastTransfer") or {}).get("transferSizeBytes")),
        "error": error_msg,
        "conditions": conds,
        "raw": amr,
    }


def list_amr(context: str | None = None, cluster: str = "") -> list[dict]:
    """List AppMirrorRelationships on a given context."""
    items = _list("appmirrorrelationship", context=context)
    out = []
    for amr in items:
        if cluster:
            amr["_cluster"] = cluster
        out.append(serialize_amr(amr))
    return out


def get_amr(namespace: str, name: str, context: str | None = None, cluster: str = "") -> dict | None:
    """Get a single AppMirrorRelationship."""
    item = _get("appmirrorrelationship", name, namespace, context=context)
    if item and cluster:
        item["_cluster"] = cluster
    return item


def serialize_amr_to_yaml(amr: dict) -> str:
    """Convert AppMirrorRelationship CR dict to valid YAML."""
    clean = {**amr}
    if "apiVersion" not in clean:
        clean["apiVersion"] = "protect.trident.netapp.io/v1"
    if "kind" not in clean:
        clean["kind"] = "AppMirrorRelationship"
    ordered = {
        "apiVersion": clean.pop("apiVersion"),
        "kind": clean.pop("kind"),
        "metadata": {},
        "spec": {},
        "status": {},
    }
    if isinstance(clean.get("metadata"), dict):
        ordered["metadata"] = {
            k: v for k, v in clean["metadata"].items() if k != "managedFields"
        }
        del clean["metadata"]
    ordered["spec"] = clean.pop("spec", {})
    ordered["status"] = clean.pop("status", {})
    for k, v in clean.items():
        ordered[k] = v
    return _yaml.dump(
        ordered, default_flow_style=False, sort_keys=False, indent=2,
        allow_unicode=True, width=4096,
    )


def validate_amr_yaml(yaml_str: str) -> tuple[bool, str]:
    """Parse + validate AppMirrorRelationship YAML (flat schema)."""
    try:
        doc = _yaml.safe_load(yaml_str)
    except _yaml.YAMLError as e:
        return False, f"YAML syntax error: {e}"
    if not isinstance(doc, dict):
        return False, "YAML must be a single document (mapping)"

    kind = doc.get("kind")
    if kind != "AppMirrorRelationship":
        return False, f"kind must be 'AppMirrorRelationship' (got '{kind}')"
    api_version = doc.get("apiVersion", "")
    if not api_version.startswith("protect.trident.netapp.io"):
        return False, f"apiVersion must start with 'protect.trident.netapp.io' (got '{api_version}')"

    metadata = doc.get("metadata", {}) or {}
    name = metadata.get("name")
    ns = metadata.get("namespace")
    if not name:
        return False, "metadata.name required"
    if not ns:
        return False, "metadata.namespace required"

    spec = doc.get("spec", {}) or {}
    if not spec.get("desiredState"):
        spec["desiredState"] = "Established"
    for field in ("sourceAppVaultRef", "destinationAppVaultRef",
                  "sourceApplicationName", "sourceApplicationUID",
                  "recurrenceRule"):
        if not spec.get(field):
            return False, f"spec.{field} required"

    return True, f"Valid — will create AppMirrorRelationship '{name}' in namespace '{ns}'"


def apply_amr_yaml(yaml_str: str, context: str | None = None, source_cluster: str = "") -> tuple[bool, str]:
    """Apply AppMirrorRelationship YAML via `kubectl apply -f -`.

    Strips legacy nested spec keys, ensures desiredState defaults,
    injects source-cluster annotation if provided.
    """
    try:
        doc = _yaml.safe_load(yaml_str)
        if not isinstance(doc, dict):
            return False, "YAML must be a single document (mapping)"
        spec = doc.setdefault("spec", {})
        spec.setdefault("desiredState", "Established")
        for k in ("source", "destination", "storageClassRef"):
            if k in spec:
                spec.pop(k)
        if source_cluster:
            md = doc.setdefault("metadata", {})
            annotations = md.setdefault("annotations", {})
            if isinstance(annotations, dict):
                annotations["trident.netapp.io/source-cluster"] = source_cluster
        yaml_str = _yaml.safe_dump(doc, default_flow_style=False, sort_keys=False, indent=2)
    except _yaml.YAMLError as e:
        return False, f"YAML parse error: {e}"

    cmd = [get_platform_cli()]
    if context:
        cmd += ["--context", context]
    cmd += ["apply", "-f", "-"]
    proc = subprocess.run(cmd, input=yaml_str, capture_output=True, text=True, timeout=30)
    if proc.returncode == 0:
        return True, (proc.stdout or "AppMirrorRelationship applied").strip()
    return False, (proc.stderr or proc.stdout).strip()


def wait_for_snapshot(namespace: str, name: str, timeout: int = 60, context: str | None = None) -> tuple[bool, str]:
    """Poll a snapshot CR until Completed or Failed state."""
    import time as _t
    deadline = _t.time() + timeout
    last_state = "Unknown"
    while _t.time() < deadline:
        snap = _get("snapshot", name, namespace, context=context)
        if snap:
            state = (snap.get("status", {}).get("state", "") or "").lower()
            last_state = state or "Unknown"
            if state in ("completed", "success"):
                return True, "Snapshot completed"
            if state in ("failed", "error"):
                return False, snap.get("status", {}).get("error", "") or "Snapshot failed"
        _t.sleep(2)
    return False, f"Snapshot did not complete within {timeout}s (last state: {last_state})"


def trigger_bootstrap_snapshot(snapshot_name: str, application_ref: str, namespace: str,
                                appvault_ref: str, context: str | None = None) -> tuple[bool, str]:
    """Create + wait for a snapshot (apply YAML via stdin)."""
    manifest = {
        "apiVersion": "protect.trident.netapp.io/v1",
        "kind": "Snapshot",
        "metadata": {"name": snapshot_name, "namespace": namespace},
        "spec": {"applicationRef": application_ref, "appVaultRef": appvault_ref},
    }
    yaml_str = _yaml.safe_dump(manifest, default_flow_style=False, sort_keys=False)
    cmd = [get_platform_cli()]
    if context:
        cmd += ["--context", context]
    cmd += ["apply", "-f", "-"]
    proc = subprocess.run(cmd, input=yaml_str, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()
    return wait_for_snapshot(namespace, snapshot_name, timeout=300, context=context)


def delete_amr(namespace: str, name: str, context: str | None = None) -> tuple[bool, str]:
    rc, out, err = _run(["delete", "appmirrorrelationship", name, "-n", namespace], context=context)
    if rc == 0:
        return True, out.strip() or f"Deleted {namespace}/{name}"
    return False, (err or out).strip()


def failover_amr(namespace: str, name: str, context: str | None = None) -> tuple[bool, str]:
    rc, out, err = oc_run(
        ["patch", "appmirrorrelationship", name, "-n", namespace,
         "--type", "merge", "-p", '{"spec":{"desiredState":"Promoted"}}'],
        context=context,
    )
    if rc == 0:
        return True, (out.strip() or f"Failover triggered for {namespace}/{name}")
    return False, (err or out).strip()


def resync_amr(namespace: str, name: str, context: str | None = None) -> tuple[bool, str]:
    rc, out, err = oc_run(
        ["patch", "appmirrorrelationship", name, "-n", namespace,
         "--type", "merge", "-p", '{"spec":{"desiredState":"Established"}}'],
        context=context,
    )
    if rc == 0:
        return True, (out.strip() or f"Resync triggered for {namespace}/{name}")
    return False, (err or out).strip()


def list_app_schedules(namespace: str, app_name: str, context: str | None = None) -> list[dict]:
    """List schedules linked to a specific application in a namespace."""
    schedules = list_schedules(context=context)
    return [
        s for s in schedules
        if s.get("applicationRef") == app_name and s.get("namespace") == namespace
    ]


def get_reverse_wizard_state(
    namespace: str,
    name: str,
    context: str | None = None,
    source_cluster: str = "",
) -> dict:
    """Gather state for the Reverse (YAML) wizard (read-only, no oc writes).

    Returns a dict with wizard-step check values:
      promoted: bool (AMR state on destination)
      recurrenceRule, sourceAppName, sourceNamespace, destinationNamespace,
      sourceAppVault, destinationAppVault: from current AMR spec
      sourceAppUID: uid of the app CR on the current destination cluster
        (becomes the new source app after reverse)
      schedulesOnSource: list of schedules linked to source app on OTHER cluster
      schedulesToDisable: same list filtered to ones still enabled
      hasScheduleOnSource: bool (≥1 schedule)
      hasSnapshotOnSource: bool (≥1 snapshot/backup)
      oldAmrDeleted: bool
      storageClassesOnDestination, defaultStorageClassOnDestination: SCs of OTHER cluster
      sourceClusterProfile, destinationClusterProfile: profile names
      activeProfile: dashboard's active profile
      destinationActiveProfileMatches: bool (Destination Cluster = active,
        required for create-AMR form which applies to active cluster)
    """
    from .config import Config
    cfg = Config.instance()

    state = {
        "amrName": name,
        "amrNamespace": namespace,
        "activeProfile": cfg.active_cluster,
        "sourceClusterProfile": source_cluster,
        "destinationClusterProfile": "",
        "promoted": False,
        "oldAmrDeleted": False,
        "hasScheduleOnSource": False,
        "hasSnapshotOnSource": False,
        "schedulesOnSource": [],
        "schedulesToDisable": [],
        "recurrenceRule": "",
        "sourceAppName": "",
        "sourceAppUID": "",
        "sourceNamespace": "",
        "destinationNamespace": "",
        "sourceAppVault": "",
        "destinationAppVault": "",
        "storageClassesOnDestination": [],
        "defaultStorageClassOnDestination": "",
        "destinationActiveProfileMatches": False,
        "amrFetchError": "",
    }

    other_ctx = None
    if source_cluster and source_cluster in cfg.profiles:
        other_ctx = (
            cfg.profiles[source_cluster].get("kubecontext")
            or cfg.profiles[source_cluster].get("kubeconfig_context")
        )

    rc, out, err = oc_run(
        ["get", "appmirrorrelationship", name, "-n", namespace, "-o", "json"],
        context=context,
    )
    src_ns = ""
    dst_ns = ""
    src_app = ""
    if rc != 0:
        state["amrFetchError"] = (err or out).strip()
        state["oldAmrDeleted"] = True
        state["promoted"] = False
    else:
        try:
            amr = json.loads(out)
        except json.JSONDecodeError:
            state["amrFetchError"] = "Failed to parse AMR JSON"
            state["oldAmrDeleted"] = True
            state["promoted"] = False
            amr = None

        if amr is not None:
            spec = amr.get("spec", {})
            ns_mapping = spec.get("namespaceMapping", [{}])
            src_ns = ns_mapping[0].get("source", "") if ns_mapping else ""
            dst_ns = ns_mapping[0].get("destination", "") if ns_mapping else ""
            src_app = spec.get("sourceApplicationName", "")
            src_vault = spec.get("sourceAppVaultRef", "")
            dst_vault = spec.get("destinationAppVaultRef", "")
            recurrence = spec.get("recurrenceRule", "")

            state["sourceAppName"] = src_app
            state["sourceNamespace"] = src_ns
            state["destinationNamespace"] = dst_ns
            state["sourceAppVault"] = dst_vault
            state["destinationAppVault"] = src_vault
            state["recurrenceRule"] = recurrence

            amr_status = amr.get("status", {})
            amr_state = (amr_status.get("state") or "").lower()
            state["promoted"] = (amr_state == "promoted")

            state["destinationClusterProfile"] = source_cluster or ""

            if context and dst_ns and src_app:
                try:
                    apps = list_applications(context=context)
                    for a in apps:
                        if a.get("namespace") == dst_ns and a.get("name") == src_app:
                            state["sourceAppUID"] = a.get("uid", "")
                            break
                except Exception:
                    pass

            if other_ctx:
                try:
                    schedules = list_app_schedules(src_ns, src_app, context=other_ctx)
                    sched_list = [
                        {
                            "name": s.get("name"),
                            "namespace": s.get("namespace"),
                            "enabled": s.get("enabled"),
                        }
                        for s in schedules
                    ]
                    state["schedulesOnSource"] = sched_list
                    state["schedulesToDisable"] = [
                        s for s in sched_list if s.get("enabled") is not False
                    ]
                    state["hasScheduleOnSource"] = len(sched_list) > 0
                except Exception:
                    pass

                try:
                    snaps = _list("snapshot", namespace=src_ns, context=other_ctx)
                    backups = _list("backup", namespace=src_ns, context=other_ctx)
                    state["hasSnapshotOnSource"] = (len(snaps) > 0 or len(backups) > 0)
                except Exception:
                    pass

                try:
                    sc_list = list_storage_classes(context=other_ctx)
                    state["storageClassesOnDestination"] = sc_list
                    for sc in sc_list:
                        if sc.get("isDefault"):
                            state["defaultStorageClassOnDestination"] = sc.get("name")
                            break
                except Exception:
                    pass

    if context and dst_ns and src_app:
        try:
            dest_schedules = list_app_schedules(dst_ns, src_app, context=context)
            dest_sched_list = [
                {
                    "name": s.get("name"),
                    "namespace": s.get("namespace"),
                    "enabled": s.get("enabled"),
                }
                for s in dest_schedules
            ]
            state["schedulesOnDestination"] = dest_sched_list
            state["hasScheduleOnDestination"] = len(dest_sched_list) > 0
        except Exception:
            pass

        try:
            dest_snaps = _list("snapshot", namespace=dst_ns, context=context)
            dest_snaps_sorted = sorted(
                dest_snaps,
                key=lambda s: s.get("metadata", {}).get("creationTimestamp", "") or "",
                reverse=True,
            )[:5]
            snap_list = []
            latest_name = ""
            latest_ts = ""
            for s in dest_snaps_sorted:
                md = s.get("metadata", {})
                st = s.get("status", {})
                ts = md.get("creationTimestamp", "") or ""
                sn_name = md.get("name", "")
                snap_list.append({
                    "name": sn_name,
                    "creationTimestamp": ts,
                    "state": st.get("state", ""),
                    "appLabel": md.get("labels", {}).get("appLabel", ""),
                    "age": ts.replace("T", " ").replace("Z", "") if ts else "",
                })
                if (not latest_name) and st.get("state", "").lower() in ("completed", "success", "available"):
                    latest_name = sn_name
                    latest_ts = ts
            state["latestSnapshotsOnDestination"] = snap_list
            state["hasSnapshotOnDestination"] = len(snap_list) > 0
            state["latestSnapshotName"] = latest_name
        except Exception:
            pass

    if context:
        rc_amr, _, _ = oc_run(
            ["get", "appmirrorrelationship", name, "-n", namespace, "-o", "name"],
            context=context,
        )
        if rc_amr != 0:
            state["oldAmrDeleted"] = True
        elif not state.get("amrFetchError"):
            state["oldAmrDeleted"] = False

    state["destinationActiveProfileMatches"] = (
        bool(source_cluster) and source_cluster == cfg.active_cluster
    )

    return state


def get_dr_overview() -> dict:
    """Get DR overview from all configured cluster profiles."""
    from .config import Config
    cfg = Config.instance()
    result = {
        "clusters": {},
        "relationships": [],
        "totals": {"total": 0, "healthy": 0, "degraded": 0, "failed": 0},
    }
    for pname, profile in cfg.profiles.items():
        ctx = profile.get("kubecontext") or profile.get("kubeconfig_context")
        try:
            apps = list_applications(context=ctx)
            amrs = list_amr(context=ctx, cluster=pname)
            result["clusters"][pname] = {
                "description": profile.get("description", pname),
                "apps": len(apps),
                "relationships": len(amrs),
                "connected": True,
            }
            for amr in amrs:
                result["relationships"].append(amr)
                result["totals"]["total"] += 1
                sc = amr.get("stateClass", "")
                if sc == "success":
                    result["totals"]["healthy"] += 1
                elif sc == "error":
                    result["totals"]["failed"] += 1
                else:
                    result["totals"]["degraded"] += 1
        except Exception as e:
            log.warning(f"DR overview failed for {pname}: {e}")
            result["clusters"][pname] = {
                "description": profile.get("description", pname),
                "apps": 0,
                "relationships": 0,
                "connected": False,
                "error": str(e),
            }
    return result


# ---------------------------------------------------------------------------
# Trident Protect version detection (per cluster, for the sidebar banner)
# ---------------------------------------------------------------------------

def _version_tuple(v: str) -> tuple:
    """Parse a dotted version string like '26.06.0' into a comparable tuple.

    Non-numeric parts are ignored.
    """
    import re as _re
    parts = _re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts)


def get_trident_protect_version(context: str | None = None) -> str:
    """Detect the Trident Protect version installed on a cluster.

    Reads the `trident-protect` namespace controller deployment image
    (e.g. `netapp/controller:26.06.0`) and returns the tag.
    Returns "" on failure (cluster unreachable, CLI error).
    """
    args = [
        "get", "deployment", "-n", "trident-protect",
        "-o", "custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image",
        "--no-headers",
    ]
    try:
        rc, out, err = oc_run(args, timeout=8, context=context)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning(f"get_trident_protect_version failed (context={context!r}): {e}")
        return ""
    if rc != 0:
        return ""
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name, image = parts[0], parts[1]
        if "netapp/controller" in image and ":" in image:
            return image.rsplit(":", 1)[-1].strip()
    return ""
