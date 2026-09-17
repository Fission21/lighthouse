#!/usr/bin/env python3
"""灯塔 全局配置 —— config.json 读写（带默认值，缺字段不报错）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("LIGHTHOUSE_CONFIG", str(ROOT_DIR / "config.json")))
REGISTRY_PATH = Path(os.environ.get("LIGHTHOUSE_REGISTRY", str(ROOT_DIR / "windows.json")))

DEFAULTS: dict = {
    "hostname": "mcp.example.com",
    "spare_hostname": "",
    "tunnel_name": "lighthouse",
    "tunnel_id": "",
    "cloudflared_config": "~/.cloudflared/lighthouse.yml",
    "state_dir": "~/.lighthouse",
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def save(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def state_dir() -> Path:
    """状态目录：审计 / 备份 / 写开关 / 日志都放这儿。"""
    return Path(os.path.expanduser(os.environ.get("LIGHTHOUSE_STATE", load()["state_dir"])))


def windows(include_disabled: bool = False) -> dict:
    """读窗口注册表；默认只返回 enabled 的窗口。"""
    try:
        reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["windows"]
    except (OSError, json.JSONDecodeError, KeyError):
        return {}
    if include_disabled:
        return reg
    return {k: v for k, v in reg.items() if v.get("enabled", True)}


def window_root(cfg: dict) -> Path:
    """窗口根目录（相对路径按仓库根解析）。"""
    p = Path(os.path.expanduser(cfg["root"]))
    if not p.is_absolute():
        p = REGISTRY_PATH.parent / p
    return p.resolve()


def window_url(cfg: dict, win_id: str, public: bool = False) -> str:
    if public:
        return f"https://{load()['hostname']}{cfg['path']}"
    return f"http://127.0.0.1:{cfg['port']}{cfg['path']}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "show":
        print(json.dumps(load(), ensure_ascii=False, indent=2))
        print("状态目录:", state_dir())
        print("窗口:", list(windows()))
