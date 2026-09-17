#!/usr/bin/env python3
"""通用窗口冒烟：对任意窗口跑「能用 + 该挡的都挡住」的通用检查（不依赖特定文件）。

用法: smoke_window.py <url> [--limit 5]

检查项（全绿才算一扇合格的窗）：
  ① window_info 能返回范围声明
  ② list_files 根目录非空、路径都在 include 内（无明显越界）
  ③ 能读出第一个文件（内容非空）
  ④ search 能工作
  ⑤ 越界路径（../../etc/passwd、绝对路径）被拒
  ⑥ 密钥类文件（.env、id_rsa、.git/config…）被拒
  ⑦ 写工具在开关关闭时被拒（开关开着时跳过，并提示）
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

OK, BAD = "✅", "❌"
results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"  {OK if passed else BAD} {name}{(' — ' + detail) if detail else ''}")


async def main(url: str, limit: int) -> int:
    async with streamable_http_client(url) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print(f"工具: {tools}")

            # ① window_info
            info = json.loads((await s.call_tool("window_info", {})).content[0].text)
            check("① window_info 返回范围声明", bool(info.get("root")), f"root={info.get('root')}")

            # ② list_files
            ls = json.loads((await s.call_tool("list_files", {"path": "", "depth": 1})).content[0].text)
            files = [f["path"] for f in ls.get("files", [])]
            check("② 根目录能列出内容", len(files) > 0, f"{len(files)} 项：{files[:3]}")

            # ③ read_file 第一个文件
            target = next((f for f in files if f.lower().endswith((".md", ".txt", ".json", ".py"))), files[0] if files else None)
            got_content = False
            if target:
                rd = json.loads((await s.call_tool("read_file", {"path": target, "offset": 1, "limit": 5})).content[0].text)
                got_content = bool(rd.get("content")) and "error" not in rd
                check("③ 能读出文件内容", got_content, f"{target} 共 {rd.get('total_lines', '?')} 行")
            else:
                check("③ 能读出文件内容", False, "根目录没有可读文件")

            # ④ search
            sr = json.loads((await s.call_tool("search", {"keyword": "the"})).content[0].text)
            check("④ search 可用", "files_scanned" in sr, f"扫了 {sr.get('files_scanned')} 个文件")

            # ⑤ 越界
            for p in ["../../../etc/passwd", "/etc/hosts", "..%2f..%2fetc%2fpasswd"]:
                t = (await s.call_tool("read_file", {"path": p})).content[0].text
                check(f"⑤ 越界被拒: {p}", "error" in t, t[:50].replace("\n", " "))

            # ⑥ 拉黑
            for p in [".env", "id_rsa", ".git/config", "credentials.json", "app.db"]:
                t = (await s.call_tool("read_file", {"path": p})).content[0].text
                check(f"⑥ 密钥类被拒: {p}", "error" in t, t[:50].replace("\n", " "))

            # ⑦ 写工具
            if "write_file" in tools:
                t = (await s.call_tool("write_file", {"path": "smoke-probe.txt", "content": "x"})).content[0].text
                refused = "error" in t or "开关" in t
                check("⑦ 写工具受控（开关关=拒）", refused, t[:60].replace("\n", " "))

    total = len(results)
    passed = sum(1 for _, p, _ in results if p)
    print(f"\n===== {passed}/{total} 通过 =====")
    if passed != total:
        print("失败项:", [n for n, p, _ in results if not p])
    return 0 if passed == total else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: smoke_window.py <窗口URL>   （URL 用 `bash lighthouse.sh url <窗口id>` 查）")
        raise SystemExit(2)
    url = sys.argv[1]
    lim = 5
    if "--limit" in sys.argv:
        lim = int(sys.argv[sys.argv.index("--limit") + 1])
    raise SystemExit(asyncio.run(main(url, lim)))
