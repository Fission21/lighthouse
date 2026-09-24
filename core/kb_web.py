#!/usr/bin/env python3
"""受控资料门户的网页层（服务端渲染，无框架、无新依赖）。

路由（都挂在窗口路径下，例如 /w-bidkb-xxxx/request）：

  GET  <窗路径>/request                申请表单（公开）
  POST <窗路径>/request                提交申请（公开：限速 + 蜜罐 + 长度上限 + 等级白名单）
  GET  <窗路径>/request/status         查进度（要「申请号 + 查询码」；批下来后显示属于他的地址）
  GET  <窗路径>/admin                  管理页（仅部署机直连 + 管理令；可另开 admin_remote 从手机看）
  POST <窗路径>/admin/decide           批准 / 驳回申请（批准时选等级与有效期）
  POST <窗路径>/admin/grant            直接给某人一条地址（不经申请）
  POST <窗路径>/admin/revoke           停用某人地址（--enable 可恢复）
  POST <窗路径>/admin/rotate           给某人换一条新地址（旧地址立即失效）
  GET  <窗路径>/admin/usage            用量看板（按人/按天/按资料）

原则：页面只能**新增申请**；批准、发放、停用、换地址一律要管理令（维护者侧）。所有动作写审计。
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import kb as KB
import kb_access as ACC
import kb_download as DL
import kb_ingest as ING
import kb_usage as USAGE

CST = timezone(timedelta(hours=8))
_RATE: dict[str, list[float]] = {}          # 申请人限速：{ip: [时间戳]}
RATE_MAX = 5                                 # 每小时最多 5 次申请
RATE_WINDOW = 3600


# ---------------------------------------------------------------- 工具
def _now() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M")


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def client_ip(scope, hdrs: dict) -> str:
    return hdrs.get("cf-connecting-ip") or (scope.get("client") or ("-", 0))[0] or "-"


def admin_token_path(state_root: Path) -> Path:
    return Path(state_root) / "state" / "kb-admin.json"


def ensure_admin_token(state_root: Path, create: bool = True) -> str:
    """管理令：只存在本机状态目录（不进版本库）。create=True 时首次访问自动生成。"""
    p = admin_token_path(state_root)
    try:
        tok = json.loads(p.read_text(encoding="utf-8")).get("token") or ""
    except (OSError, json.JSONDecodeError):
        tok = ""
    if tok or not create:
        return tok
    tok = "adm_" + secrets.token_urlsafe(24)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"token": tok, "created_at": _now()}, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return tok


def rotate_admin_token(state_root: Path) -> str:
    """换一条新的管理令（旧的立刻失效）。管理页地址一旦被转发出去就换它。"""
    tok = "adm_" + secrets.token_urlsafe(24)
    p = admin_token_path(state_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"token": tok, "created_at": _now(), "rotated": True},
                            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return tok


def _rate_ok(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW]
    if len(hits) >= RATE_MAX:
        _RATE[ip] = hits
        return False
    hits.append(now)
    _RATE[ip] = hits
    return True


def _page(title: str, body: str, base: str, *, admin: str = "", wide: bool = False) -> bytes:
    """统一外壳：手机可用、卡片式、表格可横向滚动。

    wide=True 给管理页用（表格宽），其它页面保持窄栏读书式排版。
    """
    nav = ""
    if admin:
        nav = (f'<nav><a href="{esc(base)}/request">申请页</a>'
               f'<a href="{esc(base)}/admin?k={esc(admin)}">管理页</a>'
               f'<a href="{esc(base)}/admin/usage?k={esc(admin)}">用量</a></nav>')
    width = "1180px" if wide else "760px"
    return f"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>
  :root {{ color-scheme: light;
    --ink: #14161a; --dim: #6b7280; --line: #e6e8ec; --card: #fff; --bg: #f6f7f9;
    --blue: #0a6cff; --blue-d: #0857cc; --green: #127a45; --green-b: #e6f6ec;
    --amber: #8a5a00; --amber-b: #fff6e5; --red: #a02318; --red-b: #fdecea;
    --violet: #5b21b6; --violet-b: #f1eafe; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 15px/1.65 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0 auto; padding: 22px 16px 72px; max-width: {width}; color: var(--ink); background: var(--bg); }}
  h1 {{ font-size: 22px; margin: 0 0 2px; letter-spacing: -.2px; }}
  h2 {{ font-size: 17px; margin: 30px 0 10px; }}
  h3 {{ font-size: 15px; margin: 22px 0 8px; color: #374151; }}
  p.lead {{ color: var(--dim); margin: 0 0 16px; }}
  nav {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; font-size: 14px; }}
  nav a {{ color: var(--ink); text-decoration: none; background: #fff; border: 1px solid var(--line);
          padding: 5px 12px; border-radius: 999px; }}
  nav a:hover {{ border-color: var(--blue); color: var(--blue); }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px;
          margin: 0 0 14px; }}
  form {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px;
         margin: 0 0 14px; }}
  form.inline {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 12px;
         background: #fff; }}
  form.inline label {{ margin: 0; }}
  label {{ display: block; margin: 12px 0 4px; font-size: 13px; color: #374151; font-weight: 500; }}
  input, select, textarea {{ width: 100%; font: inherit; padding: 9px 10px; border: 1px solid #d5d8de;
         border-radius: 9px; background: #fff; color: var(--ink); }}
  input:focus, select:focus, textarea:focus {{ outline: 2px solid #cfe1ff; border-color: var(--blue); }}
  textarea {{ min-height: 76px; }}
  form.inline input, form.inline select {{ width: auto; min-width: 90px; }}
  button {{ font: inherit; padding: 9px 16px; border: 0; border-radius: 9px; background: var(--blue);
         color: #fff; font-weight: 600; cursor: pointer; }}
  button:hover {{ background: var(--blue-d); }}
  button.ghost {{ background: #eceef2; color: var(--ink); font-weight: 500; }}
  button.ghost:hover {{ background: #e2e5ea; }}
  button.danger {{ background: #fff; color: var(--red); border: 1px solid #f3c9c4; font-weight: 500; }}
  button.tiny {{ padding: 5px 10px; font-size: 13px; border-radius: 7px; }}
  .toolbar {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 10px 0; }}
  .toolbar input[type=search], .toolbar input[type=text] {{ width: auto; min-width: 180px; }}
  .wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 14px; background: #fff; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #f1f3f6; vertical-align: top; }}
  th {{ background: #fbfbfd; font-weight: 600; white-space: nowrap; position: sticky; top: 0; }}
  tr:last-child td {{ border-bottom: 0; }}
  tr:hover td {{ background: #fcfdff; }}
  td.acts {{ white-space: nowrap; }}
  td.acts form {{ border: 0; padding: 0; margin: 0; background: none; display: flex; gap: 6px; align-items: center; }}
  td.acts select {{ width: auto; min-width: 96px; padding: 5px 8px; font-size: 13px; }}
  code {{ background: #f3f4f7; padding: 1px 6px; border-radius: 6px; word-break: break-all; font-size: 13px; }}
  .chip {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600;
          border: 1px solid transparent; white-space: nowrap; }}
  .c-l1 {{ background: var(--green-b); color: var(--green); border-color: #bfe6cd; }}
  .c-l2 {{ background: #e8f1ff; color: #0b4fbf; border-color: #c7dcff; }}
  .c-l3 {{ background: var(--violet-b); color: var(--violet); border-color: #ddcdfa; }}
  .c-pending {{ background: var(--amber-b); color: var(--amber); border-color: #ffd8a8; }}
  .c-approved {{ background: var(--green-b); color: var(--green); border-color: #bfe6cd; }}
  .c-rejected, .c-unsupported {{ background: var(--red-b); color: var(--red); border-color: #f3c9c4; }}
  .ok {{ background: var(--green-b); border: 1px solid #bfe6cd; border-radius: 12px; padding: 14px 16px;
        margin: 0 0 14px; }}
  .warn {{ background: var(--amber-b); border: 1px solid #ffd8a8; border-radius: 12px; padding: 14px 16px;
        margin: 0 0 14px; }}
  .hint {{ color: var(--dim); font-size: 13px; }}
  .muted {{ color: var(--dim); }}
  .big {{ font-size: 18px; font-weight: 700; letter-spacing: .5px; }}
  .drop {{ border: 2px dashed #c9cfd8; border-radius: 14px; padding: 22px; text-align: center;
        color: var(--dim); background: #fbfcfe; }}
  .drop.hot {{ border-color: var(--blue); background: #f2f7ff; color: var(--blue); }}
  .btnlabel {{ display: inline-block; padding: 8px 14px; background: #eceef2; border-radius: 9px;
          cursor: pointer; color: var(--ink); font-weight: 500; }}
  .btnlabel:hover {{ background: #e2e5ea; }}
  .grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; }}
  .bar {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; background: #f8f9fb;
        border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; margin: 8px 0 0; }}
  @media (max-width: 640px) {{ th, td {{ padding: 8px; }} th {{ position: static; }} }}
</style></head><body>{nav}<h1>{esc(title)}</h1>{body}</body></html>""".encode("utf-8")


def level_chip(level: str) -> str:
    lv = level or "-"
    cls = "c-l1" if lv.startswith("L1") else ("c-l2" if lv.startswith("L2") else
                                             ("c-l3" if lv.startswith("L3") else "c-pending"))
    return f'<span class="chip {cls}">{esc(lv)}</span>'


