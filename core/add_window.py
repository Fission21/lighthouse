#!/usr/bin/env python3
"""新增一个受控窗口：给任意本地项目登记一扇窗（只写 windows.json，不动服务）。

用法:
  add_window.py <id> <项目路径> [--title 名字] [--include "a/**,b.md"] [--exclude "x/**"]
                [--port N] [--no-write] [--public]

默认：include=**/*（全给看）、写权限登记为可用但开关默认关、visibility=local（对外前需 --public）。
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
REGISTRY = ROOT_DIR / "windows.json"
HOME = str(Path.home())
CLI = "bash lighthouse.sh"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("id", help="窗口 id（英文短名，如 miji）")
    ap.add_argument("root", help="项目根目录（绝对路径或 ~/...）")
    ap.add_argument("--title")
    ap.add_argument("--include", default="**/*", help="逗号分隔的 glob")
    ap.add_argument("--exclude", default="", help="逗号分隔的 glob")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-write", action="store_true", help="该窗口永久只读（write.enabled=false）")
    ap.add_argument("--public", action="store_true", help="标记为可对外（visibility=public）")
    a = ap.parse_args()

    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    wins = data["windows"]
    if a.id in wins:
        print(f"❌ 窗口已存在: {a.id}")
        return 1

    root = Path(a.root).expanduser().resolve()
    if not root.is_dir():
        print(f"❌ 目录不存在: {root}")
        return 1

    used_ports = {w.get("port") for w in wins.values()}
    port = a.port or next((p for p in range(8940, 9000) if p not in used_ports), None)
    if not port:
        print("❌ 没有空闲端口（8940-8999）")
        return 1
    slug = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))

    entry = {
        "title": a.title or root.name,
        "root": str(root).replace(HOME, "~"),
        "include": [x.strip() for x in a.include.split(",") if x.strip()],
        "exclude": [x.strip() for x in a.exclude.split(",") if x.strip()],
        "deny_extra": [],
        "port": port,
        "path": f"/w-{a.id}-{slug}",
        "visibility": "public" if a.public else "local",
        "max_file_kb": 512,
        "max_output_chars": 60000,
        "write": {"enabled": not a.no_write, "max_write_kb": 256, "backup": True},
    }
    wins[a.id] = entry
    REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"✅ 已登记窗口 「{a.id}」")
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    print(f"""
下一步（三步走）：
  1) 起服务 : {CLI} start
  2) 本地测 : python3 tests/smoke_window.py http://127.0.0.1:{port}{entry['path']}
  3) 发公网 : {CLI + ' publish   # 再到 ChatGPT 插件页「创建应用」填公网 URL' if a.public else '（当前是 local：确认要对外时改 visibility 或加 --public 重来）'}""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
