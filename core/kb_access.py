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


# ---------------------------------------------------------------- 细致调整（管理页/CLI 共用）
_UNSET = object()          # 区分「不改」和「改成无期限（None）」


def update_user(state_root: Path, wid: str, person: str, *, levels: list[str] | None = None,
                dept: str | None = None, note: str | None = None, expires=_UNSET,
                enabled: bool | None = None, new_name: str | None = None) -> dict | None:
    """改一个同事的权限/资料（地址不变）。

    levels 覆盖整份等级清单；dept/note 传了就覆盖；expires 传 None = 改回无期限；
    new_name 改名（保留地址、权限与统计）。
    """
    person = (person or "").strip()
    data, err = load_all(state_root)
    if err:
        raise RuntimeError(err)
    bucket = data.get(wid) or {}
    rec = bucket.get(person)
    if not rec:
        return None
    if new_name is not None:
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("姓名不能改成空")
        if new_name != person and new_name in bucket:
            raise ValueError(f"已经有一个叫「{new_name}」的同事了")
        del bucket[person]
        bucket[new_name] = rec
        rec["person"] = new_name
    if levels is not None:
        rec["levels"] = list(levels)
    if dept is not None:
        rec["dept"] = dept
    if note is not None:
        rec["note"] = note
    if expires is not _UNSET:
        rec["expires"] = float(expires) if expires else None
    if enabled is not None:
        rec["enabled"] = bool(enabled)
    data[wid] = bucket
    save_all(state_root, data)
    return rec


def delete_user(state_root: Path, wid: str, person: str) -> bool:
    """彻底删掉一个同事（那条地址立即失效，记录也没了）。"""
    person = (person or "").strip()
    data, err = load_all(state_root)
    if err:
        return False
    bucket = data.get(wid) or {}
    if person not in bucket:
        return False
    bucket.pop(person)
    data[wid] = bucket
    save_all(state_root, data)
    return True


def expiry_to_ts(days: str = "", until: str = "") -> float | None:
    """把「多少天」或「到某天（YYYY-MM-DD）」换成到期时间戳；都没有 = 无期限。"""
    until = (until or "").strip()
    if until:
        try:
            d = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=CST)
        except ValueError:
            raise ValueError("日期要写成 2026-10-31 这种格式") from None
        return d.replace(hour=23, minute=59, second=0).timestamp()
    days = (days or "").strip()
    if days and days != "0":
        try:
            n = int(days)
        except ValueError:
            raise ValueError("天数要写数字") from None
        return time.time() + n * 24 * 60 * 60
    return None
