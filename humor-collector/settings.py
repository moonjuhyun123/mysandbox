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
        "check_cache_hours": 24,
        "recheck_cache_hours": 168,
    },
    "scoring": {},
    "dedup": {},
    "output": {"exclude_flags": ["off_topic", "nsfw", "politics", "sensitive"]},
    "sources": {},
    "studio": {
        "classics_path": "reference/classics.toml",
        "panels": 4,
        "max_roles": 3,
        "max_lines_per_panel": 2,
        "max_line_chars": 24,
        "max_narration_chars": 30,
        "max_hook_chars": 20,
        "max_hashtags": 5,
        "max_classics": 1,
        "max_slang": 2,
        "max_scene_texts": 1,
        "max_scene_text_chars": 14,
        "max_text_items": 4,
        "max_common_run": 12,
        "max_line_ratio": 85.0,
        "warn_jaccard": 0.30,
        "not_banned": ["합시다", "쓰레기", "골목식당"],
        "variants": 3,
        "max_fix_rounds": 2,
        "max_source_chars": 3000,
        "min_body_chars": 60,
        "classic_cooldown": 15,
        "llm": {"enabled": False, "model": "claude-opus-5-5", "effort": "high", "max_tokens": 32000},
        "format": {"layout": "carousel", "canvas": "1080x1440", "safe_margin_x": 34, "safe_margin_y": 45,
                   "min_text_px": 48, "closing_slide": False},
        "production": {"art": "ai", "script": "llm+human", "disclosure": "그림과 글에 AI를 사용했습니다."},
    },
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
