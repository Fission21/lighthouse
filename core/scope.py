#!/usr/bin/env python3
"""灯塔·范围授权（提权）—— 把「能不能看更多」的权利交给用户。

三层状态（都带过期时间，默认就都是空的）：

  grant    已授予的额外范围       —— 由用户批准后写入，到期自动失效
  pending  agent 提出的申请        —— agent 只能「申请」，不能自己批
  arm      预授权窗口（可选）      —— 用户先开一个 N 分钟的窗口，期间 agent 的申请可被自动批准
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
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _state_root() -> Path:
    """状态目录：环境变量 LIGHTHOUSE_STATE > 配置文件 state_dir > ~/.lighthouse。"""
    env = os.environ.get("LIGHTHOUSE_STATE")
    if env:
        return Path(os.path.expanduser(env))
    for name in ("config.local.json", "config.json"):
        try:
            cfg = json.loads((Path(__file__).resolve().parent.parent / name).read_text(encoding="utf-8"))
            if cfg.get("state_dir"):
                return Path(os.path.expanduser(cfg["state_dir"]))
        except (OSError, json.JSONDecodeError):
            continue
    return Path(os.path.expanduser("~/.lighthouse"))

STATE_ROOT = _state_root()
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


# ---------------------------------------------------------------- 时长（用户自选：短到长到永久）
# 「授权多久」是用户的选择，不是工具的默认值：30m / 2h / 1d / 7d / 1w / forever。
# 解析失败**必须报错**（CLI 负责翻译成人话）——算不清时长的授权不该被授出去。
_UNIT_MINUTES = {"m": 1, "h": 60, "d": 1440, "w": 10080}
_DURATION_RE = re.compile(
    r"^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)$")
_FOREVER_WORDS = {"forever", "permanent", "always", "none", "", "永久", "永远", "无期限"}


def parse_duration(s) -> int | None:
    """用户写的时长 → 分钟数；None = 无期限。

    支持 `30m` / `2h` / `1d` / `7d` / `1w`（也接受 30min / 2hr / 3days…）；
    纯数字按分钟（向后兼容 `--minutes`）；`forever` / 空 / 0 → 无期限。
    """
    if s is None:
        return None
    t = str(s).strip().lower()
    if t in _FOREVER_WORDS or t == "0":
        return None
    if t.isdigit():
        return int(t)
    m = _DURATION_RE.match(t)
    if not m:
        raise ValueError(f"看不懂的时长 {s!r}（可用：30m / 2h / 1d / 7d / 1w / forever）")
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        raise ValueError(f"时长必须大于 0：{s!r}")
    return n * _UNIT_MINUTES[unit[0]]


def describe_duration(minutes: int | None) -> str:
    """给人看的时长：30 → '30 分钟'；120 → '2 小时'；10080 → '7 天'；None → '无期限'。"""
    if not minutes:
        return "无期限"
    for unit, name in ((10080, "周"), (1440, "天"), (60, "小时")):
        if minutes % unit == 0:
            return f"{minutes // unit} {name}"
    return f"{minutes} 分钟"


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


# ---------------------------------------------------------------- ceiling（常驻自动授权上限）
def ceiling_allows(ceiling: list[str], include: list[str]) -> tuple[bool, str]:
    """申请范围是否**完整**落在用户写在 windows.json 的常驻上限内。

    ceiling 为空 = 用户没设上限（语义由调用方决定：不作限制）。
    只做保守判断：任一条申请不被任何一条 ceiling 覆盖 → 不算过。
    """
    if not include:
        return False, "申请范围为空"
    if not ceiling:
        return True, ""
    for pat in include:
        if not any(_glob_covers(c, pat) for c in ceiling):
            return False, f"申请的范围 {pat!r} 超出常驻上限 {ceiling}"
    return True, ""


def recent_activity(wid: str, minutes: int = 10, max_lines: int = 300) -> dict:
    """最近 N 分钟的调用统计。

    存在的理由：用户最常需要的判断是「AI 说读不到 —— 是它没来问，还是被我拒了？」。
    - **没有记录** = 请求根本没到本机（平台/网络拦的，与灯塔无关）
    - **有记录但 ok=false** = 到了本机，是这扇窗按规则拒的
    """
    path = STATE_ROOT / "audit" / f"{wid}.jsonl"
    out = {"window_minutes": minutes, "total": 0, "allowed": 0, "denied": 0,
           "last": None, "audit_file": str(path)}
    if not path.exists():
        return out
    cutoff = datetime.now(CST) - timedelta(minutes=minutes)
    rows = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    except OSError:
        return out
    for line in lines:
        try:
            d = json.loads(line)
            ts = datetime.fromisoformat(d["ts"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=CST)
        if ts >= cutoff:
            rows.append(d)
    ok = sum(1 for r in rows if r.get("ok"))
    last = rows[-1] if rows else None
    out.update(total=len(rows), allowed=ok, denied=len(rows) - ok)
    if last:
        out["last"] = {"ts": last.get("ts"), "tool": last.get("tool"), "args": last.get("args"),
                       "ok": last.get("ok"), "reason": last.get("reason")}
    return out


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
        "recent_calls": recent_activity(wid),
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
