#!/usr/bin/env python3
"""灯塔·受控资料台账（kb）—— 只公开「维护者审批过」的资料，且按等级分档。

台账：<状态目录>/kb/<窗口id>/catalog.json
文本：<状态目录>/kb/<窗口id>/text/<doc_id>.md      （MCP 只读抽取结果，不读二进制原件）

闸门（fail-closed）：
  · 台账读不到 / 结构异常                → 一篇都不可读
  · status != approved                  → 不可读
  · 资料 level 不在这个人的 levels 里     → 不可读（审计记「越级访问尝试」）
  · 原文件 sha256 与登记值不一致          → 不可读（审批自动失效，需重新审批）
  · 原文件被移走 / 抽取文本缺失           → 不可读
  · 四道闸（include/exclude/DENY_PATTERNS）永远压过台账 —— 审批与分级都覆盖不了默认拉黑

「公开某篇资料」只能由维护者侧产生（lighthouse.sh kb approve / 管理页）；MCP 侧没有任何写台账的工具。
"""
from __future__ import annotations

import kb_download as DL      # 下载层：门户下载页 + 限时签名链接

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
STATUSES = ("pending", "approved", "rejected", "unsupported")
_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"     # 申请号/查询码用：去掉 0O1I


# ---------------------------------------------------------------- 路径
def kb_root(state_root: Path, wid: str) -> Path:
    return Path(state_root) / "kb" / wid


def catalog_path(state_root: Path, wid: str) -> Path:
    return kb_root(state_root, wid) / "catalog.json"


def requests_path(state_root: Path, wid: str) -> Path:
    return kb_root(state_root, wid) / "requests.json"


def text_path(state_root: Path, wid: str, rel: str) -> Path:
    return kb_root(state_root, wid) / rel


