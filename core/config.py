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


def window_chat_approval(cfg: dict) -> bool:
    """窗口是否开启「对话内授权」：用户在对话里明确同意后，agent 可带 user_confirmed=true 完成授权。

    缺省 **False**（fail-closed）。⚠️ 这是**信任式**通道：服务端无法验证「用户真说了同意」，
    它信任的是 agent 的转述 —— 只对该窗口的**本机/可信 agent** 有意义；网页 AI 窗口不要开。
    """
    return cfg.get("chat_approval") is True


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


def window_bind(cfg: dict) -> str:
    """监听地址。默认 127.0.0.1（只有本机能连）。

    用户可以显式写 "0.0.0.0"：同网段的设备（手机 / 另一台电脑上的 AI 客户端）
    就能用「本机局域网 IP:端口」直连 —— 不用域名、不用隧道。⚠️ 局域网内可见，
    路径里的随机段就是那道弱口令；只在可信网络开。启动时读取（改完要 restart）。
    """
    b = str(cfg.get("bind") or "").strip()
    return b or "127.0.0.1"


def window_json_response(cfg: dict) -> bool:
    """POST 回应用纯 JSON（application/json）而不是 SSE 帧（text/event-stream）。

    给「不吃 SSE 的隧道 / 客户端」用（部分内网穿透、部分老客户端）。
    默认关 —— 标准 MCP 客户端两种都吃，SSE 是默认形态。启动时读取（改完要 restart）。
    """
    return cfg.get("json_response") is True


def window_kb_enabled(cfg: dict) -> bool:
    """受控资料库模式：只注册 kb_* 只读工具，路径型工具一律不注册。默认关（fail-closed）。"""
    kb = cfg.get("kb")
    return isinstance(kb, dict) and kb.get("enabled") is True


def window_kb_docs_dir(cfg: dict) -> str:
    kb = cfg.get("kb")
    return (kb.get("docs_dir") if isinstance(kb, dict) else None) or "原始文档"


def window_kb_levels(cfg: dict) -> list[str]:
    """本窗允许的等级清单（顺序 = 从低到高展示）。空 = 未配置 → 读侧一律拒（fail-closed）。"""
    kb = cfg.get("kb")
    raw = kb.get("levels") if isinstance(kb, dict) else None
    return [str(x).strip() for x in raw if str(x).strip()] if isinstance(raw, list) else []


def window_kb_default_level(cfg: dict) -> str:
    kb = cfg.get("kb") or {}
    d = str(kb.get("default_level") or "").strip()
    return d if d in window_kb_levels(cfg) else (window_kb_levels(cfg)[0] if window_kb_levels(cfg) else "")


def _portal(cfg: dict) -> dict:
    kb = cfg.get("kb") or {}
    p = kb.get("portal")
    return p if isinstance(p, dict) else {}


def window_kb_download(cfg: dict) -> dict:
    """下载设置（kb.download）：enabled / original / link_minutes / max_bundle_mb / max_file_mb。

    没写 = 走 kb_download.DEFAULTS（允许下载原件）。写错类型 = 用默认值（fail-closed 到安全侧）。
    """
    import kb_download
    return kb_download.download_cfg(cfg)


def window_portal_enabled(cfg: dict) -> bool:
    """门户（申请页 + 管理页）是否开启。"""
    return window_kb_enabled(cfg) and _portal(cfg).get("enabled") is True


def window_portal_public_levels(cfg: dict) -> list[str]:
    """申请页允许填写的等级（其余等级只能由维护者手动发放）。默认 = 最低一档。"""
    raw = _portal(cfg).get("public_levels")
    lv = window_kb_levels(cfg)
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x) in lv]
    return lv[:1]


def window_portal_auto_levels(cfg: dict) -> list[str]:
    """**申请即通过**的等级（自动发放地址，不用人批）。

    默认空 = 全都要人工批。用户显式写了才自动 —— 自动档不能超出 public_levels。
    """
    raw = _portal(cfg).get("auto_approve_levels")
    pub = window_portal_public_levels(cfg)
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if str(x) in pub]


def window_portal_admin_remote(cfg: dict) -> bool:
    """管理页是否允许从公网（隧道）访问。默认否 = 仅部署机本机直连。

    打开后管理页靠管理令保护（令牌）；手机上要看申请就开这个。
    """
    return _portal(cfg).get("admin_remote") is True


def lan_ip() -> str:
    """本机在当前网络里的局域网 IP（取不到返回空串）。"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 80))   # 不发包，只为选路
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


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
