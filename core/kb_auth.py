"""门户登录：账号密码 + 签名 cookie。

为什么要有它：资料库的网页（申请页 / 文件页 / 管理页）挂在公网域名上，
只靠「一条地址口令」不够 —— 任何人拿到页面就能看、就敢试。这里加一层
账号密码：

- 密码只存 **scrypt 哈希**（加盐），文件里永远看不到明文；
- 登录后发一个 **HMAC 签名的 cookie**（密钥在本机 0600 文件里），改一个字符就失效；
- 会话有期限（默认 12 小时，可勾「记住我」30 天），过期自动作废；
- 失败限速：同一 IP + 同一账号连错 5 次锁 10 分钟，防在线爆破；
- 角色分 `admin`（管理员）与 `member`（同事）；同事登录后只看得到自己等级范围的资料；
- MCP 那条线（AI 客户端用的地址令牌）**不受影响**：机器不会登录，令牌还是令牌。

账号与同事台账分开存（`portal-users.json`），免得把密码哈希混进同事名单。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

USERS_FILE = "portal-users.json"
SECRET_FILE = "portal-secret"

SESSION_MINUTES = 12 * 60          # 默认 12 小时
REMEMBER_MINUTES = 30 * 24 * 60    # 勾「记住我」= 30 天
MAX_FAILS = 5                      # 连错几次锁
LOCK_SECONDS = 10 * 60             # 锁多久
COOKIE = "lh_sess"
PW_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉了易混的 0O1lI


# ---------------------------------------------------------------- 存储
def _state(state_root) -> Path:
    d = Path(state_root) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_secret(state_root) -> bytes:
    """会话签名密钥：没有就生成一个（0600，只在本机）。"""
    f = _state(state_root) / SECRET_FILE
    if not f.exists():
        f.write_bytes(secrets.token_bytes(48))
        os.chmod(f, 0o600)
    return f.read_bytes()


def load_users(state_root) -> dict:
    f = _state(state_root) / USERS_FILE
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def save_users(state_root, data: dict) -> None:
    f = _state(state_root) / USERS_FILE
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(f, 0o600)


# ---------------------------------------------------------------- 密码
def hash_pw(pw: str) -> str:
    """scrypt 加盐哈希，格式：scrypt$n$r$p$salt_b64$hash_b64。"""
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        alg, n, r, p, salt_b64, hash_b64 = str(stored).split("$")
        if alg != "scrypt":
            return False
        dk = hashlib.scrypt(pw.encode("utf-8"), salt=base64.b64decode(salt_b64),
                            n=int(n), r=int(r), p=int(p), dklen=len(base64.b64decode(hash_b64)))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError, MemoryError):
        return False


def gen_password(n: int = 14) -> str:
    """生成一个能念给同事听的临时密码（去掉易混字符）。"""
    return "".join(secrets.choice(PW_ALPHABET) for _ in range(n))


# ---------------------------------------------------------------- 账号
def get(state_root, user: str) -> dict | None:
    user = (user or "").strip()
    if not user or user.startswith("_"):
        return None
    rec = load_users(state_root).get(user)
    return rec if isinstance(rec, dict) else None


def set_account(state_root, user: str, *, role: str = "member", password: str | None = None,
                person: str = "", note: str = "") -> tuple[dict, str | None]:
    """建账号或改属性。password=None 表示只改属性、不动密码。返回 (记录, 明文密码或 None)。"""
    user = (user or "").strip()
    if not user:
        raise ValueError("用户名不能为空")
    if len(user) > 40:
        raise ValueError("用户名太长")
    data = load_users(state_root)
    rec = data.get(user) or {"user": user, "created_at": time.time(), "fails": 0,
                             "locked_until": 0, "last_login": None, "epoch": 0}
    rec["role"] = role if role in ("admin", "member") else "member"
    rec["person"] = person or rec.get("person") or ""
    if note:
        rec["note"] = note
    plain = None
    if password is not None:
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        rec["pw"] = hash_pw(password)
        rec["pw_at"] = int(time.time())
        rec["epoch"] = int(rec.get("epoch") or 0) + 1        # 改密即换代：旧 cookie 立刻失效
        plain = password
        rec["fails"], rec["locked_until"] = 0, 0
    data[user] = rec
    save_users(state_root, data)
    return rec, plain


def new_account(state_root, user: str, *, role: str = "member", person: str = "",
                length: int = 14) -> tuple[dict, str]:
    """建账号并生成一个临时密码（明文只返回一次，不落盘）。"""
    return set_account(state_root, user, role=role, password=gen_password(length), person=person)


def reset_password(state_root, user: str, *, length: int = 14) -> tuple[dict, str] | None:
    rec = get(state_root, user)
    if not rec:
        return None
    return set_account(state_root, user, role=rec.get("role") or "member",
                       password=gen_password(length), person=rec.get("person") or "")


def delete_account(state_root, user: str) -> bool:
    data = load_users(state_root)
    if (user or "").strip() not in data:
        return False
    data.pop((user or "").strip())
    save_users(state_root, data)
    return True


def list_accounts(state_root) -> list[dict]:
    out = []
    for u, r in sorted(load_users(state_root).items()):
        if u.startswith("_") or not isinstance(r, dict) or "pw" not in r and "role" not in r:
            continue
        out.append({"user": u, "role": r.get("role") or "member", "person": r.get("person") or "",
                    "has_pw": bool(r.get("pw")), "last_login": r.get("last_login"),
                    "locked": float(r.get("locked_until") or 0) > time.time(),
                    "note": r.get("note") or ""})
    return out


def admins(state_root) -> list[str]:
    return [u for u, r in load_users(state_root).items()
            if isinstance(r, dict) and r.get("role") == "admin"]


def has_admin(state_root) -> bool:
    return bool(admins(state_root))


# ---------------------------------------------------------------- 登录
def verify_login(state_root, user: str, pw: str, ip: str = "-",
                 now: float | None = None) -> tuple[bool, str, dict | None]:
    """验证账号密码：返回 (是否通过, 原因, 账号记录)。会累计失败次数并锁定。"""
    now = now or time.time()
    user = (user or "").strip()
    data = load_users(state_root)
    rec = data.get(user)
    lock_ip = float((_ip_locks(data).get(ip) or {}).get("locked_until") or 0)
    if rec and float(rec.get("locked_until") or 0) > now:
        left = int((float(rec["locked_until"]) - now) / 60) + 1
        return False, f"这个账号连错太多次，先等 {left} 分钟再试。", None
    if lock_ip > now:
        left = int((lock_ip - now) / 60) + 1
        return False, f"这台设备连错太多次，先等 {left} 分钟再试。", None
    if not rec or not rec.get("pw"):
        # 账号不存在也照样走一遍哈希，避免用响应时间试探账号是否存在
        verify_pw(pw or "", hash_pw("x"))
        _bump_fail(data, ip, now)          # 用户名不存在也要记一次失败（否则换个假用户名就能绕过 IP 锁定）
        save_users(state_root, data)
        return False, "用户名或密码不对。", None
    if not verify_pw(pw or "", rec["pw"]):
        rec["fails"] = int(rec.get("fails") or 0) + 1
        if rec["fails"] >= MAX_FAILS:
            rec["locked_until"] = now + LOCK_SECONDS
            rec["fails"] = 0
        _bump_fail(data, ip, now)
        save_users(state_root, data)
        return False, "用户名或密码不对。", None
    rec["fails"], rec["locked_until"], rec["last_login"] = 0, 0, now
    _ip_locks(data).pop(ip, None)
    save_users(state_root, data)
    return True, "", rec


def _ip_locks(data: dict) -> dict:
    """IP 维度的失败计数放在保留键里，别混进账号表。"""
    locks = data.get("_ip_locks")
    if not isinstance(locks, dict):
        locks = {}
        data["_ip_locks"] = locks
    return locks


def _bump_fail(data: dict, ip: str, now: float) -> None:
    if not ip or ip == "-":
        return
    locks = _ip_locks(data)
    slot = locks.get(ip) or {"fails": 0, "locked_until": 0}
    slot["fails"] = int(slot.get("fails") or 0) + 1
    if slot["fails"] >= MAX_FAILS * 2:          # 同一台设备换账号猛试 → 更狠
        slot["locked_until"] = now + LOCK_SECONDS
        slot["fails"] = 0
    locks[ip] = slot


# ---------------------------------------------------------------- 会话 cookie
def _sign(secret: bytes, payload: str) -> str:
    return base64.urlsafe_b64encode(
        hmac.new(secret, payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def make_cookie(state_root, rec: dict, *, minutes: int | None = None, now: float | None = None) -> str:
    """签发会话 cookie 的值（不含 cookie 名）。"""
    now = now or time.time()
    mins = minutes or SESSION_MINUTES
    payload = base64.urlsafe_b64encode(json.dumps({
        "u": rec.get("user"), "r": rec.get("role") or "member",
        "p": rec.get("person") or "", "exp": int(now + mins * 60), "iat": int(now),
        "ep": int(rec.get("epoch") or 0),
    }, ensure_ascii=False).encode()).decode().rstrip("=")
    return f"v1.{payload}.{_sign(ensure_secret(state_root), payload)}"


def read_cookie(state_root, value: str, *, now: float | None = None) -> dict | None:
    """验签 + 查过期；任何一处不对都返回 None。"""
    now = now or time.time()
    try:
        parts = str(value or "").split(".")
        if len(parts) != 3 or parts[0] != "v1":
            return None
        ver, payload, sig = parts
        if not hmac.compare_digest(sig, _sign(ensure_secret(state_root), payload)):
            return None
        pad = "=" * (-len(payload) % 4)
        info = json.loads(base64.urlsafe_b64decode(payload + pad).decode())
        if int(info.get("exp") or 0) <= int(now):
            return None
        user = str(info.get("u") or "")
        if not user:
            return None
        # 账号被删了 / 角色改了 / 密码换了 → 旧 cookie 立刻失效
        rec = get(state_root, user)
        if not rec or (rec.get("role") or "member") != (info.get("r") or "member"):
            return None
        if int(info.get("ep") or 0) != int(rec.get("epoch") or 0):
            return None                                   # 改过密码 / 重置过 → 旧会话作废
        return {"user": user, "role": info.get("r") or "member", "person": info.get("p") or "",
                "exp": int(info["exp"]), "epoch": int(info.get("ep") or 0)}
    except (ValueError, TypeError, KeyError):
        return None


def clear_cookie() -> str:
    return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def cookie_header(state_root, rec: dict, *, minutes: int | None = None, secure: bool = True) -> str:
    mins = minutes or SESSION_MINUTES
    bits = [f"{COOKIE}={make_cookie(state_root, rec, minutes=mins)}",
            "Path=/", f"Max-Age={int(mins * 60)}", "HttpOnly", "SameSite=Lax"]
    if secure:
        bits.append("Secure")
    return "; ".join(bits)
