#!/usr/bin/env python3
"""写模式测试：验证「开关关=绝对只读；开关开=可写但每次改动都备份+记账」。

三段式：
  A) 开关关着 → 一切写尝试必须被拒，且文件零变动
  B) 打开开关 → 创建/追加/精确替换/建目录/删除（含 confirm 与唯一匹配保护）全流程；越界/拉黑/超限仍必须被拒
  C) 关回开关 → 回到只读，再试一次写必须被拒

跑完自动把开关关回（fail-safe），并清理测试文件。

用法: python3 test_write.py <窗口URL> <窗口id> [窗口根目录]
"""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

if len(sys.argv) < 3:
    print("用法: test_write.py <窗口URL> <窗口id> [窗口根目录]")
    raise SystemExit(2)
URL = sys.argv[1]
WID = sys.argv[2]
SWITCH_ROOT = Path(os.path.expanduser(os.environ.get("LIGHTHOUSE_STATE", "~/.lighthouse")))
SWITCH = SWITCH_ROOT / "state" / "window-write.json"
BACKUPS = SWITCH_ROOT / "backups" / WID
ROOT = Path(sys.argv[3] if len(sys.argv) > 3 else ".").expanduser().resolve()
CORE = Path(__file__).resolve().parent.parent / "core"
TEST_DIR = "write-test"          # 所有测试文件都放这个目录里，最后整体清掉

fails: list[str] = []
count = 0


def check(label: str, good: bool, detail: str = "") -> None:
    global count
    count += 1
    print(f"  {'✅' if good else '❌'} {label}{(' — ' + detail) if detail else ''}")
    if not good:
        fails.append(label)


def set_switch(state: str) -> None:
    subprocess.run([sys.executable, str(CORE / "switch.py"), "set", WID, state],
                   check=True, capture_output=True)


def snapshot(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        try:
            if p.is_symlink():
                out[rel] = "symlink"
            elif p.is_file():
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            else:
                out[rel] = "dir"
        except OSError:
            out[rel] = "err"
    return out


async def call(session, name, args):
    res = await session.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in res.content if getattr(c, "type", "") == "text")
    try:
        return json.loads(text), getattr(res, "is_error", False)
    except json.JSONDecodeError:
        return {"raw": text}, getattr(res, "is_error", False)


def exclude_probe(excludes: list[str]) -> str | None:
    """从窗口的 exclude 规则里推一个应该被挡住的文件路径（测 exclude 闸门）。"""
    for pat in excludes or []:
        head = pat.split("**")[0].split("*")[0].rstrip("/")
        if head:
            return f"{head}/__probe__.md"
    return None