def now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def doc_id(rel: str) -> str:
    """doc_id = sha256(窗口内相对路径)[:8]：稳定、可重算、不泄漏路径信息。"""
    return hashlib.sha256(rel.encode("utf-8")).hexdigest()[:8]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _atomic_write(p: Path, data: dict) -> None:
    """原子替换：写一半断电不留坏文件。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------- 台账
def load_catalog(state_root: Path, wid: str) -> tuple[dict, str]:
    """返回 (台账, 错误)。错误非空 = 台账不可信 → 调用方必须拒绝一切读。

    台账文件**不存在**不算错误（= 一篇都还没批准）。"""
    p = catalog_path(state_root, wid)
    if not p.exists():
        return {"window": wid, "docs": {}}, ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"window": wid, "docs": {}}, f"台账不可读（{e.__class__.__name__}）"
    if not isinstance(data, dict) or not isinstance(data.get("docs"), dict):
        return {"window": wid, "docs": {}}, "台账结构异常（按 fail-closed 处理）"
    return data, ""


def save_catalog(state_root: Path, wid: str, data: dict) -> None:
    _atomic_write(catalog_path(state_root, wid), data)


def upsert_doc(state_root: Path, wid: str, *, rel: str, title: str = "", category: str = "",
               level: str = "", text_rel: str = "", chars: int = 0,
               sha: str = "", text_sha: str = "", status: str = "pending",
               note: str = "", keep_status: bool = False) -> dict:
    """登记/更新一篇资料。keep_status=True 时保留原有状态（内容未变时用）。"""
    data, err = load_catalog(state_root, wid)
    if err:
        raise RuntimeError(err)
    did = doc_id(rel)
    old = (data["docs"] or {}).get(did) or {}
    rec = {
        "id": did, "path": rel, "title": title or old.get("title") or Path(rel).stem,
        "category": category or old.get("category") or "",
        "level": level or old.get("level") or "",
        "tags": list(old.get("tags") or []),
        "status": (old.get("status") if keep_status and old else status) or "pending",
        "text": text_rel or old.get("text") or "",
        "chars": int(chars or old.get("chars") or 0),
        "sha256": sha or old.get("sha256") or "",
        "text_sha256": text_sha or old.get("text_sha256") or "",
        "added_at": old.get("added_at") or now_iso(),
        "approved_at": old.get("approved_at"),
        "approved_by": old.get("approved_by"),
        "note": note or old.get("note") or "",
    }
    data["docs"][did] = rec
    save_catalog(state_root, wid, data)
    return rec


def set_status(state_root: Path, wid: str, did: str, status: str, *,
               level: str = "", by: str = "", note: str = "") -> dict | None:
    """改一篇资料的状态（approve / reject）。所有调用方都是维护者侧。"""
    if status not in STATUSES:
        raise ValueError(f"未知状态 {status!r}")
    data, err = load_catalog(state_root, wid)
    if err:
        raise RuntimeError(err)
    rec = (data["docs"] or {}).get(did)
    if not rec:
        return None
    if status == "approved" and rec.get("unsupported"):
        raise ValueError("这篇的类型还没转成可读文本（需人工转成 .md/.txt 后重新 scan），不能公开")
    rec["status"] = status
    if level and status == "approved":
        rec["level"] = level
    if status == "approved":
        rec["approved_at"] = now_iso()
        rec["approved_by"] = by or "维护者"
    if note:
        rec["note"] = note
    save_catalog(state_root, wid, data)
    return rec


# ---------------------------------------------------------------- 申请
def load_requests(state_root: Path, wid: str) -> tuple[dict, str]:
    p = requests_path(state_root, wid)
    if not p.exists():
        return {"window": wid, "requests": {}}, ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"window": wid, "requests": {}}, f"申请台账不可读（{e.__class__.__name__}）"
    if not isinstance(data, dict) or not isinstance(data.get("requests"), dict):
        return {"window": wid, "requests": {}}, "申请台账结构异常"
    return data, ""


def save_requests(state_root: Path, wid: str, data: dict) -> None:
    _atomic_write(requests_path(state_root, wid), data)


def append_request(state_root: Path, wid: str, *, name: str, dept: str, purpose: str,
                   level_requested: str, contact: str, ip: str, status: str = "pending",
                   decided_note: str = "", auto: bool = False) -> dict:
    """同事侧**唯一**的写动作：新增一条申请（pending）。

    申请的等级只是「申请人想要的」，实际发放等级由维护者定（自动档例外，见 kb_portal）。
    """
    data, err = load_requests(state_root, wid)
    if err:
        raise RuntimeError(err)
    rec = {
        "id": "r_" + secrets.token_hex(4),
        "code": "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6)),
        "name": (name or "").strip()[:40], "dept": (dept or "").strip()[:40],
        "purpose": (purpose or "").strip()[:500],
        "level_requested": (level_requested or "").strip(),
        "contact": (contact or "").strip()[:80],
        "status": status, "created_at": now_iso(), "ip": ip,
        "decided_at": now_iso() if status != "pending" else None,
        "decided_note": decided_note, "auto": bool(auto),
        "notified": status != "pending",
    }
    data["requests"][rec["id"]] = rec
    save_requests(state_root, wid, data)
    return rec


def set_request_status(state_root: Path, wid: str, rid: str, status: str, *,
                       note: str = "", auto: bool = False) -> dict | None:
    data, err = load_requests(state_root, wid)
    if err:
        raise RuntimeError(err)
    rec = (data["requests"] or {}).get(rid)
    if not rec:
        return None
    rec["status"] = status
    rec["decided_at"] = now_iso()
    rec["decided_note"] = note
    rec["auto"] = bool(auto)
    save_requests(state_root, wid, data)
    return rec


def find_request(state_root: Path, wid: str, rid: str, code: str) -> tuple[dict | None, str]:
    """申请人查进度：必须同时给对「申请号 + 查询码」（免得别人乱翻别人的申请）。"""
    data, err = load_requests(state_root, wid)
    if err:
        return None, err
    rec = (data.get("requests") or {}).get((rid or "").strip())
    if not rec:
        return None, "没有这条申请"
    if str(rec.get("code") or "") != (code or "").strip().upper():
        return None, "查询码不对"
    return rec, ""


def pending_requests(state_root: Path, wid: str) -> list[dict]:
    data, _ = load_requests(state_root, wid)
    return [r for r in (data.get("requests") or {}).values() if r.get("status") == "pending"]


# ---------------------------------------------------------------- 闸门
def entry_public_view(e: dict) -> dict:
    """给外部 AI 看的一篇资料元数据 —— **绝不含内部路径**。"""
    return {"doc_id": e.get("id"), "title": e.get("title") or "",
            "category": e.get("category") or "", "level": e.get("level") or "",
            "tags": list(e.get("tags") or []), "chars": int(e.get("chars") or 0)}


def resolve_doc(state_root: Path, wid: str, catalog: dict, did: str, root: Path,
                check_path, levels: list[str] | None = None) -> tuple[dict | None, Path | None, str]:
    """闸门。levels = 这个人地址上的等级集合（None = 不限，仅维护者本机用）。

    check_path(rel) -> (ok, why)：窗口的 include/exclude/默认拉黑判定，由调用方传入 ——
    审批与分级都**覆盖不了**它（拉黑永远赢）。
    """
    e = (catalog.get("docs") or {}).get((did or "").strip())
    if not e:
        return None, None, "没有这个资料编号（只有维护者公开过的资料才有编号）"
    if e.get("status") != "approved":
        return None, None, "该资料尚未公开（待维护者审批）"
    if levels is not None and (e.get("level") or "") not in levels:
        return None, None, f"该资料属于「{e.get('level') or '未分级'}」等级，你的地址无权访问"
    rel = str(e.get("path") or "")
    ok, why = check_path(rel)
    if not ok:
        return None, None, why
    root = Path(root).resolve()          # 解析符号链接（/var → /private/var 之类），否则越界判定会误杀
    try:
        src = (root / rel).resolve()
    except (OSError, RuntimeError, ValueError) as ex:      # 符号链接环 / 空字节等
        return None, None, f"路径解析失败（{ex.__class__.__name__}）"
    if src != root and root not in src.parents:
        return None, None, "路径越界（窗口外一律拒）"
    if not src.is_file():
        return None, None, "原文件已不在（可能被移动或删除）"
    if e.get("sha256") and sha256_file(src) != e["sha256"]:
        return None, None, "原文件内容已变更，此前审批已失效（需维护者重新审批）"
    rel_text = str(e.get("text") or "")
    if not rel_text:
        return None, None, "该资料没有可读文本（抽取失败或格式不支持）"
    tp = text_path(state_root, wid, rel_text)
    if not tp.is_file():
        return None, None, "抽取文本缺失（需维护者重新索引）"
    return e, tp, ""


def approved_entries(state_root: Path, wid: str, catalog: dict, root: Path, check_path,
                     levels: list[str] | None = None) -> list[tuple[dict, Path]]:
    """此刻仍可读的已批准资料（每篇都过闸门；等级过滤已在闸门内完成）。"""
    out = []
    for did, _e in (catalog.get("docs") or {}).items():
        ent, tp, err = resolve_doc(state_root, wid, catalog, did, root, check_path, levels)
        if not err and ent and tp:
            out.append((ent, tp))
    return sorted(out, key=lambda t: (t[0].get("level") or "", t[0].get("category") or "",
                                      t[0].get("title") or ""))


# ---------------------------------------------------------------- MCP 工具（只读四个）
def _public_base_for(win) -> str:
    """这个窗口对外的基地址：配了真域名就用域名，否则退回本机（测试/局域网也不会给出假 URL）。"""
    try:
        import config as C
        host = str(C.load().get("hostname") or "").strip()
        if host and "." in host and not host.startswith(("127.", "localhost", "0.0.0.0")):
            return f"https://{host}{win.path}".rstrip("/")
    except Exception:                                                        # noqa: BLE001
        pass
    return f"http://127.0.0.1:{win.port}{win.path}".rstrip("/")


def register_tools(server, win, state_root: Path, audit, dump, redact,
                   levels_getter=lambda: None, principal_getter=lambda: "-") -> None:
    """把 kb_* 四个只读工具注册到 MCP server 上。

    levels_getter() 返回这个人地址上的等级集合（None = 不限，维护者本机用）。
    这里**没有任何**接受路径的参数，也没有任何写操作。
    """
    def _cat():
        return load_catalog(state_root, win.id)

    def _public_base() -> str:
        return _public_base_for(win)

    def _levels():
        return levels_getter()

    @server.tool(description="本资料库说明：你能看到多少篇、有哪些分类与等级、怎么用（先 kb_search 再 kb_read）。")
    def kb_info() -> str:
        cat, err = _cat()
        if err:
            audit("kb_info", {}, False, {"reason": err})
            return dump({"window": win.id, "error": err})
        docs = approved_entries(state_root, win.id, cat, win.root, win.check, _levels())
        cats: dict = {}
        lvls: dict = {}
        for e, _ in docs:
            k = e.get("category") or "未分类"
            cats[k] = cats.get(k, 0) + 1
            k2 = e.get("level") or "未分级"
            lvls[k2] = lvls.get(k2, 0) + 1
        audit("kb_info", {}, True, {"visible": len(docs)})
        return dump({"window": win.id, "title": win.title, "mode": "controlled-library",
                     "you": {"name": principal_getter(), "levels": _levels()},
                     "visible_docs": len(docs), "categories": cats, "levels": lvls,
                     "how_to_use": "先用 kb_search 定位关键词，再用 kb_read(doc_id, offset, limit) 读正文（分页）。"
                                   "要让对方拿到原文件，用 kb_link(doc_id) 取一条限时下载链接（只读，不能改）。",
                     "note": "清单之外的内容不在你的授权范围内，也查不到编号。需要更多请联系资料维护者。"})

    @server.tool(description="列出你可访问的资料清单（可按分类/等级/标签/标题关键词过滤）。不含内部路径。")
    def kb_list(query: str = "", category: str = "", level: str = "", tag: str = "",
                limit: int = 50, offset: int = 0) -> str:
        cat, err = _cat()
        if err:
            audit("kb_list", {}, False, {"reason": err})
            return dump({"window": win.id, "error": err})
        visible = approved_entries(state_root, win.id, cat, win.root, win.check, _levels())
        docs = [entry_public_view(e) for e, _ in visible]
        _dl = DL.download_cfg(win.cfg)
        for d in docs:                      # 告诉 AI：这篇能不能直接给下载链接
            d["download"] = bool(_dl["enabled"])
        q = (query or "").strip().lower()
        if q:
            docs = [d for d in docs if q in (d["title"] or "").lower()]
        if category:
            docs = [d for d in docs if d["category"] == category]
        if level:
            docs = [d for d in docs if d["level"] == level]
        if tag:
            docs = [d for d in docs if tag in d["tags"]]
        off, lim = max(0, int(offset or 0)), max(1, min(int(limit or 50), 200))
        page = docs[off:off + lim]
        audit("kb_list", {"query": query, "category": category, "level": level, "offset": off}, True,
              {"returned": len(page), "total": len(docs)})
        return dump({"window": win.id, "total": len(docs), "offset": off,
                     "next_offset": (off + lim if off + lim < len(docs) else None), "docs": page})

    @server.tool(description="取一篇资料的**限时下载链接**（默认 15 分钟，只能下这一篇）。"
                            "适合对方想要原文件时：把链接给出来即可，无需你的地址口令。")
    def kb_link(doc_id: str, minutes: int = 0) -> str:
        cat, err = _cat()
        if err:
            audit("kb_link", {"doc_id": doc_id}, False, {"reason": err})
            return dump({"window": win.id, "error": err})
        e, tp, err = resolve_doc(state_root, win.id, cat, doc_id, win.root, win.check, _levels())
        if err:
            audit("kb_link", {"doc_id": doc_id}, False, {"reason": err, "denied": True})
            return dump({"window": win.id, "doc_id": doc_id, "error": err})
        dcfg = DL.download_cfg(win.cfg)
        if not dcfg["enabled"]:
            audit("kb_link", {"doc_id": doc_id}, False, {"reason": "本资料库未开放下载"})
            return dump({"window": win.id, "error": "本资料库未开放文件下载（只能在线阅读）"})
        person = principal_getter() or "-"
        if person in ("", "-"):
            audit("kb_link", {"doc_id": doc_id}, False, {"reason": "无法确定身份"})
            return dump({"window": win.id, "error": "需要以你自己的地址访问才能生成下载链接"})
        m = DL.ttl_minutes(minutes, win.cfg)
        q, exp = DL.make_link(state_root, win.id, (e or {}).get("id") or doc_id, person, m)
        from urllib.parse import quote
        base_url = _public_base()
        url = f"{base_url}/dl/{(e or {}).get('id') or doc_id}?p={quote(person)}&{q}"
        audit("kb_link", {"doc_id": doc_id, "minutes": m}, True, {"person": person})
        return dump({"window": win.id, "doc_id": (e or {}).get("id") or doc_id,
                     "title": (e or {}).get("title"), "level": (e or {}).get("level"),
                     "url": url, "expires_in_minutes": m,
                     "note": "这是限时链接（到期自动失效）；转发给谁都行，但只会失效不会延长。"})

    @server.tool(description="在你可访问的资料里检索关键词（多关键词=全部命中）。越权/未公开资料不会命中；片段自动脱敏。")
    def kb_search(keyword: str, limit: int = 20) -> str:
        kws = [k for k in re.split(r"[,，\s]+", keyword or "") if k]
        if not kws:
            audit("kb_search", {"keyword": keyword}, False, {"reason": "空关键词"})
            return dump({"window": win.id, "error": "keyword 不能为空"})
        cat, err = _cat()
        if err:
            audit("kb_search", {"keyword": keyword}, False, {"reason": err})
            return dump({"window": win.id, "error": err})
        lim = max(1, min(int(limit or 20), 50))
        hits: list = []
        scanned = 0
        for e, tp in approved_entries(state_root, win.id, cat, win.root, win.check, _levels()):
            scanned += 1
            try:
                body = tp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for ln, line in enumerate(body.splitlines(), 1):
                if all(k.lower() in line.lower() for k in kws):
                    text, _ = redact(line.strip()[:240])
                    hits.append({"doc_id": e.get("id"), "title": e.get("title"),
                                 "level": e.get("level"), "line": ln, "text": text})
                    if len(hits) >= lim:
                        break
            if len(hits) >= lim:
                break
        audit("kb_search", {"keyword": keyword}, True, {"hits": len(hits), "docs_scanned": scanned})
        return dump({"window": win.id, "keywords": kws, "hits": len(hits),
                     "docs_scanned": scanned, "results": hits})

    @server.tool(description="按资料编号读正文（1 起，分页，带行号）。只接受 doc_id，不接受路径；内容自动脱敏。")
    def kb_read(doc_id: str, offset: int = 1, limit: int = 200) -> str:
        cat, err = _cat()
        if err:
            audit("kb_read", {"doc_id": doc_id}, False, {"reason": err})
            return dump({"window": win.id, "error": err})
        e, tp, err = resolve_doc(state_root, win.id, cat, doc_id, win.root, win.check, _levels())
        if err or tp is None or e is None:
            audit("kb_read", {"doc_id": doc_id}, False, {"reason": err, "denied": True})
            return dump({"window": win.id, "error": err, "doc_id": doc_id})
        lines = tp.read_text(encoding="utf-8", errors="replace").splitlines()
        off, lim = max(1, int(offset or 1)), max(1, min(int(limit or 200), 500))
        chunk = lines[off - 1: off - 1 + lim]
        text, n = redact("\n".join(f"{off + i}|{ln}" for i, ln in enumerate(chunk)))
        audit("kb_read", {"doc_id": doc_id, "offset": off}, True,
              {"redactions": n, "chars": len(text), "level": e.get("level")})
        return dump({"window": win.id, "doc_id": doc_id, "title": e.get("title"),
                     "category": e.get("category"), "level": e.get("level"),
                     "total_lines": len(lines), "offset": off, "returned": len(chunk),
                     "next_offset": (off + len(chunk) if off + len(chunk) <= len(lines) else None),
                     "redactions": n, "content": text})
