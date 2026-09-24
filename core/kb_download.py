#!/usr/bin/env python3
"""受控资料的「下载」层 —— 同事既能用 AI 查，也能直接把文件拿走。

两条路，同一套闸门（篇级审批 + 等级 + 拉黑 + 原文件哈希 + 越界）：

  ① 门户下载页（不依赖 AI）：用他自己的地址打开 `…/kb-<地址段>/files`
     → 列出他有权看的资料，逐条下载原件 / 抽取文本，也可勾几篇打包成 zip。
  ② MCP 工具 `kb_link(doc_id)`：返回一条**限时签名链接**（默认 15 分钟），
     AI 可以直接在回答里给出「点这里下载」。链接只能下载那一篇、
     绑定了这个人和到期时间，泄漏了也会过期。

签名（无状态、可校验、不落库）：
  sig = HMAC-SHA256(secret, f"{窗口}|{doc_id}|{人}|{到期时间戳}")[:40]
  secret 每次生成存在 <状态目录>/state/kb-dl-secret（0600，不进版本库）。
  地址被停用/到期/换新 → 校验第一步就失败（先查人，再看签名）。

原则：任何一步拿不准都拒（fail-closed）；每次下载都写审计（谁、哪篇、原件还是文本、多少字节）。
"""
from __future__ import annotations

import hashlib
import hmac
import io
import mimetypes
import secrets
import time
import zipfile
from pathlib import Path

DEFAULTS = {
    "enabled": True,       # 允许下载（关掉就只剩 MCP 在线阅读）
    "original": True,      # 允许下载原件（.docx/.pdf…）；false 则只给抽取后的文本
    "link_minutes": 15,    # kb_link 给的限时链接有效期（分钟）
    "max_bundle_mb": 200,  # 一次打包下载的总大小上限
    "max_file_mb": 50,     # 单个文件下载上限
}

TEXT_EXT = ".md"


# ---------------------------------------------------------------- 配置
def download_cfg(cfg: dict) -> dict:
    """把窗口配置里的 kb.download 合上默认值；写错类型一律按默认（fail-closed 到安全侧）。"""
    out = dict(DEFAULTS)
    raw = (cfg.get("kb") or {}).get("download")
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in ("enabled", "original") and isinstance(v, bool):
                out[k] = v
            elif k in ("link_minutes", "max_bundle_mb", "max_file_mb") and isinstance(v, (int, float)) and v > 0:
                out[k] = int(v)
    elif raw is False:
        out["enabled"] = False
    return out


# ---------------------------------------------------------------- 签名
def secret_path(state_root: Path) -> Path:
    return Path(state_root) / "state" / "kb-dl-secret"


def dl_secret(state_root: Path) -> str:
    """读取（没有就生成）本机下载签名密钥。文件权限 0600。"""
    p = secret_path(state_root)
    try:
        if p.is_file():
            v = p.read_text(encoding="utf-8").strip()
            if len(v) >= 32:
                return v
    except OSError:
        pass
    v = secrets.token_hex(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(v + "\n", encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return v


def sign(state_root: Path, wid: str, doc_id: str, person: str, exp: int) -> str:
    msg = f"{wid}|{doc_id}|{person}|{int(exp)}".encode()
    return hmac.new(dl_secret(state_root).encode(), msg, hashlib.sha256).hexdigest()[:40]


def verify(state_root: Path, wid: str, doc_id: str, person: str, exp, sig: str) -> bool:
    """签名 + 到期一起校验。任何异常/格式不对 = 不通过。"""
    try:
        exp_i = int(str(exp))
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    if not sig or len(str(sig)) != 40:
        return False
    return hmac.compare_digest(sign(state_root, wid, doc_id, person, exp_i), str(sig))


def make_link(state_root: Path, wid: str, doc_id: str, person: str, minutes: int) -> tuple[str, int]:
    """返回 (查询串, 到期时间戳)：?e=…&s=…（调用方再拼路径与地址段）。"""
    exp = int(time.time()) + max(1, int(minutes)) * 60
    return f"e={exp}&s={sign(state_root, wid, doc_id, person, exp)}", exp


def ttl_minutes(minutes, cfg: dict) -> int:
    """kb_link 的有效期：调用方给就用（夹在 1~1440），否则用窗口配置的默认。"""
    try:
        m = int(minutes) if minutes else int(download_cfg(cfg)["link_minutes"])
    except (TypeError, ValueError):
        m = int(download_cfg(cfg)["link_minutes"])
    return max(1, min(m, 1440))


def person_active(state_root: Path, wid: str, person: str) -> bool:
    """这个人的地址现在还有效吗（没停用、没过期）？——旧链接跟着人一起失效。"""
    try:
        import kb_access as ACC
        rec = ACC.get_user(state_root, wid, person)
        if not rec or not rec.get("enabled", True):
            return False
        exp = rec.get("expires")
        return not (exp and time.time() > float(exp))
    except Exception:                                                        # noqa: BLE001
        return False


# ---------------------------------------------------------------- 文件名 / MIME
def media_type(p: Path) -> str:
    mt, _ = mimetypes.guess_type(str(p))
    return mt or "application/octet-stream"


def safe_name(title: str, src: Path) -> str:
    """给人看的下载文件名：优先原标题 + 原后缀，去掉会捣乱路径的字符。"""
    base = (title or src.stem or "资料").strip()
    for ch in '/\\:*?"<>|\r\n\t':
        base = base.replace(ch, "_")
    base = base.strip(". ")[:80] or "资料"
    ext = src.suffix
    return base if base.lower().endswith(ext.lower()) else base + ext


def disposition(name: str) -> str:
    """Content-Disposition：同时给 ASCII 兜底与 UTF-8 原名（中文文件名在各浏览器都不乱码）。"""
    ascii_name = name.encode("ascii", "ignore").decode()
    if not ascii_name.strip(". "):                      # 纯中文标题 → 给个像样的兜底名
        ascii_name = "document" + (Path(name).suffix or "")
    from urllib.parse import quote
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}'


def zip_bytes(items: list[tuple[str, Path]], max_mb: int = 200) -> tuple[bytes | None, str]:
    """把若干文件打成内存 zip。超上限 → 拒绝（返回错误）。"""
    total = 0
    for _name, p in items:
        try:
            total += p.stat().st_size
        except OSError as e:
            return None, f"读不到文件（{e.__class__.__name__}）"
    if total > max_mb * 1024 * 1024:
        return None, f"合计 {total // 1048576}MB，超过一次打包上限 {max_mb}MB；请分开下载"
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, p in items:
            n = name
            i = 2
            while n in used:                       # 同名文件不覆盖
                stem, dot, ext = name.rpartition(".")
                n = f"{stem or name}({i}){dot}{ext}"
                i += 1
            used.add(n)
            try:
                z.write(p, arcname=n)
            except OSError as e:
                return None, f"打包失败（{e.__class__.__name__}）"
    return buf.getvalue(), ""
