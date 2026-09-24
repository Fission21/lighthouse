#!/usr/bin/env python3
"""只读审计：证明一扇「受控窗口」对外只有读路径、且动不了本地任何内容。

做四件事：
  ① 工具清单审计——端点上暴露的工具必须全在预期集合里（写工具锁在开关后）
  ② 写入尝试轰炸——试一批写类工具名/参数，必须全部失败
  ③ 协议与路径攻击——HTTP 方法、目录穿越、符号链接、绝对路径、空字节
  ④ 文件指纹比对——轰炸前后对窗口根目录做快照（sha256+大小+mtime），必须逐字节一致

用法: python3 audit_readonly.py <窗口URL> <窗口根目录>

提示：想测符号链接攻击，先在窗口根目录里造两个链接（run_all_tests.sh 会自动造）：
  ln -s /etc/passwd <root>/link-passwd.txt
  ln -s ~/.ssh/id_rsa <root>/link-env.txt
"""
import asyncio
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

if len(sys.argv) < 2:
    print("用法: audit_readonly.py <窗口URL> [窗口根目录]")
    raise SystemExit(2)
URL = sys.argv[1]
ROOT = Path(sys.argv[2] if len(sys.argv) > 2 else ".").expanduser().resolve()

READ_ONLY_ALLOWLIST = {"window_info", "list_files", "read_file", "search", "request_access"}
EXPECTED_TOOLS = READ_ONLY_ALLOWLIST | {"write_file", "edit_file", "make_dir", "delete_file"}
WRITE_TOOL_NAMES = [
    "write_file", "create_file", "save_file", "edit_file", "patch_file", "apply_patch",
    "delete_file", "remove_file", "move_file", "rename_file", "mkdir", "make_directory",
    "append_file", "upload_file", "exec", "shell", "run_command", "terminal",
    "window_write", "write", "update_file", "truncate",
]
PATH_ATTACKS = [
    "../../../etc/passwd",
    "/etc/passwd",
    "..\\..\\windows\\system32",
    "docs/../../etc/passwd",
    "....//....//etc/passwd",
    "docs/%2e%2e/%2e%2e/etc/passwd",
    "link-passwd.txt",          # 符号链接 → /etc/passwd（由 run_all_tests.sh 创建）
    "link-env.txt",             # 符号链接 → 用户敏感文件（同上）
    "docs/\x00.env",
    "docs/.env",
    str(Path.home() / ".ssh" / "id_rsa"),
    "../../../../../../etc/passwd",
]


def snapshot(root: Path) -> dict:
    """对目录做指纹快照：每个文件的 sha256/大小/mtime + 完整文件清单。"""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        try:
            if p.is_symlink():
                out[rel] = {"symlink": str(p.readlink())}
            elif p.is_file():
                h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                st = p.stat()
                out[rel] = {"sha256": h, "size": st.st_size, "mtime": st.st_mtime}
            else:
                out[rel] = {"dir": True}
        except OSError as e:
            out[rel] = {"error": str(e)}
    return out


