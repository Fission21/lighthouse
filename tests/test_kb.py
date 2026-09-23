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
                          "auto_approve_levels": ["L1-商务"], "admin_remote": False}},
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
    mine = [r for r in rows if r.get("principal") == "张三"]
    check("审计记到了「谁」（principal=张三）", bool(mine), f"{len(rows)} 条记录")
    check("审计带上了他的等级", all(r.get("levels") == ["L1-商务", "L2-技术"] for r in mine) if mine else False,
          str(mine[0].get("levels")) if mine else "")
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
    check("kb users 列出同事与用量（含被拒次数）", rc == 0 and "张三" in out and "被拒" in out, out[:80])
    out, rc = cli("kb", "admin-url", "kb1", env=env)
    check("kb admin-url 打印带管理令的管理页地址", rc == 0 and "/admin?k=" in out, out[:80])


# ---------------------------------------------------------------- main
async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="lh-kb-"))
    env, base_url, state, _lib, port, _reg = build_env(tmp)
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
