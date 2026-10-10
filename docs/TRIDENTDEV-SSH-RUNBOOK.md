# TRIDENTDEV SSH and Phase 3 Handoff Runbook

> **Read this first when opening a new ChatGPT chat for the Trident Protect Unified Web UI project.**
> Verified on 2026-10-10 from the `ntap_office` host. This runbook documents access instructions, not credentials; do not commit private key contents, tokens or kubeconfig secrets.

## Target project and runtime host

- GitHub: https://github.com/supawatnick/netapp_trident_protect_webui_unified
- Remote development host IP: `10.10.110.75`
- Verified remote hostname: `tridentprotect-dev`
- SSH user: `root`
- Orchestration entry point: `ntap_office` / lnwjud remote tools
- **The active ntap_office workspace `/root/src` contains the unrelated `lnwjud` repository. Do not modify that repository expecting to change the Trident Web UI.**
- The local checkout path on `tridentprotect-dev` **has not yet been verified**. Inspect it before changing code, deploying or running cluster mutations.

## Verified working SSH connection

The existing private identity file **on the ntap_office host** is:

```text
/root/.ssh/tridentprotect_dev_ed25519
```

Working command (verified twice on 2026-10-10):

```bash
ssh -i /root/.ssh/tridentprotect_dev_ed25519 \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  root@10.10.110.75 hostname
```

Expected output:

```text
tridentprotect-dev
```

**Do not try guessed usernames or unqualified `ssh root@10.10.110.75` before this verified command.** A plain SSH command was rejected with `Permission denied (publickey,password)`; the explicit key worked.

Suggested SSH alias configuration (not yet applied):

```sshconfig
Host tridentdev
    HostName 10.10.110.75
    User root
    IdentityFile /root/.ssh/tridentprotect_dev_ed25519
    IdentitiesOnly yes
    BatchMode yes
    StrictHostKeyChecking accept-new
```

### Old SSH identity cleanup — NOT completed

The pre-existing `/root/.ssh/config` contains the obsolete alias `nizki-demeter` referencing `/root/.ssh/demeter_nizki_ed25519`. That identity failed authentication to the Trident host. The user explicitly authorized removal of these obsolete files:

- `/root/.ssh/demeter_nizki_ed25519`
- `/root/.ssh/demeter_nizki_ed25519.pub`

**However neither file has been deleted and the SSH config has not been edited.** The `ntap_office` guarded file tools rejected changes outside the active workspace. Use an approved host-administration method to update the config and remove only the two obsolete identity files. Verify the working `tridentprotect_dev_ed25519` key remains intact. Do not claim the cleanup is finished until actually verified.

## Mandatory steps at the start of a new chat

1. Read this runbook in the repository first.
2. Use `ntap_office` SSH to verify `hostname` on `root@10.10.110.75` with the explicit working identity.
3. Locate the remote checkout on `tridentprotect-dev`. Confirm its `git remote -v`, `git status --short --branch`, `git log -1 --oneline`, and `git stash list` before making changes.
4. Read the **remote** implementation rather than assuming GitHub `main` and the remote working tree are identical.
5. For runtime tests, discover the current kubecontext and namespace and run read-only discovery first. Do not create/delete production Kubernetes CRs before inspecting the target environment.
6. Keep local changes and stashes intact. No `git reset --hard`, `git clean`, or force-push as a shortcut. Follow existing Flask/Jinja2/vanilla JS architecture unless a deliberate migration is approved.

## Phase 3 development context

Scope: **Application Management foundation and integration with AppVault, Snapshot, Backup, Restore and Schedules**.

At the time of inspection, GitHub `main` already had Application List, Create (YAML/Form), Detail and Delete, with Flask routes in `app/main.py`, helpers in `app/trident_protect.py`, and UI in `app/templates/applications.html`. **Do not implement a duplicate CRUD module.** First assess missing functionality and the remote source.

Known next review items:
- Handle HTTP/API failures in the Application List explicitly; avoid reporting failures as an empty application list.
- Avoid inline JavaScript handlers that embed Kubernetes-controlled names into JS strings; prefer delegated events and `data-*` attributes.
- Normalize health/protection state coloring: Burgundy-style red for error/unavailable; green for healthy/available; neutral for unknown/pending.
- Preserve desired menu behavior: AppVault/S3 submenus collapse when navigation moves to another menu, and reuse this behavior for future menus.
- Verify Application creation on the actual target cluster; then revisit the earlier AppVault/S3 end-to-end test checklist.
- Respect read-only vs admin RBAC boundaries, audit logging and production-cluster safeguards.

**Status of Phase 3 changes:** No new Phase 3 code was deployed or committed as part of the SSH investigation on 2026-10-10. Do not mistake the investigation for a completed implementation.

## Additional continuity

An orchestration-side reference file was created at `/root/src/docs/TRIDENTDEV-SSH-RUNBOOK.md`, but **this repository copy is the canonical handoff** for future chats.