async def main() -> int:
    print(f"审计目标: {URL}\n窗口根目录: {ROOT}\n")
    before = snapshot(ROOT)

    fails, ok_count = [], 0

    def check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok_count
        mark = "✅" if good else "❌"
        if good:
            ok_count += 1
        else:
            fails.append(label)
        print(f"  {mark} {label}{(' — ' + detail) if detail else ''}")

    async with streamable_http_client(URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ① 工具清单
            print("① 工具清单审计")
            tools = [t.name for t in (await session.list_tools()).tools]
            print(f"  暴露的工具: {tools}")
            check("工具集合与设计一致（4 读 + 1 提权申请 + 4 写，写工具受开关约束）", set(tools) == EXPECTED_TOOLS,
                  f"意外工具: {set(tools) - EXPECTED_TOOLS}" if set(tools) - EXPECTED_TOOLS else "")
            write_tools = set(tools) & {"write_file", "edit_file", "make_dir", "delete_file"}
            check("写工具已登记（锁在开关后）", write_tools == {"write_file", "edit_file", "make_dir", "delete_file"})
            check("提权工具是申请制（request_access 存在，且没有直接授权类工具）",
                  "request_access" in tools and not ({"grant_access", "set_scope", "approve"} & set(tools)))

            # ② 写类工具名轰炸
            print("\n② 写入尝试（应全部失败）")
            for name in WRITE_TOOL_NAMES:
                try:
                    res = await session.call_tool(name, {"path": "hacked.txt", "content": "x"})
                    text = "".join(getattr(c, "text", "") for c in res.content if getattr(c, "type", "") == "text")
                    err = getattr(res, "is_error", False)
                    # 通过标准：未知工具 / 被写开关拒绝 / 被范围拒绝——都不能产生写效果
                    good = bool(err) or any(k in text for k in (
                        "Unknown tool", "写开关", "未开放写权限", "路径越界", "给看范围", "拉黑", "confirm=true"))
                    check(f"写工具 {name} 被拒", good, text[:60].replace("\n", " "))
                except Exception as e:
                    check(f"写工具 {name} 被拒", True, type(e).__name__)

            # 读工具塞写参数（多余参数会被忽略——关键是不能产生任何写效果）
            for args in [
                {"path": "README.md", "content": "OVERWRITE"},
                {"path": "README.md", "mode": "w"},
                {"path": "hacked.txt", "content": "x", "overwrite": True},
            ]:
                try:
                    res = await session.call_tool("read_file", args)
                    text = "".join(getattr(c, "text", "") for c in res.content if getattr(c, "type", "") == "text")
                    err = getattr(res, "is_error", False)
                    # 通过标准：要么报错拒绝，要么是正常读取响应，且绝不回显注入的写入内容
                    good = bool(err) or (("window" in text or "error" in text) and "OVERWRITE" not in text)
                    check(f"read_file 塞写参数无写效果 {list(args)[1:]}", good, text[:60].replace("\n", " "))
                except Exception as e:
                    check(f"read_file 塞写参数无写效果 {list(args)[1:]}", True, type(e).__name__)

            # ③ 路径攻击
            print("\n③ 路径穿越/符号链接/绝对路径（应全部拒绝或找不到）")
            for p in PATH_ATTACKS:
                try:
                    res = await session.call_tool("read_file", {"path": p})
                    text = "".join(getattr(c, "text", "") for c in res.content if getattr(c, "type", "") == "text")
                    refused = any(k in text for k in ("越界", "拉黑", "给看范围", "不存在", "不是文件", "非法路径"))
                    check(f"路径 {p!r} 未泄露内容", refused, text[:80].replace("\n", " "))
                except Exception as e:
                    check(f"路径 {p!r} 未泄露内容", True, type(e).__name__)

            # ④ 协议层：HTTP 方法
            print("\n④ 协议层方法探测")
            for method in ["PUT", "PATCH", "DELETE", "OPTIONS"]:
                try:
                    req = urllib.request.Request(URL, method=method, data=b"{}",
                                                 headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        code = r.status
                except urllib.error.HTTPError as e:
                    code = e.code
                except Exception as e:
                    code = f"{type(e).__name__}"
                # GET/DELETE 在 MCP streamable-http 里是协议方法（读流/结束会话），不走业务写路径
                check(f"HTTP {method} 无写效果（状态 {code}）",
                      not (isinstance(code, int) and code in (200, 201, 204) and method in ("PUT", "PATCH")),
                      "仅协议方法" if method in ("GET", "DELETE") else "")

    # ⑤ 指纹比对
    print("\n⑤ 文件指纹比对（轰炸前后）")
    after = snapshot(ROOT)
    changed = []
    for k in set(before) | set(after):
        if before.get(k) != after.get(k):
            changed.append(k)
    check("窗口根目录内容零变动", not changed, f"变动: {changed[:5]}" if changed else f"{len(after)} 个条目全部一致")
    check("没有被创建 hacked.txt", not (ROOT / "hacked.txt").exists())

    print(f"\n===== 审计结论：{ok_count} 项通过，{len(fails)} 项失败 =====")
    if fails:
        print("失败项:", fails)
    return 0 if not fails else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
