# Changelog

## v2.0.0 (2026-08-05) — Unified Edition

**The unified edition merges `netapp_trident_protect_webui_ocp` (v1.4.18) and
`netapp_trident_protect_webui_k8s` (v1.4.18) into a single codebase supporting
both OpenShift Container Platform and vanilla Kubernetes.**

### Major changes

- **Cluster auth: token-only.** Username/password login removed from
  `POST /api/settings/login`. Bearer tokens or kubeconfig import are now the
  only supported paths. The API explicitly rejects any payload containing
  `username` or `password` keys (returns `400 Bad Request`).
- **Platform auto-detection.** The CLI runner detects `oc` vs `kubectl` per
  profile. Auto-detected via `_autodetect_platform()` and exposed via
  `GET /api/platform` (`ocp` or `k8s`).
- **Multi-cluster:** up to 2 profiles (DR source + destination).

### What's new vs v1.4.18 (OCP / k8s)

- Unified `app/main.py` (1529 lines, k8s base with platform detection + OCP-only extras removed).
- Unified `app/trident_protect.py` (2279 lines, k8s base with platform-aware CLI).
- 15 templates (settings_clusters.html is token-only — k8s version).
- 5 vanilla JS files + 1 NetApp CSS file.
- 4 sample CRs under `yaml/` (port from OCP).
- 3 restore walkthroughs under `restore/trident-protect-{1,2,4}/` (port from OCP).
- `DOCS.md` (port from OCP) — comprehensive architecture + API reference.
- `RELEASE.md` (port from OCP) — release procedure template.

### Token-only enforcement

```bash
# Old (v1.4.18 OCP): username/password works
curl -X POST /api/settings/login \
  -d '{"name":"prod","username":"kubeadmin","password":"..."}'

# New (v2.0.0 unified): rejected
curl -X POST /api/settings/login \
  -d '{"name":"prod","username":"kubeadmin","password":"..."}'
# → 400 {"ok":false,"message":"Cluster login is token-only..."}

# New (v2.0.0 unified): bearer token works
curl -X POST /api/settings/login \
  -d '{"name":"prod","token":"sha256~..."}'
# → 200 {"ok":true,...}
```

### Compatibility

| | OCP v1.4.18 | k8s v1.4.18 | Unified v2.0.0 |
|---|---|---|---|
| OpenShift 4.x | ✓ | ✗ | ✓ (via `oc` auto-detect) |
| Kubernetes 1.28-1.31 | ✗ | ✓ | ✓ (via `kubectl` auto-detect) |
| Username/password login | ✓ | ✗ | ✗ (rejected) |
| Bearer token login | ✓ | ✓ | ✓ |
| Kubeconfig import | ✗ | ✓ | ✓ |
| AppVault test endpoint | ✗ | ✓ | ✓ |
| All 4 restore kinds (inplace + cross-NS) | ✓ | ✓ | ✓ |
| DR / AppMirrorRelationship | ✓ | ✓ | ✓ |
| Reverse Resync wizard (5-step) | ✓ | ✓ | ✓ |
| Per-cluster Trident version banner | ✓ | ✓ | ✓ |
| Max 2 cluster profiles | ✓ | ✓ | ✓ |

### Credits

Unified edition built from the v1.4.18 OCP and k8s sister repositories.
