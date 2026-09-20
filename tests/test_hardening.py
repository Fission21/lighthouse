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
  ③ exclude 与 include 的大小写一致性（不能靠大写绕过排除目录，也不靠 list/search 绕出内容）
  ④ 路径逃逸：../ 与指向库外的符号链接
  ⑤ 体积上限：超过 max_file_kb 的文件被拒
  ⑥ 枚举面：list_files 不列出被拉黑项，search 不把拉黑文件的内容带出来
  ⑦ 正常文件不被误伤（加固不能把窗口变成什么都看不见）
  ⑧ include 写成 `dir/**` 时，目录本身必须可列举
  ⑨ 「按类型给看」的 include（`**/*.py`）不能让目录树在列举时消失
  ⑩ 公网入口只含 public 窗口（visibility=local 的必须被剔除）
  ⑪ `?` 单字符通配符：只吃一个字符、不跨 `/`；exclude 目录的**列举/检索**面不泄漏
  ⑫ 无状态模式：旧会话 id / 无会话 id 的裸 POST 不再被拒（服务重启对已连客户端无感）
  ⑬ 接入方式选项：bind=0.0.0.0 局域网直连 / json_response 纯 JSON 回应 / 默认仅本机不外露

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
import urllib.error
import urllib.request
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


def _lan_ip() -> str:
    """本机局域网 IP（取不到返回空串）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


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
    (root / "src" / "util").mkdir(parents=True)
    (root / "src" / "util" / "helper.py").write_text("def h():\n    pass\n", encoding="utf-8")
    (root / "src" / "config").mkdir(parents=True)
    (root / "src" / "config" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "src" / "config" / ".env").write_text("SECRET=src-env\n", encoding="utf-8")
    (root / "docs" / "big.txt").write_text("x" * (900 * 1024), encoding="utf-8")
    (root / "private" / "plan.md").write_text("被 exclude 的目录\n", encoding="utf-8")
    # ⑪ 用：`?` 形态的样本
    (root / "q" / "sub").mkdir(parents=True)
    (root / "q" / "file1.md").write_text("一号\n", encoding="utf-8")
    (root / "q" / "file2.md").write_text("二号\n", encoding="utf-8")
    (root / "q" / "file10.md").write_text("十号（? 只吃一个字符，不该命中）\n", encoding="utf-8")
    (root / "q" / "file.md").write_text("零号（? 不吃空字符）\n", encoding="utf-8")
    (root / "q" / "sub" / "file1.md").write_text("子目录里的（? 不跨 /）\n", encoding="utf-8")
    (root / "a").mkdir(parents=True)
    (root / "aXb.txt").write_text("一问号命中\n", encoding="utf-8")
    (root / "a" / "b.txt").write_text("斜杠不该被 ? 吃掉\n", encoding="utf-8")
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
                # 被 exclude 的目录：列举与检索两条路也不能漏内容（只堵 read 不算堵住）
                d = await call(session, "list_files", {"path": "private"})
                check("list_files('private') 被拒（exclude 目录）",
                      "files" not in d and "exclude" in str(d.get("error", "")), str(d)[:70])
                d = await call(session, "list_files", {"path": "PRIVATE"})
                check("list_files('PRIVATE') 同样被拒（大小写不敏感）", "files" not in d, str(d)[:70])
                se = json.dumps(await call(session, "search", {"keyword": "被 exclude", "limit": 5}), ensure_ascii=False)
                check("search 不返回被 exclude 目录里的内容", "private/plan.md" not in se, se[:80])

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

            print("\n⑨ 「按类型给看」的 include（**/*.py）不能让目录树在列举时消失")
            # 回归：列举时曾用 include 给目录剪枝，而 `**/*.py` 永远不匹配目录名，
            # 于是 list_files('') 返回 0 项、list_files('src') 直接拒绝 —— agent 完全发现不了文件。
            port3 = free_port()
            reg = json.loads(registry.read_text(encoding="utf-8"))
            reg["windows"]["hard3"] = {
                "title": "按类型给看窗", "root": str(root),
                "include": ["**/*.py"], "exclude": [], "deny_extra": [],
                "port": port3, "path": "/w-hard3", "visibility": "local",
                "write": {"enabled": False},
            }
            registry.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
            env3 = {**env, "WINDOW_ID": "hard3", "WINDOW_PORT": str(port3), "WINDOW_PATH": "/w-hard3"}
            srv3 = subprocess.Popen([PY, str(REPO / "core" / "server.py")], env=env3,
                                    stdout=open(tmp / "server3.log", "wb"), stderr=subprocess.STDOUT)
            try:
                for _ in range(40):
                    with socket.socket() as s_:
                        if s_.connect_ex(("127.0.0.1", port3)) == 0:
                            break
                    time.sleep(0.5)
                async with streamable_http_client(f"http://127.0.0.1:{port3}/w-hard3") as (r3, w3):
                    async with ClientSession(r3, w3) as s3:
                        await s3.initialize()
                        d = await call(s3, "list_files", {"path": "", "depth": 3})
                        check("列根目录能发现 .py 文件（不是空列表）",
                              bool(d.get("files")), f"count={d.get('count')}")
                        d = await call(s3, "list_files", {"path": "src"})
                        check("列 src 目录放行（目录名不匹配 include 也不该拒）",
                              bool(d.get("files")), json.dumps(d, ensure_ascii=False)[:70])
                        d = await call(s3, "read_file", {"path": "src/util/helper.py"})
                        check("深层 .py 可读", bool(d.get("content")), d.get("error", "")[:40])
                        d = await call(s3, "read_file", {"path": "README.md"})
                        check("非 .py 仍被拒", "content" not in d, d.get("error", "")[:40])
                        d = await call(s3, "list_files", {"path": "docs"})
                        check("没有 .py 的目录列举仍被拒", "files" not in d, json.dumps(d, ensure_ascii=False)[:60])
            finally:
                srv3.terminate()
                try:
                    srv3.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    srv3.kill()

            print("\n⑩ 公网入口只含 public 窗口（visibility=local 不得写进隧道）")
            # 回归：这里曾经只给 local 窗口加一句注释就照样写进 ingress，
            # 结果标了「私密」的窗口仍能从公网访问（实测 http=200）。
            if str(REPO / "core") not in sys.path:
                sys.path.insert(0, str(REPO / "core"))
            import render_ingress as ri
            text = ri.build(
                {"hostname": "h.test", "tunnel_id": "t-1",
                 "cloudflared_config": str(Path(tmp) / "cf-test.yml")},
                {"pub": {"port": 9001, "path": "/w-pub", "title": "公开的", "visibility": "public"},
                 "priv": {"port": 9002, "path": "/w-priv", "title": "私密的", "visibility": "local"},
                 "dflt": {"port": 9003, "path": "/w-def", "title": "没写 visibility"}},
            )
            check("public 窗口写进 ingress", "/w-pub" in text)
            check("visibility=local 的窗口不写进 ingress", "/w-priv" not in text, text[:80])
            check("没写 visibility 时按 local 处理（默认不对外）", "/w-def" not in text)

            print("\n⑪ `?` 通配符：只吃一个字符，且不跨目录分隔符")
            # 与 ⑧⑨ 同族：glob 形态盘点的最后一种（`x/**`、`**/*`、`**/*.py`、`dir/**` 已覆盖）。
            port4 = free_port()
            reg = json.loads(registry.read_text(encoding="utf-8"))
            reg["windows"]["hard4"] = {
                "title": "问号窗", "root": str(root),
                "include": ["q/file?.md", "a?b.txt"], "exclude": [], "deny_extra": [],
                "port": port4, "path": "/w-hard4", "visibility": "local",
                "write": {"enabled": False},
            }
            registry.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
            env4 = {**env, "WINDOW_ID": "hard4", "WINDOW_PORT": str(port4), "WINDOW_PATH": "/w-hard4"}
            srv4 = subprocess.Popen([PY, str(REPO / "core" / "server.py")], env=env4,
                                    stdout=open(tmp / "server4.log", "wb"), stderr=subprocess.STDOUT)
            try:
                for _ in range(40):
                    with socket.socket() as s_:
                        if s_.connect_ex(("127.0.0.1", port4)) == 0:
                            break
                    time.sleep(0.5)
                async with streamable_http_client(f"http://127.0.0.1:{port4}/w-hard4") as (r4, w4):
                    async with ClientSession(r4, w4) as s4:
                        await s4.initialize()
                        d = await call(s4, "read_file", {"path": "q/file1.md"})
                        check("q/file1.md 命中 `file?.md` 可读", bool(d.get("content")), d.get("error", "")[:40])
                        d = await call(s4, "read_file", {"path": "q/file10.md"})
                        check("q/file10.md 不命中（? 只吃一个字符）", "content" not in d, d.get("error", "")[:40])
                        d = await call(s4, "read_file", {"path": "q/file.md"})
                        check("q/file.md 不命中（? 不吃空字符）", "content" not in d, d.get("error", "")[:40])
                        d = await call(s4, "read_file", {"path": "a/b.txt"})
                        check("a/b.txt 不命中（? 不跨目录分隔符 /）", "content" not in d, d.get("error", "")[:40])
                        d = await call(s4, "read_file", {"path": "aXb.txt"})
                        check("aXb.txt 命中（? 吃一个普通字符）", bool(d.get("content")), d.get("error", "")[:40])
                        d = await call(s4, "read_file", {"path": "q/sub/file1.md"})
                        check("深层路径不因 ? 被放行", "content" not in d, d.get("error", "")[:40])
                        d = await call(s4, "list_files", {"path": "q"})
                        paths = [f.get("path") for f in (d.get("files") or [])]
                        check("列 q 目录只出现命中项（file1/file2；file10 与 sub/ 里的不出现）",
                              "q/file1.md" in paths and "q/file2.md" in paths
                              and not any("file10" in x or "/sub/" in x for x in paths),
                              json.dumps(paths, ensure_ascii=False)[:80])
            finally:
                srv4.terminate()
                try:
                    srv4.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    srv4.kill()

            print("\n⑫ 无状态模式：重启服务后，客户端手里的旧会话 id 不会失效")
            # 回归：WorkBuddy 这类客户端不会自动重新握手，服务重启后继续拿旧 mcp-session-id
            # 调用；会话模式下服务端回 “Session not found” → 对方误以为「找不到 mcp 环境」。
            # 无状态模式（stateless_http）下旧会话 id 被直接忽略、无会话 id 也照常工作。
            port5 = free_port()
            reg = json.loads(registry.read_text(encoding="utf-8"))
            reg["windows"]["hard5"] = {
                "title": "无状态窗", "root": str(root),
                "include": ["**/*.md"], "exclude": [], "deny_extra": [],
                "port": port5, "path": "/w-hard5", "visibility": "local",
                "write": {"enabled": False},
            }
            registry.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
            env5 = {**env, "WINDOW_ID": "hard5", "WINDOW_PORT": str(port5), "WINDOW_PATH": "/w-hard5"}
            srv5 = subprocess.Popen([PY, str(REPO / "core" / "server.py")], env=env5,
                                    stdout=open(tmp / "server5.log", "wb"), stderr=subprocess.STDOUT)

            _noproxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def raw_call(body: dict, sid: str | None = None) -> tuple:
                hdrs = {"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"}
                if sid:
                    hdrs["mcp-session-id"] = sid
                req = urllib.request.Request(f"http://127.0.0.1:{port5}/w-hard5",
                                             data=json.dumps(body).encode(), headers=hdrs, method="POST")
                try:
                    with _noproxy.open(req, timeout=10) as resp:
                        return resp.status, resp.read().decode("utf-8", "replace")
                except urllib.error.HTTPError as e:
                    return e.code, e.read().decode("utf-8", "replace")

            try:
                for _ in range(40):
                    with socket.socket() as s_:
                        if s_.connect_ex(("127.0.0.1", port5)) == 0:
                            break
                    time.sleep(0.5)
                log5 = (tmp / "server5.log").read_text(encoding="utf-8", errors="replace")
                check("默认窗口只绑本机（启动日志 bind=127.0.0.1）", "bind=127.0.0.1" in log5,
                      (log5.splitlines()[-1][:90] if log5 else ""))
                _lip = _lan_ip()
                if _lip:
                    _c = socket.socket(); _c.settimeout(2)
                    _refused = _c.connect_ex((_lip, port5)) != 0
                    _c.close()
                    check(f"默认 bind：局域网 IP（{_lip}）连不上（不外露）", _refused)
                body_req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "window_info", "arguments": {}}}
                st, body = raw_call(body_req)
                check("无会话 id 直接调工具也通（无状态）", st == 200 and "hard5" in body, f"{st} {body[:70]}")
                st, body = raw_call(body_req, sid="deadbeef-old-session-from-before-restart")
                check("过期/伪造的会话 id 被忽略、调用照常成功", st == 200 and "hard5" in body, f"{st} {body[:70]}")
            finally:
                srv5.terminate()
                try:
                    srv5.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    srv5.kill()

            print("\n⑬ 接入方式选项：局域网直连（bind=0.0.0.0）与纯 JSON 回应（json_response）")
            # 给「不想买域名/不想开隧道」的用户：同网段设备用 IP 直连；不吃 SSE 的隧道用 JSON 回应。
            port6 = free_port()
            reg = json.loads(registry.read_text(encoding="utf-8"))
            reg["windows"]["hard6"] = {
                "title": "局域网窗", "root": str(root),
                "include": ["**/*.md"], "exclude": [], "deny_extra": [],
                "port": port6, "path": "/w-hard6", "visibility": "local",
                "write": {"enabled": False}, "bind": "0.0.0.0", "json_response": True,
            }
            registry.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
            env6 = {**env, "WINDOW_ID": "hard6", "WINDOW_PORT": str(port6), "WINDOW_PATH": "/w-hard6"}
            srv6 = subprocess.Popen([PY, str(REPO / "core" / "server.py")], env=env6,
                                    stdout=open(tmp / "server6.log", "wb"), stderr=subprocess.STDOUT)

            _noproxy6 = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def raw6(msg: dict, host: str = "127.0.0.1"):
                req = urllib.request.Request(
                    f"http://{host}:{port6}/w-hard6", data=json.dumps(msg).encode(),
                    headers={"Content-Type": "application/json",
                             "Accept": "application/json, text/event-stream"}, method="POST")
                try:
                    with _noproxy6.open(req, timeout=10) as resp:
                        return resp.status, resp.read().decode("utf-8", "replace")
                except urllib.error.HTTPError as e:
                    return e.code, e.read().decode("utf-8", "replace")

            try:
                for _ in range(40):
                    with socket.socket() as s_:
                        if s_.connect_ex(("127.0.0.1", port6)) == 0:
                            break
                    time.sleep(0.5)
                log6 = (tmp / "server6.log").read_text(encoding="utf-8", errors="replace")
                check("bind=0.0.0.0 真的绑全网卡（启动日志可查）", "bind=0.0.0.0" in log6,
                      (log6.splitlines()[-1][:90] if log6 else ""))
                _lip6 = _lan_ip()
                if _lip6:
                    st6, body6 = raw6({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": "window_info", "arguments": {}}}, host=_lip6)
                    check(f"局域网 IP（{_lip6}）可直接访问", st6 == 200 and "hard6" in body6,
                          f"{st6} {body6[:60]}")
                msg6 = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "window_info", "arguments": {}}}
                req6 = urllib.request.Request(
                    f"http://127.0.0.1:{port6}/w-hard6", data=json.dumps(msg6).encode(),
                    headers={"Content-Type": "application/json",
                             "Accept": "application/json, text/event-stream"}, method="POST")
                with _noproxy6.open(req6, timeout=10) as resp:
                    ctype6 = resp.headers.get("content-type", "")
                    body6b = resp.read().decode("utf-8", "replace")
                check("json_response=on → 回应是 application/json（不再是 SSE 帧）",
                      "application/json" in ctype6, ctype6)
                check("纯 JSON 回应里带得回真实结果", "hard6" in body6b, body6b[:60])
                # 浏览器访问（text/html + Mozilla UA）→ 人话提示页；MCP 客户端不受影响
                req7 = urllib.request.Request(
                    f"http://127.0.0.1:{port6}/w-hard6", method="GET",
                    headers={"Accept": "text/html,application/xhtml+xml",
                             "User-Agent": "Mozilla/5.0 (iPhone)"})
                with _noproxy6.open(req7, timeout=10) as resp:
                    land = resp.read().decode("utf-8", "replace")
                check("浏览器式 GET 得到人话提示页（手机打开不再白屏转圈）",
                      "灯塔窗口" in land and "hard6" in land, land[:60].replace("\n", " "))
            finally:
                srv6.terminate()
                try:
                    srv6.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    srv6.kill()

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
