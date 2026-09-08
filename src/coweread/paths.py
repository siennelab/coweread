"""路径解析：state/（登录态、书签）与 cache/（章节索引），默认项目根目录，COWEREAD_HOME 可覆盖。"""

from __future__ import annotations

import os
from pathlib import Path


def base_dir() -> Path:
    env = os.environ.get("COWEREAD_HOME")
    if env:
        return Path(env).expanduser()
    # src/coweread/paths.py → 项目根是 parents[2]
    return Path(__file__).resolve().parents[2]


def state_dir() -> Path:
    p = base_dir() / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = base_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_path() -> Path:
    """微信读书登录态（Playwright storage_state）。"""
    return state_dir() / "weread-state.json"
