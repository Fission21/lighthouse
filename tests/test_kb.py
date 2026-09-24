#!/usr/bin/env python3
"""受控资料库测试（第 6 套）：分级台账 · 一人一条地址 · 追踪 · 网页自助申请。

自带隔离环境（临时资料库 + 临时注册表 + 临时状态目录 + 自己起一个服务 + 自己的真 CLI），
不碰 windows.local.json，也不碰 ~/.lighthouse。

用法: python3 test_kb.py [--keep]
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
sys.path.insert(0, str(REPO / "core"))

fails: list[str] = []
count = 0

LEVELS = ["L1-商务", "L2-技术", "L3-核心"]


def check(label: str, good: bool, detail: str = "") -> None:
    global count
    count += 1
    print(f"  {'✅' if good else '❌'} {label}{(' — ' + str(detail)) if detail else ''}")
    if not good:
        fails.append(label)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def cli(*args: str, env: dict) -> tuple[str, int]:
    r = subprocess.run(["bash", str(REPO / "lighthouse.sh"), *args],
                       capture_output=True, text=True, env=env, cwd=str(REPO))
    return (r.stdout + r.stderr).strip(), r.returncode


async def call(session, name: str, args: dict) -> dict:
    res = await session.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in res.content if getattr(c, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def http(url: str, data: dict | None = None, headers: dict | None = None) -> tuple[int, str]:
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        body = "&".join(f"{k}={v}" for k, v in data.items()).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, data=body, headers=hdrs)
    try:
        with urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def http_bytes(url: str, data: dict | None = None, headers: dict | None = None) -> tuple[int, bytes, dict]:
    """下载类请求：原样拿字节 + 响应头（zip、原件比对都要）"""
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        body = "&".join(f"{k}={v}" for k, v in data.items()).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, data=body, headers=hdrs)
    try:
        with urlopen(req, timeout=20) as r:
            return r.status, r.read(), dict(r.headers)
    except HTTPError as e:
        return e.code, e.read(), dict(e.headers)


async def with_session(url: str, fn):
    async with streamable_http_client(url) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            return await fn(session)


# ---------------------------------------------------------------- 隔离环境
def build_env(tmp: Path):
    lib = tmp / "lib"
    docs = lib / "原始文档"
    for sub in ("商务", "技术", "核心"):
        (docs / sub).mkdir(parents=True, exist_ok=True)
    (docs / "商务" / "报价单.md").write_text("报价：单台 12 万（这份不该给技术组看）\n", encoding="utf-8")
    (docs / "技术" / "服务器技术方案.md").write_text(
        "# 服务器技术方案\n\n本项目采用 2 路机架式服务器，双电源冗余，支持热插拔。\n"
        "API_KEY=sk-abcdefghijklmnopqrstuvwx\n", encoding="utf-8")
    (docs / "核心" / "核心网架构.md").write_text("# 核心网架构\n\n核心网采用双平面组网。\n", encoding="utf-8")
    (docs / ".env").write_text("TOKEN=sk-zzzzzzzzzzzzzzzzzzzzzz\n", encoding="utf-8")
    (docs / "技术" / "表格.xlsx").write_text("PK", encoding="utf-8")

    port = free_port()
    registry = tmp / "windows.json"
    registry.write_text(json.dumps({"windows": {"kb1": {
        "title": "测试资料库", "root": str(lib),
        "include": ["原始文档/**"], "exclude": [], "deny_extra": [],
        "port": port, "path": "/w-kb1-test", "visibility": "local",
        "max_file_kb": 1024, "max_output_chars": 60000,
        "write": {"enabled": False, "max_write_kb": 256, "backup": True},
        "kb": {"enabled": True, "docs_dir": "原始文档", "levels": LEVELS,
               "default_level": "L1-商务",
               "portal": {"enabled": True, "public_levels": LEVELS,
                          "auto_approve_levels": ["L1-商务"], "admin_remote": False},
               "download": {"enabled": True, "original": True, "link_minutes": 15,
                            "max_bundle_mb": 50},
               "upload": {"enabled": True, "max_file_mb": 1, "max_total_mb": 2}},
    }}}, ensure_ascii=False, indent=2), encoding="utf-8")

    conf = tmp / "config.json"
    conf.write_text(json.dumps({"hostname": "127.0.0.1", "spare_hostname": ""}, ensure_ascii=False),
                    encoding="utf-8")
    state = tmp / "state"
    env = {**os.environ, "LIGHTHOUSE_STATE": str(state), "LIGHTHOUSE_PY": PY,
           "LIGHTHOUSE_CONFIG": str(conf),
           "LIGHTHOUSE_REGISTRY": str(registry),
           "WINDOW_ID": "kb1", "WINDOW_PORT": str(port), "WINDOW_PATH": "/w-kb1-test",
           "WINDOW_REGISTRY": str(registry)}
    os.environ.update(env)      # 本测试进程自己也要用同一套配置（config 在 import 时读环境变量）
    return env, f"http://127.0.0.1:{port}/w-kb1-test", state, lib, port, registry


# ---------------------------------------------------------------- ① 台账闸门（纯函数）
def part_gate(tmp: Path):
    import kb
    print("① 台账闸门：状态 × 等级 × 内容变更 × 拉黑 × 越界 × 坏台账")
    root = tmp / "ga"
    (root / "原始文档").mkdir(parents=True, exist_ok=True)
    state = tmp / "gs"
    rel, rel3 = "原始文档/服务器技术方案.md", "原始文档/报价单.md"
    (root / rel).write_text("正文", encoding="utf-8")
    (root / rel3).write_text("报价", encoding="utf-8")
    did, did3 = kb.doc_id(rel), kb.doc_id(rel3)
    (state / "kb/kb1/text").mkdir(parents=True, exist_ok=True)
    for d in (did, did3):
        (state / f"kb/kb1/text/{d}.md").write_text("正文内容", encoding="utf-8")
    cat = {"window": "kb1", "docs": {
        did: {"id": did, "path": rel, "title": "服务器技术方案", "level": "L2-技术", "status": "pending",
              "text": f"text/{did}.md", "sha256": kb.sha256_file(root / rel)},
        did3: {"id": did3, "path": rel3, "title": "报价单", "level": "L1-商务", "status": "approved",
               "text": f"text/{did3}.md", "sha256": kb.sha256_file(root / rel3)}}}
    ok = lambda r: (True, "")                                                   # noqa: E731
    L2 = ["L2-技术"]

    _, _, err = kb.resolve_doc(state, "kb1", cat, did, root, ok, L2)
    check("pending 的资料读不到", "尚未公开" in err, err)
    cat["docs"][did]["status"] = "rejected"
    _, _, err = kb.resolve_doc(state, "kb1", cat, did, root, ok, L2)
    check("rejected 的资料读不到", "尚未公开" in err, err)
    cat["docs"][did]["status"] = "approved"
    ent, tp, err = kb.resolve_doc(state, "kb1", cat, did, root, ok, L2)
    check("approved + 等级命中 → 可读", err == "" and tp is not None, err)
    _, _, err = kb.resolve_doc(state, "kb1", cat, did, root, ok, ["L1-商务"])
    check("等级不符 → 拒（越级）", "无权访问" in err, err)
    _, _, err = kb.resolve_doc(state, "kb1", cat, did, root, ok, None)
    check("不限等级（维护者本机）→ 可读", err == "", err)
    (root / rel).write_text("改过了", encoding="utf-8")
    _, _, err = kb.resolve_doc(state, "kb1", cat, did, root, ok, L2)
    check("原文件变更 → 审批失效", "已失效" in err, err)

    # 拉黑压过审批与分级
    (root / "原始文档/.env").write_text("X=1", encoding="utf-8")
    denv = kb.doc_id("原始文档/.env")
    cat["docs"][denv] = {"id": denv, "path": "原始文档/.env", "title": "env", "level": "L2-技术",
                         "status": "approved", "text": f"text/{denv}.md", "sha256": ""}
    (state / f"kb/kb1/text/{denv}.md").write_text("x", encoding="utf-8")
    deny = lambda r: (False, "命中安全拉黑规则（密钥/凭据/数据库类）") if ".env" in r else (True, "")  # noqa: E731
    _, _, err = kb.resolve_doc(state, "kb1", cat, denv, root, deny, None)
    check("拉黑压过审批与分级（.env 不可读）", "拉黑" in err, err)

    # 台账里塞越界路径
    cat["docs"]["deadbeef"] = {"id": "deadbeef", "path": "../../etc/passwd", "level": "L2-技术",
                               "status": "approved", "text": "text/deadbeef.md", "sha256": ""}
    _, _, err = kb.resolve_doc(state, "kb1", cat, "deadbeef", root,
                               lambda r: (False, "不在给看范围（不匹配 include）"), None)
    check("台账里的越界路径被拒", "给看范围" in err or "越界" in err, err)

    kb.catalog_path(state, "kb1").write_text("{坏", encoding="utf-8")
    _c, e2 = kb.load_catalog(state, "kb1")
    check("台账损坏 → 返回错误（调用方全拒）", e2 != "", e2)


# ---------------------------------------------------------------- ② CLI：扫描 / 审批 / 发放
def part_cli(env: dict, state: Path, cfg: dict):
    print("\n② 维护 CLI：scan / pending / approve / reject / grant / rotate / revoke / notify")
    out, rc = cli("kb", "scan", "kb1", "--yes", env=env)
    check("kb scan 跑通并登记待批", rc == 0 and "新增待批" in out, out.splitlines()[-1] if out else "")
    import kb
    cat, _ = kb.load_catalog(state, "kb1")
    xls = [e for e in cat["docs"].values() if e["title"] == "表格"]
    check("不支持的类型被跳过（.xlsx 登记为 unsupported，不进待批）",
          "跳过" in out and xls and xls[0].get("status") == "unsupported"
          and "需人工转成" in (xls[0].get("note") or ""),
          out[-120:] + str(xls[0].get("status") if xls else "缺记录"))
    docs = cat["docs"]
    check("3 篇可抽文本的都进了台账（pending）",
          sum(1 for e in docs.values() if e["status"] == "pending" and e.get("chars")) == 3,
          f"{len(docs)} 条记录")
    tdoc = next(e for e in docs.values() if e["title"] == "服务器技术方案")
    check("分类取一级子目录名", tdoc.get("category") == "技术", tdoc.get("category"))
    check("默认等级按配置（L1-商务）", tdoc.get("level") == "L1-商务", tdoc.get("level"))
    check("抽取文本已落盘", (state / "kb/kb1" / str(tdoc.get("text"))).is_file(), tdoc.get("text"))
    check("抽取出来的正文含原文细节", "双电源冗余" in (state / "kb/kb1" / str(tdoc.get("text"))).read_text(encoding="utf-8"))

    out, _ = cli("kb", "pending", "kb1", env=env)
    check("kb pending 列出待批", "服务器技术方案" in out and "共" in out)
    out, rc = cli("kb", "approve", "kb1", tdoc["id"], "--level", "L2-技术", "--yes", env=env)
    check("kb approve --level 生效", rc == 0 and "L2-技术" in out, out[:80])
    cat, _ = kb.load_catalog(state, "kb1")
    check("台账里那篇变成 approved 且等级已改",
          cat["docs"][tdoc["id"]]["status"] == "approved" and cat["docs"][tdoc["id"]]["level"] == "L2-技术")

    core_doc = next(e["id"] for e in cat["docs"].values() if e["title"] == "核心网架构")
    out, rc = cli("kb", "approve", "kb1", core_doc, "--level", "L3-核心", "--yes", env=env)
    check("核心资料可单独批成 L3-核心", rc == 0 and "L3-核心" in out, out[:80])
    out, rc = cli("kb", "approve", "kb1", "--all-pending", "--level", "L1-商务", "--yes", env=env)
    check("--all-pending 批量批准（报价单进 L1-商务）", rc == 0 and "已公开" in out, out[:80])
    cat2, _ = kb.load_catalog(state, "kb1")
    check("批量批准踩不到「表格.xlsx」（unsupported 永远批不了）",
          all(e["status"] != "approved" for e in cat2["docs"].values() if e["title"] == "表格"),
          str([e["status"] for e in cat2["docs"].values() if e["title"] == "表格"]))
    out2, _ = cli("kb", "approve", "kb1", xls[0]["id"], "--level", "L1-商务", "--yes", env=env)
    check("硬批 unsupported 篇目 → 拒绝并说明原因", "不能公开" in out2, out2[:80])

    out, rc = cli("kb", "grant", "kb1", "--name", "张三", "--level", "L9-不存在", env=env)
    check("不认识的等级 → CLI 直接报错（fail-closed）", rc != 0 and "不认识的等级" in out, out[:60])

    out, rc = cli("kb", "grant", "kb1", "--name", "张三", "--level", "L1-商务", "--for", "30d", env=env)
    check("kb grant 发放地址", rc == 0 and "kb-" in out, out.splitlines()[0] if out else "")
    import kb_access as ACC
    zhang = ACC.get_user(state, "kb1", "张三")
    check("地址挂在「张三」这条记录上，等级 L1-商务", zhang and zhang["levels"] == ["L1-商务"], zhang and zhang["levels"])
    check("默认 30 天有效期", zhang and ACC.describe_expiry(zhang).endswith("到期"), ACC.describe_expiry(zhang) if zhang else "")
    old_token = zhang["token"]

    out, _ = cli("kb", "set-level", "kb1", "--name", "张三", "--level", "L1-商务,L2-技术", env=env)
    zhang2 = ACC.get_user(state, "kb1", "张三")
    check("改等级不换地址", zhang2["token"] == old_token and zhang2["levels"] == ["L1-商务", "L2-技术"], out.splitlines()[0][:60])

    out, _ = cli("kb", "rotate", "kb1", "--name", "张三", env=env)
    zhang3 = ACC.get_user(state, "kb1", "张三")
    check("rotate 换新地址（旧的立即失效）", zhang3["token"] != old_token, out.splitlines()[0][:60])
    check("rotate 后旧地址查不到", ACC.check_token(state, "kb1", old_token)[1] != "")

    out, _ = cli("kb", "revoke", "kb1", "--name", "张三", env=env)
    check("revoke 停用", "已收回" in out and ACC.check_token(state, "kb1", zhang3["token"])[1] != "")
    cli("kb", "revoke", "kb1", "--name", "张三", "--enable", env=env)
    check("revoke --enable 恢复", ACC.check_token(state, "kb1", zhang3["token"])[1] == "")
    return zhang3


# ---------------------------------------------------------------- ③ MCP 工具面与分级
async def part_mcp(base_url: str, state: Path, zhang: dict):
    print("\n③ MCP 工具面 + 一人一条地址的分级权限")
    import kb_access as ACC
    urls = {"张三": f"{base_url.replace('/w-kb1-test', '')}/kb-{zhang['token']}"}

    async def surface(session):
        tools = {t.name for t in (await session.list_tools()).tools}
        for name in ("kb_info", "kb_list", "kb_search", "kb_read"):
            check(f"kb 窗口提供 {name}", name in tools, str(sorted(tools)))
        for name in ("list_files", "read_file", "search", "request_access", "write_file",
                     "delete_file", "make_dir"):
            gone, why = True, ""
            try:
                res = await session.call_tool(name, {"path": "原始文档/技术/服务器技术方案.md"})
                txt = "".join(getattr(c, "text", "") for c in res.content)
                gone = ("Unknown tool" in txt) or bool(getattr(res, "isError", False))
                why = txt[:60] or f"isError={getattr(res, 'isError', None)}"
            except Exception as e:                                       # noqa: BLE001
                why = str(e)[:60]
            check(f"kb 窗口不暴露 {name}（外部 AI 看不到写/路径能力）", gone, why)
        info = await call(session, "kb_info", {})
        check("kb_info 报出「你」是谁与你的等级",
              (info.get("you") or {}).get("name") == "张三", json.dumps(info.get("you"), ensure_ascii=False))
        docs = await call(session, "kb_list", {})
        titles = [d["title"] for d in docs.get("docs", [])]
        check("L1+L2 地址能看到技术方案", "服务器技术方案" in titles, str(titles))
        check("看不到 L3-核心（没给这档）", "核心网架构" not in titles, str(titles))
        blob = json.dumps(docs, ensure_ascii=False)
        check("kb_list 不泄漏内部路径", "原始文档" not in blob and "\"path\"" not in blob, blob[:80])
        check("L1-商务档的资料也能看到（报价单）", "报价单" in titles, str(titles))

        core = await call(session, "kb_read", {"doc_id": __import__("kb").doc_id("原始文档/核心/核心网架构.md")})
        check("越级读 L3 → 拒", "无权访问" in str(core.get("error", "")), str(core.get("error"))[:60])

        import kb as KB
        did = KB.doc_id("原始文档/技术/服务器技术方案.md")
        r = await call(session, "kb_read", {"doc_id": did, "limit": 1})
        check("kb_read 分页正确", r.get("returned") == 1 and r.get("next_offset") == 2, json.dumps({k: r.get(k) for k in ("returned", "next_offset")}))
        s = await call(session, "kb_search", {"keyword": "双电源冗余"})
        check("kb_search 命中已批准资料", s.get("hits", 0) >= 1, json.dumps(s, ensure_ascii=False)[:80])
        full = await call(session, "kb_read", {"doc_id": did, "limit": 50})
        check("正文里的凭据被脱敏", "«REDACTED:api-key»" in str(full.get("content", "")),
              str(full.get("content", ""))[-60:])
        none = await call(session, "kb_read", {"doc_id": "nonexist"})
        check("不存在的编号 → 明确拒绝", "没有这个资料编号" in str(none.get("error", "")), str(none.get("error"))[:50])

    await with_session(urls["张三"], surface)

    # 李四：只给 L1-商务 → 看不到技术方案
    li = ACC.upsert_user(state, "kb1", "李四", ["L1-商务"], minutes=None)
    li_url = f"{base_url.replace('/w-kb1-test', '')}/kb-{li['token']}"

    async def other(session):
        docs = await call(session, "kb_list", {})
        titles = [d["title"] for d in docs.get("docs", [])]
        check("李四（L1-商务）看不到技术方案", "服务器技术方案" not in titles, str(titles))

    await with_session(li_url, other)

    # 认证：无口令 / 错口令
    ok_status, _ = http(base_url, data=None, headers={"Accept": "application/json"})
    check("不带地址段访问 MCP → 401", ok_status == 401, str(ok_status))
    st, body = http(f"{base_url.replace('/w-kb1-test', '')}/kb-{'x' * 40}")
    check("乱地址段 → 401", st == 401, f"{st} {body[:60]}")

    # 停用 / 到期
    ACC.set_enabled(state, "kb1", "李四", False)
    st, _ = http(li_url, data=None, headers={"Accept": "application/json"})
    check("停用后 → 401（且只影响这一条）", st == 401, str(st))
    st2, _ = http(urls["张三"], data=None, headers={"Accept": "application/json"})
    check("别人的地址不受影响", st2 in (400, 406, 200), str(st2))
    ACC.set_enabled(state, "kb1", "李四", True)
    _d, _e = ACC.load_all(state)
    _d["kb1"]["李四"]["expires"] = time.time() - 10
    ACC.save_all(state, _d)
    st3, _ = http(li_url, data=None, headers={"Accept": "application/json"})
    check("到期后 → 401", st3 == 401, str(st3))


# ---------------------------------------------------------------- ④ 网页：申请 / 查进度 / 管理页
def part_web(base_url: str, state: Path):
    print("\n④ 网页：申请页 / 自助开通 / 查进度 / 管理页边界")
    req_url = f"{base_url}/request"
    st, body = http(req_url)
    check("申请页可打开", st == 200 and "name=\"purpose\"" in body, str(st))
    check("申请页不泄漏内部路径", "原始文档" not in body)

    st, body = http(base_url, headers={"Accept": "text/html"})
    check("浏览器打开窗口根 → 转到申请页", "申请访问" in body or "request" in body, str(st))

    # 商务档：自助申请 → 立即拿到地址
    st, body = http(req_url, data={"name": "王五", "dept": "商务组", "level_requested": "L1-商务",
                                   "purpose": "要看商务资料", "contact": "13800000000", "website": ""})
    check("商务档申请即通过（自动发放）", st == 200 and "已开通" in body and "/kb-" in body, str(st))
    import kb_access as ACC
    wang = ACC.get_user(state, "kb1", "王五")
    check("自动开通确实落到了那条地址上", bool(wang) and wang["levels"] == ["L1-商务"], str(wang and wang["levels"]))

    # 技术档：要人工批
    st, body = http(req_url, data={"name": "赵六", "dept": "技术部", "level_requested": "L2-技术",
                                   "purpose": "写方案要参考以前的资料", "contact": "", "website": ""})
    check("技术档申请 → 待批（给申请号+查询码）", st == 200 and "查询码" in body and "等维护者审批" in body, str(st))
    import kb as KB
    data, _ = KB.load_requests(state, "kb1")
    zhao = next(r for r in data["requests"].values() if r["name"] == "赵六")
    check("申请记录里状态是 pending", zhao["status"] == "pending")

    # 蜜罐 / 等级白名单
    st, _ = http(req_url, data={"name": "机器人", "level_requested": "L1-商务", "purpose": "x", "website": "http://spam"})
    check("蜜罐命中 → 400 且不落库", st == 400 and not ACC.get_user(state, "kb1", "机器人"), str(st))
    st, _ = http(req_url, data={"name": "张三三", "level_requested": "L9-不存在", "purpose": "x", "website": ""})
    check("等级不在白名单 → 400（不信任前端）", st == 400, str(st))

    # 限速（换一个「客户端 IP」来测，别把本机配额用掉）
    codes = []
    for i in range(6):
        st, _ = http(req_url, data={"name": f"限速{i}", "level_requested": "L1-商务",
                                    "purpose": "test", "website": ""},
                     headers={"CF-Connecting-IP": "9.9.9.9"})
        codes.append(st)
    check("同一 IP 第 6 次申请被限速（429）", codes[-1] == 429 and codes[0] == 200, str(codes))

    # 查进度
    st, body = http(f"{req_url}/status?id={zhao['id']}&code=WRONG1")
    check("查询码不对 → 提示不对", "查询码不对" in body, str(st))
    st, body = http(f"{req_url}/status?id={zhao['id']}&code={zhao['code']}")
    check("查询码对 → 显示待批状态", "还在等维护者审批" in body, str(st))

    # 管理页边界
    admin = __import__("kb_web").ensure_admin_token(state)
    st, _ = http(f"{base_url}/admin", headers={"CF-Connecting-IP": "1.2.3.4"})
    check("带云端转发头访问管理页 → 403（公网打不到）", st == 403, str(st))
    st, _ = http(f"{base_url}/admin")
    check("本机但无管理令 → 401", st == 401, str(st))
    st, body = http(f"{base_url}/admin?k={admin}")
    check("本机 + 管理令 → 管理页打开", st == 200 and "待批申请" in body, str(st))
    check("管理页列出待批申请（赵六）", "赵六" in body)

    # 管理页批准赵六 → 发地址
    st, body = http(f"{base_url}/admin/decide", data={"k": admin, "rid": zhao["id"], "action": "approve",
                                                      "level": "L2-技术", "for_days": "30"})
    zhao_user = ACC.get_user(state, "kb1", "赵六")
    check("管理页批准 → 生成赵六的地址（L2-技术）",
          st == 200 and zhao_user and zhao_user["levels"] == ["L2-技术"], str(zhao_user and zhao_user["levels"]))
    data, _ = KB.load_requests(state, "kb1")
    check("申请状态变成 approved", data["requests"][zhao["id"]]["status"] == "approved")

    st, body = http(f"{req_url}/status?id={zhao['id']}&code={zhao['code']}")
    check("申请人自己在状态页能看到地址", "/kb-" in body, str(st))

    # 管理页直接发放 + 停用
    st, _ = http(f"{base_url}/admin/grant", data={"k": admin, "person": "孙七", "level": "L3-核心",
                                                  "for_days": "7", "dept": "核心网"})
    check("管理页可直接发放（L3-核心 / 7 天）",
          bool(ACC.get_user(state, "kb1", "孙七")) and st == 200)
    st, _ = http(f"{base_url}/admin/revoke", data={"k": admin, "person": "孙七", "enabled": "0"})
    check("管理页可停用", ACC.check_token(state, "kb1", ACC.get_user(state, "kb1", "孙七")["token"])[1] != "", str(st))

    # 用量页
    st, body = http(f"{base_url}/admin/usage?k={admin}&by=person")
    check("用量看板可打开", st == 200 and "用量" in body, str(st))


# ---------------------------------------------------------------- ⑤ 追踪
def part_track(state: Path, env: dict, zhang: dict):
    print("\n⑤ 追踪：审计留痕 + 用量报表 + 新申请提醒")
    audit = state / "audit" / "kb1.jsonl"
    check("审计文件已生成", audit.is_file())
    rows = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines() if l.strip()]
    _mgmt = ("kb_user_update", "kb_user_delete", "kb_rotate", "kb_grant", "kb_decision")
    mine = [r for r in rows if r.get("principal") == "张三" and r.get("tool") not in _mgmt]
    check("审计记到了「谁」（principal=张三）", bool(mine), f"{len(rows)} 条记录")
    check("审计里每行都有他的等级（改等级后按当时权限记）",
          all(isinstance(r.get("levels"), list) and r["levels"] for r in mine) if mine else False,
          str(sorted({str(r.get("levels")) for r in mine})))
    check("审计里有客户端 IP 与 UA 字段", all(("ip" in r and "ua" in r) for r in mine) if mine else False)
    check("越级访问被记成 denied", any(r.get("denied") and not r.get("ok") for r in rows))
    check("未授权尝试也留痕（tool=auth）", any(r.get("tool") == "auth" and r.get("ok") is False for r in rows))
    check("网页申请留痕（portal_submit）", any(r.get("tool") == "portal_submit" for r in rows))
    check("自动开通留痕（kb_auto_grant）", any(r.get("tool") == "kb_auto_grant" for r in rows))

    out, rc = cli("kb", "usage", "kb1", "--days", "1", env=env)
    check("kb usage 出表且按人分组", rc == 0 and "张三" in out and "调用" in out, out.splitlines()[0] if out else "")

    out, rc = cli("kb", "notify", "kb1", "--json", env=env)
    check("kb notify 能列出新申请（给 agent 汇报）", "赵六" in out, out[:80])
    out, _ = cli("kb", "notify", "kb1", "--ack", env=env)
    check("notify --ack 后不再重复提醒", "不再重复提醒" in out, out[-40:])

    out, rc = cli("kb", "users", "kb1", env=env)
    check("kb users 列出同事与用量（含被拒次数）",
          rc == 0 and "张三" in out and "被拒" in out and "L1-商务,L2-技术" in out, out[-160:])
    out, rc = cli("kb", "admin-url", "kb1", env=env)
    check("kb admin-url 打印带管理令的管理页地址", rc == 0 and "/admin?k=" in out, out[:80])


# ---------------------------------------------------------------- ⑥ 直接取文件（门户下载 + 限时链接）
def part_download(site: str, state: Path, zhang: dict, lib_root: str):
    print("\n⑥ 同事直接拿文件：门户下载页 / 单篇 / 打包 / 限时签名链接")
    import io, time as _t, zipfile
    import kb as KB
    import kb_access as ACC
    import kb_download as DL

    did_tech = KB.doc_id("原始文档/技术/服务器技术方案.md")
    did_core = KB.doc_id("原始文档/核心/核心网架构.md")
    did_rep = next(e["id"] for e in KB.load_catalog(state, "kb1")[0]["docs"].values()
                   if e["title"] == "报价单")
    base = site + "/w-kb1-test"

    li = ACC.get_user(state, "kb1", "李四")
    _d, _e = ACC.load_all(state)
    _d["kb1"]["李四"]["expires"] = None            # 前面测到期时改过，这里恢复
    ACC.save_all(state, _d)
    li_tok, zh_tok = li["token"], zhang["token"]

    # 配置解析（写错类型 → 走默认，fail-closed 到安全侧）
    check("download 配置：不写=默认开启、原件可下",
          DL.download_cfg({})["enabled"] and DL.download_cfg({})["original"])
    check("download 配置：写 false 才关",
          DL.download_cfg({"kb": {"download": False}})["enabled"] is False)
    check("download 配置：类型写错 → 用默认（不会变成开放）",
          DL.download_cfg({"kb": {"download": {"enabled": "yes", "max_file_mb": "x"}}})["enabled"] is True)

    # 没带地址 → 打不开
    st, body = http(f"{base}/files")
    check("没有地址打开 /files → 401 并提示怎么拿地址", st == 401 and "你的地址" in body, str(st))

    # 用自己的地址打开 → 只列他有权看的
    st, body = http(f"{site}/kb-{zh_tok}/files")
    check("用地址段打开 /files → 列出他有权的资料", st == 200 and "服务器技术方案" in body, str(st))
    check("清单里不出现没授权的那档（L3-核心）", "核心网架构" not in body)
    check("清单里给出下载入口（原件/文本）", "mode=original" in body and "mode=text" in body)
    check("清单里说明 AI 也能给下载链接", "限时下载链接" in body)
    st, body2 = http(f"{base}/kb-{zh_tok}/files")
    check("长地址（窗口路径 + /kb-<地址段>）同样能打开清单页",
          st == 200 and "服务器技术方案" in body2, str(st))

    # 等级不够 → 403
    st, _ = http(f"{base}/dl/{did_core}?t={li_tok}")
    check("等级不够下载 L3 → 403", st == 403, str(st))
    st, _ = http(f"{base}/dl/{did_tech}?t={li_tok}")
    check("等级不够下载 L2 → 403（李四只有商务档）", st == 403, str(st))

    # 等级够 → 下到原件（字节与磁盘上的一致）
    st, blob, hdrs = http_bytes(f"{base}/dl/{did_tech}?t={zh_tok}")
    src = (Path(lib_root) / "原始文档/技术/服务器技术方案.md").read_bytes()
    check("下载原件 → 200 且字节与磁盘上完全一致", st == 200 and blob == src, f"{st} 下载{len(blob)}B/原件{len(src)}B")
    cd = hdrs.get("content-disposition", "")
    check("Content-Disposition 同时给 ASCII 兜底与 UTF-8 原名",
          "attachment" in cd and "filename*=UTF-8''" in cd, cd[:80])
    st, blob, _ = http_bytes(f"{base}/dl/{did_tech}?t={zh_tok}&mode=text")
    txt = blob.decode("utf-8", "replace")
    check("下载抽取文本（mode=text）→ 是转出来的正文",
          st == 200 and "双电源冗余" in txt and txt.lstrip().startswith("#"), str(st))
    tfile = state / "kb/kb1" / ((KB.load_catalog(state, "kb1")[0]["docs"][did_tech]).get("text") or "")
    check("mode=text 下的是抽取文本（与台账里登记的那份一致）", tfile.is_file() and blob == tfile.read_bytes(),
          f"{len(blob)}B vs {tfile.stat().st_size if tfile.is_file() else -1}B")

    # 拉黑文件即使被批准也下不到
    st, _ = http(f"{base}/dl/{KB.doc_id('原始文档/.env')}?t={zh_tok}")
    check("拉黑文件（.env）即便 approved 也下不到", st in (403, 404), str(st))

    # 打包
    st, b1, h1 = http_bytes(f"{base}/zip", data={"t": zh_tok, "ids": did_tech})
    check("打包下载 → 200 且内容是 zip（看魔数 PK）",
          st == 200 and b1[:2] == b"PK" and "zip" in h1.get("content-type", ""), f"{st} {b1[:2]!r}")
    st, b2, _ = http_bytes(f"{base}/zip", data={"t": zh_tok, "ids": f"{did_tech}&ids={did_rep}"})
    names = []
    try:
        names = zipfile.ZipFile(io.BytesIO(b2)).namelist()
    except Exception as e:                                                   # noqa: BLE001
        names = [f"打不开: {e.__class__.__name__}"]
    check("打包多篇 → zip 里确实有两篇（勾选多值不被吞）", len(names) == 2, str(names)[:90])
    st, body = http(f"{base}/zip", data={"t": li_tok, "ids": did_tech})
    check("打包时越级的那篇被挡下（李四拿不到 L2）", st == 403 and "都没通过" in body, str(st))

    # 审计：下载行必须认得出人（门户路径没带 MCP 口令，最容易漏）
    rows = [json.loads(l) for l in (state / "audit/kb1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    dl = [x for x in rows
          if x.get("tool") == "kb_download" and x.get("ok")
          and (x.get("args") or {}).get("doc_id") == did_tech
          and (x.get("args") or {}).get("mode") == "original"]
    check("审计里「谁下载了哪一篇」写的是人名而不是 '-'",
          bool(dl) and dl[-1].get("principal") == "张三", str(dl[-1])[:100] if dl else "没有下载记录")
    check("下载审计带字节数（能证明确实取走了）", bool(dl) and (dl[-1].get("args") or {}).get("bytes", 0) > 0)
    check("下载审计带他当时的等级", bool(dl) and dl[-1].get("levels") == ["L1-商务", "L2-技术"],
          str(dl[-1].get("levels")) if dl else "")

    # MCP：kb_link 给限时链接
    async def link_test(session):
        r = await call(session, "kb_link", {"doc_id": did_tech})
        check("kb_link 返回限时链接", str(r.get("url", "")).startswith("http") and r.get("expires_in_minutes") == 15,
              str(r)[:120])
        url = r.get("url", "")
        st, blob, _ = http_bytes(url)
        check("AI 给的那条链接真的能下到文件",
              st == 200 and "双电源冗余" in blob.decode("utf-8", "replace"), str(st))
        bad = url.replace(did_tech, did_core)
        st, _ = http(bad)
        check("把链接里的编号换掉 → 403（签名绑定了这一篇）", st == 403, str(st))
        # 过期：自己造一条过期的签名
        old_sig = DL.sign(state, "kb1", did_tech, "张三", int(_t.time()) - 10)
        from urllib.parse import quote as _q
        st, _ = http(f"{base}/dl/{did_tech}?p={_q('张三')}&e={int(_t.time()) - 10}&s={old_sig}")
        check("过期的签名链接 → 403/401", st in (401, 403), str(st))
        r2 = await call(session, "kb_link", {"doc_id": did_core})
        check("等级不够时 kb_link 也拒（不走后门）", "无权访问" in str(r2.get("error", "")), str(r2)[:80])
    return link_test


# ---------------------------------------------------------------- ⑦ 管理页管文件（主人要的网页操作）
async def part_admin(site: str, state: Path, lib: Path, zhang: dict):
    print("\n⑦ 管理页直接管文件：扫库 / 公开 / 改等级 / 下架 / 预览 / 一次全公开")
    import kb as KB
    import kb_web as WEB

    base = site + "/w-kb1-test"
    admin = WEB.ensure_admin_token(state)
    docs = lib / "原始文档"
    (docs / "技术").mkdir(parents=True, exist_ok=True)

    # 没有管理令 → 打不开
    st, body = http(f"{base}/admin")
    check("管理页没有管理令 → 401", st == 401, str(st))

    # 先放两个新文件（一个正常、一个类型不支持）
    (docs / "技术" / "网页管理自检.md").write_text(
        "# 网页管理自检\n\n这段文字用来验证：从管理页公开之后，同事立刻能读到。\n", encoding="utf-8")
    (docs / "技术" / "表格自检.xlsx").write_bytes(b"PK\x03\x04 not-a-real-xlsx")

    st, body = http(f"{base}/admin?k={admin}")
    check("带管理令打开管理页 → 200，且有上传区 + 扫描入口",
          st == 200 and "添加资料" in body and "扫描资料目录" in body
          and 'name="files"' in body and "webkitdirectory" in body, str(st))
    check("上传区支持拖拽与批量操作栏", "拖到这里" in body and "批量定为" in body)
    check("页面上有资料目录路径", "原始文档" in body)

    # ① 扫库
    st, body = http(f"{base}/admin/scan", data={"k": admin, "action": "scan"})
    check("管理页点「扫描资料目录」→ 新增待批 1 篇", st == 200 and "新增待批 1" in body, str(st))
    check("类型不支持的被标出来（.xlsx）", "类型不支持" in body or "需人工转文本" in body)

    did_new = KB.doc_id("原始文档/技术/网页管理自检.md")
    did_xlsx = KB.doc_id("原始文档/技术/表格自检.xlsx")
    cat, _ = KB.load_catalog(state, "kb1")
    check("新文件进了台账且状态=pending", (cat["docs"].get(did_new) or {}).get("status") == "pending")
    check("默认等级来自窗口配置（L1-商务）", (cat["docs"].get(did_new) or {}).get("level") == "L1-商务",
          str((cat["docs"].get(did_new) or {}).get("level")))
    check("不支持的篇目状态=unsupported（且打了标记）",
          (cat["docs"].get(did_xlsx) or {}).get("status") == "unsupported")

    # 待批的内容，同事还读不到
    async def before(session):
        r = await call(session, "kb_read", {"doc_id": did_new})
        check("公开之前同事读不到（即便已入库）", "尚未公开" in str(r), str(r)[:80])
    await with_session(f"{site}/kb-{zhang['token']}", before)

    # ② 从管理页公开（当场选等级）
    st, body = http(f"{base}/admin/doc", data={"k": admin, "did": did_new,
                                               "action": "approve", "level": "L2-技术"})
    check("管理页点「公开」→ 当场生效", st == 200 and "已公开" in body and "L2-技术" in body, str(st)[:120])

    async def after(session):
        r = await call(session, "kb_read", {"doc_id": did_new})
        check("公开后同事立刻能读到（网页操作 = CLI 同等效力）", "网页管理自检" in str(r), str(r)[:80])
    await with_session(f"{site}/kb-{zhang['token']}", after)

    # ③ 改等级（同事的地址没变，权限跟着变）
    st, body = http(f"{base}/admin/doc", data={"k": admin, "did": did_new,
                                               "action": "setlevel", "level": "L3-核心"})
    check("管理页「改等级」→ 200", st == 200 and "等级已改成" in body, str(st)[:120])

    async def after2(session):
        r = await call(session, "kb_read", {"doc_id": did_new})
        check("改成 L3 后，只有 L2 的同事又读不到了", "无权访问" in str(r), str(r)[:80])
    await with_session(f"{site}/kb-{zhang['token']}", after2)

    # ④ 管理令可以直接预览原件（不占同事的地址）
    st, blob, _ = http_bytes(f"{base}/dl/{did_new}?k={admin}")
    check("管理页「看原件」→ 维护者能直接下到（等级不挡自己）",
          st == 200 and "网页管理自检" in blob.decode("utf-8", "replace"), str(st))

    # ⑤ 不支持的篇目：批准会被拒
    st, body = http(f"{base}/admin/doc", data={"k": admin, "did": did_xlsx,
                                               "action": "approve", "level": "L2-技术"})
    check("类型不支持的篇目 → 管理页也拒绝公开", st == 200 and "不能这么做" in body, str(body)[:140])

    # ⑥ 等级不在白名单
    st, body = http(f"{base}/admin/doc", data={"k": admin, "did": did_new,
                                               "action": "approve", "level": "L9-不存在"})
    check("乱传等级 → 被白名单拦住", "不在本窗允许清单" in body)

    # ⑦ 下架（回到待批，同事立刻失去）
    st, body = http(f"{base}/admin/doc", data={"k": admin, "did": did_new, "action": "setlevel",
                                               "level": "L2-技术"})
    st, body = http(f"{base}/admin/doc", data={"k": admin, "did": did_new, "action": "revoke"})
    check("管理页「下架」→ 200", st == 200 and "已下架" in body, str(st)[:120])

    async def after3(session):
        r = await call(session, "kb_read", {"doc_id": did_new})
        check("下架后同事立刻读不到", "尚未公开" in str(r), str(r)[:80])
    await with_session(f"{site}/kb-{zhang['token']}", after3)

    # ⑧ 一次公开全部待批
    st, body = http(f"{base}/admin/doc", data={"k": admin, "action": "approve_all", "level": "L1-商务"})
    cat2, _ = KB.load_catalog(state, "kb1")
    check("「一律公开」把待批的都公开（等级一次定）",
          st == 200 and "已公开 1 篇" in body
          and (cat2["docs"].get(did_new) or {}).get("status") == "approved"
          and (cat2["docs"].get(did_new) or {}).get("level") == "L1-商务", str(body)[:140])
    check("不支持的篇目不会被顺手公开（仍在 unsupported）",
          (cat2["docs"].get(did_xlsx) or {}).get("status") == "unsupported")

    # ⑨ 从台账删掉（文件不动）
    st, body = http(f"{base}/admin/doc", data={"k": admin, "did": did_xlsx, "action": "forget"})
    cat, _ = KB.load_catalog(state, "kb1")
    check("「从台账删掉」→ 台账里没有它了（文件还在磁盘上）",
          st == 200 and did_xlsx not in (cat.get("docs") or {}) and (docs / "技术" / "表格自检.xlsx").is_file())

    # ⑩ 管理页上的操作都留痕（actor=admin）
    rows = [json.loads(l) for l in (state / "audit/kb1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for tool in ("kb_scan", "kb_approve", "kb_setlevel", "kb_revoke", "kb_forget"):
        hit = [r for r in rows if r.get("tool") == tool and r.get("actor") == "admin"]
        check(f"审计留痕：{tool}（actor=admin）", bool(hit), f"{len(rows)} 条记录")
    prev = [r for r in rows if r.get("tool") == "kb_download" and r.get("admin")]
    check("维护者预览原件也留痕（principal=维护者(管理令)）",
          bool(prev) and prev[-1].get("principal") == "维护者(管理令)",
          str(prev[-1].get("principal")) if prev else "没有预览记录")

    # 清理自检文件
    (docs / "技术" / "网页管理自检.md").unlink(missing_ok=True)
    (docs / "技术" / "表格自检.xlsx").unlink(missing_ok=True)


# ---------------------------------------------------------------- ⑧ 上传与批量定权限
def http_multipart(url: str, fields: dict, files: list):
    """files: [(表单字段名, 上传时的文件名(可带子目录), 字节)] —— 模拟浏览器/拖拽上传。"""
    boundary = "----kbTestBoundary7d1c9f"
    out = []
    for k, v in fields.items():
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for name, fname, data in files:
        out.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                    f'filename="{fname}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode())
        out.append(data)
        out.append(b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    body = b"".join(out)
    req = Request(url, data=body,
                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


async def part_upload(site: str, state: Path, lib: Path, zhang: dict):
    print("\n⑧ 管理页上传（文件/文件夹/拖拽）与批量定权限")
    import kb as KB
    import kb_web as WEB

    base = site + "/w-kb1-test"
    admin = WEB.ensure_admin_token(state)
    docs = lib / "原始文档"
    docs.mkdir(parents=True, exist_ok=True)

    # ① 单文件上传 → 进待批
    st, body = http_multipart(f"{base}/admin/upload?k=" + admin,
                              {"k": admin, "category": "技术", "level": "L2-技术", "after": "pending"},
                              [("files", "上传自检.md", "# 上传自检\n\n第一份上传的资料。\n".encode())])
    did1 = KB.doc_id("原始文档/技术/上传自检.md")
    cat, _ = KB.load_catalog(state, "kb1")
    check("上传单个文件 → 落到资料目录里",
          st == 200 and (docs / "技术" / "上传自检.md").is_file(), str(st))
    check("上传后自动入库进待批（不用再点扫描）",
          (cat["docs"].get(did1) or {}).get("status") == "pending", str((cat["docs"].get(did1) or {}).get("status")))
    check("页面回执列出收下的文件", "已收下 1 个文件" in body and "上传自检.md" in body)

    # ② 文件夹上传（文件名带子目录）
    st, body = http_multipart(f"{base}/admin/upload?k=" + admin,
                              {"k": admin, "category": "技术", "level": "L1-商务", "after": "publish"},
                              [("files", "子方案/网络/拓扑说明.md", "# 拓扑说明\n\n子目录也要跟着建。\n".encode())])
    did2 = KB.doc_id("原始文档/技术/子方案/网络/拓扑说明.md")
    cat, _ = KB.load_catalog(state, "kb1")
    check("文件夹上传 → 子目录层级被保留",
          (docs / "技术" / "子方案" / "网络" / "拓扑说明.md").is_file())
    check("上传时选「直接公开」→ 当场就是 approved + 指定等级",
          (cat["docs"].get(did2) or {}).get("status") == "approved"
          and (cat["docs"].get(did2) or {}).get("level") == "L1-商务",
          str(cat["docs"].get(did2) or {})[:90])

    async def can_read(session, did):
        return str(await call(session, "kb_read", {"doc_id": did}))
    r = await with_session(f"{site}/kb-{zhang['token']}", lambda s: can_read(s, did2))
    check("上传即公开的那篇，同事立刻能读到", "拓扑说明" in str(r), str(r)[:80])

    # ③ 危险文件名：不许写到库外
    st, body = http_multipart(f"{base}/admin/upload?k=" + admin, {"k": admin, "category": "", "level": "L1-商务"},
                              [("files", "../../逃逸.md", "# 逃逸\n".encode())])
    outside = (lib.parent / "逃逸.md")
    check("上传带 ../ 的文件名 → 不会写到资料库外面",
          not outside.exists() and not (lib / "逃逸.md").exists(), str(outside))
    check("这种名字会被洗成库内安全名（回执里能看到）", "逃逸.md" in body, body[:120])

    st, body = http_multipart(f"{base}/admin/upload?k=" + admin, {"k": admin, "category": "", "level": "L1-商务"},
                              [("files", "/etc/passwd", b"root:x:0:0\n")])
    leaked = (docs / "etc" / "passwd")
    check("绝对路径上传 → 也只落在资料库内（不碰系统文件）",
          leaked.is_file() and leaked.read_text() == "root:x:0:0\n", str(st))

    st, body = http_multipart(f"{base}/admin/upload?k=" + admin, {"k": admin, "category": "技术", "level": "L1-商务"},
                              [("files", ".隐藏文件", b"secret\n")])
    check("隐藏文件（点开头）被拒", st == 200 and "隐藏文件不允许" in body, str(st))

    # ④ 超过单文件上限
    big = b"x" * (1024 * 1024 + 50 * 1024)      # 测试窗口上限设成 1MB
    st, body = http_multipart(f"{base}/admin/upload?k=" + admin, {"k": admin, "category": "技术", "level": "L1-商务"},
                              [("files", "太大.md", big)])
    check("超过单文件上限 → 拒收并说明上限", "超过上限" in body, body[:140])
    check("被拒的文件不会留在磁盘上", not (docs / "技术" / "太大.md").exists())

    # ⑤ 不支持的类型 → 进台账但标 unsupported
    st, body = http_multipart(f"{base}/admin/upload?k=" + admin, {"k": admin, "category": "技术", "level": "L1-商务"},
                              [("files", "上传表格.xlsx", b"PK\x03\x04nope")])
    did3 = KB.doc_id("原始文档/技术/上传表格.xlsx")
    cat, _ = KB.load_catalog(state, "kb1")
    check("不支持的类型也收下但标成 unsupported（提示要人工转文本）",
          (cat["docs"].get(did3) or {}).get("status") == "unsupported" and "类型不支持" in body)

    # ⑥ 批量：勾选两篇 → 一次定等级公开
    st, body = http(f"{base}/admin/bulk", data={"k": admin, "did": f"{did1}&did={did3}",
                                                "bulk": "approve", "bulk_level": "L3-核心"})
    cat, _ = KB.load_catalog(state, "kb1")
    check("批量公开：选中的都变成 approved",
          (cat["docs"].get(did1) or {}).get("status") == "approved", str(st))
    check("不支持的篇目不会被批量放行（会逐条报错）",
          (cat["docs"].get(did3) or {}).get("status") == "unsupported" and "不能" in body, body[-160:])
    check("批量结果有回执（完成几篇）", "已完成：公开 1 篇" in body, body[:120])

    # ⑦ 批量改等级 / 下架 / 删条目
    st, body = http(f"{base}/admin/bulk", data={"k": admin, "did": f"{did1}&did={did2}",
                                                "bulk": "setlevel", "bulk_level": "L1-商务"})
    cat, _ = KB.load_catalog(state, "kb1")
    check("批量改等级 → 两篇都变 L1-商务",
          (cat["docs"].get(did1) or {}).get("level") == "L1-商务"
          and (cat["docs"].get(did2) or {}).get("level") == "L1-商务")

    st, body = http(f"{base}/admin/bulk", data={"k": admin, "did": did2, "bulk": "revoke"})
    cat, _ = KB.load_catalog(state, "kb1")
    check("批量下架 → 回到待批", (cat["docs"].get(did2) or {}).get("status") == "pending")

    st, body = http(f"{base}/admin/bulk", data={"k": admin, "bulk": "forget", "did": did1})
    cat, _ = KB.load_catalog(state, "kb1")
    check("批量删条目 → 台账里没了，文件还在",
          did1 not in (cat.get("docs") or {}) and (docs / "技术" / "上传自检.md").is_file())

    # ⑧ 单篇按钮（一行一个 did，带自己的等级）
    st, body = http(f"{base}/admin/bulk", data={"k": admin, "one": f"{did2}@approve",
                                                f"level_{did2}": "L2-技术"})
    cat, _ = KB.load_catalog(state, "kb1")
    check("单篇「公开」只作用于这一篇、用这一行选的等级",
          (cat["docs"].get(did2) or {}).get("status") == "approved"
          and (cat["docs"].get(did2) or {}).get("level") == "L2-技术")

    # ⑨ 筛选（管理页按状态/关键词看）
    st, body = http(f"{base}/admin?k={admin}&status=pending")
    check("按状态筛选：只看待批", "上传自检" not in body and "资料清单" in body)
    from urllib.parse import quote as _q
    st, body = http(f"{base}/admin?k={admin}&q={_q('拓扑')}")
    check("按关键词搜：只列匹配的", "拓扑说明" in body and "上传表格" not in body)

    # ⑩ 审计：上传/入库/批量都留痕
    rows = [json.loads(l) for l in (state / "audit/kb1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    up = [r for r in rows if r.get("tool") == "kb_upload_file"]
    check("审计留痕：上传的文件（含字节数、分类、等级）",
          bool(up) and up[-1].get("bytes", 0) > 0 and up[-1].get("actor") == "admin",
          str(up[-1])[:90] if up else "无")
    check("审计留痕：上传后自动入库（kb_scan by=upload）",
          any(r.get("tool") == "kb_scan" and r.get("by") == "upload" for r in rows))
    check("审计留痕：上传即公开（kb_approve via=upload）",
          any(r.get("tool") == "kb_approve" and r.get("via") == "upload" for r in rows))
    check("审计留痕：批量操作标了 batch",
          any(r.get("tool") in ("kb_approve", "kb_setlevel") and r.get("batch") for r in rows))

    # 清理
    for f in ("上传自检.md", "上传表格.xlsx"):
        (docs / "技术" / f).unlink(missing_ok=True)
    import shutil as _sh
    _sh.rmtree(docs / "技术" / "子方案", ignore_errors=True)
    _sh.rmtree(docs / "etc", ignore_errors=True)


# ---------------------------------------------------------------- ⑨ 同事管理：改等级与其它
async def part_users(site: str, state: Path, zhang: dict):
    print("\n⑨ 同事管理页：改等级（多档）/ 有效期 / 部门备注 / 停用 / 换地址 / 改名 / 删除")
    import kb as KB
    import kb_web as WEB
    import kb_access as ACC

    base = site + "/w-kb1-test"
    admin = WEB.ensure_admin_token(state)
    did_tech = KB.doc_id("原始文档/技术/服务器技术方案.md")        # L2-技术
    did_core = KB.doc_id("原始文档/核心/核心网架构.md")            # L3-核心

    st, body = http(f"{base}/admin?k={admin}")
    check("同事区一行就能改（有勾选框、有效期、保存按钮）",
          st == 200 and 'name="levels"' in body and "能看哪些等级（可多选）" in body
          and "更换地址" in body and "看地址" in body)
    check("同事行里能看到地址（带复制按钮）", "/kb-" in body and "已复制" in body)

    async def can_read(did):
        async def _f(session):
            return str(await call(session, "kb_read", {"doc_id": did}))
        try:
            return await with_session(f"{site}/kb-{zhang['token']}", _f)
        except Exception as e:                                                   # noqa: BLE001
            return f"连接被拒：{e.__class__.__name__}"

    # ① 加一档（L3）→ 立刻能读 L3 的资料
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三",
                                                "new_name": "张三", "dept": "技术部", "note": "对接 XX 项目",
                                                "levels": "L1-商务&levels=L2-技术&levels=L3-核心",
                                                "for_days": "", "until": "", "enabled": "",
                                                "action": "save"})
    u = ACC.get_user(state, "kb1", "张三")
    check("改等级：一次能挂多档（L1+L2+L3）", st == 200 and set(u["levels"]) == {"L1-商务", "L2-技术", "L3-核心"},
          str(u.get("levels")))
    check("改名/部门/备注一起存下（地址不变）",
          u["dept"] == "技术部" and u["note"] == "对接 XX 项目" and u["token"] == zhang["token"])
    check("回执说清了改了什么", "已更新" in body and "等级" in body, body[:120])
    r = await can_read(did_core)
    check("加档后同事立刻能读 L3 的资料（不用重启）", "核心网架构" in str(r), str(r)[:80])

    # ② 减档 → 立刻读不到
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三", "new_name": "张三",
                                                "levels": "L1-商务&levels=L2-技术", "action": "save"})
    r = await can_read(did_core)
    check("减档后立刻读不到（权限是现读现算）", "无权访问" in str(r), str(r)[:80])

    # ③ 有效期：给到具体某天
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三", "new_name": "张三",
                                                "levels": "L1-商务&levels=L2-技术",
                                                "until": "2027-01-31", "action": "save"})
    u = ACC.get_user(state, "kb1", "张三")
    exp = __import__("datetime").datetime.fromtimestamp(float(u["expires"]), __import__("datetime").timezone(
        __import__("datetime").timedelta(hours=8)))
    check("有效期能定到具体某天（当天 23:59 到期）",
          st == 200 and exp.strftime("%Y-%m-%d %H:%M") == "2027-01-31 23:59", exp.isoformat())

    # ④ 改成无期限
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三", "new_name": "张三",
                                                "levels": "L1-商务", "for_days": "0", "action": "save"})
    u = ACC.get_user(state, "kb1", "张三")
    check("能改成无期限", u["expires"] is None)

    # ⑤ 停用 / 启用
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三", "new_name": "张三",
                                                "levels": "L1-商务", "enabled": "0", "action": "save"})
    r = await can_read(did_tech)
    check("停用后他的地址立刻失效",
          ("停用" in str(r) or "未授权" in str(r) or "连接被拒" in str(r) or "地址" in str(r)), str(r)[:70])
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三", "new_name": "张三",
                                                "levels": "L1-商务&levels=L2-技术", "enabled": "1",
                                                "action": "save"})
    r = await can_read(did_tech)
    check("再启用 + 加回 L2 → 又能读", "双电源冗余" in str(r), str(r)[:80])

    # ⑥ 改名（地址与权限跟着走）
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三", "new_name": "张三丰",
                                                "levels": "L1-商务&levels=L2-技术", "action": "save"})
    u1, u2 = ACC.get_user(state, "kb1", "张三"), ACC.get_user(state, "kb1", "张三丰")
    check("改名：新名字下有记录、旧名字清掉、地址不变",
          u1 is None and u2 is not None and u2["token"] == zhang["token"], str(bool(u1)) + str(bool(u2)))
    r = await can_read(did_tech)
    check("改名后他的地址照样能用", "双电源冗余" in str(r), str(r)[:80])
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三丰", "new_name": "李四",
                                                "levels": "L1-商务", "action": "save"})
    check("改名撞已有的人 → 拦住并说明", "已经有一个叫" in body, body[:120])
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三丰", "new_name": "张三",
                                                "levels": "L1-商务&levels=L2-技术", "action": "save"})

    # ⑦ 校验：乱传等级 / 一个都不勾
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三", "new_name": "张三",
                                                "levels": "L9-不存在", "action": "save"})
    u = ACC.get_user(state, "kb1", "张三")
    check("乱传等级 → 拒绝且不改动", "不在本窗允许清单" in body and set(u["levels"]) == {"L1-商务", "L2-技术"},
          str(u.get("levels")))
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "张三", "new_name": "张三",
                                                "levels": "", "action": "save"})
    check("一个等级都不勾 → 提示（并让他用停用/删除）", "至少要留一个等级" in body, body[:120])

    # ⑧ 换新地址 / 删除（拿一个一次性的同事试，别毁掉后面段要用的张三）
    ACC.upsert_user(state, "kb1", "测试-待删", ["L1-商务"], dept="临时")
    old_token = ACC.get_user(state, "kb1", "测试-待删")["token"]
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "测试-待删", "action": "rotate"})
    new_token = ACC.get_user(state, "kb1", "测试-待删")["token"]
    check("换新地址：token 变了、旧地址作废",
          st == 200 and new_token != old_token and ACC.check_token(state, "kb1", old_token)[1] != "", body[:60])
    st, body = http(f"{base}/admin/user", data={"k": admin, "person": "测试-待删", "action": "delete"})
    check("删除：记录消失、地址作废",
          st == 200 and ACC.get_user(state, "kb1", "测试-待删") is None
          and ACC.check_token(state, "kb1", new_token)[1] != "", body[:60])
    check("删掉的人不再出现在名单里",
          not any(u["person"] == "测试-待删" for u in ACC.list_users(state, "kb1")))
    # 收尾：把张三恢复成正常状态（后面的段还要用他的地址）
    ACC.update_user(state, "kb1", "张三", levels=["L1-商务", "L2-技术"], dept="技术部",
                    note="对接 XX 项目", expires=ACC.expiry_to_ts("30", ""), enabled=True)

    # ⑨ 用量看板：按人看明细
    from urllib.parse import quote as _q
    st, body = http(f"{base}/admin/usage?k={admin}&person={_q('张三')}")
    check("用量看板能只看某个人", st == 200 and "只看" in body and "看所有人" in body, str(st))
    st, body = http(f"{base}/admin/usage?k={admin}&by=person")
    check("用量看板仍能按人汇总", st == 200 and "按人" in body, str(st))

    # ⑩ 都留痕
    rows = [json.loads(l) for l in (state / "audit/kb1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for tool in ("kb_user_update", "kb_rotate", "kb_user_delete"):
        check(f"审计留痕：{tool}", any(r.get("tool") == tool and r.get("actor") == "admin" for r in rows))
# ---------------------------------------------------------------- main
async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="lh-kb-"))
    env, base_url, state, lib, port, _reg = build_env(tmp)
    print(f"受控资料库测试 | 隔离环境: {tmp}\n窗口: kb1（资料库模式，等级 {'/'.join(LEVELS)}）  服务: {base_url}\n")

    part_gate(tmp)

    srv = subprocess.Popen([PY, str(REPO / "core" / "server.py")], env=env,
                           stdout=open(tmp / "server.log", "wb"), stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.5)
        else:
            print("❌ 服务没起来")
            print((tmp / "server.log").read_text()[-1200:])
            return 2

        import config as C
        cfg = C.windows()["kb1"]
        zhang = part_cli(env, state, cfg)
        await part_mcp(base_url, state, zhang)
        part_web(base_url, state)
        site = base_url.replace("/w-kb1-test", "")
        link_test = part_download(site, state, zhang, str(lib))
        await with_session(f"{site}/kb-{zhang['token']}", link_test)
        await part_admin(site, state, lib, zhang)
        await part_upload(site, state, lib, zhang)
        await part_users(site, state, zhang)
        part_track(state, env, zhang)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            srv.kill()

    print(f"\n════════ {count - len(fails)}/{count} 通过 ════════")
    if fails:
        print("失败项：")
        for f in fails:
            print(f"  ❌ {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
