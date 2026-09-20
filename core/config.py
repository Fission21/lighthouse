#!/usr/bin/env python3
"""灯塔 全局配置 —— config.json 读写（带默认值，缺字段不报错）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
# 优先用本机私有配置（*.local.json，不进版本库）；没有就用仓库里的示例配置
_local_cfg = ROOT_DIR / "config.local.json"
_local_reg = ROOT_DIR / "windows.local.json"
CONFIG_PATH = Path(os.environ.get("LIGHTHOUSE_CONFIG",
                                  str(_local_cfg if _local_cfg.exists() else ROOT_DIR / "config.json")))
# 注册表路径：LIGHTHOUSE_REGISTRY（CLI）> WINDOW_REGISTRY（服务定义里用的）> 本机私有 > 仓库示例。
# 两者都认，是为了让 CLI 改的和正在跑的服务读的是**同一个文件**——否则会出现「命令说改好了、
# 服务还照旧」的静默不一致。
REGISTRY_PATH = Path(os.environ.get(
    "LIGHTHOUSE_REGISTRY",
    os.environ.get("WINDOW_REGISTRY",
                   str(_local_reg if _local_reg.exists() else ROOT_DIR / "windows.json"))))

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


# ---------------------------------------------------------------- 提权策略（用户声明）
def clean_patterns(val) -> list[str]:
    """只留安全的相对 glob（滤掉绝对路径 / `..` / 超长）。类型不对 → []（fail-closed）。"""
    if not isinstance(val, list):
        return []
    out = []
    for p in val:
        if not isinstance(p, str):
            continue
        p = p.strip()
        if not p or len(p) > 200 or p.startswith("/") or ".." in p:
            continue
        out.append(p)
    return out


def window_auto_grant(cfg: dict) -> bool:
    """窗口是否开了「申请即授予」。缺省 **False** —— 默认一切提权都要用户批（fail-closed）。"""
    return cfg.get("auto_grant") is True


def window_ceiling(cfg: dict) -> list[str]:
    """常驻自动授权上限：只有申请范围完整落在里面才会自动生效。空 = 不限（仍受拉黑/exclude 约束）。"""
    return clean_patterns(cfg.get("elevation_ceiling"))


def window_auto_grant_ttl(cfg: dict) -> int | None:
    """常驻策略里「自动授予的时长」（分钟数）；缺省 / 可疑 = None（无期限，维持老行为）。

    注意：它只是「授多久」的旋钮，不是权限边界 —— 边界由 auto_grant / ceiling 决定
    （那两个都 fail-closed）。类型不对就当没写：避免手改配置写错一个字，把自动授予搞成不好用。
    """
    raw = cfg.get("auto_grant_ttl_minutes")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return None


def auto_grant_policy(cfg: dict) -> tuple[bool, list[str]]:
    """(是否自动授予, 上限)。

    fail-closed：`auto_grant` 不是显式 true、或上限配置有任何可疑（类型错 / 绝对路径 / `..`
    / 写了项全被清洗掉）→ 一律退化成 (False, [])，回到「必须用户批准」。
    """
    if cfg.get("auto_grant") is not True:
        return False, []
    raw = cfg.get("elevation_ceiling")
    if raw is None:
        return True, []
    if not isinstance(raw, list) or not raw:
        return False, []
    clean = clean_patterns(raw)
    if len(clean) != len(raw):
        return False, []
    return True, clean


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
