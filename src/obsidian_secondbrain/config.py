"""Vault configuration.

Everything about the second-brain *taxonomy* lives in data, not code:
a JSON file inside the vault at `.secondbrain/config.json`. Edit that file to
reshape the method without touching the server.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_DIR = ".secondbrain"
CONFIG_FILE = "config.json"

# Karpathy-style default: a raw append-only capture stream that is never
# rewritten, plus a distilled layer of atomic notes the agent maintains.
DEFAULT_CONFIG: dict = {
    "folders": {
        # raw, append-only. Never edited, only distilled.
        "inbox": "00-Inbox",
        "log": "10-Log",
        "sessions": "20-Sessions",
        # distilled, agent-maintained
        "notes": "30-Notes",
        "maps": "40-Maps",
        "archive": "90-Archive",
    },
    "daily_note_format": "%Y-%m-%d",
    "session_note_format": "%Y-%m-%d-%H%M",
    # Frontmatter key that marks a raw capture as already distilled.
    "distilled_key": "distilled",
    # Files/dirs never touched by read/search/write.
    "exclude": [".obsidian", ".trash", ".git", ".secondbrain"],
    "note_extension": ".md",
    # Written into the vault README so the method is self-documenting.
    "method": "append-only capture -> agent distillation -> atomic linked notes",
}


class ConfigError(RuntimeError):
    pass


@dataclass
class VaultConfig:
    root: Path
    data: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_CONFIG)))

    # -- folder accessors -------------------------------------------------
    def folder(self, key: str) -> Path:
        try:
            name = self.data["folders"][key]
        except KeyError as exc:  # pragma: no cover - guarded by schema
            raise ConfigError(f"unknown folder key {key!r}") from exc
        return self.root / name

    @property
    def folders(self) -> dict[str, str]:
        return dict(self.data["folders"])

    @property
    def exclude(self) -> set[str]:
        return set(self.data.get("exclude", []))

    @property
    def ext(self) -> str:
        return self.data.get("note_extension", ".md")

    @property
    def distilled_key(self) -> str:
        return self.data.get("distilled_key", "distilled")

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    # -- persistence ------------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_DIR / CONFIG_FILE

    def save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def ensure_folders(self) -> list[str]:
        """Create any missing structural folders. Returns the ones created."""
        created = []
        for name in self.data["folders"].values():
            p = self.root / name
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created.append(name)
        return created


def _resolve_root(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("OBSIDIAN_VAULT")
    if not raw:
        raise ConfigError(
            "No vault path. Set the OBSIDIAN_VAULT environment variable in your "
            "MCP server config to the absolute path of your Obsidian vault."
        )
    root = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
    if not root.exists():
        raise ConfigError(f"Vault path does not exist: {root}")
    if not root.is_dir():
        raise ConfigError(f"Vault path is not a directory: {root}")
    return root


def load_config(explicit_root: str | None = None) -> VaultConfig:
    root = _resolve_root(explicit_root)
    cfg = VaultConfig(root=root)
    path = cfg.config_path
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Malformed {path}: {exc}") from exc
        # shallow merge, with folders merged one level deeper
        folders = {**cfg.data["folders"], **user.get("folders", {})}
        cfg.data.update(user)
        cfg.data["folders"] = folders
    return cfg
