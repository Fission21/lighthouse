"""敏感下载守望：只报告「上次看过之后新出现的」高等级资料下载。

给运维脚本 / cron 用 —— 有人在几分钟内下载了核心资料，就推一条到用户面前。
两个原则：

* **只读**：不写台账、不写审计，只动一个 offset 文件；出任何问题就当没有新东西（fail-quiet），
  守望脚本自己绝不能变成噪音源。
* **只报新的**：按行号记住上次看到哪，重复跑不会重复报；offset 文件丢了最多补报一次，
  不会漏（宁可多报一次，不可漏报）。
"""
from __future__ import annotations

import json
from pathlib import Path

import kb as KB


def audit_lines(state_root: Path, wid: str) -> list[dict]:
    """把审计读成行列表；坏行跳过（一行坏数据不能毁掉守望）。"""
    p = Path(state_root) / "audit" / f"{wid}.jsonl"
    if not p.is_file():
        return []
    out = []
    with open(p, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def read_offset(state_file: Path) -> int:
    try:
        return max(0, int(Path(state_file).read_text(encoding="utf-8").strip() or 0))
    except (OSError, ValueError):
        return 0


def write_offset(state_file: Path, n: int) -> None:
    p = Path(state_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(int(n)), encoding="utf-8")


_ACTIONS = {"kb_download": "下载", "kb_bundle": "打包下载", "kb_link": "要了限时链接"}


def offset_file(state_root: Path, wid: str) -> Path:
    """默认的 offset 文件 —— 网页横幅和 CLI 必须看同一个，否则一边「已读」另一边还报。"""
    return Path(state_root) / "state" / f"kb-watch-{wid}.offset"


def unread(state_root: Path, wid: str, levels: list[str], state_file: Path | None = None,
           limit: int = 20) -> list[dict]:
    """还没被「知道了」过的敏感下载（只看不动 offset，给网页横幅用）。"""
    return watch(state_root, wid, levels, state_file or offset_file(state_root, wid),
                 peek=True, limit=limit)


def ack(state_root: Path, wid: str, state_file: Path | None = None) -> int:
    """把「看过了」记下来（offset 推到当前审计末尾），返回推到了第几行。"""
    p = state_file or offset_file(state_root, wid)
    n = len(audit_lines(state_root, wid))
    write_offset(p, n)
    return n


def _level_map(state_root: Path, wid: str) -> dict[str, dict]:
    cat, _err = KB.load_catalog(state_root, wid)
    return dict((cat or {}).get("docs") or {})


def watch(state_root: Path, wid: str, levels: list[str], state_file: Path,
          tools: tuple[str, ...] = ("kb_download", "kb_bundle", "kb_link"),
          skip_admin: bool = True, skip: tuple[str, ...] = (),
          peek: bool = False, limit: int = 20) -> list[dict]:
    """返回新的敏感下载（列表按时间先后）。peek=True 只看看、不推进 offset。"""
    rows = audit_lines(state_root, wid)
    start = min(read_offset(state_file), len(rows))
    docs = _level_map(state_root, wid)
    want = {str(x) for x in levels if str(x).strip()}
    out: list[dict] = []
    for r in rows[start:]:
        if r.get("tool") not in tools or not r.get("ok"):
            continue
        who = str(r.get("principal") or "-")
        if skip_admin and who.startswith("维护者"):
            continue
        if any(sk and sk in who for sk in skip):          # 自己人的账号不吵自己
            continue
        args = r.get("args") or {}
        ids = [str(x) for x in (args.get("ids") or ([args["doc_id"]] if args.get("doc_id") else []))]
        hits = [(i, docs.get(i) or {}) for i in ids]
        hits = [(i, e) for i, e in hits if str(e.get("level") or "") in want]
        if not hits:
            continue
        out.append({
            "ts": str(r.get("ts") or "")[:16].replace("T", " "),
            "who": who,
            "levels": r.get("levels") or [],
            "tool": str(r.get("tool")),
            "action": _ACTIONS.get(str(r.get("tool") or ""), str(r.get("tool") or "")),
            "mode": args.get("mode") or "",
            "docs": [{"doc_id": i, "title": (e.get("title") or i), "level": e.get("level") or ""}
                     for i, e in hits],
            "bytes": args.get("bytes") or 0,
            "ip": str(r.get("ip") or ""),
        })
    if not peek:
        write_offset(state_file, len(rows))
    return out[-limit:] if limit else out


def fmt_line(rec: dict) -> str:
    """一行中文，给 cron 直接推给人看。"""
    docs = "、".join(f"{d['title']}（{d['level']}）" for d in rec["docs"])
    lv = "、".join(str(x) for x in (rec.get("levels") or [])) or "无等级"
    mb = f"　{round(rec['bytes'] / 1048576, 2)} MB" if rec.get("bytes") else ""
    mode = {"original": "（原件）", "text": "（文本）"}.get(rec.get("mode"), "")
    return (f"⚠️ {rec['ts']}　{rec['who']}（{lv}）{rec['action']}{mode}：{docs}{mb}"
            f"　来源 {rec['ip'] or '-'}")