async def main() -> int:
    print(f"写模式测试 | 窗口={WID} | URL={URL}\n根目录={ROOT}\n")
    set_switch("off")  # 从干净状态开始
    before = snapshot(ROOT)

    async with streamable_http_client(URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("A) 开关关闭 → 必须全拒")
            info, _ = await call(session, "window_info", {})
            check("window_info 报告只读", info.get("write", {}).get("currently_writable") is False)
            for tool, args in [
                ("write_file", {"path": f"{TEST_DIR}/a.md", "content": "x"}),
                ("edit_file", {"path": "README.md", "old_string": "灯塔", "new_string": "灯塔2"}),
                ("make_dir", {"path": f"{TEST_DIR}/d"}),
                ("delete_file", {"path": "docs/notes.md", "confirm": True}),
            ]:
                res, err = await call(session, tool, args)
                check(f"{tool} 被拒（开关关闭）", "写开关" in json.dumps(res, ensure_ascii=False),
                      json.dumps(res, ensure_ascii=False)[:70])
            check("测试文件没被创建", not (ROOT / TEST_DIR).exists())
            check("README 未被动过", snapshot(ROOT) == before)

            print("\nB) 打开开关 → 写入流程")
            set_switch("on")
            info, _ = await call(session, "window_info", {})
            check("window_info 报告可写", info.get("write", {}).get("currently_writable") is True)

            res, _ = await call(session, "write_file", {"path": f"{TEST_DIR}/hello.md", "content": "第一行\n"})
            check("write_file 创建成功", res.get("ok") is True, json.dumps(res, ensure_ascii=False)[:90])
            f = ROOT / TEST_DIR / "hello.md"
            check("文件真的落盘且内容正确", f.is_file() and f.read_text() == "第一行\n")

            res, _ = await call(session, "write_file", {"path": f"{TEST_DIR}/hello.md", "content": "第二行\n", "mode": "append"})
            check("append 追加成功", res.get("ok") is True and f.read_text() == "第一行\n第二行\n")

            res, _ = await call(session, "edit_file", {"path": f"{TEST_DIR}/hello.md", "old_string": "第二行", "new_string": "第二行（已改）"})
            check("edit_file 精确替换成功", res.get("ok") is True and "已改" in f.read_text(),
                  f"sha {res.get('sha_before')}→{res.get('sha_after')}")

            res, _ = await call(session, "edit_file", {"path": f"{TEST_DIR}/hello.md", "old_string": "行", "new_string": "X"})
            check("多个匹配时被拒（要求唯一）", "匹配到" in json.dumps(res, ensure_ascii=False))

            res, _ = await call(session, "make_dir", {"path": f"{TEST_DIR}/sub"})
            check("make_dir 成功", res.get("ok") is True and (ROOT / TEST_DIR / "sub").is_dir())

            # 开关开着，但范围/拉黑闸门依然有效
            res, _ = await call(session, "write_file", {"path": "../escaped.txt", "content": "x"})
            check("开关开着也不能越界写", "越界" in json.dumps(res, ensure_ascii=False))
            res, _ = await call(session, "write_file", {"path": "docs/.env", "content": "x"})
            check("开关开着也不能写拉黑文件", "拉黑" in json.dumps(res, ensure_ascii=False))
            probe = exclude_probe(info.get("exclude", []))
            if probe:
                res, _ = await call(session, "write_file", {"path": probe, "content": "x"})
                check(f"开关开着也不能写 exclude 范围（{probe}）", "给看范围" in json.dumps(res, ensure_ascii=False))
            else:
                check("开关开着也不能写 exclude 范围（该窗口无 exclude 规则，跳过）", True)
            res, _ = await call(session, "write_file", {"path": f"{TEST_DIR}/big.md", "content": "x" * 300_000})
            check("超限内容被拒", "超限" in json.dumps(res, ensure_ascii=False))

            # 删除保护 + 备份
            res, _ = await call(session, "delete_file", {"path": f"{TEST_DIR}/hello.md"})
            check("删除必须显式 confirm", "confirm" in json.dumps(res, ensure_ascii=False))
            res, _ = await call(session, "delete_file", {"path": f"{TEST_DIR}/hello.md", "confirm": True})
            check("confirm 后删除成功且给了备份路径", res.get("ok") is True and res.get("backup"), json.dumps(res, ensure_ascii=False)[:110])
            check("测试文件已删", not f.exists())
            check("备份目录里有备份", BACKUPS.exists() and any(BACKUPS.iterdir()))

            print("\nC) 关回开关 → 回到只读")
            set_switch("off")
            res, _ = await call(session, "write_file", {"path": f"{TEST_DIR}/again.md", "content": "x"})
            check("再写被拒", "写开关" in json.dumps(res, ensure_ascii=False))
            info, _ = await call(session, "window_info", {})
            check("window_info 回到只读", info.get("write", {}).get("currently_writable") is False)

    # 清理测试目录（用文件系统直接清，测试目录本来就只属于本测试）
    import shutil
    if (ROOT / TEST_DIR).exists():
        shutil.rmtree(ROOT / TEST_DIR)
    after = snapshot(ROOT)
    check("清理后目录回到测试前状态", after == before, f"差异: {set(after) ^ set(before)}" if after != before else "")

    print(f"\n===== 写模式测试：{count - len(fails)} 项通过，{len(fails)} 项失败 =====")
    if fails:
        print("失败项:", fails)
    return 0 if not fails else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