def status_chip(status: str) -> str:
    m = {"approved": ("c-approved", "已公开"), "pending": ("c-pending", "待批"),
         "rejected": ("c-rejected", "不公开"), "unsupported": ("c-unsupported", "类型不支持")}
    cls, text = m.get(status or "", ("c-pending", status or "-"))
    return f'<span class="chip {cls}">{esc(text)}</span>'


# ---------------------------------------------------------------- 页面
def page_request(base: str, cfg_kb: dict, levels: list[str], msg: str = "") -> bytes:
    opts = "".join(f'<option value="{esc(l)}">{esc(l)}</option>' for l in levels)
    body = f"""<p class="lead">这是一个只读资料库。填写下面的申请，维护者审批通过后，
你会拿到一条**属于你自己的访问地址**，把它填进你的 AI 助手（如 ChatGPT 连接器）就能用。</p>
{msg}
<form method="post" action="{esc(base)}/request">
  <label>姓名 <span class="hint">（必填）</span></label>
  <input name="name" maxlength="40" required>
  <label>部门 / 小组</label>
  <input name="dept" maxlength="40">
  <label>想看的等级</label>
  <select name="level_requested">{opts}</select>
  <p class="hint">商务资料可自助开通；技术 / 核心资料需要维护者审批。</p>
  <label>用途 <span class="hint">（必填，一两句就行）</span></label>
  <textarea name="purpose" maxlength="500" required></textarea>
  <label>联系方式</label>
  <input name="contact" maxlength="80" placeholder="手机 / 邮箱 / 微信，任选">
  <input name="website" tabindex="-1" autocomplete="off"
         style="position:absolute;left:-9999px" aria-hidden="true">
  <button type="submit">提交申请</button>
</form>
<p class="hint" style="margin-top:16px">已经申请过？
<a href="{esc(base)}/request/status">用「申请号 + 查询码」查进度 / 取地址</a>。</p>
<p class="hint">看不到的资料就是没授权，需要更多请再提交一次申请并说明理由。</p>"""
    return _page("申请访问招投标资料库", body, base)


def page_submitted(base: str, rec: dict, address: str = "") -> bytes:
    if address:
        body = f"""<div class="ok"><b>✅ 已开通（{esc(rec.get('level_requested'))}）</b></div>
<p class="lead">你的申请号 <code>{esc(rec['id'])}</code>，查询码 <code>{esc(rec['code'])}</code>（建议记下，之后可查）。</p>
<h2>你的专属访问地址</h2>
<p><code>{esc(address)}</code></p>
<h2>怎么用</h2>
<ol>
  <li>ChatGPT → 设置 → 安全防护 → 打开「开发者模式」→ 插件页右上「创建应用」</li>
  <li>名称：招投标资料库　服务器 URL：粘贴上面那条地址　身份验证：无 → 创建</li>
  <li>回到对话，输入框里打 <code>@招投标资料库</code> 选中它，然后提问</li>
</ol>
<p class="hint">不想用 AI 也行：在浏览器里打开 <code>&lt;你的地址&gt;/files</code>，勾选资料直接下载原件或打包带走。<br>
其他客户端（WorkBuddy / Cherry Studio / Claude 等）：把同一条地址填进它的 MCP 配置即可。<br>
这条地址是你专用的；只能读、不能改，请勿转发。默认 {ACC.DEFAULT_MINUTES // 1440} 天有效。</p>"""
    else:
        body = f"""<div class="warn"><b>已收到你的申请</b>（需要维护者审批）</div>
<p class="lead">申请号 <code>{esc(rec['id'])}</code>　查询码 <code>{esc(rec['code'])}</code></p>
<p>请把这两样记下来。审批通过后回到
<a href="{esc(base)}/request/status">查进度页面</a>输入它们，就能看到属于你的访问地址。</p>
<p class="hint">已记录，正在等维护者审批（技术 / 核心等级需要人工确认）。处理完你在这里就能取到地址。</p>"""
    return _page("申请已提交", body, base)


def page_status(base: str, rec: dict | None, err: str = "", address: str = "") -> bytes:
    if err or not rec:
        body = f"""<p class="lead">输入申请时拿到的「申请号 + 查询码」。</p>
{'<div class="warn">' + esc(err) + '</div>' if err else ''}
<form method="get" action="{esc(base)}/request/status">
  <label>申请号</label><input name="id" maxlength="40" required>
  <label>查询码</label><input name="code" maxlength="12" required>
  <button type="submit">查询</button>
</form>"""
        return _page("查询申请进度", body, base)

    st = rec.get("status")
    if st == "pending":
        inner = '<div class="warn">还在等维护者审批。</div>'
    elif st == "denied":
        inner = f'<div class="warn">这条申请被驳回了。{esc(rec.get("decided_note") or "")}</div>'
    else:
        inner = (f'<div class="ok"><b>✅ 已通过</b></div>'
                 f'<h2>你的访问地址</h2><p><code>{esc(address)}</code></p>'
                 f'<p class="hint">把它填进你的 AI 助手（ChatGPT 连接器 / WorkBuddy / 其他 MCP 客户端）。'
                 f'只能读、不能改，请勿转发。</p>')
    body = (f'<p class="lead">申请号 <code>{esc(rec.get("id"))}</code>　'
            f'姓名 {esc(rec.get("name"))}　申请等级 {esc(rec.get("level_requested"))}</p>{inner}')
    return _page("申请进度", body, base)


def _users_table(state_root: Path, wid: str, admin: str, base: str) -> str:
    rows = []
    for u in ACC.list_users(state_root, wid):
        state = "✅ 有效" if u.get("enabled", True) else "⛔ 已停用"
        exp = ACC.describe_expiry(u)
        last = (u.get("last_seen") or "从未使用").replace("T", " ")[:16]
        actions = (f'<form method="post" action="{esc(base)}/admin/rotate" style="display:inline">'
                   f'<input type="hidden" name="k" value="{esc(admin)}">'
                   f'<input type="hidden" name="person" value="{esc(u["person"])}">'
                   f'<button class="ghost" type="submit">换地址</button></form> '
                   f'<form method="post" action="{esc(base)}/admin/revoke" style="display:inline">'
                   f'<input type="hidden" name="k" value="{esc(admin)}">'
                   f'<input type="hidden" name="person" value="{esc(u["person"])}">'
                   f'<input type="hidden" name="enabled" value="{"0" if u.get("enabled", True) else "1"}">'
                   f'<button class="ghost" type="submit">{"停用" if u.get("enabled", True) else "恢复"}</button></form>')
        rows.append(f'<tr><td>{esc(u["person"])}</td><td>{esc(", ".join(u.get("levels") or []))}</td>'
                    f'<td>{esc(state)}<br><span class="hint">{esc(exp)}</span></td>'
                    f'<td>{esc(last)}<br><span class="hint">调 {u.get("calls", 0)} / 拒 {u.get("denied", 0)}</span></td>'
                    f'<td>{actions}</td></tr>')
    if not rows:
        return '<p class="hint">还没有给任何人发过地址。</p>'
    return ('<table><tr><th>同事</th><th>等级</th><th>状态</th><th>使用</th><th>操作</th></tr>'
            + "".join(rows) + "</table>")


def page_files(base: str, person: str, levels: list[str], docs: list[dict], token: str,
               dcfg: dict, msg: str = "") -> bytes:
    """同事的「我的资料」页：能用 AI 查，也能在这儿直接把文件拿走。"""
    rows = []
    for d in docs:
        did = esc(d["doc_id"])
        link = f"{esc(base)}/dl/{did}?t={esc(token)}"
        orig = (f'<a href="{link}&mode=original">原件</a>'
                if dcfg.get("original", True) else '<span class="hint">原件未开放</span>')
        rows.append(
            f'<tr><td><input type="checkbox" name="ids" value="{did}" style="width:auto"></td>'
            f'<td>{esc(d.get("title") or "")}<div class="hint">{esc(d.get("category") or "未分类")}'
            f' · {int(d.get("chars") or 0)} 字</div></td>'
            f'<td>{esc(d.get("level") or "")}</td>'
            f'<td>{orig} · <a href="{link}&mode=text">文本</a></td></tr>')
    table = ("<table><tr><th style=\"width:28px\"></th><th>资料</th><th>等级</th><th>下载</th></tr>"
             + "".join(rows) + "</table>") if rows else \
            '<div class="warn">你的等级下暂时还没有可看的资料。需要更多请到申请页再申请。</div>'
    body = f"""{msg}
<p class="lead">你是 <b>{esc(person)}</b>，等级：{esc("、".join(levels) or "无")}。下面是你能拿到的资料。</p>
<form method="post" action="{esc(base)}/zip">
  <input type="hidden" name="t" value="{esc(token)}">
  {table}
  <p class="hint">勾选几篇 → 一起打包成 zip 下载；也可以直接点每行的「原件 / 文本」。</p>
  <button type="submit">打包下载勾选的资料</button>
</form>
<h2>用 AI 也能拿文件</h2>
<p class="hint">把你的地址（本页网址去掉 <code>/files</code>）填进 ChatGPT 等客户端的 MCP 配置，
然后直接说「把《XX》的原件给我」，AI 会返回一条<b>限时下载链接</b>（默认 15 分钟）。
两种方式都只读、都记在访问日志里。</p>
<p class="hint">只读；你的地址可以随时被收回或更换。资料涉及项目信息，请勿外传。</p>"""
    return _page("我的资料", body, base)


