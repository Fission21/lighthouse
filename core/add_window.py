#!/usr/bin/env python3
"""新增一个受控窗口：给任意本地项目登记一扇窗（只写注册表文件，不动服务）。

**第一原则：范围由用户决定。** 所以本工具不会替你默认「全给看」——
没给 --include / --preset 时会**问你**（交互）；非交互环境则要求你显式选择，拒绝静默默认。

用法:
  add_window.py <id> <项目路径> [--title 名字] [--preset docs|docs+code|all|none]
                [--include "a/**,b.md"] [--exclude "x/**"] [--port N] [--no-write] [--public] [--yes]

预设：
  docs       只给文档       README* / docs/** / *.md
  docs+code  文档 + 源码    ↑ + src/** tests/** + 常见源码后缀
  all        全部（除密钥与 exclude 规则外都能看）
  none       全不给（先登记，之后随时用写入 --scope 或对话里申请提权）
"""
from __future__ import annotations

import argparse
import json
import random
import string
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "core"))
import config as C  # noqa: E402  —— 注册表路径的唯一来源

# ⚠️ 不要自己拼路径：服务端（server.py / render_services.py）读的是 config.REGISTRY_PATH，
#    CLI 必须写到同一份文件，否则会出现「命令说登记好了、服务根本没这扇窗」。
REGISTRY = C.REGISTRY_PATH
HOME = str(Path.home())
CLI = "bash lighthouse.sh"

PRESETS = {
    "docs": ["README*", "docs/**", "*.md"],
    "docs+code": ["README*", "docs/**", "*.md", "src/**", "app/**", "lib/**", "tests/**",
                  "*.py", "*.js", "*.ts", "*.tsx", "*.go", "*.rs", "*.java", "*.rb", "*.php",
                  "*.c", "*.cpp", "*.h", "*.hpp", "*.cs", "*.swift", "*.kt", "*.sh"],
    "all": ["**/*"],
    "none": [],
}
DEFAULT_EXCLUDES = ["node_modules/**", ".venv/**", "venv/**", "__pycache__/**", ".git/**"]


def _port_free(port: int) -> bool:
    """端口没被占用才返回 True（避免和别的服务撞端口，撞了会一直重启失败）。"""
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def ask_scope() -> list[str]:
    """交互问范围——把选择权交给用户。"""
    print("\n这扇窗要给外面的 AI 看多大范围？（这一步由你决定，别默认全放开）\n")
    print("  1) 只给文档          README* / docs/** / *.md")
    print("  2) 文档 + 源码       ↑ + src/** tests/** 及常见源码后缀")
    print("  3) 全部（除密钥与排除项）  **/*")
    print("  4) 先全不给，之后按需申请提权")
    print("  5) 我自己列（逗号分隔的 glob）\n")
    while True:
        try:
            choice = input("选择 [1-5]（默认 1）: ").strip() or "1"
        except EOFError:
            print("❌ 非交互环境：请显式给 --preset docs|docs+code|all|none 或 --include \"...\"")
            raise SystemExit(2)
        if choice in ("1", "2", "3", "4"):
            key = {"1": "docs", "2": "docs+code", "3": "all", "4": "none"}[choice]
            return list(PRESETS[key])
        if choice == "5":
            try:
                raw = input("范围（逗号分隔的 glob，例如 src/**,*.py）: ").strip()
            except EOFError:
                raise SystemExit(2)
            got = [x.strip() for x in raw.split(",") if x.strip()]
            if got:
                return got
            print("至少要给一条规则。")
        else:
            print("请输入 1-5。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("id", help="窗口 id（英文短名，如 myproj）")
    ap.add_argument("root", help="项目根目录（绝对路径或 ~/...）")
    ap.add_argument("--title")
    ap.add_argument("--preset", choices=sorted(PRESETS), help="范围预设（不选也不给 --include 时会问你）")
    ap.add_argument("--include", help="逗号分隔的 glob（显式指定，优先于 --preset）")
    ap.add_argument("--exclude", default="", help="逗号分隔的 glob（默认已排除 node_modules/.venv/__pycache__/.git）")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-write", action="store_true", help="该窗口永久只读（write.enabled=false）")
    ap.add_argument("--public", action="store_true", help="标记为可对外（visibility=public）")
    ap.add_argument("--yes", action="store_true", help="跳过确认（脚本化用）")
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

    # ---- 范围：显式 > 预设 > 问用户 ----
    if a.include:
        include = [x.strip() for x in a.include.split(",") if x.strip()]
        src = "--include"
    elif a.preset:
        include = list(PRESETS[a.preset])
        src = f"--preset {a.preset}"
    else:
        include = ask_scope()
        src = "交互选择"

    exclude = [x.strip() for x in a.exclude.split(",") if x.strip()] or list(DEFAULT_EXCLUDES)

    used_ports = {w.get("port") for w in wins.values()}
    port = a.port or next((p for p in range(8940, 9000) if p not in used_ports and _port_free(p)), None)
    if not port:
        print("❌ 没有空闲端口（8940-8999）")
        return 1
    slug = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))

    # ---- 给用户看清楚：这扇窗能看到什么 ----
    print("\n──── 这扇窗的范围（请确认）────")
    print(f"  项目根目录 : {root}")
    print(f"  能看的     : {include or '（什么都不给看，之后可按需申请提权）'}")
    print(f"  不给看的   : {exclude}")
    print(f"  永远看不到 : .env / 私钥 / 凭据 / 数据库 等密钥类文件（默认拉黑，不可关）")
    print(f"  范围来源   : {src}")
    print(f"  写权限     : {'登记为永久只读' if a.no_write else '登记为可用（运行时开关默认关着，需要你手动开）'}")
    print(f"  对外发布   : {'可发公网（--public）' if a.public else '仅本机（local；要对外需改 visibility）'}")
    if not a.yes:
        try:
            ans = input("\n就按这个范围开窗？[y/N] ").strip().lower()
        except EOFError:
            ans = "y"
        if ans not in ("y", "yes"):
            print("已取消（什么都没改）。想调整就用 --include / --exclude 重来。")
            return 1

    entry = {
        "title": a.title or root.name,
        "root": str(root).replace(HOME, "~"),
        "include": include,
        "exclude": exclude,
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

    print(f"\n✅ 已登记窗口 「{a.id}」")
    print(f"""
下一步（三步走）：
  1) 起服务 : {CLI} start
  2) 本地测 : python3 tests/smoke_window.py http://127.0.0.1:{port}{entry['path']}
  3) 发公网 : {CLI + ' publish   # 再到 ChatGPT 插件页「创建应用」填公网 URL' if a.public else '（当前是 local：确认要对外时改 visibility 或加 --public 重来）'}

范围永远可以再调：
  · 想扩大      → 对话里让 agent 调 request_access 申请，然后你批准：{CLI} approve {a.id}
  · 想先授权给它自己提 → {CLI} elevate {a.id} 30        （30 分钟内的申请自动批准）
  · 想收回      → {CLI} deny {a.id}
  · 想改注册表  → 直接编辑 windows.json 里 {a.id} 那一条""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
