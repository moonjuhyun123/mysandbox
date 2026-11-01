"""Configuration loading: built-in defaults deep-merged with config/settings.toml."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

try:                                   # Python 3.11+
    import tomllib
except ModuleNotFoundError:            # Python 3.10 (Ubuntu 22.04)
    import tomli as tomllib

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config" / "settings.toml"

DEFAULTS: dict[str, Any] = {
    "general": {
        "db_path": "data/humor.db",
        "log_dir": "data/logs",
        "export_dir": "data/exports",
        "lock_file": "data/collector.lock",
        "user_agent": "humor-collector/0.1 (personal research; respects robots.txt)",
        "request_timeout": 20.0,
        "max_retries": 3,
        "backoff_base": 2.0,
        "default_delay": 3.0,
        "respect_robots": True,
        "blocked_cooldown_hours": 12,
        "detail_max_attempts": 3,
        "check_cache_hours": 168,
    },
    "scoring": {},
    "dedup": {},
    "output": {"exclude_flags": ["off_topic", "nsfw", "politics", "sensitive"]},
    "sources": {},
}


def load_toml(path: str | Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    settings = deep_merge(DEFAULTS, load_toml(config_path) if config_path.exists() else {})
    general = settings["general"]
    for key in ("db_path", "log_dir", "export_dir", "lock_file"):
        p = Path(general[key])
        general[key] = str(p if p.is_absolute() else PROJECT_DIR / p)
    return settings
