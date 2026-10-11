"""Trident Protect Web UI - Flask entry point (Kubernetes edition, v1.4.3)."""
import logging
import os as _os
import sys as _sys
import time

from flask import Flask, render_template, jsonify, request, redirect, url_for, session

from .config import Config, get_kubecontext
from . import trident_protect
from . import auth as tp_auth
from . import audit as tp_audit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tp-web")


# ---------------------------------------------------------------------------
# Auth + audit metadata
# ---------------------------------------------------------------------------

# Endpoints that never require dashboard login.
_AUTH_EXEMPT_PATHS = {
    "/login",
    "/api/auth/login",
    "/healthz",
    "/static",
    "/favicon.ico",
}

# Read-only allowlist for the `readonly` role — these mutating calls are
# permitted because they are part of the "switch profile" capability.
_READONLY_WRITE_ALLOWLIST = {
    "/api/settings/switch",
    "/api/settings/login",
    "/api/settings/test",
    "/api/settings/kubeconfig",
    "/api/settings/restart",
}

# Endpoints restricted to `admin` even for reads (sensitive secrets / mgmt).
_ADMIN_ONLY_PATHS = {
    "/api/credentials/",
    "/api/audit/",
}

# Page routes that are admin-only management UIs (hidden for readonly).
_ADMIN_ONLY_PAGES = {
    "/settings/credentials",
    "/logs",
}

# Audit log endpoint label map.
_AUDIT_ENDPOINT_MAP: dict[str, tuple[str, str]] = {
    "api_schedule_apply": ("schedule", "create"),
    "api_delete_schedule": ("schedule", "delete"),
    "api_enable_schedule": ("schedule", "enable"),
    "api_disable_schedule": ("schedule", "disable"),
    "api_backup_apply": ("backup", "create"),
    "api_delete_backup": ("backup", "delete"),
    "api_trigger_backuprestore": ("backuprestore", "trigger"),
    "api_trigger_backup_inplace": ("backupinplacerestore", "trigger"),
    "api_snapshot_apply": ("snapshot", "create"),
    "api_delete_snapshot": ("snapshot", "delete"),
    "api_trigger_snapshotrestore": ("snapshotrestore", "trigger"),
    "api_trigger_snapshot_inplace": ("snapshotinplacerestore", "trigger"),
    "api_application_apply": ("application", "create"),
    "api_delete_application": ("application", "delete"),
    "api_create_appvault": ("appvault", "create"),
    "api_delete_appvault": ("appvault", "delete"),
    "api_delete_restore": ("restore", "delete"),
    "api_restore_apply": ("restore", "apply"),
    "api_dr_create_relationship": ("dr_relationship", "create"),
    "api_dr_delete_relationship": ("dr_relationship", "delete"),
    "api_dr_failover_relationship": ("dr_relationship", "failover"),
    "api_dr_resync_relationship": ("dr_relationship", "resync"),
    "api_dr_reverse_wizard_create_bootstrap_snapshot": ("dr_relationship", "bootstrap_snapshot"),
    "api_save_profile": ("cluster_profile", "save"),
    "api_delete_profile": ("cluster_profile", "delete"),
    "api_switch_profile": ("cluster_profile", "switch"),
    "api_login_profile": ("cluster", "login"),
    "api_import_kubeconfig": ("cluster", "import"),
    "api_creds_users_add": ("local_user", "create"),
    "api_creds_users_update": ("local_user", "update"),
    "api_creds_users_delete": ("local_user", "delete"),
    "api_creds_ldap_save": ("ldap", "update"),
    "api_auth_login": ("auth", "login"),
    "logout_page": ("auth", "logout"),
}

_AUDIT_SKIP_ENDPOINTS: set[str] = {
    "api_schedule_validate",
    "api_backup_validate",
    "api_snapshot_validate",
    "api_application_validate",
    "api_dr_validate_relationship",
    "api_creds_ldap_test",
    "api_test_connection",
    "api_test_appvault",
}


def _is_static(path: str) -> bool:
    return path.startswith("/static/") or path == "/static" or path == "/favicon.ico"


def _auth_enabled() -> bool:
    cfg = Config.instance()
    auth = (cfg.auth or {})
    return bool(auth.get("enabled", False))


def _current_user():
    if not session.get("user"):
        return None
    return {
        "user": session.get("user"),
        "role": session.get("role", "readonly"),
        "source": session.get("source", "local"),
    }


def _require_login_response(path: str):
    if path.startswith("/api/") or path == "/healthz":
        return jsonify({"ok": False, "error": "auth required"}), 401
    return redirect(url_for("login_page"))