def _docs_panel(base: str, state_root: Path, wid: str, admin: str, levels: list[str],
                root: Path, docs_rel: str, q: dict | None = None) -> str:
    """管理页的「资料」面板：上传（文件/文件夹/拖拽）→ 定权限（逐篇或批量）→ 预览。

    命令行等价：lighthouse.sh kb scan|pending|approve|revoke —— 同一份台账、同一套闸门。
    """
    q = q or {}
    cat, err = KB.load_catalog(state_root, wid)
    docs = dict((cat or {}).get("docs") or {})
    if err:
        return '<h2>资料</h2><div class="warn">台账读不到：' + esc(err) + '</div>'

    kw = (q.get("q") or [""])[0].strip().lower()
    f_st = (q.get("status") or [""])[0]
    f_cat = (q.get("cat") or [""])[0]
    f_lv = (q.get("level") or [""])[0]

    def opts(cur: str, items: list[str]) -> str:
        return "".join('<option value="' + esc(i) + '"' + (" selected" if i == cur else "") + ">"
                       + esc(i) + "</option>" for i in items)

    lv_opts = "".join('<option value="' + esc(l) + '">' + esc(l) + "</option>" for l in levels)
    cats = sorted({(e.get("category") or "") for e in docs.values()})
    cand = sorted(docs.items(), key=lambda t: ((t[1].get("category") or ""), (t[1].get("title") or "")))
    shown = []
    for did, e in cand:
        if f_st and e.get("status") != f_st:
            continue
        if f_cat and (e.get("category") or "") != f_cat:
            continue
        if f_lv and (e.get("level") or "") != f_lv:
            continue
        if kw and kw not in ((e.get("title") or "") + " " + did + " " + (e.get("rel") or "")).lower():
            continue
        shown.append((did, e))

    n_by = {}
    for e in docs.values():
        n_by[e.get("status")] = n_by.get(e.get("status"), 0) + 1
    n_app, n_pen = n_by.get("approved", 0), n_by.get("pending", 0)
    n_rej, n_uns = n_by.get("rejected", 0), n_by.get("unsupported", 0)
    stat = "已公开 <b>%d</b> · 待批 <b>%d</b>" % (n_app, n_pen)
    if n_rej:
        stat += " · 不公开 %d" % n_rej
    if n_uns:
        stat += " · 类型不支持 %d" % n_uns

    rows = []
    for did, e in shown:
        title = ('<b>' + esc(e.get("title")) + "</b><br><span class=\"hint\">"
                 + esc(e.get("category") or "-") + " · " + str(int(e.get("chars") or 0)) + " 字 · <code>"
                 + esc(did) + "</code></span>")
        note = e.get("note") or ""
        if note:
            title += '<br><span class="warn" style="padding:4px 8px;display:inline-block">' + esc(note) + "</span>"
        prev = ('<a class="hint" href="' + esc(base) + "/dl/" + esc(did) + "?k=" + esc(admin)
                + '" target="_blank">看原件</a>')
        st = e.get("status")
        acts = ""
        if st == "approved":
            acts = ('<button class="tiny" name="one" value="' + esc(did) + '@setlevel">保存等级</button>'
                    '<button class="tiny ghost" name="one" value="' + esc(did) + '@revoke">下架</button>')
        elif st == "unsupported":
            acts = ('<button class="tiny danger" name="one" value="' + esc(did) + '@forget">移除条目</button>')
        else:
            acts = ('<button class="tiny" name="one" value="' + esc(did) + '@approve">公开</button>')
        title += "　" + prev
        rows.append('<tr><td><input type="checkbox" name="did" value="' + esc(did) + '" class="pick"></td>'
                    "<td>" + title + "</td><td>" + status_chip(st) + "</td>"
                    '<td><select name="level_' + esc(did) + '">' + opts(e.get("level") or levels[0], levels)
                    + "</select></td>"
                    '<td class="acts">' + acts + "</td></tr>")
    table = ("<table><tr><th style=\"width:28px\"><input type=\"checkbox\" id=\"all\"></th>"
             "<th>资料</th><th>状态</th><th>等级</th><th>操作</th></tr>"
             + "".join(rows) + "</table>") if rows else \
        '<p class="hint">没有符合条件的资料。换个筛选，或先上传/扫描。</p>'

    upload = (
        '<form method="post" action="' + esc(base) + '/admin/upload?k=' + esc(admin)
        + '" enctype="multipart/form-data" id="upform">'
        '<input type="hidden" name="k" value="' + esc(admin) + '">'
        '<h3 style="margin-top:0">添加资料（文件 / 整个文件夹 / 拖进来）</h3>'
        '<div class="drop" id="dz">把文件或文件夹<b>拖到这里</b>　·　'
        '<label class="btnlabel"><input type="file" name="files" multiple id="f1" hidden>选择文件</label>　'
        '<label class="btnlabel"><input type="file" name="files" webkitdirectory id="f2" hidden>选择文件夹'
        '</label>'
        '<div class="hint" id="uplist" style="margin-top:6px">还没选文件</div></div>'
        '<div class="grid2" style="margin-top:12px">'
        '<div><label>放进哪个分类</label><select name="category">'
        '<option value="">（资料库根目录）</option>' + "".join('<option value="' + esc(c) + '">' + esc(c)
                                                                + "</option>" for c in cats if c)
        + '</select></div>'
        '<div><label>或者新建一个分类名</label><input name="newcat" maxlength="40" placeholder="例如：技术方案"></div>'
        '<div><label>这批资料的等级</label><select name="level">' + lv_opts + "</select></div>"
        "</div>"
        '<div class="bar">'
        '<button type="submit" name="after" value="pending">上传，先进待批</button>'
        '<button class="ghost" type="submit" name="after" value="publish">上传并直接公开</button>'
        '<span class="hint">上传后会自动抽取文本（Word/PDF/PPT 等）；原件保留，同事可直接下载</span>'
        "</div></form>")

    scanform = ('<form class="inline" method="post" action="' + esc(base) + '/admin/scan">'
                '<input type="hidden" name="k" value="' + esc(admin) + '">'
                '<button class="ghost" type="submit">扫描资料目录</button>'
                '<span class="hint">文件已经用 Finder 放进 <code>' + esc(str(root / docs_rel))
                + '</code> 时点这个（只认新增/改动，不动已定好的等级）</span></form>')

    failed = (f_cat or kw or f_st or f_lv)
    clear = ('<a class="hint" href="' + esc(base) + "/admin?k=" + esc(admin) + '">重置筛选</a>') if failed else ""
    toolbar = (
        '<form class="inline" method="get" action="' + esc(base) + '/admin">'
        '<input type="hidden" name="k" value="' + esc(admin) + '">'
        '<input type="search" name="q" placeholder="搜标题 / 编号 / 路径" value="' + esc((q.get("q") or [""])[0]) + '">'
        '<select name="status"><option value="">全部状态</option>'
        + opts(f_st, ["pending", "approved", "rejected", "unsupported"])
        + '</select><select name="cat"><option value="">全部分类</option>'
        + "".join('<option value="' + esc(c) + '"' + (" selected" if c == f_cat else "") + ">"
                  + esc(c or "(根目录)") + "</option>" for c in cats)
        + '</select><select name="level"><option value="">全部等级</option>'
        + "".join('<option value="' + esc(l) + '"' + (" selected" if l == f_lv else "") + ">" + esc(l)
                  + "</option>" for l in levels)
        + '</select><button class="tiny" type="submit">筛选</button>' + clear + "</form>")

    bulkbar = (
        '<div class="bar">'
        '<span class="hint" id="cnt">已选 0 篇</span>'
        '<label style="margin:0">批量定为</label><select name="bulk_level">' + lv_opts + "</select>"
        '<button name="bulk" value="approve">设为公开</button>'
        '<button class="ghost" name="bulk" value="setlevel">只改等级</button>'
        '<button class="ghost" name="bulk" value="revoke">下架</button>'
        '<button class="danger" name="bulk" value="forget">移除条目（不删文件）</button>'
        '<span class="hint">先勾选左边小方框：「设为公开」= 按右边等级放开　·　「只改等级」= 已公开的换个等级　·　'
        '「下架」= 回到待批（资料还在）　·　「移除条目」= 不再管这篇（文件不动）</span></div>')

    js = """<script>
(function(){
  var f1=document.getElementById('f1'), f2=document.getElementById('f2'), dz=document.getElementById('dz'),
      list=document.getElementById('uplist'), up=document.getElementById('upform');
  function show(files){
    if(!files || !files.length){ list.textContent='还没选文件'; return; }
    var names=[], n=0; for(var i=0;i<files.length;i++){ n++; if(i<3) names.push(files[i].name); }
    list.textContent='共 '+n+' 个文件：'+names.join('、')+(n>3?' …':'');
  }
  if(f1) f1.addEventListener('change', function(){ show(f1.files); });
  if(f2) f2.addEventListener('change', function(){ show(f2.files); });
  if(dz && up){
    dz.addEventListener('dragover', function(e){ e.preventDefault(); dz.classList.add('hot'); });
    dz.addEventListener('dragleave', function(){ dz.classList.remove('hot'); });
    dz.addEventListener('drop', function(e){
      e.preventDefault(); dz.classList.remove('hot');
      var dt=e.dataTransfer; if(!dt) return;
      if(dt.items && dt.items.length && f1){ f1.files = dt.files; } else if(f1){ f1.files = dt.files; }
      show(dt.files);
      if(f1 && f1.files && f1.files.length){ up.querySelector('button[value=pending]').click(); }
    });
  }
  var all=document.getElementById('all');
  function picks(){ return document.querySelectorAll('input.pick'); }
  function sync(){
    var n=0, ps=picks(); for(var i=0;i<ps.length;i++) if(ps[i].checked) n++;
    var c=document.getElementById('cnt'); if(c) c.textContent='已选 '+n+' 篇';
  }
  if(all) all.addEventListener('change', function(){ var ps=picks();
    for(var i=0;i<ps.length;i++) ps[i].checked=all.checked; sync(); });
  document.addEventListener('change', function(e){ if(e.target && e.target.className==='pick') sync(); });
  sync();
})();
</script>"""

    return ("<h2>资料</h2><p class=\"lead\">" + stat + "</p>" + upload
            + scanform
            + "<h3>资料清单</h3>" + toolbar
            + '<form method="post" action="' + esc(base) + '/admin/bulk">'
              '<input type="hidden" name="k" value="' + esc(admin) + '">'
            + bulkbar
            + '<div class="wrap">' + table + "</div>"
            + '<div class="bar" style="margin-top:10px">'
              '<span class="hint">单篇：先在「等级」列选好，再点「保存等级」或「公开」。'
              '「看原件」= 你自己预览（不受等级限制，也不占同事的地址）</span></div>'
            + "</form>"
            + (f'<p class="hint">共 {len(docs)} 篇在台账里，当前显示 {len(shown)} 篇。'
               f'资料目录：<code>{esc(str(root / docs_rel))}</code></p>')
            + js)


