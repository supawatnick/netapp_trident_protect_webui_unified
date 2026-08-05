"""Audit log — JSON-lines append-only writer + filter/paginate query.

Records every mutating action performed through the Web UI (which user,
what action, which resource, success/fail). Stored at
``<project root>/logs/audit.log`` as one JSON object per line. The file is
gitignored (``*.log``).

* Writes are serialized with a ``threading.Lock`` (Flask dev server is
  threaded; an atomic append for a small line is POSIX-safe but a lock
  keeps line boundaries unambiguous if a concurrent writer interleaves).
* Auto-prune keeps the file bounded: when the line count exceeds
  ``max_rows`` (from config ``logs.max_rows``) the file is rewritten
  retaining only the last ``max_rows`` entries. Pruning happens at
  startup and every ``PRUNE_EVERY`` writes to amortize cost.
* ``query()`` does a single linear scan, filters in memory, paginates
  newest-first. For the configured cap (~10k lines) this is well under
  a millisecond.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("tp-web.audit")

# Project root = parent of the app/ package directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
AUDIT_FILE = LOGS_DIR / "audit.log"

# How often to check + prune. Pruning rewrites the file, which is O(n),
# so we amortize by only doing it every N writes.
PRUNE_EVERY = 200

# How long response detail messages are kept (chars). 500 is enough to
# show e.g. "Schedule my-schedule/my-namespace enabled" while bounding
# memory + log size.
DETAIL_MAX_CHARS = 500

# Sentinel user identity for unauthenticated / blocked requests.
ANON_USER = "-"

_lock = threading.RLock()

# Counters used to amortize the prune check.
_writes_since_prune = 0
_pruned_at_startup = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """Return whether audit logging is currently enabled (from config)."""
    try:
        from .config import Config
        logs_cfg = (Config.instance().logs or {})
    except Exception:
        # Config not ready (early import) — default to enabled.
        return True
    # Missing or empty section => enabled by default.
    if not logs_cfg:
        return True
    return bool(logs_cfg.get("enabled", True))


def max_rows() -> int:
    """Return the configured cap. 0 means unlimited."""
    try:
        from .config import Config
        logs_cfg = (Config.instance().logs or {})
    except Exception:
        return 10000
    if not logs_cfg:
        return 10000
    try:
        return int(logs_cfg.get("max_rows", 10000))
    except (TypeError, ValueError):
        return 10000


def write(record: dict) -> None:
    """Append one record to the audit log (no-op if disabled)."""
    if not is_enabled():
        return
    try:
        _ensure_dir()
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with _lock:
            with AUDIT_FILE.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            global _writes_since_prune, _pruned_at_startup
            _writes_since_prune += 1
            cap = max_rows()
            if cap > 0:
                if (not _pruned_at_startup) or _writes_since_prune >= PRUNE_EVERY:
                    _prune_if_needed(cap)
                    _writes_since_prune = 0
                    _pruned_at_startup = True
    except OSError as e:
        # Audit failure must never break a request.
        log.warning("audit write failed: %s", e)


def build_record(
    *,
    user: Optional[str],
    role: Optional[str],
    source: Optional[str],
    action: str,
    kind: str,
    namespace: str = "",
    name: str = "",
    detail: str = "",
    ip: str = "",
    method: str = "",
    path: str = "",
    status: int = 0,
    ok: bool = True,
) -> dict:
    """Normalize a record dict. Truncates ``detail`` and fills defaults."""
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user": (user or ANON_USER),
        "role": (role or "-"),
        "source": (source or "-"),
        "action": action,
        "kind": kind,
        "namespace": namespace or "",
        "name": name or "",
        "detail": (detail or "")[:DETAIL_MAX_CHARS],
        "ip": ip or "",
        "method": method or "",
        "path": path or "",
        "status": int(status or 0),
        "ok": bool(ok),
    }


def query(
    *,
    user: str = "",
    action: str = "",
    kind: str = "",
    result: str = "",   # "ok" | "fail" | ""
    q: str = "",        # free text across user/name/namespace/detail
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Return ``{items, total, users}`` (newest first).

    ``items`` is already paginated. ``total`` is the filtered match count.
    ``users`` is the list of distinct usernames (most recent first, capped
    at 50) — used to populate the user filter dropdown.
    """
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    user_q = (user or "").strip().lower()
    action_q = (action or "").strip().lower()
    kind_q = (kind or "").strip().lower()
    result_q = (result or "").strip().lower()
    free_q = (q or "").strip().lower()

    items: list[dict] = []
    total = 0
    users_seen: list[str] = []
    users_set: set[str] = set()

    if not AUDIT_FILE.exists():
        return {"items": [], "total": 0, "users": []}

    try:
        # Read all lines, newest first. Cheap up to ~10k lines; the
        # configured prune keeps the file bounded.
        with _lock:
            with AUDIT_FILE.open("r", encoding="utf-8") as f:
                lines = f.readlines()
    except OSError as e:
        log.warning("audit read failed: %s", e)
        return {"items": [], "total": 0, "users": []}

    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue

        rec_user = str(rec.get("user", "")).lower()
        rec_action = str(rec.get("action", "")).lower()
        rec_kind = str(rec.get("kind", "")).lower()
        rec_ok = bool(rec.get("ok", True))
        rec_result = "ok" if rec_ok else "fail"

        if user_q and user_q not in rec_user:
            continue
        if action_q and rec_action != action_q:
            continue
        if kind_q and rec_kind != kind_q:
            continue
        if result_q and rec_result != result_q:
            continue
        if free_q:
            hay = " ".join([
                rec_user,
                str(rec.get("name", "")).lower(),
                str(rec.get("namespace", "")).lower(),
                str(rec.get("detail", "")).lower(),
            ])
            if free_q not in hay:
                continue

        total += 1

        # Distinct users, most recent first, capped.
        u = str(rec.get("user", "")) or ANON_USER
        if u not in users_set:
            users_set.add(u)
            users_seen.append(u)
            if len(users_seen) >= 50:
                pass  # keep scanning for total, but no more user entries

        if total > offset and len(items) < limit:
            items.append(rec)

    return {"items": items, "total": total, "users": users_seen}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _ensure_dir() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    # Best-effort: keep the audit log readable only by the service user.
    try:
        os.chmod(LOGS_DIR, 0o750)
    except OSError:
        pass


