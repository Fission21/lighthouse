#!/usr/bin/env python3
"""灯塔·范围授权（提权）—— 把「能不能看更多」的权利交给主人。

三层状态（都带过期时间，默认就都是空的）：

  grant    已授予的额外范围       —— 由主人批准后写入，到期自动失效
  pending  agent 提出的申请        —— agent 只能「申请」，不能自己批
  arm      预授权窗口（可选）      —— 主人先开一个 N 分钟的窗口，期间 agent 的申请可被自动批准
                                      （窗口内可限定「最多能提到多大范围」）

原则：
  · agent 的 `request_access` 在没有 arm 的情况下只会得到 pending —— 它永远无法自我提权。
  · grant 只做「加宽 include」；**exclude 与默认拉黑永远压过 grant**（密钥类、你排除的目录照样看不到）。
  · 一切申请/批准/失效都写审计。

状态文件：<状态目录>/state/window-scope.json（默认 ~/.lighthouse）
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_ROOT = Path(os.path.expanduser(os.environ.get("LIGHTHOUSE_STATE", "~/.lighthouse")))
SCOPE_PATH = STATE_ROOT / "state" / "window-scope.json"
CST = timezone(timedelta(hours=8))

_cache: dict = {"mtime": None, "data": {}}


def _now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def load() -> dict:
    """读状态（带 mtime 缓存：文件没变就不重复读）。"""
    try:
        mt = SCOPE_PATH.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] == mt:
        return _cache["data"]
    try:
        data = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    _cache.update(mtime=mt, data=data)
    return data


def save(data: dict) -> None:
    SCOPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCOPE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _cache.update(mtime=None, data={})          # 让下次 load 重新读


def _entry(data: dict, wid: str) -> dict:
    return data.setdefault(wid, {})


def _live(block: dict | None) -> dict | None:
    """过滤过期条目：返回仍生效的块（或 None）。"""
    if not block:
        return None
    until = block.get("until")
    if until and time.time() > float(until):
        return None
    return block


def _describe_until(until) -> str:
    if not until:
        return "无期限"
    return f"{datetime.fromtimestamp(float(until), CST):%Y-%m-%d %H:%M} 自动失效"


# ---------------------------------------------------------------- grant（已授予的额外范围）
def active_grant(wid: str) -> dict | None:
    return _live(load().get(wid, {}).get("grant"))


def grant_include(wid: str) -> list[str]:
    g = active_grant(wid)
    return list(g.get("include", [])) if g else []


def set_grant(wid: str, include: list[str], minutes: int | None, note: str, by: str = "master") -> dict:
    data = load()
    entry = _entry(data, wid)
    entry["grant"] = {
        "include": list(include),
        "until": (time.time() + minutes * 60) if minutes else None,
        "note": note,
        "by": by,
        "at": _now_iso(),
    }
    entry.pop("pending", None)                  # 批准即清掉待批
    save(data)
    return entry["grant"]


def clear_grant(wid: str) -> None:
    data = load()
    entry = _entry(data, wid)
    entry.pop("grant", None)
    entry.pop("pending", None)
    entry.pop("arm", None)
    save(data)


# ---------------------------------------------------------------- pending（agent 的申请）
def set_pending(wid: str, include: list[str], reason: str) -> dict:
    data = load()
    entry = _entry(data, wid)
    entry["pending"] = {"include": list(include), "reason": reason, "at": _now_iso()}
    save(data)
    return entry["pending"]


def get_pending(wid: str) -> dict | None:
    return load().get(wid, {}).get("pending")


# ---------------------------------------------------------------- arm（预授权窗口）
def active_arm(wid: str) -> dict | None:
    return _live(load().get(wid, {}).get("arm"))


def set_arm(wid: str, minutes: int, allowed: list[str], note: str = "") -> dict:
    data = load()
    entry = _entry(data, wid)
    entry["arm"] = {
        "until": time.time() + minutes * 60,
        "allowed": list(allowed),               # 空 = 不限制（申请多少给多少，仍受 deny/exclude 约束）
        "note": note,
        "at": _now_iso(),
    }
    save(data)
    return entry["arm"]


def clear_arm(wid: str) -> None:
    data = load()
    entry = _entry(data, wid)
    entry.pop("arm", None)
    save(data)


# ---------------------------------------------------------------- 判定辅助
def _glob_covers(big: str, small: str) -> bool:
    """粗判 big 这个 glob 是否覆盖 small（给 arm.allowed 做上限约束，只做保守判断）。"""
    if big in ("**/*", "*", "**"):
        return True
    b = big.rstrip("*").rstrip("/")
    s = small.rstrip("*").rstrip("/")
    return bool(b) and (s == b or s.startswith(b + "/"))


def arm_allows(wid: str, include: list[str]) -> tuple[bool, str]:
    """申请的范围是否在预授权上限内。"""
    arm = active_arm(wid)
    if not arm:
        return False, "没有生效中的预授权窗口"
    allowed = arm.get("allowed") or []
    if not allowed:
        return True, ""
    for pat in include:
        if not any(_glob_covers(a, pat) for a in allowed):
            return False, f"申请的范围 {pat!r} 超出预授权上限 {allowed}"
    return True, ""


def summary(wid: str) -> dict:
    """给人看的当前授权状态。"""
    data = load().get(wid, {})
    g = _live(data.get("grant"))
    arm = _live(data.get("arm"))
    return {
        "granted_extra_include": (g or {}).get("include", []),
        "granted_until": _describe_until((g or {}).get("until")) if g else None,
        "granted_note": (g or {}).get("note") if g else None,
        "pending": data.get("pending"),
        "elevation_window": ({"until": _describe_until(arm.get("until")), "max_scope": arm.get("allowed") or "不限"
                              } if arm else None),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    cmd = sys.argv[1]
    wid = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "show":
        print(json.dumps(summary(wid) if wid else load(), ensure_ascii=False, indent=2))
    elif cmd == "clear" and wid:
        clear_grant(wid)
        print(f"已清空 {wid} 的授权（grant/pending/arm 全撤）")
    else:
        print(__doc__)