def page_admin(base: str, state_root: Path, wid: str, admin: str, levels: list[str],
               host: str, msg: str = "", remote: bool = False,
               root: Path | None = None, docs_rel: str = "", q: dict | None = None) -> bytes:
    pend = KB.pending_requests(state_root, wid)
    prows = []
    for r in sorted(pend, key=lambda x: x.get("created_at") or ""):
        opts = "".join(f'<option value="{esc(l)}"{" selected" if l == r.get("level_requested") else ""}>'
                       f'{esc(l)}</option>' for l in levels)
        prows.append(
            f'<tr><td>{esc(r.get("name"))}<br><span class="hint">{esc(r.get("dept"))}</span></td>'
            f'<td>{esc(r.get("level_requested"))}<br><span class="hint">{esc((r.get("created_at") or "")[:16].replace("T", " "))}</span></td>'
            f'<td>{esc(r.get("purpose"))}<br><span class="hint">{esc(r.get("contact"))}</span></td>'
            f'<td><form method="post" action="{esc(base)}/admin/decide">'
            f'<input type="hidden" name="k" value="{esc(admin)}">'
            f'<input type="hidden" name="rid" value="{esc(r.get("id"))}">'
            f'<select name="level">{opts}</select>'
            f'<select name="for_days"><option value="30">30 天</option><option value="7">7 天</option>'
            f'<option value="90">90 天</option><option value="0">无期限</option></select>'
            f'<button type="submit" name="action" value="approve">批准</button> '
            f'<button class="ghost" type="submit" name="action" value="deny">驳回</button>'
            f'</form></td></tr>')
    ptable = ('<table><tr><th>申请人</th><th>等级/时间</th><th>用途</th><th>处理</th></tr>'
              + "".join(prows) + "</table>") if prows else '<p class="hint">没有待批申请。</p>'

    all_levels = "".join(f'<option value="{esc(l)}">{esc(l)}</option>' for l in levels)
    docs_html = (_docs_panel(base, state_root, wid, admin, levels, root, docs_rel, q)
                 if root is not None else '')
    body = f"""{msg}
<p class="lead">管理页 · {esc(_now())}　{'（可从公网访问：请勿把本页地址转发给别人）' if remote else '（仅部署机本机可访问）'}</p>

{docs_html}

<h2>待批申请（{len(pend)}）</h2>
{ptable}

<h2>已授权的同事（{len(ACC.list_users(state_root, wid))}）</h2>
{_users_table(state_root, wid, admin, base)}

<h2>直接发一条地址（不经申请）</h2>
<form method="post" action="{esc(base)}/admin/grant">
  <input type="hidden" name="k" value="{esc(admin)}">
  <label>姓名</label><input name="person" maxlength="40" required>
  <label>部门</label><input name="dept" maxlength="40">
  <label>等级</label><select name="level">{all_levels}</select>
  <label>有效期</label><select name="for_days"><option value="30">30 天</option><option value="7">7 天</option>
    <option value="90">90 天</option><option value="0">无期限</option></select>
  <label>备注</label><input name="note" maxlength="80">
  <button type="submit">发放</button>
</form>
<p class="hint" style="margin-top:18px">用量看板：<a href="{esc(base)}/admin/usage?k={esc(admin)}">按人 / 按天 / 按资料</a>
　·　命令行等价：<code>bash lighthouse.sh kb usage &lt;窗口&gt;</code></p>
<p class="hint">地址段（token）在本机 <code>~/.lighthouse/state/kb-users.json</code>，也可用
<code>bash lighthouse.sh kb users &lt;窗口&gt; --show-token</code> 查看。</p>"""
    return _page("资料库管理页", body, base, admin=admin, wide=True)


def page_usage(base: str, state_root: Path, wid: str, admin: str, by: str, days: int) -> bytes:
    rows = USAGE.read_audit(state_root, wid, days)
    summary = USAGE.summarize(rows, by)
    head = {"person": "同事", "day": "日期", "doc": "资料", "tool": "工具"}[by]
    th = f'<tr><th>{esc(head)}</th><th>调用</th><th>成功</th><th>被拒</th><th>最后活跃</th><th>常读资料</th></tr>'
    trs = "".join(
        f'<tr><td>{esc(r["key"])}</td><td>{r["calls"]}</td><td>{r["ok"]}</td><td>{r["denied"]}</td>'
        f'<td>{esc(r["last_seen"])}</td><td>{esc(r["top_docs"] or r["top_tools"])}</td></tr>'
        for r in summary)
    links = " · ".join(f'<a href="{esc(base)}/admin/usage?k={esc(admin)}&by={b}">{n}</a>'
                       for b, n in (("person", "按人"), ("day", "按天"), ("doc", "按资料"), ("tool", "按工具")))
    recent = [r for r in rows if r.get("principal") and r.get("principal") != "-"][-15:][::-1]
    rrows = "".join(f'<tr><td>{esc(str(r.get("ts"))[:16].replace("T", " "))}</td><td>{esc(r.get("principal"))}</td>'
                    f'<td>{esc(r.get("tool"))}</td><td>{esc(json.dumps(r.get("args") or {}, ensure_ascii=False))[:80]}</td>'
                    f'<td>{"✅" if r.get("ok") else "⛔ " + esc(str(r.get("reason") or "")[:40])}</td></tr>'
                    for r in recent)
    body = f"""<p class="lead">用量看板 · 近 {days} 天 · 共 {len(rows)} 条记录</p>
<p>{links}</p>
<table>{th}{trs or '<tr><td colspan="6" class="hint">没有记录</td></tr>'}</table>
<h2>最近调用明细</h2>
<table><tr><th>时间</th><th>谁</th><th>工具</th><th>参数</th><th>结果</th></tr>{rrows or '<tr><td colspan="5" class="hint">没有记录</td></tr>'}</table>"""
    return _page("用量看板", body, base, admin=admin)


