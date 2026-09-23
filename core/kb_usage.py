#!/usr/bin/env python3
"""从审计 JSONL 聚合「谁在用、用了什么」。纯读，不改任何状态。

用法（也可通过 `bash lighthouse.sh kb usage <窗口>` 调用）：
  kb_usage.py <窗口> [--days 7] [--person 张三] [--by person|day|doc|tool] [--csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))


def _state_root() -> Path:
    """状态目录：与 server / scope 同一套优先级（LIGHTHOUSE_STATE > 配置 > ~/.lighthouse）。"""
    import os
    env = os.environ.get("LIGHTHOUSE_STATE")
    if env:
        return Path(os.path.expanduser(env))
    here = Path(__file__).resolve().parent
    for name in ("config.local.json", "config.json"):
        try:
            cfg = json.loads((here.parent / name).read_text(encoding="utf-8"))
            if cfg.get("state_dir"):
                return Path(os.path.expanduser(cfg["state_dir"]))
        except (OSError, json.JSONDecodeError):
            continue
    return Path(os.path.expanduser("~/.lighthouse"))


def read_audit(state_root: Path, wid: str, days: int | None = None) -> list[dict]:
    """读审计；坏行跳过（一行坏数据不能毁掉整张报表）。"""
    p = Path(state_root) / "audit" / f"{wid}.jsonl"
    if not p.is_file():
        return []
    cutoff = None
    if days:
        cutoff = (datetime.now(CST) - timedelta(days=int(days))).timestamp()
    rows = []
    with open(p, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff:
                try:
                    ts = datetime.fromisoformat(str(rec.get("ts"))).timestamp()
                except (TypeError, ValueError):
                    continue
                if ts < cutoff:
                    continue
            rows.append(rec)
    return rows


def _key_for(rec: dict, by: str) -> str:
    if by == "person":
        return str(rec.get("principal") or "-")
    if by == "day":
        return str(rec.get("ts") or "")[:10]
    if by == "tool":
        return str(rec.get("tool") or "-")
    if by == "doc":
        did = (rec.get("args") or {}).get("doc_id") or (rec.get("args") or {}).get("path")
        if did:
            return str(did)
        return f"（{rec.get('tool')}）"
    return "-"


def summarize(rows: list[dict], by: str = "person") -> list[dict]:
    buckets: dict[str, dict] = {}
    for rec in rows:
        k = _key_for(rec, by)
        b = buckets.setdefault(k, {"key": k, "calls": 0, "ok": 0, "denied": 0,
                                   "last_seen": "", "docs": Counter(), "tools": Counter()})
        b["calls"] += 1
        b["ok"] += 1 if rec.get("ok") else 0
        b["denied"] += 1 if (rec.get("ok") is False) else 0
        ts = str(rec.get("ts") or "")
        b["last_seen"] = max(b["last_seen"], ts)
        b["tools"][str(rec.get("tool") or "-")] += 1
        did = (rec.get("args") or {}).get("doc_id")
        if did and rec.get("ok"):
            b["docs"][str(did)] += 1
    out = []
    for b in buckets.values():
        out.append({"key": b["key"], "calls": b["calls"], "ok": b["ok"], "denied": b["denied"],
                    "last_seen": b["last_seen"][:16].replace("T", " "),
                    "top_docs": "; ".join(f"{d}×{n}" for d, n in b["docs"].most_common(3)),
                    "top_tools": "; ".join(f"{t}×{n}" for t, n in b["tools"].most_common(3))})
    return sorted(out, key=lambda r: -r["calls"])


def _width(s: str) -> int:
    """显示宽度：中文等宽字符算 2（len() 对中文会错位）。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _width(s))


def render_table(rows: list[dict], header: list[tuple[str, str]]) -> str:
    if not rows:
        return "（没有记录）"
    widths = []
    for key, label in header:
        widths.append(max(_width(label), *(_width(str(r.get(key) or "")) for r in rows)))
    head = "  ".join(_pad(label, w) for (_, label), w in zip(header, widths))
    out = [head, "-" * _width(head)]
    for r in rows:
        out.append("  ".join(_pad(str(r.get(key) or ""), w) for (key, _), w in zip(header, widths)))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="受控资料库用量报表（从审计聚合）")
    ap.add_argument("window")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--person", default="")
    ap.add_argument("--by", choices=["person", "day", "doc", "tool"], default="person")
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--state-root", default="")
    a = ap.parse_args(argv)

    state_root = Path(a.state_root) if a.state_root else _state_root()
    rows = read_audit(state_root, a.window, a.days)
    if a.person:
        rows = [r for r in rows if str(r.get("principal") or "-") == a.person]
    summary = summarize(rows, a.by)
    header = [("key", {"person": "同事", "day": "日期", "doc": "资料", "tool": "工具"}[a.by]),
              ("calls", "调用"), ("ok", "成功"), ("denied", "被拒"),
              ("last_seen", "最后活跃"), ("top_docs", "常读资料")]
    if a.by == "tool":
        header[-1] = ("top_tools", "说明")

    if a.csv:
        w = csv.writer(sys.stdout)
        w.writerow([k for k, _ in header])
        for r in summary:
            w.writerow([r.get(k, "") for k, _ in header])
        return 0

    total = len(rows)
    denied = sum(1 for r in rows if r.get("ok") is False)
    print(f"窗口 {a.window} · 近 {a.days} 天 · 共 {total} 次记录（成功 {total - denied} / 被拒 {denied}）")
    print(render_table(summary, header))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
