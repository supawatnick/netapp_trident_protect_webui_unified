# Release Notes — v1.3.0

**Release date:** 2026-07-31
**Tag:** `v1.3.0`
**Trident Protect CLI tested with:** v26.06.0 (Trident v26.6)

---

## 1. Summary

v1.3.0 is an **architectural** release focused on the dashboard's reverse-direction flow:

| Theme | Impact |
|-------|--------|
| Reverse (YAML) wizard | Apply-only, 6-step gated; the dashboard never orchestrates |
| Row button rename | "Reverse (YAML)" → "Reverse Resync" + warning colour |
| Bootstrapping | Snapshot picker (latest vs new) — no more "No snapshots found" failures |
| Orchestrator removed | All dead code (Sync, Reverse Direction) deleted from frontend + backend |
| Uninstall | New `uninstall.sh` (thorough) |
| Version pulse | Sidebar reads `v1.3.0 (Trident v26.6)` |

This release **renounces orchestration**. **Every** dashboard action is form → YAML → `oc apply -f -` or a single `oc` command. No hidden CR creation, no auto-disables, no implicit deletes.

It is fully backward-compatible with v1.2.0 — no config or data migration required.

---

## 2. What is included in the tag

- All `app/` (Python Flask backend + HTML templates + static assets)
- `install.sh`, **`uninstall.sh`** (NEW), `run.sh`, `stop.sh`
- `config.example.yaml`, `requirements.txt`
- `systemd/trident-protect-webui.service`
- `README.md`, `DOCS.md`, `CHANGELOG.md`, `RELEASE.md`

Excluded from the tarball:
- `.git/`
- `.venv/` (rebuilt by installer)
- `bin/tridentprotect-ctl` (downloaded by installer)
- `config.yaml` (your secrets — use `config.example.yaml` as template)
- `__pycache__/`

---

## 3. Install v1.3.0

### Option A — Clone + install on target server (recommended)

```bash
git clone https://github.com/supawatnick/netapp_trident_protect_webui_ocp.git
cd netapp_trident_protect_webui_ocp
sudo ./install.sh
```

`install.sh` is a 10-step interactive installer:
1. Check prerequisites (Python 3.9+, pip, curl, oc/kubectl)
2. Set up directories under `/opt/trident-protect-webui`
3. Create a Python virtual environment
4. Install Python dependencies
5. Download `tridentprotect-ctl` from NetApp's GitHub releases (v26.06.0)
6. Configure cluster connection (interactive)
7. Set up authentication (interactive)
8. Install systemd service
9. Configure firewall (open port 8080)
10. Verify installation

After install:
```bash
systemctl status trident-protect-webui        # service status
journalctl -u trident-protect-webui -f       # live logs
# UI: http://<server-ip>:8080
```

### Option B — Install from a prebuilt package

```bash
# (if provided as a tarball)
tar xzf trident-protect-webui-v1.3.0.tar.gz
cd trident-protect-webui-v26.06.0
sudo ./install.sh
```

### Option C — Update an existing installation

```bash
cd /opt/trident-protect-webui
sudo git pull origin main
sudo bash install.sh --upgrade  # (if supported)
# OR rerun the installer; it preserves /opt/trident-protect-webui/config.yaml
```

---

## 4. Uninstall v1.3.0

Use the NEW `uninstall.sh` shipped with v1.3.0:

```bash
cd /path/to/source
sudo ./uninstall.sh
# Confirms with [y/N]
# → Stops + disables service
# → Backs up config.yaml to /tmp/trident-webui-config-backup-<ts>.yaml
# → Removes /opt/trident-protect-webui
# → Removes firewall rule (ufw or firewalld)
```

`config.yaml` (containing your SA tokens) is **backed up** before deletion. Recovery is `cp /tmp/trident-webui-config-backup-*.yaml /opt/trident-protect-webui/config.yaml && sudo ./install.sh --restore`.

---

## 5. Verification checklist

- [ ] `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/` → **200**
- [ ] `systemctl is-active trident-protect-webui` → **active**
- [ ] `journalctl -u trident-protect-webui --since "-1 min" -p err --no-pager` → empty
- [ ] Sidebar reads `v1.3.0 (Trident v26.6)`
- [ ] `/api/dr/overview` returns valid JSON list of AMRs
- [ ] DR page → click **Reverse Resync** on a Promoted AMR → warning modal appears

---

## 6. Rollback (if v1.3.0 has issues)

```bash
# Roll forward — there is no v1.3.0-backup (we intentionally removed it to start fresh).
# If you have your own backup tag locally:
git checkout main-pre-obliterate  # or any backup tag
# OR re-install v1.2.0:
git clone -b v1.2.0 https://github.com/supawatnick/netapp_trident_protect_webui_ocp.git
cd netapp_trident_protect_webui_ocp && sudo ./install.sh
```

---

## 7. Files changed since v1.2.0

- `app/main.py` — new routes: `GET .../reverse-wizard-state`, `POST .../reverse-wizard-create-bootstrap-snapshot`. Removed routes: `.../sync`, `.../pre-reverse-direction`, `.../reverse-direction`.
- `app/trident_protect.py` — new helper `get_reverse_wizard_state()`, `trigger_bootstrap_snapshot()`. Removed `trigger_amr_sync()`, `reverse_application_direction()`.
- `app/templates/disaster_recovery.html` — `Reverse Resync` row button (warning), pre-wizard warning modal, 6-step wizard, snapshots list + Step 4 choice UX, Finish (no auto-open-form).
- `app/templates/base.html` — sidebar version `v1.3.0 (Trident v26.6)`.
- **NEW** `uninstall.sh`
- `README.md`, `DOCS.md`, `CHANGELOG.md`, `RELEASE.md` — refreshed
- `make-package.sh` — `CLI_VERSION="26.06.0"`
- `systemd/` (unchanged)
