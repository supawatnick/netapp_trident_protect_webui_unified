# Phase 3 acceptance checklist

Reference: https://docs.netapp.com/us-en/trident/trident-protect/trident-protect-manage-applications.html

## Automated checks

- [x] Application manifest validation tests pass in GitHub Actions
- [x] Python compile and JavaScript syntax checks pass
- [ ] Validate Flask Application API HTTP error contract with mocked CLI errors
- [ ] Confirm all template actions render correctly in a browser

## Remote environment

- [x] Verify SSH authentication to root@10.10.110.75 using /root/.ssh/tridentprotect_dev_ed25519 from ntap_office
- [ ] Inspect /root/netapp_trident_protect_webui_unified Git status, branch, remote and stashes
- [ ] Confirm cluster kubecontext and namespace scopes before any mutation
- [ ] Confirm Application CRD version and Trident Protect controller health
- [ ] Deploy approved branch to the development instance without disrupting existing edits

## Functional acceptance

- [ ] Create Application with metadata labels
- [ ] Create Application spanning multiple namespaces with independent label selectors
- [ ] Create Application with optional cluster-scoped resource selector
- [ ] Create PVC-only Application including PersistentVolumeClaim, PersistentVolume and VolumeSnapshotClass
- [ ] Inspect Application details, readiness and attached resources
- [ ] Update Application through YAML without altering system-managed status fields
- [ ] Test readonly and admin RBAC outcomes
- [ ] Test AppVault readiness with S3 credentials and an actual application
- [ ] Test on-demand Snapshot, Backup, Schedule and non-destructive Restore on a disposable workload
- [ ] Verify graceful API failure/error display and Burgundy/green status semantics
- [ ] Verify audit events for application mutations

Do not call Phase 3 production-ready until these live checks pass. Avoid testing destructive operations against real customer applications.
