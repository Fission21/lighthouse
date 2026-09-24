#!/usr/bin/env python3
"""把原始资料抽成纯文本（MCP 只读抽取结果，不读二进制原件）。

支持：
  .md/.txt/.markdown  直读（UTF-8）
  .docx/.doc/.rtf     macOS 自带 textutil
  .pdf                MinerU（首选，保结构）→ 退化到 pdftext（纯文字）
不支持：
  .xlsx/.xls/.pptx    登记为「需人工转文本」，**不可审批通过**（fail-closed，不假装成功）

原则：抽取失败 / 结果为空 → 返回错误，调用方**不得**把这篇标成可读。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import kb as KB
import kb_folder as FOLD  # 台账读写（kb.py 不反向依赖本模块，无循环）

SUPPORTED_TEXT = {".md", ".txt", ".markdown"}
SUPPORTED_TEXTUTIL = {".docx", ".doc", ".rtf", ".odt", ".html", ".htm"}
SUPPORTED_PDF = {".pdf"}
MINERU_VENV = Path.home() / "demo" / "ai-tools" / "mineru-venv"


def _mineru() -> str:
    for cand in (MINERU_VENV / "bin" / "mineru", Path("/opt/homebrew/bin/mineru")):
        if cand.is_file():
            return str(cand)
    return shutil.which("mineru") or ""


def _pdftext() -> str:
    cand = MINERU_VENV / "bin" / "pdftext"
    if cand.is_file():
        return str(cand)
    return shutil.which("pdftext") or ""


def supported_exts() -> set[str]:
    return SUPPORTED_TEXT | SUPPORTED_TEXTUTIL | SUPPORTED_PDF


def extract(src: Path, mode: str = "auto") -> tuple[str | None, str]:
    """返回 (文本, 错误)。错误非空 = 这篇不能被批准。"""
    src = Path(src)
    ext = src.suffix.lower()
    if ext in SUPPORTED_TEXT:
        try:
            return src.read_text(encoding="utf-8", errors="replace"), ""
        except OSError as e:
            return None, f"读取失败（{e.__class__.__name__}）"
    if ext in SUPPORTED_TEXTUTIL:
        return _extract_textutil(src)
    if ext in SUPPORTED_PDF:
        return _extract_pdf(src, mode)
    return None, f"暂不支持的类型 {ext}（需人工转成 .md/.txt 后再 scan）"


def _extract_textutil(src: Path) -> tuple[str | None, str]:
    if not shutil.which("textutil"):
        return None, "本机没有 textutil（非 macOS 时请先转成 .md/.txt）"
    try:
        out = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(src)],
                             capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"textutil 调用失败（{e.__class__.__name__}）"
    if out.returncode != 0:
        return None, f"textutil 抽取失败（{(out.stderr or '').strip()[:120]}）"
    text = out.stdout or ""
    if not text.strip():
        return None, "textutil 抽出来是空的（可能是扫描件/图片版）"
    return text, ""


def _extract_pdf(src: Path, mode: str) -> tuple[str | None, str]:
    if mode != "none":
        text, err = _extract_pdf_mineru(src)
        if text and text.strip():
            return text, ""
        mineru_err = err
    else:
        mineru_err = "已按要求跳过 MinerU"

    text, err2 = _extract_pdf_text(src)
    if text and text.strip():
        head = f"<!-- 抽取方式: pdftext（纯文字，未保结构）；MinerU: {mineru_err} -->\n\n"
        return head + text, ""
    return None, f"PDF 抽取失败（MinerU: {mineru_err}；pdftext: {err2}）"


def _extract_pdf_mineru(src: Path) -> tuple[str | None, str]:
    """MinerU（保结构、带标题层级）。长文档必须限制处理窗口，否则 MPS 上会崩。"""
    bin_ = _mineru()
    if not bin_:
        return None, f"没找到 MinerU（期望 {MINERU_VENV}/bin/mineru）"
    with tempfile.TemporaryDirectory(prefix="kb-mineru-") as td:
        env = {**os.environ, "MINERU_PROCESSING_WINDOW_SIZE": os.environ.get("MINERU_PROCESSING_WINDOW_SIZE", "32")}
        env.pop("PYTHONPATH", None)          # 会话里注入的 PYTHONPATH 会污染 conda/venv
        try:
            p = subprocess.run([bin_, "-p", str(src), "-o", td, "-b", "pipeline"],
                               capture_output=True, text=True, env=env, timeout=1800)
        except (OSError, subprocess.SubprocessError) as e:
            return None, f"MinerU 调用失败（{e.__class__.__name__}）"
        if p.returncode != 0:
            return None, f"MinerU 退出码 {p.returncode}（{(p.stderr or '').strip()[-160:]}）"
        mds = sorted(Path(td).rglob("*.md"))
        if not mds:
            return None, "MinerU 跑完但没有产出 markdown"
        best = max(mds, key=lambda f: f.stat().st_size)
        return best.read_text(encoding="utf-8", errors="replace"), ""


def _extract_pdf_text(src: Path) -> tuple[str | None, str]:
    """pdftext（pypdfium2 自带）：只抽文字层，扫描件会得到空。"""
    bin_ = _pdftext()
    if not bin_:
        return None, "没找到 pdftext"
    try:
        out = subprocess.run([bin_, str(src)], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"pdftext 调用失败（{e.__class__.__name__}）"
    if out.returncode != 0:
        return None, f"pdftext 退出码 {out.returncode}"
    return out.stdout, ""


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        t, e = extract(Path(arg))
        print(f"{arg}: {'✅ ' + str(len(t)) + ' 字符' if t else '⛔ ' + e}")


# --------------------------------------------------------------------------- 扫库（CLI 与网页端共用）
def walk_docs(docs_dir: Path) -> list[Path]:
    """遍历资料目录里的文件（跳过点开头的文件/目录）。"""
    if not docs_dir.is_dir():
        return []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(docs_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in sorted(filenames):
            if f.startswith("."):
                continue
            out.append(Path(dirpath) / f)
    return sorted(out)


def scan_library(state: Path, wid: str, root: Path, docs_rel: str, extract_mode: str = "auto",
                 default_level: str = "", folder_levels: dict | None = None,
                 explicit_level: str = "") -> dict:
    """把资料目录同步进台账：新增 → pending；内容变了 → 退回 pending；没变 → 跳过。

    返回 {"rows": [(标记, 相对路径, 说明)], "new/changed/same/skipped/failed": int, "docs_dir": str}
    只写台账与抽取文本，不改已批准篇目的等级 —— 审批状态永远由维护者决定。
    """
    docs_dir = root / docs_rel
    files = walk_docs(docs_dir)
    res = {"rows": [], "new": 0, "changed": 0, "same": 0, "skipped": 0, "failed": 0,
           "docs_dir": str(docs_dir), "total": len(files)}
    if not files:
        return res
    cat0, _err = KB.load_catalog(state, wid)
    old_docs = dict((cat0 or {}).get("docs") or {})
    for f in files:
        rel = str(f.relative_to(root))
        did = KB.doc_id(rel)
        cat_name = f.parent.name if f.parent != docs_dir else ""
        fdir = "" if f.parent == docs_dir else f.parent.relative_to(docs_dir).as_posix()
        # 等级：先看这个文件夹（含父文件夹）的默认等级，没有再用窗口默认
        lvl = explicit_level or FOLD.level_for(folder_levels or {}, fdir, default_level or "")
        if f.suffix.lower() not in supported_exts():
            KB.upsert_doc(state, wid, rel=rel, category=cat_name, level=lvl, status="unsupported",
                          note=f"暂不支持的类型 {f.suffix}（需人工转成 .md/.txt）", keep_status=False)
            c, _ = KB.load_catalog(state, wid)
            e2 = (c.get("docs") or {}).get(did)
            if e2:
                e2["unsupported"] = True
                KB.save_catalog(state, wid, c)
            res["skipped"] += 1
            res["rows"].append(("跳", rel, "类型不支持"))
            continue
        sha = KB.sha256_file(f)
        old = old_docs.get(did) or {}
        if old.get("sha256") == sha and old.get("text"):
            res["same"] += 1
            continue
        text, err = extract(f, extract_mode)
        if err or not text:
            KB.upsert_doc(state, wid, rel=rel, category=cat_name, level=lvl, status="pending",
                          sha=sha, note=f"抽取失败：{err}", keep_status=False)
            res["failed"] += 1
            res["rows"].append(("错", rel, err or "抽取为空"))
            continue
        tp = KB.kb_root(state, wid) / "text" / f"{did}.md"
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text(text, encoding="utf-8")
        was_approved = old.get("status") == "approved"
        KB.upsert_doc(state, wid, rel=rel, category=cat_name, level=old.get("level") or lvl,
                      text_rel=f"text/{did}.md", chars=len(text), sha=sha,
                      text_sha=KB.sha256_file(tp), status="pending",
                      note=("原文件内容已变更 → 审批失效，需重新审批" if was_approved else ""),
                      keep_status=False)
        if old:
            res["changed"] += 1
            res["rows"].append(("改", rel, f"{len(text)} 字符（内容变了，已退回待批）"))
        else:
            res["new"] += 1
            res["rows"].append(("新", rel, f"{len(text)} 字符"))
    return res
