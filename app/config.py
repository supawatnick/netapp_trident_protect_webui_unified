"""Configuration loader for Trident Protect Web UI."""
import os
import yaml
from pathlib import Path


class Config:
    """Singleton config object loaded from config.yaml."""

    _instance = None

    def __init__(self):
        config_path = os.environ.get("TP_CONFIG", str(Path(__file__).parent.parent / "config.yaml"))
        if not os.path.exists(config_path):
            raise FileNotFoundError(
                f"Config not found at {config_path}. "
                f"Copy config.example.yaml to {config_path} and edit it."
            )
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        self.app = data.get("app", {})
        self.cli = data.get("cli", {})
        self.refresh = data.get("refresh", {})
        self.appvault = data.get("appvault", {})

        # Audit log settings (enabled by default when section is absent)
        self.logs = data.get("logs", {})

        # Multi-cluster profiles
        self.clusters = data.get("clusters", {})
        self.active_cluster = self.clusters.get("active", "")
        self.profiles = self.clusters.get("profiles", {}) or {}

        # Active profile (or empty if no profiles)
        self.active_profile = self.profiles.get(self.active_cluster, {})

        # Auth (dashboard login + LDAP)
        # Default to enabled=False so legacy installs keep working until operator opts in.
        self.auth = data.get("auth") or {}

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reload(cls):
        """Reload config from disk (after Settings save)."""
        cls._instance = None
        return cls.instance()

    @staticmethod
    def config_path() -> Path:
        return Path(os.environ.get("TP_CONFIG", str(Path(__file__).parent.parent / "config.yaml")))

    def save(self) -> None:
        """Save current state back to config.yaml."""
        data = {
            "app": self.app,
            "cli": self.cli,
            "refresh": self.refresh,
            "appvault": self.appvault,
            "clusters": {
                "active": self.active_cluster,
                "profiles": self.profiles,
            },
        }
        # Preserve logs section if it was loaded
        if getattr(self, "logs", None):
            data["logs"] = self.logs
        # Preserve auth section if it was loaded (LDAP config lives here)
        if getattr(self, "auth", None):
            data["auth"] = self.auth
        path = self.config_path()
        with open(path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        Config.reload()

    def save_auth(self, auth: dict) -> None:
        """Persist auth section (LDAP config, enabled flag) and reload."""
        self.auth = auth or {}
        self.save()


def get_kubecontext(profile_name: str | None = None) -> str | None:
    """Return kubeconfig context name for a profile, or None if not set.

    If profile_name is None, use the active profile.
    """
    cfg = Config.instance()
    name = profile_name or cfg.active_cluster
    profile = cfg.profiles.get(name, {})
    if not profile:
        return None
    return profile.get("kubecontext") or profile.get("kubeconfig_context") or None