def create_app() -> Flask:
    cfg = Config.instance()
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = cfg.app.get("secret_key", "dev-key")
    app.config["JSON_SORT_KEYS"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # Bootstrap: seed default admin if auth enabled + no users exist yet
    try:
        tp_auth.seed_default_admin_if_empty()
    except Exception as e:
        log.exception("seed_default_admin_if_empty failed: %s", e)

    register_routes(app)
    return app


def register_routes(app: Flask) -> None:
    """Register page routes (HTML) and API routes (JSON)."""

    # ---- Auth guard: enforce on every request -----------------------------
    @app.before_request
    def _tp_auth_guard():
        path = request.path or "/"

        if not _auth_enabled():
            return None
        if _is_static(path):
            return None
        if path in _AUTH_EXEMPT_PATHS:
            return None
        if path.startswith("/api/auth/"):
            return None

        user = _current_user()
        if not user:
            return _require_login_response(path)

        if user["role"] != "admin":
            for p in _ADMIN_ONLY_PATHS:
                if path == p or path.startswith(p):
                    return jsonify({"ok": False, "error": "admin only"}), 403
            for p in _ADMIN_ONLY_PAGES:
                if path == p:
                    return redirect("/settings")
                if path.startswith(p + "/"):
                    return redirect("/settings")

        if user["role"] == "readonly" and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if path in _READONLY_WRITE_ALLOWLIST:
                return None
            return jsonify({"ok": False,
                            "error": "readonly role cannot perform this action"}), 403
        return None

    @app.after_request
    def _tp_audit_hook(response):
        try:
            method = request.method or ""
            path = request.path or ""
            if not path:
                return response
            is_logout = (method == "GET" and path == "/logout")
            if not is_logout and method not in ("POST", "PUT", "PATCH", "DELETE"):
                return response
            if not (path.startswith("/api/") or is_logout):
                return response
            if path == "/healthz" or path.startswith("/static/") or path == "/favicon.ico":
                return response

            endpoint = (request.url_rule.endpoint if request.url_rule else "") or ""
            if endpoint in _AUDIT_SKIP_ENDPOINTS:
                return response

            kind, action = _AUDIT_ENDPOINT_MAP.get(endpoint, ("", ""))
            if not action:
                if method == "DELETE":
                    action = "delete"
                elif method in ("POST", "PUT", "PATCH"):
                    action = "modify"
                else:
                    action = method.lower() or "other"
            if not kind:
                seg = path[len("/api/"):].split("/", 1)[0] if path.startswith("/api/") else ""
                kind = seg or "other"

            view_args = request.view_args or {}
            namespace = str(view_args.get("namespace", "") or "")
            name = str(view_args.get("name", "") or "")
            if not name and view_args.get("profile_name"):
                name = str(view_args.get("profile_name", ""))
            if not name and view_args.get("username"):
                name = str(view_args.get("username", ""))

            status = response.status_code or 0
            ok = 200 <= status < 400

            detail = ""
            try:
                data = response.get_json(silent=True)
                if isinstance(data, dict):
                    msg = data.get("message") or data.get("error") or ""
                    if isinstance(msg, str):
                        detail = msg
            except Exception:
                pass

            user = _current_user()
            rec = tp_audit.build_record(
                user=(user or {}).get("user"),
                role=(user or {}).get("role"),
                source=(user or {}).get("source"),
                action=action,
                kind=kind,
                namespace=namespace,
                name=name,
                detail=detail,
                ip=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
                method=method,
                path=path,
                status=status,
                ok=ok,
            )
            tp_audit.write(rec)
        except Exception as e:
            log.warning("audit hook error: %s", e)
        return response

    @app.context_processor
    def _inject_auth_context():
        return {"tp_user": _current_user()}

    # ---- Page routes ------------------------------------------------------
    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/applications")
    def applications_page():
        return render_template("applications.html")

    @app.route("/backups")
    def backups_page():
        return render_template("backups.html")

    @app.route("/snapshots")
    def snapshots_page():
        return render_template("snapshots.html")

    @app.route("/schedules")
    def schedules_page():
        return render_template("schedules.html")

    @app.route("/restore")
    def restore_page():
        return render_template("restore.html")

    @app.route("/settings")
    def settings_page():
        return render_template("settings_overview.html")

    @app.route("/disaster-recovery")
    def dr_page():
        return render_template("disaster_recovery.html")

    @app.route("/settings/clusters")
    def settings_clusters_page():
        return render_template("settings_clusters.html")

    @app.route("/settings/appvaults")
    def settings_appvaults_page():
        return render_template("settings_appvaults.html")

    @app.route("/healthz")
    def healthz():
        ok, msg = trident_protect.test_connection()
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 503)

    # ---- API: dashboard / apps / backups / snapshots / schedules ----------
    @app.route("/api/dashboard")
    def api_dashboard():
        try:
            return jsonify(trident_protect.list_dashboard())
        except Exception as e:
            log.exception("Dashboard failed")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/applications")
    def api_applications():
        ns = request.args.get("namespace") or None
        try:
            items = trident_protect.list_applications(ns)
        except (RuntimeError, OSError, TimeoutError) as exc:
            log.exception("Unable to list Trident Protect Applications")
            return jsonify({"ok": False, "error": str(exc)}), 503
        return jsonify({"ok": True, "items": items, "total": len(items)})

    @app.route("/api/backups")
    def api_backups():
        ns = request.args.get("namespace") or None
        items = trident_protect.list_backups(ns)
        return jsonify({"items": items, "total": len(items)})

    @app.route("/api/snapshots")
    def api_snapshots():
        ns = request.args.get("namespace") or None
        items = trident_protect.list_snapshots(ns)
        return jsonify({"items": items, "total": len(items)})

    @app.route("/api/schedules")
    def api_schedules():
        items = trident_protect.list_schedules()
        return jsonify({"items": items, "total": len(items)})

    @app.route("/api/schedule/<namespace>/<name>")
    def api_schedule_detail(namespace, name):
        item = trident_protect.get_schedule(namespace, name)
        if not item:
            return jsonify({"error": "Not found"}), 404
        fmt = request.args.get("format", "json").lower()
        if fmt == "yaml":
            from flask import Response
            return Response(
                trident_protect.serialize_schedule_to_yaml(item),
                mimetype="text/yaml",
                headers={"Content-Disposition": "inline"},
            )
        return jsonify(item)

    @app.route("/api/schedule/validate", methods=["POST"])
    def api_schedule_validate():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok, msg = trident_protect.validate_schedule_yaml(yaml_str)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/schedule/apply", methods=["POST"])
    def api_schedule_apply():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok, msg = trident_protect.validate_schedule_yaml(yaml_str)
        if not ok:
            return jsonify({"ok": False, "message": f"Invalid YAML: {msg}"}), 400
        ok, msg = trident_protect.apply_schedule_yaml(yaml_str)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/schedule/<namespace>/<name>", methods=["DELETE"])
    def api_delete_schedule(namespace, name):
        ok, msg = trident_protect.delete_schedule(namespace, name)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    def _resolve_schedule_context(namespace: str, name: str) -> str:
        """Find which configured cluster actually owns the Schedule CR.

        No cluster-naming convention is assumed — the Schedule CR is looked up
        on every configured profile's cluster (active first for determinism).
        Returns the kubecontext of the owning cluster, or "" if not found.

        Callers may override by passing ?context=<kubecontext>.
        """
        cfg = Config.instance()
        active = cfg.active_cluster
        ordered = [active] + [n for n in cfg.profiles if n != active]
        checked: list[str] = []
        for pname in ordered:
            if not pname:
                continue
            ctx = get_kubecontext(pname)
            if not ctx:
                continue
            try:
                if trident_protect._get("schedule", name, namespace, context=ctx):
                    return ctx
                checked.append(f"{pname}({ctx})")
            except Exception as e:
                log.warning(f"_resolve_schedule_context: lookup on {pname} failed: {e}")
                checked.append(f"{pname}({ctx}, error)")
        return ""

    def _schedule_context_error(namespace: str, name: str, checked: list[str]) -> tuple:
        cfg = Config.instance()
        if not checked:
            checked = [n for n in cfg.profiles if get_kubecontext(n)]
        msg = (
            f"Schedule '{namespace}/{name}' not found in any configured cluster"
            + (f" (checked: {', '.join(checked)})" if checked else
               " (no profiles with kubecontext configured)")
            + ". Verify the schedule exists and that profile kubecontexts are set in Settings."
        )
        return jsonify({"ok": False, "message": msg}), 400

    @app.route("/api/schedules/<namespace>/<name>/enable", methods=["POST"])
    def api_enable_schedule(namespace, name):
        override = request.args.get("context") or None
        ctx = override or _resolve_schedule_context(namespace, name)
        if not ctx:
            return _schedule_context_error(namespace, name, [])
        rc, out, err = trident_protect.oc_run(
            ["patch", "schedule", name, "-n", namespace,
             "--type", "merge", "-p", '{"spec":{"enabled":true}}'],
            context=ctx,
        )
        if rc == 0:
            return jsonify({"ok": True, "message": f"Schedule {namespace}/{name} enabled"})
        return jsonify({"ok": False, "message": (err or out or "Failed").strip()}), 400

    @app.route("/api/schedules/<namespace>/<name>/disable", methods=["POST"])
    def api_disable_schedule(namespace, name):
        override = request.args.get("context") or None
        ctx = override or _resolve_schedule_context(namespace, name)
        if not ctx:
            return _schedule_context_error(namespace, name, [])
        rc, out, err = trident_protect.oc_run(
            ["patch", "schedule", name, "-n", namespace,
             "--type", "merge", "-p", '{"spec":{"enabled":false}}'],
            context=ctx,
        )
        if rc == 0:
            return jsonify({"ok": True, "message": f"Schedule {namespace}/{name} disabled"})
        return jsonify({"ok": False, "message": (err or out or "Failed").strip()}), 400

    @app.route("/api/namespaces")
    def api_namespaces():
        return jsonify({"items": trident_protect.list_namespaces()})

    @app.route("/api/backup/<namespace>/<name>")
    def api_backup_detail(namespace, name):
        item = trident_protect.get_backup(namespace, name)
        if not item:
            return jsonify({"error": "Not found"}), 404
        fmt = request.args.get("format", "json").lower()
        if fmt == "yaml":
            from flask import Response
            return Response(
                trident_protect.serialize_backup_to_yaml(item),
                mimetype="text/yaml",
                headers={"Content-Disposition": "inline"},
            )
        if fmt == "summary":
            return jsonify(trident_protect.serialize_backup(item))
        return jsonify(item)

    @app.route("/api/snapshot/<namespace>/<name>")
    def api_snapshot_detail(namespace, name):
        item = trident_protect.get_snapshot(namespace, name)
        if not item:
            return jsonify({"error": "Not found"}), 404
        fmt = request.args.get("format", "json").lower()
        if fmt == "yaml":
            from flask import Response
            return Response(
                trident_protect.serialize_snapshot_to_yaml(item),
                mimetype="text/yaml",
                headers={"Content-Disposition": "inline"},
            )
        if fmt == "summary":
            return jsonify(trident_protect.serialize_snapshot(item))
        return jsonify(item)

    @app.route("/api/backup", methods=["POST"])
    def api_trigger_backup():
        body = request.get_json() or {}
        ns = body.get("namespace")
        app_ref = body.get("applicationRef")
        name = body.get("name")
        if not ns or not app_ref or not name:
            return jsonify({"ok": False, "message": "namespace, applicationRef, name required"}), 400
        appvault, av_ns = trident_protect.get_active_appvault()
        appvault = body.get("appvault", appvault)
        ok, msg = trident_protect.trigger_backup(ns, app_ref, appvault, name)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/backup/validate", methods=["POST"])
    def api_backup_validate():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok, msg, _ = trident_protect.validate_backup_yaml(yaml_str)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/backup/apply", methods=["POST"])
    def api_backup_apply():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok, msg, modified_yaml = trident_protect.validate_backup_yaml(yaml_str)
        if not ok:
            return jsonify({"ok": False, "message": f"Invalid YAML: {msg}"}), 400
        ok, msg = trident_protect.apply_backup_yaml(modified_yaml)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/backup/<namespace>/<name>/backuprestores", methods=["POST"])
    def api_trigger_backuprestore(namespace, name):
        body = request.get_json() or {}
        dest_ns = body.get("destination_namespace")
        if not dest_ns:
            return jsonify({"ok": False, "message": "destination_namespace required"}), 400
        appvault, _ = trident_protect.get_active_appvault()
        appvault = body.get("appvault", appvault)
        backup = trident_protect.get_backup(namespace, name)
        if not backup:
            return jsonify({"ok": False, "message": "Backup not found"}), 404
        app_archive_path = backup.get("status", {}).get("appArchivePath", "")
        if not app_archive_path:
            return jsonify({"ok": False, "message": "Backup has no appArchivePath"}), 400
        ok, msg = trident_protect.trigger_backup_restore(
            destination_namespace=dest_ns,
            backup_name=name,
            source_namespace=namespace,
            appvault=appvault,
            app_archive_path=app_archive_path,
            destination_app_name=body.get("destinationApplicationName"),
            # storageclass_mapping removed (v1.4.8)
        )
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/snapshot/<namespace>/<name>/snapshotrestores", methods=["POST"])
    def api_trigger_snapshotrestore(namespace, name):
        body = request.get_json() or {}
        dest_ns = body.get("destination_namespace")
        if not dest_ns:
            return jsonify({"ok": False, "message": "destination_namespace required"}), 400
        appvault, _ = trident_protect.get_active_appvault()
        appvault = body.get("appvault", appvault)
        snapshot = trident_protect.get_snapshot(namespace, name)
        if not snapshot:
            return jsonify({"ok": False, "message": "Snapshot not found"}), 404
        app_archive_path = snapshot.get("status", {}).get("appArchivePath", "")
        if not app_archive_path:
            return jsonify({"ok": False, "message": "Snapshot has no appArchivePath"}), 400
        ok, msg = trident_protect.trigger_snapshot_restore(
            destination_namespace=dest_ns,
            snapshot_name=name,
            source_namespace=namespace,
            appvault=appvault,
            app_archive_path=app_archive_path,
            destination_app_name=body.get("destinationApplicationName"),
            # storageclass_mapping removed (v1.4.8)
        )
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/restore/sources")
    def api_restore_sources():
        return jsonify(trident_protect.get_restore_source_namespaces())

    @app.route("/api/restore/destinations")
    def api_restore_destinations():
        return jsonify({
            "namespaces": trident_protect.get_namespaces(),
            "storageclasses": trident_protect.get_storageclasses(),
        })

    @app.route("/api/restores")
    def api_list_restores():
        items = trident_protect.list_restores()
        return jsonify({"items": items})

    @app.route("/api/backup/<namespace>/<name>/inplacerestore", methods=["POST"])
    def api_trigger_backup_inplace(namespace, name):
        backup = trident_protect.get_backup(namespace, name)
        if not backup:
            return jsonify({"ok": False, "message": "Backup not found"}), 404
        # Inplace restore: CLI auto-resolves AppVault + archive path
        # from --backup <ns>/<name>; no destination namespace or
        # storageclass mapping is used.
        ok, msg = trident_protect.trigger_backup_inplace(
            backup_name=name, source_namespace=namespace,
        )
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/snapshot/<namespace>/<name>/inplacerestore", methods=["POST"])
    def api_trigger_snapshot_inplace(namespace, name):
        snapshot = trident_protect.get_snapshot(namespace, name)
        if not snapshot:
            return jsonify({"ok": False, "message": "Snapshot not found"}), 404
        ok, msg = trident_protect.trigger_snapshot_inplace(
            snapshot_name=name, source_namespace=namespace,
        )
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/restore/validate", methods=["POST"])
    def api_restore_validate():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok, msg = trident_protect.validate_restore_yaml(yaml_str)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/restore/apply", methods=["POST"])
    def api_restore_apply():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok1, msg1 = trident_protect.validate_restore_yaml(yaml_str)
        if not ok1:
            return jsonify({"ok": False, "message": f"Invalid: {msg1}"}), 400
        ok2, msg2 = trident_protect.apply_restore_yaml(yaml_str)
        return jsonify({"ok": ok2, "message": msg2}), (200 if ok2 else 400)

    @app.route("/api/restore/<restore_type>/<namespace>/<name>", methods=["DELETE"])
    def api_delete_restore(restore_type, namespace, name):
        ok, msg = trident_protect.delete_restore(restore_type, namespace, name)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/backuprestore/<namespace>/<name>")
    def api_backuprestore_detail(namespace, name):
        item = trident_protect.get_backuprestore(namespace, name)
        if not item:
            return jsonify({"error": "Not found"}), 404
        fmt = request.args.get("format", "json").lower()
        if fmt == "yaml":
            from flask import Response
            return Response(
                trident_protect.serialize_restore_to_yaml(item, "BackupRestore"),
                mimetype="text/yaml",
            )
        if fmt == "summary":
            return jsonify(trident_protect.serialize_restore(item, "BackupRestore"))
        return jsonify(item)

    @app.route("/api/snapshotrestore/<namespace>/<name>")
    def api_snapshotrestore_detail(namespace, name):
        item = trident_protect.get_snapshotrestore(namespace, name)
        if not item:
            return jsonify({"error": "Not found"}), 404
        fmt = request.args.get("format", "json").lower()
        if fmt == "yaml":
            from flask import Response
            return Response(
                trident_protect.serialize_restore_to_yaml(item, "SnapshotRestore"),
                mimetype="text/yaml",
            )
        if fmt == "summary":
            return jsonify(trident_protect.serialize_restore(item, "SnapshotRestore"))
        return jsonify(item)

    @app.route("/api/backup/<namespace>/<name>", methods=["DELETE"])
    def api_delete_backup(namespace, name):
        ok, msg = trident_protect.delete_backup(namespace, name)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/snapshot/<namespace>/<name>", methods=["DELETE"])
    def api_delete_snapshot(namespace, name):
        ok, msg = trident_protect.delete_snapshot(namespace, name)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/snapshot/validate", methods=["POST"])
    def api_snapshot_validate():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok, msg, _ = trident_protect.validate_snapshot_yaml(yaml_str)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/snapshot/apply", methods=["POST"])
    def api_snapshot_apply():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok, msg, modified_yaml = trident_protect.validate_snapshot_yaml(yaml_str)
        if not ok:
            return jsonify({"ok": False, "message": f"Invalid YAML: {msg}"}), 400
        ok, msg = trident_protect.apply_snapshot_yaml(modified_yaml)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/application/<namespace>/<name>")
    def api_application_detail(namespace, name):
        item = trident_protect.get_application(namespace, name)
        if not item:
            return jsonify({"error": "Not found"}), 404
        fmt = request.args.get("format", "json").lower()
        if fmt == "yaml":
            from flask import Response
            return Response(
                trident_protect.serialize_app_to_yaml(item),
                mimetype="text/yaml",
                headers={"Content-Disposition": "inline"},
            )
        return jsonify(item)

    @app.route("/api/application/validate", methods=["POST"])
    def api_application_validate():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok, msg = trident_protect.validate_application_yaml(yaml_str)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/application/apply", methods=["POST"])
    def api_application_apply():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok, msg = trident_protect.validate_application_yaml(yaml_str)
        if not ok:
            return jsonify({"ok": False, "message": f"Invalid YAML: {msg}"}), 400
        ok, msg = trident_protect.apply_application_yaml(yaml_str)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/application/<namespace>/<name>", methods=["DELETE"])
    def api_delete_application(namespace, name):
        ok, msg = trident_protect.delete_application(namespace, name)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    # ---- Settings ---------------------------------------------------------
    @app.route("/api/settings")
    def api_get_settings():
        cfg = Config.instance()
        whoami = trident_protect.oc_whoami()
        return jsonify({
            "profiles": cfg.profiles,
            "active": cfg.active_cluster,
            "app": cfg.app,
            "cli": cfg.cli,
            "refresh": cfg.refresh,
            "whoami": whoami,
            "platform": trident_protect.detect_platform(),
            "maxProfiles": 2,
            "profileCount": len(cfg.profiles),
            "canAddProfile": len(cfg.profiles) < 2,
        })

    @app.route("/api/cluster-info")
    def api_cluster_info():
        return jsonify(trident_protect.get_cluster_info())

    @app.route("/api/platform")
    def api_platform():
        return jsonify({
            "platform": trident_protect.detect_platform(),
            "cli": trident_protect.get_platform_cli(),
        })

    # ---- Trident Protect version banner (per profile) ---------------------
    _TRIDENT_VERSION_TTL = 600
    _TRIDENT_VERSION_CACHE: dict = {"ts": 0.0, "data": None}

    def _invalidate_trident_version_cache():
        _TRIDENT_VERSION_CACHE["ts"] = 0.0
        _TRIDENT_VERSION_CACHE["data"] = None

    @app.route("/api/trident-version")
    def api_trident_version():
        cfg = Config.instance()
        profiles = cfg.profiles or {}
        if not profiles:
            return jsonify({
                "ok": True,
                "matched": True,
                "display_version": "",
                "checked": 0,
                "total": 0,
                "refresh_sec": _TRIDENT_VERSION_TTL,
                "versions": [],
                "unreachable": [],
            })
        now = time.time()
        if request.args.get("refresh") != "1" and _TRIDENT_VERSION_CACHE["data"] is not None \
                and (now - _TRIDENT_VERSION_CACHE["ts"]) < _TRIDENT_VERSION_TTL:
            return jsonify(_TRIDENT_VERSION_CACHE["data"])

        per_cluster = []
        reachable_versions: list[str] = []
        unreachable: list[str] = []

        for name, profile in profiles.items():
            ctx = get_kubecontext(name)
            if not ctx:
                per_cluster.append({"name": name, "version": "", "reachable": False})
                unreachable.append(name)
                continue
            try:
                v = trident_protect.get_trident_protect_version(ctx) or ""
            except Exception as e:
                log.warning(f"trident-version: {name} failed: {e}")
                v = ""
            entry = {"name": name, "version": v, "reachable": bool(v)}
            per_cluster.append(entry)
            if v:
                reachable_versions.append(v)
            else:
                unreachable.append(name)

        distinct = sorted(set(reachable_versions), key=trident_protect._version_tuple)
        matched = len(distinct) <= 1
        display = distinct[-1] if distinct else ""
        data = {
            "ok": True,
            "matched": matched,
            "display_version": display,
            "checked": len(reachable_versions),
            "total": len(profiles),
            "refresh_sec": _TRIDENT_VERSION_TTL,
            "versions": per_cluster,
            "unreachable": unreachable,
        }
        _TRIDENT_VERSION_CACHE["ts"] = now
        _TRIDENT_VERSION_CACHE["data"] = data
        return jsonify(data)

    # ---- Cluster profile management (k8s-only, no oc) --------------------
    @app.route("/api/settings/profile", methods=["POST"])
    def api_save_profile():
        """Add or update a cluster profile (Kubernetes only)."""
        body = request.get_json() or {}
        name = body.get("name", "").strip()
        if not name:
            return jsonify({"ok": False, "message": "name required"}), 400
        api_url = body.get("api_url", "").strip()
        if not api_url:
            return jsonify({"ok": False, "message": "api_url required"}), 400

        cfg = Config.instance()
        if name not in cfg.profiles and len(cfg.profiles) >= 2:
            return jsonify({
                "ok": False,
                "message": "Maximum 2 cluster profiles allowed (DR requires source + destination). Delete an existing profile first.",
            }), 400
        profile = {
            "api_url": api_url,
            "appvault": body.get("appvault", "ontap-s3-appvault"),
            "appvault_namespace": body.get("appvault_namespace", "trident-protect"),
            "insecure_skip_tls": body.get("insecure_skip_tls", True),
            "description": body.get("description", ""),
        }
        token = body.get("token", "").strip()
        if token:
            profile["token"] = token
        kubeconfig_context = body.get("kubeconfig_context", "").strip()
        if kubeconfig_context:
            profile["kubeconfig_context"] = kubeconfig_context
        cfg.profiles[name] = profile
        cfg.save()
        _invalidate_trident_version_cache()
        return jsonify({"ok": True, "message": f"Profile '{name}' saved", "restart_required": False})

    @app.route("/api/settings/profile/<name>", methods=["DELETE"])
    def api_delete_profile(name):
        if name == Config.instance().active_cluster:
            return jsonify({"ok": False, "message": "Cannot delete active profile — switch first"}), 400
        cfg = Config.instance()
        if name not in cfg.profiles:
            return jsonify({"ok": False, "message": "Profile not found"}), 404
        del cfg.profiles[name]
        cfg.save()
        _invalidate_trident_version_cache()
        return jsonify({"ok": True, "message": f"Profile '{name}' deleted"})

    @app.route("/api/settings/switch", methods=["POST"])
    def api_switch_profile():
        """Switch active cluster profile + auto-login using saved token (port 105).

        Order:
          1. If the profile has a stored token, call `oc_login_with_token`
             automatically (writes a stable kubeconfig entry, re-uses the
             same cluster/user/context name as the first login). On success
             return {auto_login: true}.
          2. If the profile has a `kubeconfig_context` and the token login
             already succeeded, switch the active context to it.
          3. If the token is missing or the re-login failed, return
             {auto_login: false, needs_reauth: true} so the UI shows a
             "Login" button instead of looping on a failing Switch.
        """
        body = request.get_json() or {}
        name = body.get("name", "").strip()
        if not name:
            return jsonify({"ok": False, "message": "name required"}), 400
        cfg = Config.instance()
        if name not in cfg.profiles:
            return jsonify({"ok": False, "message": "Profile not found"}), 404
        profile = cfg.profiles[name]

        cfg.active_cluster = name
        cfg.save()
        _invalidate_trident_version_cache()

        api_url = profile["api_url"]
        insecure = profile.get("insecure_skip_tls", True)
        stored_token = profile.get("token", "").strip()
        ctx = profile.get("kubeconfig_context", "")

        if stored_token:
            ok, msg = trident_protect.oc_login_with_token(api_url, stored_token, insecure)
            if ok:
                # Re-discover kubeconfig_context if missing (legacy profiles)
                if not ctx:
                    ctx_name = trident_protect.find_kubecontext_for_api(api_url)
                    if ctx_name:
                        profile["kubeconfig_context"] = ctx_name
                        cfg.save()
                return jsonify({
                    "ok": True,
                    "message": f"Switched to '{name}' and authenticated. Data now shows cluster '{name}'.",
                    "auto_login": True,
                })
            # Token may be expired or invalid
            return jsonify({
                "ok": True,
                "auto_login": False,
                "needs_reauth": True,
                "message": f"Switched to '{name}' but saved token is invalid or expired ({msg}). Click 'Login' to re-authenticate.",
            })

        if ctx:
            # No stored token — just switch the active context (works if the
            # kubeconfig is already on disk and a previous login set the entry).
            rc, out, err = trident_protect.oc_run(["config", "use-context", ctx])
            if rc == 0:
                return jsonify({
                    "ok": True,
                    "message": f"Switched to '{name}' (kubeconfig context: {ctx}).",
                    "auto_login": False,
                    "needs_reauth": False,
                })
            return jsonify({
                "ok": True,
                "auto_login": False,
                "needs_reauth": True,
                "message": f"Could not switch context: {err or out}. Click 'Login' to re-authenticate.",
            })

        return jsonify({
            "ok": True,
            "auto_login": False,
            "needs_reauth": True,
            "message": f"Active profile set to '{name}'. Use 'Login & Switch' to authenticate.",
        })

    @app.route("/api/settings/login", methods=["POST"])
    def api_login_profile():
        """Login to a cluster profile using a bearer token (Kubernetes only).

        Writes a kubeconfig via `kubectl config set-cluster/set-credentials`
        (or via `oc login --token=` when oc is the active CLI), then switches
        context. The token is stored in the profile for future auto-login.
        """
        body = request.get_json() or {}
        # Unified edition: token-only cluster auth. Reject username/password explicitly.
        if "username" in body or "password" in body:
            return jsonify({
                "ok": False,
                "message": "Cluster login is token-only in this edition. Username/password is not supported. Use a bearer token or import a kubeconfig instead.",
            }), 400
        name = body.get("name", "").strip()
        token = body.get("token", "").strip()

        if not name:
            return jsonify({"ok": False, "message": "name required"}), 400
        if not token:
            return jsonify({"ok": False, "message": "token required (bearer token)"}), 400

        cfg = Config.instance()
        if name not in cfg.profiles:
            return jsonify({"ok": False, "message": "Profile not found"}), 404
        profile = cfg.profiles[name]
        api_url = profile["api_url"]
        insecure = profile.get("insecure_skip_tls", True)

        # The CLI runner auto-detects oc vs kubectl; for k8s-only this is kubectl.
        ok, msg = trident_protect.oc_login_with_token(api_url, token, insecure)
        if not ok:
            return jsonify({"ok": False, "message": f"Login failed: {msg}"}), 400

        # Persist token + set active
        profile["token"] = token
        ctx_name = trident_protect.find_kubecontext_for_api(api_url)
        if ctx_name:
            profile["kubeconfig_context"] = ctx_name
        cfg.active_cluster = name
        cfg.save()
        _invalidate_trident_version_cache()
        return jsonify({
            "ok": True,
            "message": f"Switched to '{name}' and authenticated. Data now shows cluster '{name}'.",
            "auto_login": True,
        })

    @app.route("/api/settings/kubeconfig", methods=["POST"])
    def api_import_kubeconfig():
        """Import a kubeconfig YAML file.

        Writes to ~/.kube/config (merging clusters/users/contexts), then
        auto-creates a cluster profile from the current-context.
        """
        import shutil as _shutil
        import yaml
        from pathlib import Path

        body = request.get_json() or {}
        kubeconfig_yaml = body.get("kubeconfig", "").strip()
        if not kubeconfig_yaml:
            return jsonify({"ok": False, "message": "kubeconfig content required"}), 400

        try:
            kc = yaml.safe_load(kubeconfig_yaml)
        except yaml.YAMLError as e:
            return jsonify({"ok": False, "message": f"Invalid YAML: {e}"}), 400
        if not isinstance(kc, dict):
            return jsonify({"ok": False, "message": "kubeconfig must be a YAML mapping"}), 400

        clusters = kc.get("clusters", []) or []
        users = kc.get("users", []) or []
        contexts = kc.get("contexts", []) or []
        current_context = kc.get("current-context", "")

        if not clusters or not users or not contexts:
            return jsonify({
                "ok": False,
                "message": "kubeconfig must have at least one cluster, user, and context",
            }), 400
        if not current_context:
            if len(contexts) == 1:
                current_context = contexts[0].get("name", "")
            else:
                return jsonify({
                    "ok": False,
                    "message": "kubeconfig has no 'current-context' and multiple contexts — please add 'current-context'",
                }), 400

        ctx_obj = next((c for c in contexts if c.get("name") == current_context), None)
        if not ctx_obj:
            return jsonify({"ok": False, "message": f"current-context '{current_context}' not found"}), 400

        ctx_cluster = ctx_obj.get("context", {}).get("cluster", "")
        ctx_user = ctx_obj.get("context", {}).get("user", "")
        cluster_obj = next((c for c in clusters if c.get("name") == ctx_cluster), None)
        user_obj = next((u for u in users if u.get("name") == ctx_user), None)
        if not cluster_obj:
            return jsonify({"ok": False, "message": f"cluster '{ctx_cluster}' not found"}), 400
        if not user_obj:
            return jsonify({"ok": False, "message": f"user '{ctx_user}' not found"}), 400

        server_url = cluster_obj.get("cluster", {}).get("server", "")
        insecure_skip_tls = cluster_obj.get("cluster", {}).get("insecure-skip-tls-verify", False)
        user_name = user_obj.get("name", "")
        if not server_url:
            return jsonify({"ok": False, "message": "current context's cluster has no server URL"}), 400

        # Merge into existing ~/.kube/config
        kube_path = Path.home() / ".kube" / "config"
        kube_path.parent.mkdir(parents=True, exist_ok=True)
        existing_kc = {}
        if kube_path.exists():
            try:
                existing_kc = yaml.safe_load(kube_path.read_text()) or {}
            except Exception:
                existing_kc = {}
        existing_clusters = {c.get("name"): c for c in existing_kc.get("clusters", [])}
        existing_users = {u.get("name"): u for u in existing_kc.get("users", [])}
        existing_contexts = {c.get("name"): c for c in existing_kc.get("contexts", [])}
        for c in clusters:
            existing_clusters[c.get("name")] = c
        for u in users:
            existing_users[u.get("name")] = u
        for c in contexts:
            existing_contexts[c.get("name")] = c
        merged = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": list(existing_clusters.values()),
            "users": list(existing_users.values()),
            "contexts": list(existing_contexts.values()),
            "current-context": current_context,
        }
        try:
            kube_path.write_text(yaml.dump(merged, default_flow_style=False, sort_keys=False, width=4096))
        except Exception as e:
            return jsonify({"ok": False, "message": f"Failed to write kubeconfig: {e}"}), 500

        # Auto-create profile in config.yaml
        profile_name = body.get("profile_name", "").strip() or current_context or "imported-cluster"
        profile_name = profile_name.replace("/", "-").replace(" ", "-")

        cfg = Config.instance()
        # Enforce max 2 profiles (for DR source + destination), allow updates of
        # existing profile (e.g. re-importing the same kubeconfig).
        if profile_name not in cfg.profiles and len(cfg.profiles) >= 2:
            return jsonify({
                "ok": False,
                "message": "Maximum 2 cluster profiles allowed (DR requires source + destination). Delete an existing profile first.",
            }), 400
        if profile_name in cfg.profiles:
            profile_name = f"{profile_name}-imported"
        cfg.profiles[profile_name] = {
            "api_url": server_url,
            "appvault": "ontap-s3-appvault",
            "appvault_namespace": "trident-protect",
            "insecure_skip_tls": insecure_skip_tls,
            "description": f"Imported from kubeconfig (context: {current_context})",
            "kubeconfig_context": current_context,
        }
        cfg.save()
        _invalidate_trident_version_cache()
        return jsonify({
            "ok": True,
            "message": f"Kubeconfig imported. Profile '{profile_name}' created.",
            "preview": {
                "server": server_url,
                "user": user_name,
                "context": current_context,
                "profile": profile_name,
                "insecure_skip_tls": insecure_skip_tls,
            },
        })

    @app.route("/api/settings/test", methods=["POST"])
    def api_test_connection():
        ok, msg = trident_protect.test_connection()
        return jsonify({"ok": ok, "message": msg})

    @app.route("/api/settings/token")
    def api_get_token():
        u = _current_user()
        if not u or u["role"] != "admin":
            token = trident_protect.oc_get_token()
            if not token:
                return jsonify({"ok": False, "message": "Not logged in or token unavailable"}), 400
            return jsonify({
                "ok": True,
                "preview": token[:20] + "...",
                "length": len(token),
                "full": None,
                "restricted": True,
            })
        token = trident_protect.oc_get_token()
        if not token:
            return jsonify({"ok": False, "message": "Not logged in or token unavailable"}), 400
        return jsonify({
            "ok": True,
            "preview": token[:20] + "...",
            "length": len(token),
            "full": token,
        })

    @app.route("/api/settings/restart", methods=["POST"])
    def api_restart():
        import threading
        def _restart():
            time.sleep(0.5)
            _os.execv(_sys.executable, [_sys.executable, "-m", "app.main"])
        threading.Thread(target=_restart, daemon=True).start()
        return jsonify({"ok": True, "message": "Restart initiated"})

    # ---- AppVaults --------------------------------------------------------
    @app.route("/api/appvaults")
    def api_list_appvaults():
        ns = request.args.get("namespace") or ""
        items = trident_protect.list_appvaults(ns)
        return jsonify({"items": items, "total": len(items)})

    @app.route("/api/appvaults/<namespace>/<name>")
    def api_appvault_detail(namespace, name):
        item = trident_protect.get_appvault(namespace, name)
        if not item:
            return jsonify({"error": "Not found"}), 404
        fmt = request.args.get("format", "json").lower()
        if fmt == "yaml":
            from flask import Response
            return Response(
                trident_protect.serialize_appvault_to_yaml(item),
                mimetype="text/yaml",
                headers={"Content-Disposition": "inline"},
            )
        return jsonify(item)

    @app.route("/api/appvaults", methods=["POST"])
    def api_create_appvault():
        body = request.get_json() or {}
        name = body.get("name", "").strip()
        namespace = body.get("namespace", "trident-protect").strip()
        provider = body.get("provider", "OntapS3").strip()
        endpoint = body.get("endpoint", "").strip()
        bucket = body.get("bucket", "").strip()
        skip_cert = body.get("skip_cert", True)
        secret_name = body.get("secret_name", "").strip()
        access_key = body.get("access_key", "").strip()
        secret_key = body.get("secret_key", "").strip()

        if not all([name, endpoint, bucket, secret_name, access_key, secret_key]):
            return jsonify({"ok": False,
                            "message": "name, endpoint, bucket, secret_name, access_key, secret_key required"}), 400
        ok, msg = trident_protect.create_appvault_secret(namespace, secret_name, access_key, secret_key)
        if not ok:
            return jsonify({"ok": False, "message": f"Failed to create secret: {msg}"}), 500
        ok, msg = trident_protect.create_appvault(
            name=name,
            namespace=namespace,
            provider=provider,
            endpoint=endpoint,
            bucket=bucket,
            skip_cert=skip_cert,
            secret_name=secret_name,
        )
        if not ok:
            return jsonify({"ok": False, "message": f"Secret created but AppVault failed: {msg}"}), 500
        return jsonify({"ok": True, "message": f"AppVault {namespace}/{name} created (provider={provider})"})

    @app.route("/api/appvaults/<namespace>/<name>", methods=["DELETE"])
    def api_delete_appvault(namespace, name):
        ok, msg = trident_protect.delete_appvault(namespace, name)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/appvaults/<namespace>/<name>/test", methods=["POST"])
    def api_test_appvault(namespace, name):
        item = trident_protect._get("appvault", name, namespace)
        if not item:
            return jsonify({"ok": False, "message": "AppVault not found"}), 404
        st = item.get("status", {}).get("state", "Unknown")
        ok = st.lower() in ("available", "ready")
        return jsonify({"ok": ok, "message": f"AppVault state: {st}"})

    # ---- Disaster Recovery (AppMirrorRelationships) -----------------------
    @app.route("/api/dr/overview")
    def api_dr_overview():
        try:
            return jsonify(trident_protect.get_dr_overview())
        except Exception as e:
            log.exception("DR overview failed")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/dr/relationships/create", methods=["POST"])
    def api_dr_create_relationship():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        if not yaml_str:
            return jsonify({"ok": False, "message": "yaml required"}), 400
        ok, msg = trident_protect.validate_amr_yaml(yaml_str)
        if not ok:
            return jsonify({"ok": False, "message": f"Invalid YAML: {msg}"}), 400
        cfg = Config.instance()
        active = cfg.active_cluster
        if not active or active not in cfg.profiles:
            return jsonify({"ok": False, "message": "No active cluster profile set. Activate one in Settings."}), 400
        ctx = get_kubecontext(active)
        if not ctx:
            return jsonify({"ok": False, "message": f"Active profile '{active}' has no kubecontext configured."}), 400
        ok, msg = trident_protect.apply_amr_yaml(yaml_str, context=ctx, source_cluster=active)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/dr/relationships/validate", methods=["POST"])
    def api_dr_validate_relationship():
        body = request.get_json() or {}
        yaml_str = body.get("yaml", "")
        ok, msg = trident_protect.validate_amr_yaml(yaml_str)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/dr/relationships/<namespace>/<name>")
    def api_dr_relationship_detail(namespace, name):
        ctx = request.args.get("context") or None
        cluster = ""
        if ctx:
            cfg = Config.instance()
            for pname, profile in cfg.profiles.items():
                if profile.get("kubecontext") == ctx or profile.get("kubeconfig_context") == ctx:
                    cluster = pname
                    break
        item = trident_protect.get_amr(namespace, name, context=ctx, cluster=cluster)
        if not item:
            return jsonify({"error": "Not found"}), 404
        fmt = request.args.get("format", "json").lower()
        if fmt == "yaml":
            from flask import Response
            return Response(
                trident_protect.serialize_amr_to_yaml(item),
                mimetype="text/yaml",
                headers={"Content-Disposition": "inline"},
            )
        if fmt == "summary":
            return jsonify(trident_protect.serialize_amr(item))
        return jsonify(item)

    @app.route("/api/dr/relationships/<namespace>/<name>", methods=["DELETE"])
    def api_dr_delete_relationship(namespace, name):
        ctx = request.args.get("context") or None
        ok, msg = trident_protect.delete_amr(namespace, name, context=ctx)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/dr/relationships/<namespace>/<name>/failover", methods=["POST"])
    def api_dr_failover_relationship(namespace, name):
        ctx = request.args.get("context") or None
        ok, msg = trident_protect.failover_amr(namespace, name, context=ctx)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/dr/relationships/<namespace>/<name>/resync", methods=["POST"])
    def api_dr_resync_relationship(namespace, name):
        ctx = request.args.get("context") or None
        ok, msg = trident_protect.resync_amr(namespace, name, context=ctx)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/dr/relationships/<namespace>/<name>/reverse-wizard-state", methods=["GET"])
    def api_dr_reverse_wizard_state(namespace, name):
        ctx = request.args.get("context") or None
        source_cluster = request.args.get("source_cluster", "")
        state = trident_protect.get_reverse_wizard_state(
            namespace, name, context=ctx, source_cluster=source_cluster,
        )
        return jsonify(state)

    @app.route(
        "/api/dr/relationships/<namespace>/<name>/reverse-wizard-create-bootstrap-snapshot",
        methods=["POST"],
    )
    def api_dr_reverse_wizard_create_bootstrap_snapshot(namespace, name):
        body = request.get_json() or {}
        ctx = request.args.get("context") or None
        snapshot_name = body.get("name", "").strip()
        application_ref = body.get("application_ref", "").strip()
        appvault_ref = body.get("appvault_ref", "").strip()
        if not (snapshot_name and application_ref and appvault_ref):
            return jsonify({"ok": False, "message": "name, application_ref, appvault_ref required"}), 400
        ok, msg = trident_protect.trigger_bootstrap_snapshot(
            snapshot_name=snapshot_name,
            application_ref=application_ref,
            namespace=namespace,
            appvault_ref=appvault_ref,
            context=ctx,
        )
        return jsonify({"ok": ok, "message": msg, "snapshot_name": snapshot_name}), (200 if ok else 400)

    @app.route("/api/dr/apps")
    def api_dr_apps():
        ctx = request.args.get("context") or None
        ns = request.args.get("namespace") or None
        items = trident_protect.list_applications(namespace=ns, context=ctx)
        return jsonify({"items": items, "total": len(items)})

    @app.route("/api/dr/appvaults")
    def api_dr_appvaults():
        ctx = request.args.get("context") or None
        items = trident_protect.list_appvaults(context=ctx)
        return jsonify({"items": items, "total": len(items)})

    @app.route("/api/dr/storageclasses")
    def api_dr_storageclasses():
        ctx = request.args.get("context") or None
        items = trident_protect.get_storageclasses(context=ctx)
        return jsonify({"items": items, "total": len(items)})

    @app.route("/api/dr/namespaces")
    def api_dr_namespaces():
        ctx = request.args.get("context") or None
        items = trident_protect.list_namespaces_for_context(ctx)
        return jsonify({"items": items, "total": len(items)})

    # ---- Dashboard Auth (login/logout/credentials) -----------------------
    @app.route("/login")
    def login_page():
        if _current_user() and _auth_enabled():
            return redirect("/")
        return render_template("login.html")

    @app.route("/logout")
    def logout_page():
        session.clear()
        return redirect("/login")

    @app.route("/api/auth/login", methods=["POST"])
    def api_auth_login():
        if not _auth_enabled():
            return jsonify({"ok": True, "message": "Auth disabled",
                            "user": "anonymous", "role": "admin"}), 200
        body = request.get_json(silent=True) or {}
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        if not username or not password:
            return jsonify({"ok": False, "message": "username and password required"}), 400
        cfg = Config.instance()
        user = tp_auth.authenticate(cfg.auth or {}, username, password)
        if not user:
            time.sleep(0.5)
            return jsonify({"ok": False, "message": "Invalid username or password"}), 401
        session.clear()
        session["user"] = user["username"]
        session["role"] = user["role"]
        session["source"] = user["source"]
        return jsonify({
            "ok": True,
            "user": user["username"],
            "role": user["role"],
            "source": user["source"],
        })

    # ---- Credentials (admin only) -----------------------------------------
    @app.route("/settings/credentials")
    def settings_credentials_page():
        return render_template("settings_credentials.html")

    @app.route("/api/credentials/users", methods=["GET"])
    def api_creds_users_list():
        source = request.args.get("source")
        if source not in (None, "", "local", "ldap"):
            return jsonify({"ok": False, "message": "source must be 'local' or 'ldap'"}), 400
        return jsonify({"items": tp_auth.list_users(source=source or None)})

    @app.route("/api/credentials/users", methods=["POST"])
    def api_creds_users_add():
        body = request.get_json(silent=True) or {}
        ok, msg = tp_auth.add_local_user(
            body.get("username", ""),
            body.get("password", ""),
            body.get("role", "readonly"),
        )
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/credentials/users/<username>", methods=["POST"])
    def api_creds_users_update(username):
        body = request.get_json(silent=True) or {}
        ok, msg = tp_auth.update_local_user(
            username,
            role=body.get("role"),
            password=body.get("password"),
        )
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/credentials/users/<username>", methods=["DELETE"])
    def api_creds_users_delete(username):
        ok, msg = tp_auth.delete_local_user(username)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    @app.route("/api/credentials/ldap", methods=["GET"])
    def api_creds_ldap_get():
        cfg = Config.instance()
        auth = (cfg.auth or {})
        ldap = (auth.get("ldap") or {}).copy()
        if "bind_password" in ldap and ldap["bind_password"]:
            ldap["bind_password_set"] = True
            ldap["bind_password"] = ""
        return jsonify({
            "ldap": ldap,
            "enabled": bool(auth.get("enabled", False)),
            "default_role": auth.get("default_role", "readonly"),
        })

    @app.route("/api/credentials/ldap", methods=["POST"])
    def api_creds_ldap_save():
        body = request.get_json(silent=True) or {}
        cfg = Config.instance()
        current = dict(cfg.auth or {})
        ldap = dict(current.get("ldap") or {})
        if "auth_enabled" in body:
            current["enabled"] = bool(body.get("auth_enabled"))
        if "default_role" in body:
            current["default_role"] = body.get("default_role", "readonly")
        ldap["enabled"] = bool(body.get("ldap_enabled", ldap.get("enabled", False)))
        for k in ("server", "port", "use_tls", "skip_cert_verify", "bind_dn",
                  "base_dn", "user_filter", "username_attribute",
                  "netbios_domain"):
            if k in body:
                ldap[k] = body[k]
        bp = body.get("bind_password")
        if bp:
            ldap["bind_password"] = bp
        if "admin_group_dns" in body:
            val = body["admin_group_dns"]
            if isinstance(val, str):
                ldap["admin_group_dns"] = [s.strip() for s in val.splitlines() if s.strip()]
            elif isinstance(val, list):
                ldap["admin_group_dns"] = [str(s).strip() for s in val if str(s).strip()]
            else:
                ldap["admin_group_dns"] = []
        current["ldap"] = ldap
        cfg.save_auth(current)
        return jsonify({"ok": True, "message": "LDAP settings saved"})

    @app.route("/api/credentials/ldap/test", methods=["POST"])
    def api_creds_ldap_test():
        body = request.get_json(silent=True) or {}
        cfg = Config.instance()
        auth_cfg = dict(cfg.auth or {})
        if body.get("ldap"):
            auth_cfg = {**auth_cfg, "ldap": {**(auth_cfg.get("ldap") or {}), **body["ldap"]}}
        if "username" in body:
            ok, msg = tp_auth.test_ldap(auth_cfg, body.get("username"), body.get("password"))
        else:
            ok, msg = tp_auth.test_ldap(auth_cfg)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)

    # ---- Audit log (admin only) -------------------------------------------
    @app.route("/logs")
    def logs_page():
        return render_template("logs.html")

    @app.route("/api/audit/logs")
    def api_audit_logs():
        result = tp_audit.query(
            user=request.args.get("user", ""),
            action=request.args.get("action", ""),
            kind=request.args.get("kind", ""),
            result=request.args.get("result", ""),
            q=request.args.get("q", ""),
            limit=request.args.get("limit", 100, type=int),
            offset=request.args.get("offset", 0, type=int),
        )
        return jsonify({
            "ok": True,
            "enabled": tp_audit.is_enabled(),
            "items": result["items"],
            "total": result["total"],
            "users": result["users"],
        })


if __name__ == "__main__":
    cfg = Config.instance()
    app = create_app()
    log.info(f"Starting Trident Protect Web UI on http://{cfg.app['host']}:{cfg.app['port']}")
    app.run(
        host=cfg.app["host"],
        port=cfg.app["port"],
        debug=cfg.app.get("debug", False),
    )
