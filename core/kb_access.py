#!/usr/bin/env python3
"""灯塔·受控资料的「同事记录」—— 一个同事 = 一条专属地址，权限挂在这条地址上。

文件：<状态目录>/state/kb-users.json
{
  "bidkb": {"张三": {"person": "张三", "dept": "技术部", "token": "2wQ8…",
                     "levels": ["L2-技术"], "note": "", "created_at": "…",
                     "expires": null, "enabled": true, "rotated_at": null,
                     "last_seen": null, "calls": 0, "denied": 0}}
}

原则：
  · 一人一条（secrets.token_urlsafe(32)）；改权限 / 换地址都在那一条上做；
  · 每请求现读文件 → 停用 / 收回 / 换地址 / 到期**立即**生效，不用重启服务；
  · 读不到 / 结构异常 / token 为空 → 一律拒（fail-closed）。
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
DEFAULT_MINUTES = 30 * 24 * 60          # 默认有效期 30 天（用户 2026-09-23 定的）


def _path(state_root: Path) -> Path:
    return Path(state_root) / "state" / "kb-users.json"


def now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def load_all(state_root: Path) -> tuple[dict, str]:
    p = _path(state_root)
    if not p.exists():
        return {}, ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {}, f"同事记录不可读（{e.__class__.__name__}）"      # 调用方必须拒（fail-closed）
    return (data if isinstance(data, dict) else {}), ""


def save_all(state_root: Path, data: dict) -> None:
    p = _path(state_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def upsert_user(state_root: Path, wid: str, person: str, levels: list[str], *,
                dept: str = "", note: str = "", minutes: int | None = DEFAULT_MINUTES,
                notify: bool = True) -> dict:
    """给某人一条地址。**已存在则更新等级/有效期，地址不变**。minutes=None = 无期限。"""
    person = (person or "").strip()
    if not person:
        raise ValueError("person 不能为空")
    data, err = load_all(state_root)
    if err:
        raise RuntimeError(err)
    bucket = data.setdefault(wid, {})
    rec = bucket.get(person)
    exp = (time.time() + minutes * 60) if minutes else None
    if rec:                                   # 已有 → 更新权限，不换地址
        rec["levels"] = list(levels)
        rec["dept"] = dept or rec.get("dept", "")
        rec["note"] = note or rec.get("note", "")
        rec["expires"] = exp
        rec["enabled"] = True
    else:
        rec = {"person": person, "dept": dept, "token": secrets.token_urlsafe(32),
               "levels": list(levels), "note": note, "created_at": now_iso(),
               "expires": exp, "enabled": True, "rotated_at": None,
               "last_seen": None, "calls": 0, "denied": 0}
        bucket[person] = rec
    save_all(state_root, data)
    return rec


def get_user(state_root: Path, wid: str, person: str) -> dict | None:
    data, _ = load_all(state_root)
    return (data.get(wid) or {}).get((person or "").strip())


def list_users(state_root: Path, wid: str) -> list[dict]:
    data, _ = load_all(state_root)
    return sorted((data.get(wid) or {}).values(), key=lambda r: r.get("created_at") or "")


def set_enabled(state_root: Path, wid: str, person: str, enabled: bool) -> bool:
    """停用 / 启用。停用 = 那条地址立刻 401（保留记录，随时可再启用）。"""
    data, err = load_all(state_root)
    if err:
        return False
    rec = (data.get(wid) or {}).get((person or "").strip())
    if not rec:
        return False
    rec["enabled"] = bool(enabled)
    save_all(state_root, data)
    return True


def rotate(state_root: Path, wid: str, person: str, *, minutes: int | None = DEFAULT_MINUTES) -> dict | None:
    """换一条新地址（权限不变）：旧地址立即失效。地址泄漏时用这个。"""
    data, err = load_all(state_root)
    if err:
        return None
    rec = (data.get(wid) or {}).get((person or "").strip())
    if not rec:
        return None
    rec["token"] = secrets.token_urlsafe(32)
    rec["rotated_at"] = now_iso()
    if minutes is not None:
        rec["expires"] = time.time() + minutes * 60
    save_all(state_root, data)
    return rec


def check_token(state_root: Path, wid: str, token: str) -> tuple[dict | None, str]:
    """token → 同事记录。任何异常都返回错误（fail-closed）。"""
    if not token or len(token) < 16:
        return None, "缺少或格式不对的访问地址"
    if token.startswith("adm_"):
        return None, "这是管理令，不是同事的访问地址"
    data, err = load_all(state_root)
    if err:
        return None, err
    for rec in (data.get(wid) or {}).values():
        if secrets.compare_digest(str(rec.get("token") or ""), token):
            if not rec.get("enabled", True):
                return None, "你的这条地址已被停用"
            exp = rec.get("expires")
            if exp and time.time() > float(exp):
                return None, "你的这条地址已到期"
            return rec, ""
    return None, "这条地址不在记录里（可能已收回或换过新地址）"


def touch(state_root: Path, wid: str, person: str, *, denied: bool = False) -> None:
    """记一次使用（每请求调；失败不抛 —— 统计不能影响可用性）。"""
    data, err = load_all(state_root)
    if err:
        return
    rec = (data.get(wid) or {}).get((person or "").strip())
    if not rec:
        return
    rec["last_seen"] = now_iso()
    rec["calls"] = int(rec.get("calls") or 0) + 1
    if denied:
        rec["denied"] = int(rec.get("denied") or 0) + 1
    try:
        save_all(state_root, data)
    except OSError:
        pass


def describe_expiry(rec: dict) -> str:
    exp = rec.get("expires")
    if not exp:
        return "无期限"
    return datetime.fromtimestamp(float(exp), CST).strftime("%Y-%m-%d %H:%M") + " 到期"
