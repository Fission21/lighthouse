#!/usr/bin/env python3
"""闸门加固测试（第 5 套）：专打 DENY / exclude / include 的绕过与边界。

自带隔离环境（临时项目 + 临时注册表 + 临时状态 + 自己起一个服务），
不碰 windows.json，也不碰 ~/.lighthouse。

为什么有这套：在 macOS/Windows 这类**大小写不敏感**的文件系统上，
`docs/.ENV` 与 `docs/.env` 是同一个文件。若规则按字符串大小写敏感比较，
一个字母换个大小写就能读到本该拉黑的密钥；同理 `PRIVATE/x` 能绕过 exclude
的 `private/**`。这套测试把那些绕过钉死。

覆盖：
  ① 拉黑名单：.env / id_rsa / *.pem / credentials / token / apikey 及其大小写变体
  ② 版本库内部：.git/config、.git/logs/HEAD、.git/HEAD 一律不可读
  ③ exclude 与 include 的大小写一致性（不能靠大写绕过排除目录）
  ④ 路径逃逸：../ 与指向库外的符号链接
  ⑤ 体积上限：超过 max_file_kb 的文件被拒
  ⑥ 枚举面：list_files 不列出被拉黑项，search 不把拉黑文件的内容带出来
  ⑦ 正常文件不被误伤（加固不能把窗口变成什么都看不见）

用法: python3 test_hardening.py [--keep]
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable

fails: list[str] = []
count = 0


def check(label: str, good: bool, detail: str = "") -> None:
    global count
    count += 1
    print(f"  {'✅' if good else '❌'} {label}{(' — ' + detail) if detail else ''}")
    if not good:
        fails.append(label)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def call(session, name: str, args: dict) -> dict:
    res = await session.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in res.content if getattr(c, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


async def main() -> int:
    port = free_port()
    tmp = Path(tempfile.mkdtemp(prefix="lh-harden-"))
    root = tmp / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "private").mkdir(parents=True)
    (root / ".git" / "logs").mkdir(parents=True)

    # 敏感样本。注意 macOS 上大小写不敏感：写 .env 一次即可，.ENV 指向同一 inode
    (root / "docs" / ".env").write_text("SECRET=demo-lower\n", encoding="utf-8")
    (root / "docs" / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n", encoding="utf-8")
    (root / "docs" / "apikey.txt").write_text("KEY=abcdef123456\n", encoding="utf-8")
    (root / "docs" / "my-secrets.txt").write_text("PASSWORD=hunter2\n", encoding="utf-8")
    (root / "docs" / "config.env").write_text("TOKEN=config-env-token\n", encoding="utf-8")
    (root / "docs" / "credentials.yaml").write_text("user: a\npass: b\n", encoding="utf-8")
    (root / "docs" / "token.md").write_text("TOKEN=doc-token\n", encoding="utf-8")
    (root / "docs" / "normal.md").write_text("# 正常文档\n", encoding="utf-8")
    (root / "src" / "config").mkdir(parents=True)
    (root / "src" / "config" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "src" / "config" / ".env").write_text("SECRET=src-env\n", encoding="utf-8")
    (root / "docs" / "big.txt").write_text("x" * (900 * 1024), encoding="utf-8")
    (root / "private" / "plan.md").write_text("被 exclude 的目录\n", encoding="utf-8")
    (root / ".git" / "config").write_text('[remote "origin"]\n\turl = https://u:GHTOKEN@github.com/x/y.git\n', encoding="utf-8")
    (root / ".git" / "logs" / "HEAD").write_text("0000 aa Someone <a@b.c> 1 +0800\tcommit: init\n", encoding="utf-8")
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    try:
        os.symlink("/etc/hosts", root / "docs" / "link_out.txt")
    except OSError:
        pass

    registry = tmp / "windows.json"
    registry.write_text(json.dumps({"windows": {"hard": {
        "title": "加固测试窗", "root": str(root),
        "include": ["**/*"], "exclude": ["private/**"], "deny_extra": [],
        "port": port, "path": "/w-hard", "visibility": "local",
        "auto_grant": True, "write": {"enabled": False},
    }}}, ensure_ascii=False, indent=2), encoding="utf-8")

    state = tmp / "state"
    env = {**os.environ, "LIGHTHOUSE_STATE": str(state), "LIGHTHOUSE_PY": PY,
           "LIGHTHOUSE_REGISTRY": str(registry), "WINDOW_REGISTRY": str(registry),
           "WINDOW_ID": "hard", "WINDOW_PORT": str(port), "WINDOW_PATH": "/w-hard"}
    url = f"http://127.0.0.1:{port}/w-hard"
    print(f"加固测试 | 隔离环境: {tmp}\n窗口: hard (include=**/*, exclude=private/**)  服务: {url}\n")

    srv = subprocess.Popen([PY, str(REPO / "core" / "server.py")], env=env,
                           stdout=open(tmp / "server.log", "wb"), stderr=subprocess.STDOUT)
    try:
        for _ in range(40):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.5)
        else:
            print("❌ 服务没起来")
            print((tmp / "server.log").read_text()[-800:])
            return 2

        async with streamable_http_client(url) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()

                print("① 拉黑名单（含大小写变体 —— 大小写不敏感文件系统上的绕过）")
                for path, label in [
                    ("docs/.env", "小写 .env"),
                    ("docs/.ENV", "大写 .ENV（同一文件）"),
                    ("docs/id_rsa", "小写 id_rsa"),
                    ("docs/Id_Rsa", "混合大小写 Id_Rsa（同一文件）"),
                    ("docs/apikey.txt", "apikey.txt"),
                    ("docs/my-secrets.txt", "my-secrets.txt（前缀非行首）"),
                    ("docs/config.env", "config.env（非 .env 开头）"),
                    ("docs/token.md", "token.md"),
                    ("docs/credentials.yaml", "credentials.yaml"),
                ]:
                    d = await call(session, "read_file", {"path": path})
                    check(f"{label} 不可读", "content" not in d, d.get("error", "")[:40])

                print("\n② 版本库内部（.git）一律不可读")
                for path in (".git/config", ".git/logs/HEAD", ".git/HEAD"):
                    d = await call(session, "read_file", {"path": path})
                    check(f"{path} 不可读", "content" not in d, d.get("error", "")[:40])

                print("\n③ exclude / include 的大小写一致性")
                d = await call(session, "read_file", {"path": "PRIVATE/plan.md"})
                check("大写 PRIVATE/ 不能绕过 exclude private/**", "content" not in d, d.get("error", "")[:40])
                d = await call(session, "read_file", {"path": "private/plan.md"})
                check("小写 private/ 同样被排除", "content" not in d, d.get("error", "")[:40])

                print("\n④ 路径逃逸")
                d = await call(session, "read_file", {"path": "../../etc/hosts"})
                blob = json.dumps(d, ensure_ascii=False)
                check("../../ 逃逸被拒", "content" not in d and ("越界" in blob or "给看范围" in blob), d.get("error", "")[:40])
                d = await call(session, "read_file", {"path": "docs/link_out.txt"})
                blob = json.dumps(d, ensure_ascii=False)
                check("指向库外的符号链接被拒", "content" not in d and ("越界" in blob or "给看范围" in blob), d.get("error", "")[:40])

                print("\n⑤ 体积上限")
                d = await call(session, "read_file", {"path": "docs/big.txt"})
                check("超 max_file_kb 的文件被拒", "content" not in d, d.get("error", "")[:60])

                print("\n⑥ 枚举面不泄漏")
                lf = json.dumps(await call(session, "list_files", {"path": "", "depth": 3}), ensure_ascii=False)
                check("list_files 不列出 .git", ".git" not in lf)
                check("list_files 不列出 private", "private" not in lf)
                check("list_files 不列出被拉黑文件", ".env" not in lf and "id_rsa" not in lf)
                se = json.dumps(await call(session, "search", {"keyword": "SECRET", "limit": 5}), ensure_ascii=False)
                check("search 不把拉黑文件内容带出来", "SECRET=" not in se)

                print("\n⑦ 加固不误伤：正常文件照旧可读")
                d = await call(session, "read_file", {"path": "docs/normal.md"})
                check("普通文档可读", bool(d.get("content")), str(d)[:60])
                ra = await call(session, "request_access", {"include": ["**"], "reason": "整套测试"})
                check("提权流程未被加固破坏", ra.get("status") == "granted", json.dumps(ra, ensure_ascii=False)[:60])

            print("\n⑧ include 写成 `dir/**` 时，目录本身必须可列举")
            # 回归：`src/**` 曾生成 `^src/.*$`，于是 `list_files("src")` 被判「不匹配 include」——
            # 「列出某个子目录」是最基本的操作，实测被 ChatGPT 当场撞到。
            port2 = free_port()
            reg = json.loads(registry.read_text(encoding="utf-8"))
            reg["windows"]["hard2"] = {
                "title": "目录列举窗", "root": str(root),
                "include": ["src/**"], "exclude": [], "deny_extra": [],
                "port": port2, "path": "/w-hard2", "visibility": "local",
                "write": {"enabled": False},
            }
            registry.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
            env2 = {**env, "WINDOW_ID": "hard2", "WINDOW_PORT": str(port2), "WINDOW_PATH": "/w-hard2"}
            srv2 = subprocess.Popen([PY, str(REPO / "core" / "server.py")], env=env2,
                                    stdout=open(tmp / "server2.log", "wb"), stderr=subprocess.STDOUT)
            try:
                for _ in range(40):
                    with socket.socket() as s_:
                        if s_.connect_ex(("127.0.0.1", port2)) == 0:
                            break
                    time.sleep(0.5)
                async with streamable_http_client(f"http://127.0.0.1:{port2}/w-hard2") as (r2, w2):
                    async with ClientSession(r2, w2) as s2:
                        await s2.initialize()
                        d = await call(s2, "list_files", {"path": "src"})
                        check("list_files('src') 不再被判越界", "files" in d, json.dumps(d, ensure_ascii=False)[:70])
                        d = await call(s2, "list_files", {"path": "src/config"})
                        check("list_files('src/config') 可列", "files" in d, json.dumps(d, ensure_ascii=False)[:70])
                        d = await call(s2, "read_file", {"path": "src/config/app.py"})
                        check("src/config/app.py 可读", bool(d.get("content")), d.get("error", "")[:40])
                        d = await call(s2, "read_file", {"path": "src/config/.env"})
                        check("放宽匹配后 .env 仍被拉黑", "content" not in d, d.get("error", "")[:40])
                        d = await call(s2, "read_file", {"path": "docs/normal.md"})
                        check("`src/**` 之外的路径仍被拒", "content" not in d, d.get("error", "")[:40])
                        d = await call(s2, "list_files", {"path": "srx"})   # 前缀相似但不是它
                        check("形近目录 'srx' 不被误放行", "files" not in d or not d.get("files"),
                              json.dumps(d, ensure_ascii=False)[:70])
            finally:
                srv2.terminate()
                try:
                    srv2.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    srv2.kill()

    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        if "--keep" not in sys.argv:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== 加固测试：{count - len(fails)} 项通过，{len(fails)} 项失败 =====")
    if fails:
        print("失败项:", fails)
    return 0 if not fails else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
