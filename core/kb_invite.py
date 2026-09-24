"""邀请码：管理员发码，同事凭码自助注册 —— 每一步都能追溯到「谁发的、发给了谁、谁用了」。

为什么要它：把「注册」这件事从「谁都能申请」收紧成「必须有一张管理员发的码」，
而每张码都记着：谁生成的、打算给谁、什么等级、有效期、用没用、被谁在什么时间什么 IP 用的。
码是一次性的，用完即失效；管理员随时能停用或删掉。

存储：`<state>/kb-invites.json` → `{窗口: {码: {...}}}`（权限 0600，码本身等于半张门票）
"""

from __future__ import annotations

import json
import os
import secrets
import string
import time
from pathlib import Path

FILE = "kb-invites.json"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # 去掉易混的 0O1I
CODE_LEN = 10
GROUP = 5                                          # 展示成 XXXXX-XXXXX，好念好抄
DEFAULT_DAYS = 30                                  # 码本身的有效期
DEFAULT_USES = 1                                   # 一张码默认只能用一次


def _path(state_root) -> Path:
    d = Path(state_root) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / FILE


def load(state_root) -> dict:
    f = _path(state_root)
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def save(state_root, data: dict) -> None:
    f = _path(state_root)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(f, 0o600)


def fmt(ts) -> str:
    """把时间戳写成「2026-09-24 15:30」，别把 epoch 数字丢给人看。"""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "—"


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    return f"{raw[:GROUP]}-{raw[GROUP:]}"


def pretty(code: str) -> str:
    """统一成大写、去掉杂符；用户抄错大小写/横杠也能用。"""
    raw = "".join(c for c in str(code or "").upper() if c.isalnum())
    return f"{raw[:GROUP]}-{raw[GROUP:]}" if len(raw) > GROUP else raw


def create(state_root, wid: str, *, levels: list[str], person: str = "", note: str = "",
           days: int = DEFAULT_DAYS, max_uses: int = DEFAULT_USES, actor: str = "admin") -> dict:
    """生成一张码。person 非空 = 只允许这个人用；days=0 表示码不过期。"""
    data = load(state_root)
    bucket = data.setdefault(wid, {})
    code = new_code()
    for _ in range(20):                                    # 撞了就重摇
        if code not in bucket:
            break
        code = new_code()
    rec = {"code": code, "levels": list(levels), "person": (person or "").strip(),
           "note": (note or "").strip(), "created_at": time.time(), "created_by": actor,
           "expires": (time.time() + int(days) * 86400) if int(days or 0) > 0 else None,
           "max_uses": max(1, int(max_uses or 1)), "uses": [], "enabled": True}
    bucket[code] = rec
    data[wid] = bucket
    save(state_root, data)
    return rec


def list_codes(state_root, wid: str) -> list[dict]:
    return sorted((load(state_root).get(wid) or {}).values(),
                  key=lambda r: float(r.get("created_at") or 0), reverse=True)


def status(rec: dict, now: float | None = None) -> str:
    """可用 / 已用完 / 已过期 / 已停用 —— 给报表和界面用同一套判断。"""
    now = now or time.time()
    if not rec.get("enabled", True):
        return "已停用"
    if rec.get("expires") and float(rec["expires"]) < now:
        return "已过期"
    if len(rec.get("uses") or []) >= int(rec.get("max_uses") or 1):
        return "已用完"
    return "可用"


def check(state_root, wid: str, code: str) -> tuple[dict | None, str]:
    """验码：能不能用。返回 (记录, 错误说明)。"""
    key = pretty(code)
    rec = (load(state_root).get(wid) or {}).get(key)
    if not rec:
        return None, "邀请码不对（或者已经被维护者删掉了）。"
    why = status(rec)
    if why != "可用":
        return None, f"这张邀请码{why}，用不了了。"
    return rec, ""


def use(state_root, wid: str, code: str, *, person: str, username: str = "", ip: str = "-") -> dict | None:
    """登记一次使用（一次性码用满即失效）。返回更新后的记录。"""
    key = pretty(code)
    data = load(state_root)
    rec = (data.get(wid) or {}).get(key)
    if not rec:
        return None
    rec.setdefault("uses", []).append({"person": person, "username": username, "ip": ip,
                                       "at": time.time()})
    save(state_root, data)
    return rec


def revoke(state_root, wid: str, code: str, enabled: bool = False) -> bool:
    key = pretty(code)
    data = load(state_root)
    bucket = data.get(wid) or {}
    if key not in bucket:
        return False
    bucket[key]["enabled"] = bool(enabled)
    save(state_root, data)
    return True


def delete(state_root, wid: str, code: str) -> bool:
    key = pretty(code)
    data = load(state_root)
    bucket = data.get(wid) or {}
    if key not in bucket:
        return False
    bucket.pop(key)
    data[wid] = bucket
    save(state_root, data)
    return True


def summary(state_root, wid: str) -> dict:
    rows = list_codes(state_root, wid)
    counts: dict[str, int] = {}
    for r in rows:
        counts[status(r)] = counts.get(status(r), 0) + 1
    return {"total": len(rows), "available": counts.get("可用", 0), "used": counts.get("已用完", 0),
            "expired": counts.get("已过期", 0), "disabled": counts.get("已停用", 0)}


def gen_password(n: int = 12) -> str:
    alpha = string.ascii_letters + string.digits
    return "".join(secrets.choice(alpha) for _ in range(n))
