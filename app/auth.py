"""Dashboard authentication (local users + LDAP) — separate from OCP cluster profile login.

* Local users: stored in ``auth_users.json`` (project root, gitignored).
  Passwords are hashed via :func:`werkzeug.security.generate_password_hash`.
* LDAP users: authenticated by bind against the configured AD/LDAP server.
  Role is determined by ``admin_group_dns`` membership (``admin`` or ``readonly``).
* Sessions: Flask session carries ``user``, ``role`` and ``source`` (``local``/``ldap``).
* Bootstrap: if ``auth.enabled`` and no local users exist at startup, a default
  ``admin``/``admin123`` user is seeded (logged prominently so it can be rotated).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger("tp-web.auth")

USERS_FILE = Path(__file__).parent.parent / "auth_users.json"

# Valid roles
ROLE_ADMIN = "admin"
ROLE_READONLY = "readonly"
VALID_ROLES = (ROLE_ADMIN, ROLE_READONLY)

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# User store
# ---------------------------------------------------------------------------

def _empty_users() -> dict:
    return {"version": 1, "users": {}}


def _load() -> dict:
    if not USERS_FILE.exists():
        return _empty_users()
    try:
        with USERS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "users" not in data:
            return _empty_users()
        # Normalize
        data.setdefault("version", 1)
        data.setdefault("users", {})
        if not isinstance(data["users"], dict):
            data["users"] = {}
        return data
    except (OSError, json.JSONDecodeError) as e:
        log.error("Failed to read %s: %s — starting with empty store", USERS_FILE, e)
        return _empty_users()


def _save(data: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(USERS_FILE.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
    os.replace(tmp, USERS_FILE)
    try:
        os.chmod(USERS_FILE, 0o600)
    except OSError:
        pass


def list_users(source: Optional[str] = None) -> list[dict]:
    """Return public info for all users (no password hashes).

    If ``source`` is provided (``"local"`` or ``"ldap"``), only matching records
    are returned. The ``source`` field is also normalized to ``"local"`` for
    records that lack one.
    """
    with _lock:
        data = _load()
        out = []
        for username, rec in sorted(data["users"].items()):
            rec_source = rec.get("source", "local")
            if source and rec_source != source:
                continue
            out.append({
                "username": username,
                "role": rec.get("role", ROLE_READONLY),
                "source": rec_source,
                "created_at": rec.get("created_at", ""),
                "last_login": rec.get("last_login", ""),
            })
        return out


def add_local_user(username: str, password: str, role: str) -> tuple[bool, str]:
    username = (username or "").strip().lower()
    if not username:
        return False, "username required"
    # Reject characters that would force the username into the LDAP path
    if "\\" in username or "@" in username:
        return False, "username must not contain '\\' or '@' (reserved for domain users)"
    if not password or len(password) < 4:
        return False, "password must be at least 4 characters"
    if role not in VALID_ROLES:
        return False, f"role must be one of {', '.join(VALID_ROLES)}"
    with _lock:
        data = _load()
        if username in data["users"]:
            return False, f"user '{username}' already exists"
        data["users"][username] = {
            "password_hash": generate_password_hash(password),
            "role": role,
            "source": "local",
            "created_at": _now(),
            "last_login": "",
        }
        _save(data)
    return True, f"user '{username}' created"


def update_local_user(username: str, *, role: Optional[str] = None,
                      password: Optional[str] = None) -> tuple[bool, str]:
    with _lock:
        data = _load()
        rec = data["users"].get(username)
        if not rec:
            return False, f"user '{username}' not found"
        if rec.get("source") != "local":
            return False, "cannot edit a non-local (LDAP) user here"
        if role is not None:
            if role not in VALID_ROLES:
                return False, f"role must be one of {', '.join(VALID_ROLES)}"
            rec["role"] = role
        if password is not None:
            if not password or len(password) < 4:
                return False, "password must be at least 4 characters"
            rec["password_hash"] = generate_password_hash(password)
        data["users"][username] = rec
        _save(data)
    return True, f"user '{username}' updated"


def delete_local_user(username: str) -> tuple[bool, str]:
    with _lock:
        data = _load()
        rec = data["users"].get(username)
        if not rec:
            return False, f"user '{username}' not found"
        if rec.get("source") != "local":
            return False, "cannot delete a non-local (LDAP) user here"
        # Safety: do not let the last admin lock everyone out
        if rec.get("role") == ROLE_ADMIN:
            admins = [u for u, r in data["users"].items() if r.get("role") == ROLE_ADMIN]
            if len(admins) <= 1:
                return False, "cannot delete the last remaining admin"
        del data["users"][username]
        _save(data)
    return True, f"user '{username}' deleted"


def _touch_last_login(username: str) -> None:
    with _lock:
        data = _load()
        rec = data["users"].get(username)
        if rec:
            rec["last_login"] = _now()
            data["users"][username] = rec
            _save(data)


# ---------------------------------------------------------------------------
# Local authentication
# ---------------------------------------------------------------------------

def _authenticate_local(username: str, password: str) -> Optional[dict]:
    """Return user record on local success, else None."""
    with _lock:
        data = _load()
        rec = data["users"].get(username)
        if not rec or rec.get("source") != "local":
            return None
        if not check_password_hash(rec.get("password_hash", ""), password):
            return None
        return {"username": username, "role": rec.get("role", ROLE_READONLY),
                "source": "local"}


# ---------------------------------------------------------------------------
# LDAP authentication
# ---------------------------------------------------------------------------

def _ldap_available() -> bool:
    try:
        import ldap3  # noqa: F401
        return True
    except ImportError:
        return False


def _ldap_server_target(cfg: dict) -> tuple[str, int, bool]:
    """Return (host, port, use_ssl) parsed from ``cfg`` (``server`` may be a URL)."""
    server = (cfg.get("server") or "").strip()
    use_tls = bool(cfg.get("use_tls", False))
    port = int(cfg.get("port") or 389)
    if server.startswith("ldaps://"):
        host = server[len("ldaps://"):].split("/", 1)[0]
        if ":" in host:
            host, p = host.rsplit(":", 1)
            try:
                port = int(p)
            except ValueError:
                pass
        return host, port, True
    if server.startswith("ldap://"):
        host = server[len("ldap://"):].split("/", 1)[0]
        if ":" in host:
            host, p = host.rsplit(":", 1)
            try:
                port = int(p)
            except ValueError:
                pass
        return host, port, False
    # Plain host[:port]
    if ":" in server:
        host, p = server.rsplit(":", 1)
        try:
            port = int(p)
        except ValueError:
            host = server
    else:
        host = server
    return host, port, use_tls


def _ldap_user_dn_and_attrs(server, bind_dn: str, bind_password: str, base_dn: str,
                            user_filter: str, username: str) -> Optional[dict]:
    """Service-account bind then search for the user's DN + memberOf.

    Returns a dict with keys ``dn``, ``memberOf`` (list of group DNs) on success.
    """
    import ldap3
    from ldap3 import Connection, Server, SUBTREE
    from ldap3.core.exceptions import LDAPException

    conn = Connection(
        server,
        user=bind_dn,
        password=bind_password,
        auto_bind=True,
        receive_timeout=10,
    )
    try:
        flt = user_filter.replace("{username}", _escape_filter(username))
        conn.search(
            search_base=base_dn,
            search_filter=flt,
            search_scope=SUBTREE,
            attributes=["distinguishedName", "memberOf", "cn", "sAMAccountName", "uid"],
            size_limit=2,
        )
        if not conn.entries:
            return None
        entry = conn.entries[0]
        member_of = []
        try:
            for v in entry.memberOf.values:
                member_of.append(str(v).lower())
        except (KeyError, AttributeError):
            pass
        return {"dn": str(entry.entry_dn), "memberOf": member_of}
    finally:
        try:
            conn.unbind()
        except Exception:
            pass


def _ldap_user_bind(server, user_dn: str, password: str) -> bool:
    """Attempt a simple bind as the resolved user DN."""
    import ldap3
    from ldap3 import Connection
    from ldap3.core.exceptions import LDAPException

    try:
        conn = Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=True,
            receive_timeout=10,
        )
        conn.unbind()
        return True
    except LDAPException as e:
        log.debug("LDAP user bind failed: %s", e)
        return False
    except Exception as e:
        log.warning("LDAP user bind error: %s", e)
        return False


def _escape_filter(value: str) -> str:
    """Escape an LDAP filter per RFC 4515."""
    return (
        value.replace("\\", "\\5c")
             .replace("*", "\\2a")
             .replace("(", "\\28")
             .replace(")", "\\29")
             .replace("\x00", "\\00")
    )


def _ldap_role_for(user_attrs: dict, auth_cfg: dict) -> str:
    """Return ``admin`` if user is a member of any configured admin group, else
    ``default_role`` (or ``readonly``).

    ``auth_cfg`` is the full auth config (contains both top-level ``default_role``
    and the ``ldap`` sub-dict with ``admin_group_dns``).
    """
    ldap = auth_cfg.get("ldap") or {}
    admin_groups = [g.strip().lower() for g in (ldap.get("admin_group_dns") or []) if g.strip()]
    user_groups = [g.lower() for g in user_attrs.get("memberOf", [])]
    if admin_groups:
        for ag in admin_groups:
            if ag in user_groups:
                return ROLE_ADMIN
    fallback = auth_cfg.get("default_role") or ROLE_READONLY
    return fallback if fallback in VALID_ROLES else ROLE_READONLY


def test_ldap(auth_cfg: dict, username: Optional[str] = None,
              password: Optional[str] = None) -> tuple[bool, str]:
    """Test the LDAP server.

    * If ``username``/``password`` are provided, perform a full end-to-end check
      (service bind → user search → user bind) and return role.
    * If only the server/bind config is provided, just verify the service bind.
    """
    if not _ldap_available():
        return False, "ldap3 not installed (run: pip install ldap3)"
    try:
        import ldap3
        from ldap3 import Server, Connection, Tls
    except ImportError:
        return False, "ldap3 not installed"
    ldap = auth_cfg.get("ldap") or auth_cfg
    host, port, use_ssl = _ldap_server_target(ldap)
    if not host:
        return False, "LDAP server not configured"
    bind_dn = (ldap.get("bind_dn") or "").strip()
    bind_pw = ldap.get("bind_password") or ""
    base_dn = (ldap.get("base_dn") or "").strip()
    user_filter = ldap.get("user_filter") or "(uid={username})"
    skip_cert = bool(ldap.get("skip_cert_verify", True))
    tls = None
    if use_ssl and skip_cert:
        tls = Tls(validate=0)
    try:
        server = Server(host, port=port, use_ssl=use_ssl, tls=tls, connect_timeout=5)
        if not bind_dn:
            # Anonymous bind check
            conn = Connection(server, auto_bind=True, receive_timeout=10)
            conn.unbind()
            return True, f"connected to {host}:{port} (anonymous bind OK)"
        conn = Connection(server, user=bind_dn, password=bind_pw, auto_bind=True,
                          receive_timeout=10)
        conn.unbind()
    except Exception as e:
        return False, f"service bind to {host}:{port} failed: {e}"

    if not (username and password and base_dn):
        return True, f"service bind to {host}:{port} OK"

    # End-to-end: resolve user DN then user bind. Accept both bare and qualified
    # usernames (DOMAIN\user or user@domain); use the bare part for the search.
    bare, _ = _split_login_username(username)

    try:
        user_info = _ldap_user_dn_and_attrs(server, bind_dn, bind_pw, base_dn,
                                            user_filter, bare)
    except Exception as e:
        return False, f"user search failed: {e}"
    if not user_info:
        return False, f"user '{bare}' not found under {base_dn}"
    if not _ldap_user_bind(server, user_info["dn"], password):
        return False, "user bind failed (bad password or account locked)"
    role = _ldap_role_for(user_info, auth_cfg)
    return True, f"authenticated as '{username}', role={role}, dn={user_info['dn']}"


def _authenticate_ldap(auth_cfg: dict, username: str, password: str) -> Optional[dict]:
    if not _ldap_available():
        log.warning("LDAP enabled but ldap3 not installed")
        return None
    ldap = auth_cfg.get("ldap") or {}
    if not ldap.get("enabled"):
        return None
    try:
        import ldap3
        from ldap3 import Server, Connection, Tls
    except ImportError:
        return None
    host, port, use_ssl = _ldap_server_target(ldap)
    if not host:
        log.warning("LDAP enabled but server not set")
        return None
    bind_dn = (ldap.get("bind_dn") or "").strip()
    bind_pw = ldap.get("bind_password") or ""
    base_dn = (ldap.get("base_dn") or "").strip()
    user_filter = ldap.get("user_filter") or "(uid={username})"
    skip_cert = bool(ldap.get("skip_cert_verify", True))
    tls = None
    if use_ssl and skip_cert:
        tls = Tls(validate=0)
    try:
        server = Server(host, port=port, use_ssl=use_ssl, tls=tls, connect_timeout=5)
    except Exception as e:
        log.warning("LDAP server construct failed: %s", e)
        return None
    if not (bind_dn and base_dn):
        log.warning("LDAP missing bind_dn or base_dn")
        return None
    try:
        user_info = _ldap_user_dn_and_attrs(server, bind_dn, bind_pw, base_dn,
                                            user_filter, username)
    except Exception as e:
        log.warning("LDAP user search failed: %s", e)
        return None
    if not user_info:
        return None
    if not _ldap_user_bind(server, user_info["dn"], password):
        return None
    role = _ldap_role_for(user_info, auth_cfg)
    return {"username": username, "role": role, "source": "ldap"}


# ---------------------------------------------------------------------------
# Public authenticate
# ---------------------------------------------------------------------------

def _split_login_username(raw: str) -> tuple[str, bool]:
    """Split a login input into (bare_username, qualified).

    * ``DOMAIN\\user`` (netbios) → ``(user, True)``
    * ``user@domain`` (UPN) → ``(user, True)``
    * anything else → ``(raw, False)`` (treated as local)
    """
    raw = (raw or "").strip()
    if "\\" in raw:
        return raw.rsplit("\\", 1)[1].strip(), True
    if "@" in raw:
        return raw.split("@", 1)[0].strip(), True
    return raw, False


def _canonical_ldap_key(raw: str, netbios_domain: str) -> str:
    """Build a canonical storage key for a qualified login input.

    Both ``DOMAIN\\user`` and ``user@domain`` for the same AD user should map
    to the same record. The canonical form is ``{netbios_domain}\\\\user`` in
    lower case. When the input is already a netbios form (``DOMAIN\\\\user``)
    we keep it. When it is a UPN form (``user@domain``) we replace the UPN
    suffix with the configured netbios domain.

    Falls back to the lowercased raw input if the netbios domain is not
    configured (UPN records will not dedupe in that case, but login still
    works).
    """
    raw = (raw or "").strip()
    if not raw:
        return raw
    bare, _ = _split_login_username(raw)
    nb = (netbios_domain or "").strip().lower()
    if not nb:
        return raw.lower()
    if "\\" in raw:
        return raw.lower()
    return f"{nb}\\{bare.lower()}"


def _upsert_ldap_record(username: str, role: str) -> None:
    """Create or update an LDAP user record keyed by ``username``.

    Refuses to overwrite an existing **local** user record (defensive — the
    canonical key was chosen to avoid collisions, but if one does exist we
    keep the local record intact and update nothing).
    """
    with _lock:
        data = _load()
        rec = data["users"].get(username)
        if rec is None:
            data["users"][username] = {
                "role": role,
                "source": "ldap",
                "created_at": _now(),
                "last_login": _now(),
            }
        elif rec.get("source") == "local":
            log.warning("Refusing to overwrite local user record '%s' with LDAP data", username)
            return
        else:
            rec["last_login"] = _now()
            rec["role"] = role
            data["users"][username] = rec
        _save(data)


def authenticate(auth_cfg: dict, username: str, password: str) -> Optional[dict]:
    """Authenticate a login attempt.

    * A bare username (no ``\\`` or ``@``) is treated as a **local** user only.
    * A qualified name (``DOMAIN\\user`` or ``user@domain``) is routed to **LDAP**
      only. If LDAP is disabled or authentication fails, the result is ``None``
      (no fallback to local — this disambiguates same-named local and domain users).
    * LDAP records are stored under the canonical key ``{netbios_domain}\\\\user``
      (lower case), so UPN and netbios forms of the same AD user share a single
      record. ``netbios_domain`` is read from ``auth_cfg['ldap']['netbios_domain']``
      (or the top-level ``auth.netbios_domain``); when not set the raw input is
      used as the key (no dedupe across UPN/netbios).
    """
    raw = (username or "").strip()
    bare, qualified = _split_login_username(raw)

    if not qualified:
        # Local only
        user = _authenticate_local(raw, password)
        if user:
            _touch_last_login(raw)
        return user

    # Qualified: LDAP only
    user = _authenticate_ldap(auth_cfg or {}, bare, password)
    if not user:
        return None
    netbios = ((auth_cfg or {}).get("ldap") or {}).get("netbios_domain") \
        or (auth_cfg or {}).get("netbios_domain") or ""
    stored = _canonical_ldap_key(raw, netbios)
    user = {**user, "username": stored}
    _upsert_ldap_record(stored, user["role"])
    return user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_default_admin_if_empty() -> None:
    """Create a default admin if auth is enabled and no users exist.

    Logs a prominent reminder to rotate the password. Idempotent.
    """
    from .config import Config
    cfg = Config.instance()
    auth_cfg = cfg.auth or {}
    if not auth_cfg.get("enabled", False):
        return
    with _lock:
        data = _load()
        if data["users"]:
            return
        data["users"]["admin"] = {
            "password_hash": generate_password_hash("admin123"),
            "role": ROLE_ADMIN,
            "source": "local",
            "created_at": _now(),
            "last_login": "",
        }
        _save(data)
    log.warning("=" * 70)
    log.warning("Bootstrap: seeded default dashboard user 'admin' / 'admin123'.")
    log.warning("CHANGE THIS PASSWORD via Settings → Credentials immediately.")
    log.warning("=" * 70)
