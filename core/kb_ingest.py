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
