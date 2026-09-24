#!/usr/bin/env python3
"""灯塔 · 文档一致性检查（纯 Python，无第三方依赖）

跟着 tests/run_all_tests.sh 一起跑，也可以单跑：python3 tests/check_docs.py

检查项：
  1. 相对链接：Markdown 里的 `](路径)` 目标存在（外链、纯锚点跳过；代码块内容不参与）
  2. ADR：文件名编号唯一且连续、每篇都有状态行、与 decisions/INDEX.md 双向一致
  3. 套件名：run_all_tests.sh 里的每个套件名必须在 AGENTS.md 出现；
     当前状态类文档不得硬编码套件数量（"五套"这种写法会随新套件漂移）
  4. 对外称呼：仓库文档里不出现私人称呼
  5. 编码：文本文件为 UTF-8 无 BOM，且不含替换字符 U+FFFD

失败退出码 1，输出 "N/M 通过"。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache"}

# 历史记录类文档：允许写当时的套件数量（它们描述的是过去的事实）
HISTORY_DOCS = {"CHANGELOG.md", "ISSUES.md"}

TEXT_SUFFIXES = {".md", ".py", ".sh", ".json", ".toml", ".yml", ".yaml", ".css", ".html", ".js", ".txt"}
PRIVATE_WORDS = re.compile("主" + "人|宝" + "贝|老公|老婆|亲爱的")
SELF = Path(__file__).resolve()
FENCE = re.compile(r"^\s*(```|~~~)")
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
ADR_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{4})-([a-z0-9-]+)\.md$")
SUITE_RUN = re.compile(r'^run\s+"([^"]+)"')

results: list[tuple[bool, str, str]] = []


def record(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))


def walk_files(suffixes: set[str]) -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        out.append(p)
    return sorted(out)


def strip_code_fences(text: str) -> str:
    lines, inside, out = text.splitlines(), False, []
    for line in lines:
        if FENCE.match(line):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return "\n".join(out)


def strip_history_section(text: str) -> str:
    """砍掉「更新日志 / Changelog」之后的内容：那里写的是当时的套件数量，属于历史事实。"""
    kept: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,3}\s*(更新日志|版本历史|Changelog|History)\s*$", line.strip(), re.IGNORECASE):
            break
        kept.append(line)
    return "\n".join(kept)


def check_links() -> None:
    broken: list[str] = []
    for path in walk_files({".md"}):
        text = strip_code_fences(path.read_text(encoding="utf-8", errors="replace"))
        for raw in LINK.findall(text):
            target = raw.strip().strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#", "/")):
                continue
            file_part = target.split("#", 1)[0]
            if not file_part:
                continue
            if not (path.parent / file_part).exists():
                broken.append(f"{path.relative_to(ROOT)} → {target}")
    record(not broken, "Markdown 相对链接可达", "\n      ".join(broken[:12]))


def check_adr() -> None:
    adr_dir = ROOT / "decisions" / "adr"
    index = ROOT / "decisions" / "INDEX.md"
    problems: list[str] = []
    if not adr_dir.is_dir() or not index.is_file():
        record(False, "ADR 编号与索引一致", "缺少 decisions/adr/ 或 decisions/INDEX.md")
        return

    files = sorted(p for p in adr_dir.rglob("*.md") if p.name != "_template.md")
    numbers: list[int] = []
    index_text = index.read_text(encoding="utf-8")
    for path in files:
        m = ADR_NAME.match(path.name)
        if not m:
            problems.append(f"文件名不合规范（YYYY-MM-DD-NNNN-slug.md）：{path.relative_to(ROOT)}")
            continue
        numbers.append(int(m.group(2)))
        body = path.read_text(encoding="utf-8")
        if "- **状态**：" not in body:
            problems.append(f"缺状态行：{path.name}")
        rel = path.relative_to(ROOT / "decisions").as_posix()
        if rel not in index_text:
            problems.append(f"INDEX.md 未收录：{rel}")

    if len(set(numbers)) != len(numbers):
        problems.append("ADR 编号有重复")
    if numbers and sorted(numbers) != list(range(1, len(numbers) + 1)):
        problems.append(f"ADR 编号不连续：{sorted(numbers)}")

    indexed = {int(n) for n in re.findall(r"\| \[(\d{4})\]", index_text)}
    for n in sorted(indexed - set(numbers)):
        problems.append(f"INDEX.md 提到 ADR-{n:04d}，但没有对应文件")
    for n in sorted(set(numbers) - indexed):
        problems.append(f"ADR-{n:04d} 没进 INDEX.md 的按编号表")

    record(not problems, f"ADR 编号与索引一致（{len(files)} 篇）", "\n      ".join(problems[:12]))


def check_suites() -> None:
    runner = ROOT / "tests" / "run_all_tests.sh"
    problems: list[str] = []
    names: list[str] = []
    for line in runner.read_text(encoding="utf-8").splitlines():
        m = SUITE_RUN.match(line.strip())
        if m:
            names.append(re.sub(r"\s*\(.*?\)\s*$", "", m.group(1)).strip())

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for name in names:
        if name not in agents:
            problems.append(f"AGENTS.md 没提到套件「{name}」")

    count_word = re.compile(r"[五六七八九十]套|(?<!第)\d+\s*套|\b(?:five|six|seven|eight|nine|ten)\s+suites?\b")
    for path in walk_files({".md"}):
        if path.name in HISTORY_DOCS:
            continue
        for lineno, line in enumerate(strip_history_section(path.read_text(encoding="utf-8")).splitlines(), 1):
            if count_word.search(line):
                problems.append(f"{path.relative_to(ROOT)}:{lineno} 写死了套件数量，改成套件名")
    record(not problems, f"套件名与文档一致（{len(names)} 套）", "\n      ".join(problems[:12]))


def check_private_words() -> None:
    hits: list[str] = []
    for path in walk_files({".md", ".py"}):
        if path.resolve() == SELF:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if PRIVATE_WORDS.search(line) and "git grep" not in line:
                hits.append(f"{path.relative_to(ROOT)}:{lineno}")
    record(not hits, "对外文案无私人称呼", "\n      ".join(hits[:12]))


def check_encoding() -> None:
    bad: list[str] = []
    for path in walk_files(TEXT_SUFFIXES):
        raw = path.read_bytes()
        rel = path.relative_to(ROOT)
        if raw.startswith(b"\xef\xbb\xbf"):
            bad.append(f"{rel} 带 BOM")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            bad.append(f"{rel} 不是 UTF-8（{exc}）")
            continue
        if "\ufffd" in text:
            bad.append(f"{rel} 含替换字符 U+FFFD（可能被错误编码写过）")
    record(not bad, "文本编码 UTF-8 无 BOM", "\n      ".join(bad[:12]))


def main() -> int:
    check_links()
    check_adr()
    check_suites()
    check_private_words()
    check_encoding()

    for ok, name, detail in results:
        print(f"  {'✅' if ok else '❌'} {name}" + (f"\n      {detail}" if detail and not ok else ""))
    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
