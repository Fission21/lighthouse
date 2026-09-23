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


def _rate_ok(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW]
    if len(hits) >= RATE_MAX:
        _RATE[ip] = hits
        return False
    hits.append(now)
    _RATE[ip] = hits
    return True


def _page(title: str, body: str, base: str, *, admin: str = "") -> bytes:
    """统一外壳：窄栏、手机可用、浅色。"""
    nav = ""
    if admin:
        nav = (f'<nav><a href="{esc(base)}/request">申请页</a> · '
               f'<a href="{esc(base)}/admin?k={esc(admin)}">管理页</a> · '
               f'<a href="{esc(base)}/admin/usage?k={esc(admin)}">用量</a></nav>')
    return f"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>
  :root {{ color-scheme: light; }}
  body {{ font: 15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         margin: 0 auto; padding: 24px 18px 60px; max-width: 760px; color: #1c1c1e; background: #fafafa; }}
  h1 {{ font-size: 21px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 26px 0 8px; }}
  p.lead {{ color: #6b6b70; margin: 0 0 18px; }}
  nav {{ color: #6b6b70; margin-bottom: 14px; font-size: 14px; }}
  nav a {{ color: #0a6cff; text-decoration: none; }}
  form {{ background: #fff; border: 1px solid #e5e5ea; border-radius: 12px; padding: 18px; }}
  label {{ display: block; margin: 12px 0 4px; font-size: 14px; color: #3a3a3c; }}
  input, select, textarea {{ width: 100%; box-sizing: border-box; font: inherit;
         padding: 9px 10px; border: 1px solid #d1d1d6; border-radius: 8px; background: #fff; }}
  textarea {{ min-height: 76px; }}
  button {{ font: inherit; margin-top: 16px; padding: 10px 18px; border: 0; border-radius: 8px;
         background: #0a6cff; color: #fff; font-weight: 600; }}
  button.ghost {{ background: #efeff4; color: #1c1c1e; font-weight: 500; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e5e5ea;
         border-radius: 12px; overflow: hidden; font-size: 14px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #f0f0f3; }}
  th {{ background: #f7f7f9; font-weight: 600; }}
  code {{ background: #f2f2f7; padding: 1px 6px; border-radius: 5px; word-break: break-all; }}
  .ok {{ background: #e8f8ee; border: 1px solid #b7e4c7; border-radius: 12px; padding: 16px; }}
  .warn {{ background: #fff6e5; border: 1px solid #ffd8a8; border-radius: 12px; padding: 16px; }}
  .hint {{ color: #6b6b70; font-size: 13px; }}
  .big {{ font-size: 18px; font-weight: 700; letter-spacing: .5px; }}
</style></head><body>{nav}<h1>{esc(title)}</h1>{body}</body></html>""".encode("utf-8")


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
<p class="hint">其他客户端（WorkBuddy / Cherry Studio / Claude 等）：把同一条地址填进它的 MCP 配置即可。<br>
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


def page_admin(base: str, state_root: Path, wid: str, admin: str, levels: list[str],
               host: str, msg: str = "", remote: bool = False) -> bytes:
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
    body = f"""{msg}
<p class="lead">管理页 · {esc(_now())}　{'（可从公网访问：请勿把本页地址转发给别人）' if remote else '（仅部署机本机可访问）'}</p>

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
    return _page("资料库管理页", body, base, admin=admin)


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
        method = scope.get("method", "GET")

        # ⚠️ MCP 客户端 POST 的正是「窗口路径本身」。除了下面这几个网页路由，
        #    其余一切（含窗口路径本体）原样交给 MCP —— 绝不去读它的请求体。
        portal_routes = {"/request", "/request/status", "/admin", "/admin/usage",
                         "/admin/decide", "/admin/grant", "/admin/revoke", "/admin/rotate", "/healthz"}
        if sub not in portal_routes:
            accept = hdrs.get("accept", "")
            if sub in ("", "/") and method == "GET" and "text/html" in accept and "text/event-stream" not in accept:
                return await self._send(send, self._redirect(f"{self.base}/request"))
            return await self.app(scope, receive, send)

        form: dict = {}
        if method == "POST":
            body = b""
            more = True
            while more:
                msg = await receive()
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
            form = {k: v[0] for k, v in parse_qs(body.decode(errors="replace")).items()}

        try:
            if sub == "/request":
                if method == "GET":
                    return await self._send(send, page_request(self.base, self.cfg, self.public_levels))
                return await self._submit(send, form, ip)
            if sub == "/request/status":
                return await self._status(send, qs)
            if sub == "/healthz":
                return await self._send(send, b'{"ok":true}', ctype="application/json")
            return await self._admin(scope, send, hdrs, qs, form, sub, ip, method)
        except Exception as e:                                              # noqa: BLE001
            self.audit("portal_error", {"path": path}, False, {"reason": f"{e.__class__.__name__}: {e}"})
            return await self._send(send, _page("出错了", f'<div class="warn">{esc(e)}</div>', self.base), 500)

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
            return await self._send(send, page_admin(self.base, self.state_root, self.win.id, admin,
                                                     self.levels, self.host, remote=self.remote))
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
                               {"person": u["person"], "actor": "admin", "ip": ip})
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
                           {"actor": "admin", "ip": ip, "expires": u.get("expires")})
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
        return await self._send(send, page_admin(self.base, self.state_root, self.win.id, admin,
                                                 self.levels, self.host, msg, remote=self.remote))


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