# ---------------------------------------------------------------- ASGI 中间件
class _Portal:
    def __init__(self, app, base: str, win, state_root: Path, audit, cfg: dict, host: str):
        self.app = app
        self.base = base.rstrip("/")
        self.win = win
        self.state_root = Path(state_root)
        self.audit = audit
        self.cfg = cfg
        self.host = host
        self.levels = _levels_from(cfg)
        self.public_levels = _public_levels_from(cfg)
        self.auto_levels = _auto_levels_from(cfg)
        self.remote = _admin_remote_from(cfg)
        self._ip = "-"          # 每个请求进来时更新，供审计用
        self._multi: dict = {}  # 本次请求的表单多值字段
        self._token = ""        # 本次请求里出现的地址段（下载页里的链接要用）

    # ---- 小工具 ----
    async def _send(self, send, body: bytes, status: int = 200, ctype: str = "text/html; charset=utf-8"):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype.encode()),
                                (b"content-length", str(len(body)).encode()),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    def _redirect(self, base_path: str, q: str = "") -> bytes:
        return f'<meta http-equiv="refresh" content="0;url={base_path}{q}">'.encode()

    def _address(self, token: str) -> str:
        return f"https://{self.host}/kb-{token}"

    def _admin_gate(self, scope, hdrs: dict, qs: dict) -> tuple[bool, str]:
        """管理页门禁：① 是否允许从公网来 ② 是不是部署机本机 ③ 管理令对不对。"""
        proxied = bool(hdrs.get("cf-connecting-ip") or hdrs.get("x-forwarded-for"))
        ip = (scope.get("client") or ("-", 0))[0]
        if not proxied and ip not in ("127.0.0.1", "::1"):
            return False, "管理页只允许在部署机上访问。"
        if proxied and not self.remote:
            return False, ("管理页默认不开放给公网访问。要能从手机上看，"
                           "请在窗口配置里把 kb.portal.admin_remote 设为 true（并保管好管理令）。")
        tok = (qs.get("k") or [""])[0] or hdrs.get("x-admin-token", "")
        want = ensure_admin_token(self.state_root)
        if not want or not secrets.compare_digest(str(tok), str(want)):
            return False, "管理令不对（或缺失）。本机执行 `bash lighthouse.sh kb admin-url <窗口>` 取地址。"
        return True, ""

    # ---- 主入口 ----
    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if not (path == self.base or path.startswith(self.base + "/")):
            return await self.app(scope, receive, send)

        hdrs = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        qs = parse_qs(scope.get("query_string", b"").decode(errors="replace"))
        sub = path[len(self.base):].rstrip("/") or "/"
        ip = client_ip(scope, hdrs)
        self._ip = ip
        method = scope.get("method", "GET")

        # ⚠️ MCP 客户端 POST 的正是「窗口路径本身」。除了下面这几个网页路由，
        #    其余一切（含窗口路径本体）原样交给 MCP —— 绝不去读它的请求体。
        portal_routes = {"/request", "/request/status", "/admin", "/admin/usage", "/files", "/zip",
                         "/admin/decide", "/admin/grant", "/admin/revoke", "/admin/rotate",
                         "/admin/scan", "/admin/doc", "/admin/upload", "/admin/bulk", "/healthz"}
        if sub not in portal_routes and not sub.startswith("/dl/"):
            accept = hdrs.get("accept", "")
            if sub in ("", "/") and method == "GET" and "text/html" in accept and "text/event-stream" not in accept:
                return await self._send(send, self._redirect(f"{self.base}/request"))
            return await self.app(scope, receive, send)

        # 上传是 multipart，必须拿原始请求体自己解析（不能像普通表单那样先读成 dict）
        if sub == "/admin/upload" and method == "POST":
            qs_k = (qs.get("k") or [""])[0] or hdrs.get("x-admin-token", "")
            ok, why = self._admin_gate(scope, hdrs, {"k": [qs_k]})
            if not ok:
                self.audit("portal_admin", {"path": sub}, False, {"reason": why, "ip": ip})
                return await self._send(send, _page("管理页无法访问",
                                                    f'<div class="warn">{esc(why)}</div>',
                                                    self.base), 403 if "公网" in why else 401)
            return await self._upload(scope, receive, send, ensure_admin_token(self.state_root), ip)

        form: dict = {}
        if method == "POST":
            body = b""
            more = True
            while more:
                msg = await receive()
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
            parsed = parse_qs(body.decode(errors="replace"))
            self._multi = parsed                                       # 多选字段（ids）要保留全部
            form = {k: v[0] for k, v in parsed.items()}

        try:
            if sub == "/request":
                if method == "GET":
                    return await self._send(send, page_request(self.base, self.cfg, self.public_levels))
                return await self._submit(send, form, ip)
            if sub == "/request/status":
                return await self._status(send, qs)
            if sub == "/healthz":
                return await self._send(send, b'{"ok":true}', ctype="application/json")
            if sub == "/files" or sub.startswith("/dl/") or sub == "/zip":
                return await self._download(scope, send, qs, form, sub)
            return await self._admin(scope, send, hdrs, qs, form, sub, ip, method)
        except Exception as e:                                              # noqa: BLE001
            self.audit("portal_error", {"path": path}, False, {"reason": f"{e.__class__.__name__}: {e}"})
            return await self._send(send, _page("出错了", f'<div class="warn">{esc(e)}</div>', self.base), 500)

    # ---- 下载（同事取文件）----
    def _who(self, scope, qs: dict, form: dict | None = None) -> tuple[str, list[str], str]:
        """认出「现在是谁」：① 由地址段进来的（scope.kb_principal）② 链接里带 t=地址段 ③ 签名链接。"""
        p = scope.get("kb_principal")
        if isinstance(p, dict) and p.get("person"):
            self._token = str(p.get("token") or "")
            return p["person"], list(p.get("levels") or []), ""
        tok = (qs.get("t") or [""])[0] or ((form or {}).get("t") or "")
        if tok:
            rec, err = ACC.check_token(self.state_root, self.win.id, tok)
            if err or not rec:
                return "", [], err or "地址无效"
            self._token = tok
            return rec.get("person") or "", list(rec.get("levels") or []), ""
        return "", [], ""

    @staticmethod
    def _signed_parts(qs: dict) -> tuple[str, str, str]:
        """签名链接的三个参数：p=人 &e=到期 &s=签名（给 AI 用来发的那种）。"""
        return ((qs.get("p") or [""])[0], (qs.get("e") or [""])[0], (qs.get("s") or [""])[0])

    def _pick(self, did: str, levels: list[str], mode: str):
        """过篇级闸门 → 返回 (条目, 要下载的文件, 错误)。"""
        cat, err = KB.load_catalog(self.state_root, self.win.id)
        if err:
            return None, None, err
        e, tp, err = KB.resolve_doc(self.state_root, self.win.id, cat, did, self.win.root,
                                    self.win.check, levels)
        if err:
            return None, None, err
        dcfg = DL.download_cfg(self.cfg)
        want_original = (mode != "text") and bool(dcfg.get("original", True))
        f = (self.win.root / str((e or {}).get("path") or "")) if want_original else tp
        if not f.is_file():
            return None, None, "文件不在了（可能被移动或删除）"
        mb = float(dcfg.get("max_file_mb", 50))
        try:
            if f.stat().st_size > mb * 1024 * 1024:
                return None, None, f"文件超过单次下载上限 {mb:.0f}MB"
        except OSError:
            return None, None, "读不到文件"
        return e, f, ""

    async def _download(self, scope, send, qs: dict, form: dict, sub: str):
        # 管理令（管理页里的「看原件」）：维护者预览用，等级放开，审计记成「维护者(管理令)」
        adm = (qs.get("k") or [""])[0] or (form.get("k") or "")
        admin_preview = bool(adm) and adm == ensure_admin_token(self.state_root, create=False)
        sp, sexp, ssig = self._signed_parts(qs)
        signed = False
        if admin_preview:
            person, levels, err = "维护者(管理令)", list(self.levels), ""
        else:
            person, levels, err = self._who(scope, qs, form)
            if not person and sp:
                # 签名链接：先认人（等级按他的人查），**签名等知道是哪一篇之后再验**
                person, signed = sp, True
                rec = ACC.get_user(self.state_root, self.win.id, person) or {}
                levels = list(rec.get("levels") or [])
                if not DL.person_active(self.state_root, self.win.id, person):
                    person = ""
                    err = "这个人的地址已停用或到期"
        if not person:
            err = err or "需要你的地址或有效链接"
            self.audit("kb_download", {"path": sub}, False, {"reason": err, "denied": True})
            return await self._send(send, _page(
                "打不开", f'<div class="warn">{esc(err)}</div>'
                '<p class="hint">请用你自己的那条地址打开：<code>&lt;你的地址&gt;/files</code>。'
                '地址丢了就去申请页重新申请。</p>', self.base), 401)

        dcfg = DL.download_cfg(self.cfg)
        if not dcfg.get("enabled"):
            self.audit("kb_download", {"path": sub}, False, {"reason": "未开放下载", "person": person, "levels": levels, "admin": admin_preview})
            return await self._send(send, _page("未开放下载", '<div class="warn">本资料库只开放在线阅读，'
                                                              "不提供文件下载。</div>", self.base), 403)

        # ① 清单页（必须用他自己的地址打开：签名只绑单篇，不能拿来列清单）
        if sub == "/files":
            if signed:
                self.audit("kb_files", {}, False, {"reason": "签名链接不能打开清单页", "person": person, "levels": levels})
                return await self._send(send, _page(
                    "用你自己的地址打开",
                    '<div class="warn">下载链接只能取那一篇文件；要列出全部资料，请用你<b>自己的地址</b>打开'
                    " <code>&lt;你的地址&gt;/files</code>。</div>", self.base), 403)
            cat, _ = KB.load_catalog(self.state_root, self.win.id)
            docs = [KB.entry_public_view(e) for e, _ in
                    KB.approved_entries(self.state_root, self.win.id, cat or {}, self.win.root,
                                        self.win.check, levels)]
            tok = (qs.get("t") or [""])[0] or self._token
            self.audit("kb_files", {"count": len(docs)}, True, {"person": person, "levels": levels, "ip": self._ip, "admin": admin_preview})
            return await self._send(send, page_files(self.base, person, levels, docs, tok, dcfg))

        # ② 单篇下载
        if sub.startswith("/dl/"):
            did = sub[len("/dl/"):].strip("/")
            if signed and not DL.verify(self.state_root, self.win.id, did, person, sexp, ssig):
                self.audit("kb_download", {"doc_id": did}, False,
                           {"reason": "签名无效或已过期", "person": person, "levels": levels, "denied": True, "ip": self._ip})
                return await self._send(send, _page(
                    "链接失效", '<div class="warn">这条下载链接无效或已过期（默认 15 分钟）。'
                               '请让对方重新生成一条，或直接打开你自己的地址页下载。</div>', self.base), 403)
            mode = ((qs.get("mode") or ["original"])[0] or "original").lower()
            e, f, err = self._pick(did, levels, mode)
            if err:
                self.audit("kb_download", {"doc_id": did, "mode": mode}, False,
                           {"reason": err, "person": person, "levels": levels, "denied": True, "ip": self._ip})
                return await self._send(send, _page("下载被拒", f'<div class="warn">{esc(err)}</div>',
                                                    self.base), 403)
            try:
                data = f.read_bytes()
            except OSError:
                return await self._send(send, _page("读不到文件", '<div class="warn">读不到文件。</div>',
                                                    self.base), 500)
            name = DL.safe_name(str((e or {}).get("title") or ""), f)
            if f.suffix.lower() == ".md" and not name.lower().endswith((".md",)):
                name += ".md"
            self.audit("kb_download", {"doc_id": did, "mode": mode, "bytes": len(data)}, True,
                       {"person": person, "levels": levels, "title": (e or {}).get("title"), "ip": self._ip,
                                   "admin": admin_preview})
            await send({"type": "http.response.start", "status": 200, "headers": [
                (b"content-type", DL.media_type(f).encode()),
                (b"content-length", str(len(data)).encode()),
                (b"content-disposition", DL.disposition(name).encode()),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff")]})
            await send({"type": "http.response.body", "body": data})
            return

        # ③ 打包（同上：只认地址段）
        if signed:
            self.audit("kb_bundle", {}, False, {"reason": "签名链接不能打包", "person": person, "levels": levels})
            return await self._send(send, _page("用你自己的地址下载",
                                                '<div class="warn">打包下载请用你自己的地址页。</div>',
                                                self.base), 403)
        ids = list(dict.fromkeys((self._multi or {}).get("ids") or
                                 [x for x in (form.get("ids") or "").split(",") if x]))
        if not ids:
            return await self._send(send, _page("没勾选", '<div class="warn">没有勾选任何资料。</div>',
                                                self.base), 400)
        items, bad = [], []
        for did in ids[:50]:
            e, f, err = self._pick(did, levels, "text" if not dcfg.get("original", True) else "original")
            if err:
                bad.append(f"{did}: {err}")
                self.audit("kb_bundle", {"doc_id": did}, False,
                           {"reason": err, "person": person, "levels": levels, "denied": True, "ip": self._ip})
                continue
            items.append((DL.safe_name(str((e or {}).get("title") or ""), f), f))
        if not items:
            return await self._send(send, _page("都没通过", '<div class="warn">勾选的资料都没通过检查：'
                                                          f"{esc('; '.join(bad))}</div>", self.base), 403)
        blob, err = DL.zip_bytes(items, int(dcfg.get("max_bundle_mb", 200)))
        if err:
            self.audit("kb_bundle", {"ids": ids}, False, {"reason": err, "person": person, "levels": levels})
            return await self._send(send, _page("打包失败", f'<div class="warn">{esc(err)}</div>',
                                                self.base), 400)
        self.audit("kb_bundle", {"ids": ids, "bytes": len(blob), "skipped": len(bad)}, True,
                   {"person": person, "levels": levels, "ip": self._ip})
        name = f"资料打包-{time.strftime('%Y%m%d-%H%M')}.zip"
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", b"application/zip"),
            (b"content-length", str(len(blob)).encode()),
            (b"content-disposition", DL.disposition(name).encode()),
            (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": blob})

    # ---- 申请 ----
    async def _submit(self, send, form: dict, ip: str):
        if (form.get("website") or "").strip():          # 蜜罐：只有机器人会填
            return await self._send(send, _page("已忽略", '<div class="warn">提交未通过校验。</div>', self.base), 400)
        name = (form.get("name") or "").strip()
        purpose = (form.get("purpose") or "").strip()
        level = (form.get("level_requested") or "").strip()
        if not name or not purpose:
            return await self._send(send, page_request(self.base, self.cfg, self.public_levels,
                                                       '<div class="warn">姓名和用途都要填。</div>'), 400)
        if level not in self.public_levels:              # 不信任前端：等级必须在白名单里
            return await self._send(send, page_request(self.base, self.cfg, self.public_levels,
                                                       '<div class="warn">等级不在可申请范围内。</div>'), 400)
        if not _rate_ok(ip):
            self.audit("portal_submit", {"name": name}, False, {"reason": "限速", "ip": ip})
            return await self._send(send, _page("提交太频繁",
                                                f'<div class="warn">同一网络每小时最多 {RATE_MAX} 次申请，'
                                                f'请稍后再试。</div>', self.base), 429)
        rec = KB.append_request(self.state_root, self.win.id, name=name, dept=form.get("dept", ""),
                                purpose=purpose, level_requested=level,
                                contact=form.get("contact", ""), ip=ip)
        address = ""
        if level in self.auto_levels:                    # 自助档：申请即通过
            u = ACC.upsert_user(self.state_root, self.win.id, name, [level],
                                dept=form.get("dept", ""), note=f"自助申请 {rec['id']}", minutes=ACC.DEFAULT_MINUTES)
            KB.set_request_status(self.state_root, self.win.id, rec["id"], "approved",
                                  note=f"自动开通（{level}）", auto=True)
            address = self._address(u["token"])
            self.audit("kb_auto_grant", {"request": rec["id"], "name": name, "level": level}, True,
                       {"issuance": "auto", "ip": ip})
        self.audit("portal_submit", {"request": rec["id"], "name": name, "level": level},
                   True, {"auto": bool(address), "ip": ip})
        return await self._send(send, page_submitted(self.base, rec, address))

    # ---- 查进度 ----
    async def _status(self, send, qs: dict):
        rid = (qs.get("id") or [""])[0]
        code = (qs.get("code") or [""])[0]
        if not rid and not code:
            return await self._send(send, page_status(self.base, None))
        rec, err = KB.find_request(self.state_root, self.win.id, rid, code)
        address = ""
        if rec and rec.get("status") == "approved":
            u = ACC.get_user(self.state_root, self.win.id, rec.get("name") or "")
            if u:
                address = self._address(u["token"])
            else:
                err = "已通过，但地址已被维护者收回或更换，请联系维护者。"
        return await self._send(send, page_status(self.base, rec, err, address))

    # ---- 管理 ----
    # ---- 上传：文件 / 整个文件夹（webkitdirectory 会给相对路径）/ 拖拽 ----
    def _safe_rel(self, name: str, docs_dir: Path) -> tuple[Path | None, str]:
        """把上传时的文件名/相对路径洗成资料目录内的安全路径（挡 ../ 与绝对路径）。"""
        raw = (name or "").replace("\\", "/").strip().lstrip("/")
        parts = [p for p in raw.split("/") if p not in ("", ".", "..")]
        if not parts:
            return None, "文件名为空"
        rel = Path(*parts[-6:])                       # 最多保留 6 层，别让超长路径进来
        target = (docs_dir / rel).resolve()
        try:
            target.relative_to(docs_dir.resolve())
        except ValueError:
            return None, "路径越界"
        if target.name.startswith("."):
            return None, "隐藏文件不允许"
        return target, ""

    async def _upload(self, scope, receive, send, admin: str, ip: str):
        import config as C
        from starlette.datastructures import Headers
        from starlette.formparsers import MultiPartParser

        ucfg = (self.cfg.get("kb") or {}).get("upload") or {}
        if ucfg.get("enabled") is False:
            self.audit("kb_upload", {}, False, {"reason": "上传未开启", "actor": "admin", "ip": ip})
            return await self._send(send, _page("上传未开启", '<div class="warn">本窗口关闭了网页上传'
                                                '（kb.upload.enabled=false）。可先把文件放进资料目录再点扫描。</div>',
                                                self.base), 403)
        max_file = int(ucfg.get("max_file_mb", 100)) * 1024 * 1024
        max_total = int(ucfg.get("max_total_mb", 300)) * 1024 * 1024
        docs_rel = C.window_kb_docs_dir(self.cfg)
        docs_dir = (self.win.root / docs_rel).resolve()
        docs_dir.mkdir(parents=True, exist_ok=True)

        async def stream():
            more = True
            while more:
                msg = await receive()
                body = msg.get("body", b"")
                if body:
                    yield body
                more = msg.get("more_body", False)

        try:
            parser = MultiPartParser(Headers(raw=scope.get("headers", [])), stream(),
                                     max_files=500, max_fields=50, max_part_size=max_file)
            form = await parser.parse()
        except Exception as e:                                                   # noqa: BLE001
            self.audit("kb_upload", {}, False, {"reason": f"{e.__class__.__name__}: {e}",
                                                "actor": "admin", "ip": ip})
            return await self._send(send, _page("上传失败", f'<div class="warn">解析上传内容出错：{esc(e)}</div>',
                                                self.base), 400)
        try:
            newcat = (form.get("newcat") or "").strip()
            cat = newcat or (form.get("category") or "").strip()
            level = str(form.get("level") or "").strip() or self.levels[0]
            after = form.get("after") or "pending"
            if level not in self.levels:
                level = self.levels[0]
            files = [f for f in form.getlist("files") if getattr(f, "filename", "")]
            if not files:
                return await self._send(send, _page("没有文件", '<div class="warn">这次没有选中任何文件。</div>'
                                                    '<p><a href="' + esc(self.base) + "/admin?k=" + esc(admin)
                                                    + '">返回管理页</a></p>', self.base), 400)
            saved, lines, total = [], [], 0
            base_dir = (docs_dir / cat).resolve() if cat else docs_dir
            try:
                base_dir.relative_to(docs_dir)
            except ValueError:
                return await self._send(send, _page("分类名不合法", '<div class="warn">分类名里有非法路径。</div>',
                                                    self.base), 400)
            base_dir.mkdir(parents=True, exist_ok=True)
            for uf in files:
                target, why = self._safe_rel(uf.filename, base_dir if cat is None else base_dir)
                if target is None:
                    lines.append("⛔ " + esc(uf.filename) + "：" + esc(why))
                    continue
                size = 0
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)   # 文件夹上传要带上原有层级
                    with open(target, "wb") as fh:
                        while True:
                            chunk = await uf.read(1024 * 512)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_file or total + size > max_total:
                                raise ValueError(f"超过上限（单文件 {max_file // 1048576}MB / 本次共 "
                                                 f"{max_total // 1048576}MB）")
                            fh.write(chunk)
                except Exception as e:                                            # noqa: BLE001
                    target.unlink(missing_ok=True)
                    lines.append("⛔ " + esc(uf.filename) + "：" + esc(str(e)))
                    continue
                total += size
                rel = str(target.relative_to(self.win.root.resolve()))
                saved.append(rel)
                lines.append("✅ " + esc(rel) + "（" + str(round(size / 1024)) + " KB）")
                self.audit("kb_upload_file", {"rel": rel}, True,
                           {"actor": "admin", "ip": ip, "bytes": size, "category": cat,
                            "level": level, "after": after})
        finally:
            try:
                await form.close()
            except Exception:                                                    # noqa: BLE001
                pass

        res = ING.scan_library(self.state_root, self.win.id, self.win.root, docs_rel, "auto",
                               default_level=C.window_kb_default_level(self.cfg))
        self.audit("kb_scan", {"extract": "auto"}, True,
                   {"actor": "admin", "ip": ip, "by": "upload", "new": res["new"],
                    "changed": res["changed"], "same": res["same"],
                    "skipped": res["skipped"], "failed": res["failed"]})
        published, bad = 0, []
        if after == "publish":
            for rel in saved:
                did = KB.doc_id(rel)
                try:
                    KB.set_status(self.state_root, self.win.id, did, "approved", level=level, by="维护者")
                    self.audit("kb_approve", {"doc_id": did, "level": level}, True,
                               {"actor": "admin", "ip": ip, "via": "upload"})
                    published += 1
                except (ValueError, RuntimeError) as e:                            # noqa: PERF203
                    bad.append(str(e))
        msg = ('<div class="ok"><b>已收下 ' + str(len(saved)) + " 个文件</b>（"
               + str(round(total / 1048576, 2)) + " MB）"
               + ("，其中 <b>" + str(published) + "</b> 篇已按 " + esc(level) + " 公开（同事现在就能看/能下）"
                  if published else "，已进「待批」，你在下面逐篇定等级即可")
               + "</div>"
               + ('<ul class="hint">' + "".join("<li>" + x + "</li>" for x in lines[:40]) + "</ul>")
               + ("".join('<div class="warn">' + esc(x) + "</div>" for x in bad))
               + ('<p class="hint">抽取：新增 ' + str(res["new"]) + " · 退回待批 " + str(res["changed"])
                  + " · 未变 " + str(res["same"]) + " · 类型不支持 " + str(res["skipped"]) + "</p>"))
        return await self._send(send, page_admin(self.base, self.state_root, self.win.id, admin,
                                                 self.levels, self.host, msg, remote=self.remote,
                                                 root=self.win.root, docs_rel=docs_rel))

    # ---- 批量/单篇的权限调整（一个表单里同时支持勾选批量与单篇按钮）----
    async def _bulk(self, send, form: dict, admin: str, ip: str):
        import config as C
        dids = [d for d in (self._multi.get("did") or []) if d]
        action = str(form.get("bulk") or "")
        one = str(form.get("one") or "")
        if one and "@" in one:
            did, action = one.rsplit("@", 1)
            dids = [did]
        lv = {k[len("level_"):]: v for k, v in form.items() if k.startswith("level_")}
        target_level = (lv.get(dids[0]) if len(dids) == 1 and dids[0] in lv else form.get("bulk_level") or "")
        target_level = str(target_level or "").strip()
        if not dids:
            return '<div class="warn">没有选中任何资料。</div>'
        if action not in ("approve", "setlevel", "revoke", "forget", "reject"):
            return '<div class="warn">不认识的操作。</div>'
        if action in ("approve", "setlevel") and target_level not in self.levels:
            return '<div class="warn">等级不在本窗允许清单里。</div>'
        if action in ("approve", "setlevel") and target_level == "":
            return '<div class="warn">要先选一个等级。</div>'

        ok_n, errs = 0, []
        for did in dids:
            try:
                if action in ("approve", "setlevel"):
                    rec = KB.set_status(self.state_root, self.win.id, did, "approved",
                                        level=target_level, by="维护者")
                    if not rec:
                        errs.append(did + "：台账里没有这一篇")
                        continue
                    self.audit("kb_approve" if action == "approve" else "kb_setlevel",
                               {"doc_id": did, "level": target_level}, True,
                               {"actor": "admin", "ip": ip, "title": rec.get("title"),
                                "batch": len(dids) > 1})
                elif action in ("revoke", "reject"):
                    rec = KB.set_status(self.state_root, self.win.id, did,
                                        "pending" if action == "revoke" else "rejected", by="维护者")
                    self.audit("kb_revoke" if action == "revoke" else "kb_reject",
                               {"doc_id": did}, True,
                               {"actor": "admin", "ip": ip, "title": (rec or {}).get("title"),
                                "batch": len(dids) > 1})
                else:                                                            # forget
                    cat, err = KB.load_catalog(self.state_root, self.win.id)
                    if err or did not in (cat.get("docs") or {}):
                        errs.append(did + "：台账里没有这一篇")
                        continue
                    cat["docs"].pop(did, None)
                    KB.save_catalog(self.state_root, self.win.id, cat)
                    self.audit("kb_forget", {"doc_id": did}, True, {"actor": "admin", "ip": ip})
                ok_n += 1
            except (ValueError, RuntimeError) as e:                                # noqa: PERF203
                errs.append(did + "：" + str(e))
        verb = {"approve": "公开", "setlevel": "改等级", "revoke": "下架", "reject": "设为不公开",
                "forget": "从台账删掉"}[action]
        extra = ("，定为 " + esc(target_level)) if action in ("approve", "setlevel") else ""
        msg = ('<div class="ok">✅ 已完成：' + verb + " " + str(ok_n) + " 篇" + extra + "</div>"
               + "".join('<div class="warn">' + esc(x) + "</div>" for x in errs[:10]))
        return msg

    async def _admin(self, scope, send, hdrs, qs, form, sub, ip, method):
        tok_hint = (qs.get("k") or [""])[0] or (form.get("k") or "") or hdrs.get("x-admin-token", "")
        ok, why = self._admin_gate(scope, hdrs, {"k": [tok_hint]})
        if not ok:
            self.audit("portal_admin", {"path": sub}, False, {"reason": why, "ip": ip})
            code = 403 if ("公网" in why or "部署机" in why) else 401
            return await self._send(send, _page("管理页无法访问",
                                                f'<div class="warn">{esc(why)}</div>', self.base), code)
        admin = ensure_admin_token(self.state_root)

        if sub == "/admin/usage" and method == "GET":
            by = (qs.get("by") or ["person"])[0]
            days = int((qs.get("days") or ["30"])[0] or 30)
            if by not in ("person", "day", "doc", "tool"):
                by = "person"
            return await self._send(send, page_usage(self.base, self.state_root, self.win.id, admin, by, days))
        if sub == "/admin" and method == "GET":
            return await self._send(send, page_admin(
                self.base, self.state_root, self.win.id, admin, self.levels, self.host,
                remote=self.remote, root=self.win.root, q=qs,
                docs_rel=(self.cfg.get("kb") or {}).get("docs_dir") or "原始文档"))
        if method != "POST":
            return await self._send(send, _page("没有这个页面", '<p class="lead">没有这个页面。</p>', self.base), 404)

        msg = ""
        if sub == "/admin/decide":
            rid = form.get("rid", "")
            act = form.get("action", "")
            req = next((r for r in KB.load_requests(self.state_root, self.win.id)[0].get("requests", {}).values()
                        if r.get("id") == rid), None)
            if not req:
                msg = '<div class="warn">找不到这条申请。</div>'
            elif act == "approve":
                level = form.get("level", "")
                if level not in self.levels:
                    msg = '<div class="warn">等级不在本窗允许清单里。</div>'
                else:
                    days = int(form.get("for_days", "30") or 0)
                    minutes = days * 1440 if days else None
                    u = ACC.upsert_user(self.state_root, self.win.id, req.get("name") or "", [level],
                                        dept=req.get("dept", ""), note=f"来自申请 {rid}", minutes=minutes)
                    KB.set_request_status(self.state_root, self.win.id, rid, "approved",
                                          note=f"批准（{level}）")
                    self.audit("kb_decision", {"request": rid, "action": "approve", "level": level}, True,
                               {"person": u["person"], "levels": [level], "actor": "admin", "ip": ip})
                    msg = (f'<div class="ok">✅ 已批准 <b>{esc(u["person"])}</b>（{esc(level)}，'
                           f'{esc(ACC.describe_expiry(u))}）<br>地址：<code>{esc(self._address(u["token"]))}</code>'
                           f'<br><span class="hint">这条地址可以直接发给他；他也能在查进度页自己看到。</span></div>')
            else:
                KB.set_request_status(self.state_root, self.win.id, rid, "denied",
                                      note=form.get("reason", "") or "未说明")
                self.audit("kb_decision", {"request": rid, "action": "deny"}, True, {"actor": "admin", "ip": ip})
                msg = '<div class="warn">已驳回。</div>'
        elif sub == "/admin/grant":
            person = (form.get("person") or "").strip()
            level = form.get("level", "")
            if not person or level not in self.levels:
                msg = '<div class="warn">姓名和等级都要填对。</div>'
            else:
                days = int(form.get("for_days", "30") or 0)
                minutes = days * 1440 if days else None
                u = ACC.upsert_user(self.state_root, self.win.id, person, [level],
                                    dept=form.get("dept", ""), note=form.get("note", ""), minutes=minutes)
                self.audit("kb_grant", {"person": person, "level": level}, True,
                           {"person": person, "levels": [level], "actor": "admin", "ip": ip, "expires": u.get("expires")})
                msg = (f'<div class="ok">✅ {esc(person)} 的地址已发放（{esc(level)}，{esc(ACC.describe_expiry(u))}）'
                       f'<br><code>{esc(self._address(u["token"]))}</code></div>')
        elif sub == "/admin/revoke":
            person = form.get("person", "")
            enabled = form.get("enabled", "0") == "1"
            if ACC.set_enabled(self.state_root, self.win.id, person, enabled):
                self.audit("kb_revoke", {"person": person, "enabled": enabled}, True, {"actor": "admin", "ip": ip})
                msg = f'<div class="ok">已{"恢复" if enabled else "停用"} {esc(person)} 的地址。</div>'
            else:
                msg = '<div class="warn">找不到这个人。</div>'
        elif sub == "/admin/rotate":
            person = form.get("person", "")
            u = ACC.rotate(self.state_root, self.win.id, person)
            if u:
                self.audit("kb_rotate", {"person": person}, True, {"actor": "admin", "ip": ip})
                msg = (f'<div class="ok">✅ 已给 {esc(person)} 换新地址（旧地址立刻失效）'
                       f'<br><code>{esc(self._address(u["token"]))}</code></div>')
            else:
                msg = '<div class="warn">找不到这个人。</div>'
        if sub == "/admin/scan":
            docs_rel = (self.cfg.get("kb") or {}).get("docs_dir") or "原始文档"
            import config as C
            res = ING.scan_library(self.state_root, self.win.id, self.win.root, docs_rel, "auto",
                                   default_level=C.window_kb_default_level(self.cfg))
            self.audit("kb_scan", {"extract": "auto"}, True,
                       {"actor": "admin", "ip": ip, "new": res["new"], "changed": res["changed"],
                        "same": res["same"], "skipped": res["skipped"], "failed": res["failed"]})
            rows = "".join("<li>" + esc(m) + " " + esc(rel) + " — " + esc(note) + "</li>"
                           for m, rel, note in res["rows"][:30])
            msg = ('<div class="ok">扫描完成：新增待批 ' + str(res["new"]) + " · 内容变化退回待批 "
                   + str(res["changed"]) + " · 未变 " + str(res["same"]) + " · 跳过 "
                   + str(res["skipped"]) + " · 失败 " + str(res["failed"]) + "</div>"
                   + ('<ul class="hint">' + rows + "</ul>" if rows else ""))

        elif sub == "/admin/bulk":
            msg = await self._bulk(send, form, admin, ip)

        elif sub == "/admin/doc":
            did = form.get("did", "")
            act = form.get("action", "")
            level = form.get("level", "")

            def _one(d: str, lvl: str) -> str:
                """公开一篇（返回提示文本；失败抛 ValueError）。"""
                rec = KB.set_status(self.state_root, self.win.id, d, "approved", level=lvl, by="维护者")
                if not rec:
                    raise ValueError("台账里没有这一篇")
                self.audit("kb_approve", {"doc_id": d, "level": lvl}, True,
                           {"actor": "admin", "ip": ip, "title": rec.get("title"),
                            "category": rec.get("category")})
                return esc(rec.get("title") or d)

            try:
                if act == "approve":
                    if level not in self.levels:
                        msg = '<div class="warn">等级不在本窗允许清单里。</div>'
                    else:
                        msg = '<div class="ok">✅ 已公开：' + _one(did, level) + "（" + esc(level) + "）</div>"
                elif act == "setlevel":
                    if level not in self.levels:
                        msg = '<div class="warn">等级不在本窗允许清单里。</div>'
                    else:
                        title = _one(did, level)
                        self.audit("kb_setlevel", {"doc_id": did, "level": level}, True,
                                   {"actor": "admin", "ip": ip})
                        msg = ('<div class="ok">✅ ' + title + " 的等级已改成 " + esc(level)
                               + "（有这条等级地址的同事立即生效，无需重启）</div>")
                elif act in ("reject", "revoke"):
                    rec = KB.set_status(self.state_root, self.win.id, did,
                                        "rejected" if act == "reject" else "pending",
                                        by="维护者", note=("维护者下架" if act == "revoke" else ""))
                    self.audit("kb_revoke" if act == "revoke" else "kb_reject", {"doc_id": did}, True,
                               {"actor": "admin", "ip": ip, "title": (rec or {}).get("title")})
                    msg = ('<div class="ok">' + ("已下架（回到待批，同事立刻看不到也下不到）："
                                                 if act == "revoke" else "已设为不公开：")
                           + esc((rec or {}).get("title") or did) + "</div>")
                elif act == "forget":
                    cat, err = KB.load_catalog(self.state_root, self.win.id)
                    if err or did not in (cat.get("docs") or {}):
                        msg = '<div class="warn">台账里没有这一篇。</div>'
                    else:
                        cat["docs"].pop(did, None)
                        KB.save_catalog(self.state_root, self.win.id, cat)
                        self.audit("kb_forget", {"doc_id": did}, True, {"actor": "admin", "ip": ip})
                        msg = '<div class="ok">已从台账删掉（文件本身没动；下次扫描会重新进来）。</div>'
                elif act == "approve_all":
                    if level not in self.levels:
                        msg = '<div class="warn">等级不在本窗允许清单里。</div>'
                    else:
                        cat, _err = KB.load_catalog(self.state_root, self.win.id)
                        pend = [d for d, e in (cat.get("docs") or {}).items() if e.get("status") == "pending"]
                        done, bad = 0, []
                        for d in pend:
                            try:
                                _one(d, level)
                                done += 1
                            except (ValueError, RuntimeError) as e:                  # noqa: PERF203
                                bad.append(d + "：" + str(e))
                        msg = ('<div class="ok">✅ 已公开 ' + str(done) + " 篇（" + esc(level) + "）</div>"
                               + ("".join('<div class="warn">' + esc(x) + "</div>" for x in bad) if bad else ""))
                else:
                    msg = '<div class="warn">不认识的动作用。</div>'
            except (ValueError, RuntimeError) as e:
                msg = '<div class="warn">⛔ 不能这么做：' + esc(str(e)) + "</div>"

        return await self._send(send, page_admin(self.base, self.state_root, self.win.id, admin,
                                                 self.levels, self.host, msg, remote=self.remote,
                                                 root=self.win.root, q=qs,
                                                 docs_rel=(self.cfg.get("kb") or {}).get("docs_dir") or "原始文档"))



def _levels_from(cfg: dict) -> list[str]:
    raw = (cfg.get("kb") or {}).get("levels")
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _public_levels_from(cfg: dict) -> list[str]:
    lv = _levels_from(cfg)
    raw = ((cfg.get("kb") or {}).get("portal") or {}).get("public_levels")
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x) in lv]
    return lv[:1]


def _auto_levels_from(cfg: dict) -> list[str]:
    pub = _public_levels_from(cfg)
    raw = ((cfg.get("kb") or {}).get("portal") or {}).get("auto_approve_levels")
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw if str(x) in pub]


def _admin_remote_from(cfg: dict) -> bool:
    return ((cfg.get("kb") or {}).get("portal") or {}).get("admin_remote") is True


def mount(app, win, state_root, audit):
    """ASGI 包装：路径命中网页路由就自己处理，否则交给 MCP。"""
    cfg = dict(win.cfg)
    host = ""
    try:
        import config as C
        host = C.load().get("hostname") or ""
    except Exception:                                                        # noqa: BLE001
        host = ""
    return _Portal(app, win.path, win, state_root, audit, cfg, host or "localhost")