def _prune_if_needed(cap: int) -> None:
    """If the audit file exceeds ``cap`` lines, keep only the last ``cap``."""
    try:
        # Count lines cheaply with a buffer scan (no json decode yet).
        with AUDIT_FILE.open("rb") as f:
            data = f.read()
    except OSError:
        return
    if not data:
        return
    # Fast line count on bytes; trailing newline is fine.
    line_count = data.count(b"\n")
    if line_count <= cap:
        return
    # Drop oldest (cap) lines from the end by trimming the head. JSON
    # lines are newline-delimited so this preserves record boundaries.
    # Find the offset where the last `cap` lines start.
    keep_from = -cap
    # Count newlines from the tail until we've skipped (line_count - cap)
    # of them, then start keeping.
    to_skip = line_count - cap
    idx = 0
    skipped = 0
    for i, b in enumerate(data):
        if b == 10:  # b"\n"
            skipped += 1
            if skipped == to_skip:
                idx = i + 1
                break
    if idx <= 0 or idx >= len(data):
        return
    new_data = data[idx:]
    tmp = AUDIT_FILE.with_suffix(AUDIT_FILE.suffix + ".tmp")
    try:
        with tmp.open("wb") as f:
            f.write(new_data)
        os.replace(tmp, AUDIT_FILE)
        try:
            os.chmod(AUDIT_FILE, 0o640)
        except OSError:
            pass
        log.info("audit log pruned to last %d lines", cap)
    except OSError as e:
        log.warning("audit prune failed: %s", e)
