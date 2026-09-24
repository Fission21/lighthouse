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
from urllib.parse import parse_qs, quote, urlencode

import kb as KB
import kb_access as ACC
import kb_download as DL
import kb_auth as AUTH
import kb_invite as INV
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


def _page(title: str, body: str, base: str, *, admin: str = "", wide: bool = False,
          who: str = "") -> bytes:
    """统一外壳：手机可用、卡片式、表格可横向滚动。

    wide=True 给管理页用（表格宽），其它页面保持窄栏读书式排版。
    """
    nav = ""
    if admin:
        nav = (f'<nav><a href="{esc(base)}/request">申请页</a>'
               f'<a href="{esc(base)}/admin?k={esc(admin)}">管理页</a>'
               f'<a href="{esc(base)}/admin/usage?k={esc(admin)}">用量</a>'
               + (f'<a href="{esc(base)}/files">我的资料</a>' if who else "")
               + (f'<span class="hint" style="margin-left:auto">{esc(who)}</span>'
                  f'<a href="{esc(base)}/logout">退出</a>' if who else "") + '</nav>')
    elif who:
        nav = (f'<nav><a href="{esc(base)}/files">资料</a>'
               f'<a href="{esc(base)}/request">申请</a>'
               f'<span class="hint" style="margin-left:auto">{esc(who)}</span>'
               f'<a href="{esc(base)}/logout">退出</a></nav>')
    width = "1180px" if wide else "760px"
    return f"""<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>
  :root {{ color-scheme: light;
    --ink: #14161a; --dim: #6b7280; --line: #e6e8ec; --card: #fff; --bg: #f6f7f9;
    --blue: #0a6cff; --blue-d: #0857cc; --green: #127a45; --green-b: #e6f6ec;
    --amber: #8a5a00; --amber-b: #fff6e5; --red: #a02318; --red-b: #fdecea;
    --violet: #5b21b6; --violet-b: #f1eafe; --accent: #0a6cff; }}
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
  .dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px;
        vertical-align: middle; background: #c2c7d0; }}
  .dot.on {{ background: #127a45; }}
  .dot.off {{ background: #a02318; }}
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
  .sr {{ position: absolute; width: 1px; height: 1px; opacity: 0; overflow: hidden; z-index: -1; }}
  .grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; }}
  .bar {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; background: #f8f9fb;
        border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; margin: 8px 0 0; }}
  .lvpick {{ display: flex; flex-wrap: wrap; gap: 9px; margin-top: 6px; }}
  .lvpick label {{ display: inline-flex; align-items: center; gap: 9px; padding: 8px 13px; cursor: pointer;
        font-weight: 400; border: 1px solid var(--line); border-radius: 999px; line-height: 1; background: #fff; }}
  .lvpick label:has(input:checked) {{ border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, #fff); }}
  .lvpick input {{ width: 17px; height: 17px; margin: 0; flex: none; }}
  .rowform {{ border-top: 1px dashed var(--line); padding-top: 10px; }}
  .rowform .grid2 {{ grid-template-columns: repeat(2, minmax(150px, 1fr)); }}
  a.minor {{ color: var(--accent); text-decoration: underline; text-underline-offset: 2px; }}
  .perm {{ min-width: 380px; }}
  form .bar {{ margin-top: 12px; }}
  td .bar .hint {{ margin-left: 2px; }}
  /* 小字提示改成“鼠标移上去才出现”的气泡 */
  .q {{ display: inline-flex; align-items: center; justify-content: center; width: 15px; height: 15px;
        margin-left: 5px; border-radius: 50%; border: 1px solid #c9cfd8; color: var(--dim); font-size: 11px;
        font-weight: 600; cursor: help; vertical-align: middle; background: #fff; flex: none; }}
  .q:hover {{ border-color: var(--blue); color: var(--blue); }}
  /* 同事折叠卡：默认只看一行，点「编辑」才展开 */
  details.ucard {{ background: #fff; border: 1px solid var(--line); border-radius: 14px;
        margin: 0 0 10px; padding: 0 14px; }}
  details.ucard[open] {{ border-color: #cfdcf7; }}
  details.ucard > summary {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
        list-style: none; cursor: pointer; padding: 12px 0; }}
  details.ucard > summary::-webkit-details-marker {{ display: none; }}
  details.ucard > summary .name {{ font-weight: 600; }}
  details.ucard > summary .caret {{ margin-left: auto; color: var(--blue); font-size: 13px; font-weight: 600; }}
  details.ucard[open] > summary .caret::before {{ content: "收起 ▴"; }}
  details.ucard:not([open]) > summary .caret::before {{ content: "编辑 ▾"; }}
  details.ucard .ubody {{ border-top: 1px dashed var(--line); padding: 2px 0 14px; }}
  details.ucard .ubody form.box {{ border: 1px solid var(--line); border-radius: 12px; padding: 12px;
        margin: 0; background: #fff; }}
  details.ucard .two {{ display: grid; grid-template-columns: minmax(300px, 1.6fr) minmax(230px, 1fr);
        gap: 14px; align-items: start; }}
  details.ucard .box {{ border: 1px solid var(--line); border-radius: 12px; padding: 12px; }}
  /* 分页 */
  .pager {{ display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 10px 0 0;
        font-size: 13px; color: var(--dim); }}
  .pager a, .pager span.cur {{ padding: 4px 10px; border: 1px solid var(--line); border-radius: 8px;
        background: #fff; color: var(--ink); text-decoration: none; }}
  .pager span.cur {{ background: var(--blue); color: #fff; border-color: var(--blue); font-weight: 600; }}
  .pager a.off {{ opacity: .4; pointer-events: none; }}
  @media (max-width: 720px) {{ details.ucard .two {{ grid-template-columns: 1fr; }} }}
  @media (max-width: 640px) {{ th, td {{ padding: 8px; }} th {{ position: static; }} .perm {{ min-width: 280px; }} }}
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
def page_login(base: str, nxt: str, msg: str = "", user: str = "") -> bytes:
    body = f"""{msg}
<p class="lead">这是一个内部资料库。请用**管理员给你的账号**登录；AI 助手那条线不受影响，
继续用你那条专属地址就行。</p>
<form method="post" action="{esc(base)}/login">
  <input type="hidden" name="next" value="{esc(nxt)}">
  <label>用户名</label><input name="user" maxlength="40" value="{esc(user)}" autofocus required>
  <label>密码</label><input type="password" name="pw" required>
  <label style="display:flex;align-items:center;gap:8px;margin:10px 0;font-weight:400">
    <input type="checkbox" name="remember" value="1" style="width:16px;height:16px"> 记住我（30 天）</label>
  <button type="submit">登录</button>
  <span class="hint" style="margin-left:8px">连错 5 次会锁 10 分钟</span>
</form>
<p class="hint">有邀请码？<a class="minor" href="{esc(base)}/register">在这里注册</a>（注册后会同时拿到地址和账号）。</p>
<p class="hint">密码忘了？让维护者在这台机器上执行
<code>bash lighthouse.sh kb passwd &lt;窗口&gt; --user 你的用户名</code> 重置（会生成新的临时密码）。</p>"""
    return _page("登录", body, base)


def page_register(base: str, levels: list[str], msg: str = "", code: str = "",
                  bound: str = "") -> bytes:
    """凭邀请码自助注册：填码 + 姓名/部门 + 自设用户名密码 → 当场发地址 + 能登录。"""
    if bound:
        who = (f'<div class="ok">这张邀请码是**给 {esc(bound)}** 的：请用本人姓名注册，'
               '维护者那边能对上号。</div>')
    else:
        who = ""
    body = f"""{msg}{who}
<p class="lead">这是一个内部资料库。注册需要**维护者发给你的邀请码**（一码一人、有有效期）。
填完你会同时拿到两样东西：**一条专属地址**（填进你的 AI 助手）和**一个网页账号**（看/下载资料用）。</p>
<form method="post" action="{esc(base)}/register">
  <label>邀请码</label><input name="code" value="{esc(code)}" placeholder="例如 A7K2M-9PQRS" required autofocus>
  <div class="grid2" style="margin-top:10px">
    <div><label>你的姓名</label><input name="name" maxlength="40" value="{esc(bound)}" required></div>
    <div><label>部门</label><input name="dept" maxlength="40"></div>
  </div>
  <label>用途（给维护者看的）</label><input name="purpose" maxlength="80"
    placeholder="例如：XX 项目写方案，要参考以前的报价" required>
  <div class="grid2" style="margin-top:10px">
    <div><label>网页用户名（登录用）</label><input name="user" maxlength="40"
      placeholder="字母数字，如 zhangsan" required></div>
    <div><label>密码（至少 8 位）</label><input type="password" name="pw" required></div>
  </div>
  <label style="margin-top:10px">再输一次密码</label><input type="password" name="pw2" required>
  <button type="submit" style="margin-top:12px">注册并领取地址</button>
  <span class="hint" style="margin-left:8px">已有账号？<a href="{esc(base)}/login">去登录</a></span>
</form>
<p class="hint">没收到邀请码就别试了 —— 码不对、过期、用过都不行。找维护者要一张。</p>"""
    return _page("凭邀请码注册", body, base)


def page_request(base: str, cfg_kb: dict, levels: list[str], msg: str = "", who: str = "") -> bytes:
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
    return _page("申请访问招投标资料库", body, base, who=who)


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


def _q(text: str) -> str:
    """小字提示的替身：一个「?」，鼠标移上去才显示说明。"""
    return f'<span class="q" title="{esc(text)}">?</span>'


def _pager(base: str, admin: str, key: str, page: int, pages: int, total: int, unit: str = "条",
           extra: dict | None = None) -> str:
    """分页条：上一页 / 页码 / 下一页 + 共多少。extra 用来保留筛选条件。"""
    if pages <= 1:
        return f'<div class="pager">共 {total} {unit}</div>'

    def link(p: int) -> str:
        qs = {"k": admin, key: str(p)}
        for k2, v2 in (extra or {}).items():
            if v2:
                qs[k2] = v2
        return esc(base + "/admin?" + urlencode(qs))

    out = [f'<div class="pager"><span>共 {total} {unit} · 第 {page}/{pages} 页</span>',
           f'<a class="{"off" if page <= 1 else ""}" href="{link(page - 1)}">上一页</a>']
    for p2 in range(1, pages + 1):
        if pages > 9 and abs(p2 - page) > 2 and p2 not in (1, pages):
            if p2 in (2, pages - 1):
                out.append("<span>…</span>")
            continue
        out.append(f'<span class="cur">{p2}</span>' if p2 == page
                   else f'<a href="{link(p2)}">{p2}</a>')
    out.append(f'<a class="{"off" if page >= pages else ""}" href="{link(page + 1)}">下一页</a></div>')
    return "".join(out)


def _invites_section(base: str, state_root: Path, wid: str, admin: str, levels: list[str]) -> str:
    """邀请码：生成 / 看状态 / 看谁用了 / 停用删除。注册的入口靠它把关。"""
    rows = INV.list_codes(state_root, wid)
    s = INV.summary(state_root, wid)
    boxes = "".join(f'<label><input type="checkbox" name="levels" value="{esc(l)}"><span>{esc(l)}</span></label>'
                    for l in levels)
    out = [f'''<h2>邀请码（同事凭它自助注册）</h2>
<p class="hint">一码一人、可设有效期；同事拿注册链接自助领取地址和账号{_q("同事打开注册链接 → 填邀请码 + 姓名/部门/用途 + 自设用户名密码 → 当场拿到一条专属地址（填进他的 AI）和一个网页账号。谁发的码、发给了谁、谁在什么时间什么 IP 用的，都记在这张表里。")}</p>
<div class="bar" style="background:transparent;border:0;padding:0;margin:6px 0">
  <span class="chip c-approved">可用 {s["available"]}</span>
  <span class="chip c-pending">已用完 {s["used"]}</span>
  <span class="chip c-unsupported">已过期 {s["expired"]}</span>
  <span class="chip c-rejected">已停用 {s["disabled"]}</span>
</div>
<form method="post" action="{esc(base)}/admin/invite">
  <input type="hidden" name="k" value="{esc(admin)}">
  <label style="margin-top:6px">这张码给什么等级（可多选）</label>
  <div class="lvpick">{boxes}</div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
       gap:10px;margin-top:10px">
    <div><label>码的有效期</label><select name="days">
      <option value="7">7 天</option><option value="30" selected>30 天</option>
      <option value="90">90 天</option><option value="0">不过期</option></select></div>
    <div><label>指定给谁（可选）</label><input name="person" maxlength="40"
      placeholder="只认本人姓名"></div>
    <div><label>可用次数</label><input name="max_uses" type="number" value="1" min="1" max="20"></div>
    <div><label>备注（给谁 / 什么项目）</label><input name="note" maxlength="60"
      placeholder="如：XX 项目-采购部"></div>
  </div>
  <div class="bar" style="background:transparent;border:0;padding:0;margin:12px 0 0">
    <button type="submit" name="action" value="create">生成邀请码</button>
    <span class="hint">生成后把「注册链接」发给他，他填码就能自助拿到地址和账号</span>
  </div>
</form>''']
    if not rows:
        out.append('<p class="hint">还没有生成过邀请码。</p>')
        return "".join(out)
    trs = []
    for r in rows:
        st = INV.status(r)
        cls = {"可用": "c-approved", "已用完": "c-pending", "已过期": "c-unsupported"}.get(st, "c-rejected")
        uses = r.get("uses") or []
        used = "<br>".join(f'{esc(u.get("person"))}　{esc(INV.fmt(u.get("at")))}'
                           f'　{esc(u.get("ip"))}' for u in uses) or "—"
        link = f"{base}/register?c={r['code']}"
        trs.append(f'''<tr>
<td><code style="font-size:13px">{esc(r["code"])}</code>
  <div class="bar" style="background:transparent;border:0;padding:0;margin:4px 0 0">
    <button type="button" class="tiny ghost" onclick="navigator.clipboard.writeText('{esc(link)}').then(()=>{{this.textContent='已复制'}},()=>{{}})">复制注册链接</button>
  </div>
  <span class="hint">可用 {len(uses)}/{int(r.get("max_uses") or 1)} 次</span></td>
<td>{esc("、".join(r.get("levels") or []))}<br><span class="hint">{esc(r.get("note") or "")}</span></td>
<td>{esc(r.get("person") or "（不限）")}<br><span class="hint">生成于 {esc(INV.fmt(r.get("created_at")))}</span></td>
<td><span class="chip {cls}">{esc(st)}</span><br>
  <span class="hint">{esc(("到期 " + INV.fmt(r["expires"])) if r.get("expires") else "不过期")}</span></td>
<td><span class="hint">{used}</span></td>
<td><form method="post" action="{esc(base)}/admin/invite">
  <input type="hidden" name="k" value="{esc(admin)}">
  <input type="hidden" name="code" value="{esc(r["code"])}">
  <button class="tiny" name="action" value="{"revoke" if r.get("enabled", True) else "enable"}">
    {"停用" if r.get("enabled", True) else "恢复"}</button>
  <button class="tiny danger" name="action" value="delete"
    onclick="return confirm(`删掉这张邀请码？已经没人能用它注册了。`)">删除</button>
</form></td></tr>''')
    out.append('<div class="wrap"><table><tr><th>邀请码</th><th>等级 / 备注</th><th>给谁 / 生成时间</th>'
               '<th>状态</th><th>谁用了</th><th>操作</th></tr>' + "".join(trs) + "</table></div>")
    return "".join(out)


def _users_table(state_root: Path, wid: str, admin: str, base: str, levels: list[str],
                 page: int = 1, per: int = 8) -> str:
    """已授权的同事：**默认只显示一行摘要，点「编辑」才展开**表单（等级/有效期/部门/停用/换址/删除/账号）。"""
    users = ACC.list_users(state_root, wid)
    if not users:
        return '<p class="hint">还没有给任何人发过地址。下面可以直接发一条，或让同事去申请页自己申请。</p>'
    total = len(users)
    pages = max(1, (total + per - 1) // per)
    cur = min(max(1, int(page or 1)), pages)
    cards = []
    for u in users[(cur - 1) * per: cur * per]:
        person = u.get("person") or ""
        token = str(u.get("token") or "")
        address = (base.split("/w-")[0] if "/w-" in base else "") + f"/kb-{token}"
        my_levels = list(u.get("levels") or [])
        lv_boxes = "".join(
            f'<label><input type="checkbox" name="levels" value="{esc(l)}"'
            f'{" checked" if l in my_levels else ""}><span>{esc(l)}</span></label>' for l in levels)
        acct = AUTH.get(state_root, person) or {}
        acct_btn = "重置密码" if acct else "开通账号"
        acct_drop = ('<button class="tiny ghost" name="action" value="drop" '
                     'onclick="return confirm(`删掉这个账号？他的地址与资料权限不受影响。`);">删账号</button>'
                     if acct else "")
        acct_note = ("已开通" + (f"（最后登录 {str(acct.get('last_login'))[:16].replace('T', ' ')}）"
                                 if acct.get("last_login") else "（还没登录过）")) if acct else "还没开通"
        state = u.get("enabled", True)
        exp = u.get("expires")
        cur_days = ""
        if exp:
            left = (float(exp) - time.time()) / 86400
            cur_days = f"{int(left)} 天" if left > 0 else "已过期"
        sc = "c-approved" if state else "c-unsupported"
        chips = "".join(level_chip(l) for l in my_levels) or '<span class="chip c-pending">无等级</span>'
        seen = str(u.get("last_seen") or "—")[:16].replace("T", " ")
        exp_full = ACC.describe_expiry(u)
        m_exp = re.search(r"(\d{4})-(\d{2}-\d{2})", exp_full)
        exp_short = f"至 {m_exp.group(2)}" if m_exp else exp_full
        cards.append(f'''<details class="ucard">
<summary>
  <span class="name">{esc(person)}</span>
  <span class="hint">{esc(u.get("dept") or "—")}</span>
  {chips}
  <span class="hint"><span class="dot {"on" if state else "off"}"></span>{"启用中" if state else "已停用"}</span>
  <span class="hint">有效{esc(exp_short)}</span>
  <span class="hint" title="调用 {int(u.get("calls") or 0)} 次 · 被拒 {int(u.get("denied") or 0)} 次 ·
最后活跃 {esc(seen)}">调用 {int(u.get("calls") or 0)} 次</span>
  <span class="caret"></span>
</summary>
<div class="ubody"><div class="two">
  <form class="box" method="post" action="{esc(base)}/admin/user">
    <input type="hidden" name="k" value="{esc(admin)}">
    <input type="hidden" name="person" value="{esc(person)}">
    <div class="grid2" style="gap:8px;margin-top:0">
      <div><label style="margin-top:0">姓名{_q("改名字不影响他的地址，地址照旧有效。")}</label>
        <input name="new_name" value="{esc(person)}" maxlength="40"></div>
      <div><label style="margin-top:0">部门</label>
        <input name="dept" value="{esc(u.get("dept") or "")}" maxlength="40"></div>
      <div><label style="margin-top:0">备注</label>
        <input name="note" value="{esc(u.get("note") or "")}" maxlength="80"></div>
    </div>
    <label>能看哪些等级（可多选）{_q("至少留一个等级 —— 全不勾等于他什么都看不到；要断权限请用「停用」或「删除」。")}</label>
    <div class="lvpick">{lv_boxes}</div>
    <div class="grid2" style="gap:8px">
      <div><label>有效期{_q("不改 = 保持现有到期时间；也可选天数或指定某一天；「无期限」= 长期有效。")}</label>
        <select name="for_days">
          <option value="">不改（现在：{esc(cur_days or "无期限")}）</option>
          <option value="7">7 天</option><option value="30">30 天</option><option value="90">90 天</option>
          <option value="180">180 天</option><option value="365">一年</option>
          <option value="0">改成无期限（长期有效）</option>
        </select></div>
      <div><label>或到某天</label><input type="date" name="until"></div>
      <div><label>状态{_q("停用 = 地址立刻失效，但记录留着，以后还能启用；删除 = 记录也没了。")}</label>
        <select name="enabled">
          <option value="">不改（现在：{"启用中" if state else "已停用"}）</option>
          <option value="1">启用</option><option value="0">停用</option>
        </select></div>
    </div>
    <div class="bar" style="margin-top:10px">
      <button class="tiny" name="action" value="save">保存</button>
      <button class="tiny ghost" name="action" value="rotate"
              onclick="return confirm('给他换一条新地址？旧地址立刻失效。');">更换地址</button>
      <button class="tiny danger" name="action" value="delete"
              onclick="return confirm('彻底删掉 {esc(person)}？这条地址立刻失效，记录也没了。');">删除</button>
      <a class="minor" href="{esc(base)}/admin/usage?k={esc(admin)}&person={esc(person)}">使用明细</a>
    </div>
  </form>
  <form class="box" method="post" action="{esc(base)}/admin/pass">
    <input type="hidden" name="k" value="{esc(admin)}">
    <input type="hidden" name="person" value="{esc(person)}">
    <label style="margin-top:0">网页账号{_q("给他一个登录网页用的用户名 + 一次性临时密码（页面只显示一次）。他登录后只能看自己等级内的资料；账号和地址是两回事。")}</label>
    <input name="user" value="{esc(acct.get("user") or "")}" placeholder="给他一个用户名" maxlength="40">
    <div class="bar" style="margin-top:8px">
      <button class="tiny ghost" name="action" value="save">{acct_btn}</button>
      {acct_drop}
      <span class="hint">{esc(acct_note)}</span>
    </div>
    <details style="margin-top:10px"><summary class="hint" style="cursor:pointer">看地址</summary>
      <code style="display:block;margin:4px 0">{esc(address)}</code>
      <button type="button" class="tiny ghost"
        onclick="navigator.clipboard.writeText('{esc(address)}').then(()=>{{this.textContent='已复制'}},()=>{{}});">复制</button>
      {_q("这条地址等于他的口令，只发给他本人；换地址后旧地址立刻失效。")}
    </details>
  </form>
</div></div>
</details>''')
    return "".join(cards) + _pager(base, admin, "pg", cur, pages, total, "位同事")


def page_files(base: str, person: str, levels: list[str], docs: list[dict], token: str,
               dcfg: dict, msg: str = "", who: str = "") -> bytes:
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
    return _page("我的资料", body, base, who=who)


def _docs_panel(base: str, state_root: Path, wid: str, admin: str, levels: list[str],
                root: Path, docs_rel: str, q: dict | None = None, page: int = 1,
                per: int = 20) -> str:
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

    total_docs = len(shown)
    pages = max(1, (total_docs + per - 1) // per)
    cur = min(max(1, int(page or 1)), pages)
    page_items = shown[(cur - 1) * per: cur * per]

    rows = []
    for did, e in page_items:
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
             + "".join(rows) + "</table>"
             + _pager(base, admin, "dp", cur, pages, total_docs, "篇资料",
                      {k2: v2[0] for k2, v2 in q.items()
                       if k2 in ("q", "status", "cat", "level") and v2 and v2[0]})) if rows else \
        '<p class="hint">没有符合条件的资料。换个筛选，或先上传/扫描。</p>'

    upload = (
        '<form method="post" action="' + esc(base) + '/admin/upload?k=' + esc(admin)
        + '" enctype="multipart/form-data" id="upform">'
        '<input type="hidden" name="k" value="' + esc(admin) + '">'
        '<h3 style="margin-top:0">添加资料（文件 / 整个文件夹 / 拖进来）</h3>'
        '<div class="drop" id="dz">把文件或<b>整个文件夹拖到这里</b><br>'
        '<label class="btnlabel" style="margin-top:8px">'
        '<input class="sr" type="file" name="files" multiple id="f1">选择文件</label>　'
        '<button class="btnlabel" type="button" id="pickdir">选择文件夹</button>'
        '<input class="sr" type="file" name="files" webkitdirectory id="f2">'
        '<div class="hint" id="uplist" style="margin-top:8px">还没选文件</div>'
        '<div class="hint" style="margin-top:4px">把文件或文件夹拖进来' + _q(
        "拖文件夹进来最省事。万一「选择文件夹」没弹出系统窗口，就把文件夹拖进框里，"
        "或用 Finder 放进资料目录再点下面的「扫描资料目录」。上限：单文件 200MB、一次共 1GB、最多 2000 个文件。")
        + '</div></div>'
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
        + _q("上传后自动抽取文本（Word/PDF/PPT 等），原件保留、同事能直接下载。"
             "同名同内容的文件不会重复入库。")
        + "</div></form>")

    scanform = ('<form class="inline" method="post" action="' + esc(base) + '/admin/scan">'
                '<input type="hidden" name="k" value="' + esc(admin) + '">'
                '<button class="ghost" type="submit">扫描资料目录</button>'
                + _q("文件已经用 Finder 放进 " + str(root / docs_rel)
             + " 时点这个；只认新增和内容有变化的，已定好的等级不会被改动。") + "</form>")

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
        + _q("先勾选左边小方框：「设为公开」= 按右边等级放开；「只改等级」= 已公开的换个等级；"
             "「下架」= 回到待批（资料还在）；「移除条目」= 不再管这篇（文件不动）。") + "</div>")

    js = """<script>
(function(){
  var UP=document.getElementById('upform'), dz=document.getElementById('dz'),
      list=document.getElementById('uplist'), f1=document.getElementById('f1'),
      f2=document.getElementById('f2'), pick=document.getElementById('pickdir');
  if(!UP) return;
  var queue=[];                       // [{file, rel}] —— rel 是相对路径（文件夹会带层级）
  function kb(n){ return n<1024?n+' B':(n<1048576?(n/1024).toFixed(0)+' KB':(n/1048576).toFixed(1)+' MB'); }
  function render(msg){
    if(msg){ list.textContent=msg; return; }
    if(!queue.length){ list.textContent='还没选文件'; return; }
    var tot=0, names=[]; for(var i=0;i<queue.length;i++){ tot+=queue[i].file.size; if(i<3) names.push(queue[i].rel); }
    list.textContent='已选 '+queue.length+' 个文件（'+kb(tot)+'）：'+names.join('、')+(queue.length>3?' …':'');
  }
  function fill(files, useRel){
    queue=[];
    for(var i=0;i<(files?files.length:0);i++){
      var f=files[i];
      var rel=(useRel && f.webkitRelativePath) ? f.webkitRelativePath : f.name;
      queue.push({file:f, rel:rel});
    }
    render();
  }
  if(f1) f1.addEventListener('change', function(){ fill(f1.files, false); });
  if(f2) f2.addEventListener('change', function(){ fill(f2.files, true); });
  if(pick) pick.addEventListener('click', async function(e){
    e.preventDefault();
    if(!window.showDirectoryPicker){ if(f2) f2.click(); return; }      // 老浏览器退回 webkitdirectory
    try{
      var dir=await window.showDirectoryPicker();
      queue=[]; render('正在读取文件夹…');
      await walkHandle(dir, '');
      render();
    }catch(err){ render('没有选择文件夹（'+(err && err.name || '取消')+'）'); }
  });
  async function walkHandle(h, prefix){
    for await (var kv of h.entries()){
      var name=kv[0], child=kv[1];
      if(child.kind==='file'){ queue.push({file: await child.getFile(), rel: prefix+name}); }
      else if(child.kind==='directory'){ await walkHandle(child, prefix+name+'/'); }
    }
  }
  async function walkEntry(entry, prefix){
    if(!entry) return;
    if(entry.isFile){ var f=await new Promise(function(r, j){ entry.file(r, j); });
      queue.push({file:f, rel:prefix+entry.name}); return; }
    if(entry.isDirectory){
      var rd=entry.createReader(), all=[], batch;
      do{ batch=await new Promise(function(r, j){ rd.readEntries(r, j); });
          all=all.concat(batch);
      }while(batch.length);
      for(var i=0;i<all.length;i++) await walkEntry(all[i], prefix+entry.name+'/');
    }
  }
  if(dz){
    dz.addEventListener('dragover', function(e){ e.preventDefault(); dz.classList.add('hot'); });
    dz.addEventListener('dragleave', function(){ dz.classList.remove('hot'); });
    dz.addEventListener('drop', async function(e){
      e.preventDefault(); dz.classList.remove('hot');
      var dt=e.dataTransfer; if(!dt) return;
      queue=[]; render('正在读取…');
      var items=dt.items, handled=false;
      if(items && items.length && items[0].webkitGetAsEntry){
        for(var i=0;i<items.length;i++){
          var en=items[i].webkitGetAsEntry && items[i].webkitGetAsEntry();
          if(en){ handled=true; await walkEntry(en, ''); }
        }
      }
      if(!handled && dt.files && dt.files.length){ fill(dt.files, false); }
      render();
      if(queue.length){ var b=UP.querySelector('button[value=pending]'); if(b) b.click(); }
    });
  }
  UP.addEventListener('submit', function(e){
    e.preventDefault();
    if(!queue.length){ render('还没选文件 —— 先拖进来或点「选择文件 / 选择文件夹」'); return; }
    var after=(e.submitter && e.submitter.value) || 'pending';
    send(after);
  });
  function send(after){
    var fd=new FormData();
    fd.append('category', (UP.querySelector('[name=category]')||{}).value || '');
    fd.append('newcat', (UP.querySelector('[name=newcat]')||{}).value || '');
    fd.append('level', (UP.querySelector('[name=level]')||{}).value || '');
    fd.append('after', after);
    for(var i=0;i<queue.length;i++) fd.append('files', queue[i].file, queue[i].rel);
    var x=new XMLHttpRequest();
    x.open('POST', UP.getAttribute('action'));
    x.upload.onprogress=function(ev){ if(ev.lengthComputable) render('上传中… '+Math.round(ev.loaded/ev.total*100)+'%（'+kb(ev.loaded)+'/'+kb(ev.total)+'）'); };
    x.onload=function(){ document.open(); document.write(x.responseText); document.close(); };
    x.onerror=function(){ render('上传失败：网络或隧道中断，请重试'); };
    render('准备上传…');
    x.send(fd);
  }
  window.__kbQueue=function(arr){          // 给自动化测试用的钩子
    queue=(arr||[]).map(function(x){ return {file:x.file||new File([x.data||'x'], x.name||'a.txt'),
                                            rel:x.rel||x.name||'a.txt'}; });
    render();
  };
  window.__kbSend=send;
  render();
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
              '<span class="hint">单篇：先在「等级」列选好，再点「保存等级」或「公开」。</span>'
              + _q("「看原件」= 你自己预览，不受等级限制，也不占同事的地址。") + '</div>'
            + "</form>"
            + (f'<p class="hint">共 {len(docs)} 篇在台账里，筛选后 {len(shown)} 篇，本页显示 {len(page_items)} 篇。'
               f'资料目录：<code>{esc(str(root / docs_rel))}</code></p>')
            + js)


def page_admin(base: str, state_root: Path, wid: str, admin: str, levels: list[str],
               host: str, msg: str = "", remote: bool = False,
               root: Path | None = None, docs_rel: str = "", q: dict | None = None,
               who: str = "") -> bytes:
    q = q or {}                       # 上传后的回执页不带查询串，这里要兜住 None
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
    all_levels_boxes = "".join(f'<label><input type="checkbox" name="levels" value="{esc(l)}">'
                               f'<span>{esc(l)}</span></label>' for l in levels)
    def _pnum(key: str) -> int:
        try:
            return int((q.get(key) or ["1"])[0] or 1)
        except (TypeError, ValueError):
            return 1

    docs_html = (_docs_panel(base, state_root, wid, admin, levels, root, docs_rel, q, page=_pnum("dp"))
                 if root is not None else '')
    body = f"""{msg}
<p class="lead">管理页 · {esc(_now())}　{'（可从公网访问：请勿把本页地址转发给别人）' if remote else '（仅部署机本机可访问）'}</p>

{docs_html}

<h2>待批申请（{len(pend)}）</h2>
{ptable}

<h2>已授权的同事（{len(ACC.list_users(state_root, wid))}）</h2>
{_users_table(state_root, wid, admin, base, levels, page=_pnum("pg"))}

{_invites_section(base, state_root, wid, admin, levels)}

<h2>直接发一条地址（不经申请）</h2>
<form method="post" action="{esc(base)}/admin/grant">
  <input type="hidden" name="k" value="{esc(admin)}">
  <div class="grid2">
    <div><label>姓名</label><input name="person" maxlength="40" required></div>
    <div><label>部门</label><input name="dept" maxlength="40"></div>
    <div><label>有效期</label><select name="for_days"><option value="30">30 天</option>
      <option value="7">7 天</option><option value="90">90 天</option>
      <option value="180">180 天</option><option value="0">无期限</option></select></div>
    <div><label>备注</label><input name="note" maxlength="80" placeholder="例如：XX 项目对接"></div>
  </div>
  <div style="margin-top:12px"><label>能看哪些等级（可多选）</label>
    <div class="lvpick">{all_levels_boxes}</div></div>
  <button type="submit">发放地址</button>
</form>
<p class="hint" style="margin-top:18px">用量看板：<a href="{esc(base)}/admin/usage?k={esc(admin)}">按人 / 按天 / 按资料</a>
　·　命令行等价：<code>bash lighthouse.sh kb usage &lt;窗口&gt;</code></p>
<p class="hint">地址段（token）在本机 <code>~/.lighthouse/state/kb-users.json</code>，也可用
<code>bash lighthouse.sh kb users &lt;窗口&gt; --show-token</code> 查看。</p>"""
    return _page("资料库管理页", body, base, admin=admin, wide=True, who=who)


def page_usage(base: str, state_root: Path, wid: str, admin: str, by: str, days: int,
               person: str = "") -> bytes:
    rows = USAGE.read_audit(state_root, wid, days)
    if person:
        rows = [r for r in rows if str(r.get("principal") or "") == person]
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
    who = (f' · 只看 <b>{esc(person)}</b>　<a class="hint" href="{esc(base)}/admin/usage?k={esc(admin)}&by={esc(by)}&days={days}">'
           f'看所有人</a>' if person else "")
    extra = "".join(f' · <a href="{esc(base)}/admin/usage?k={esc(admin)}&person={esc(p)}&days={days}">{esc(p)}</a>'
                    for p in sorted({str(r.get("principal")) for r in USAGE.read_audit(state_root, wid, days)
                                     if r.get("principal") and r.get("principal") != "-"}))
    body = f"""<p class="lead">用量看板 · 近 {days} 天 · 共 {len(rows)} 条记录{who}</p>
<p>{links}{'　·　按人：' + extra if extra else ''}</p>
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
        self.public_request = bool((cfg.get("kb") or {}).get("portal", {}).get("allow_public_request", False))
        self._ip = "-"          # 每个请求进来时更新，供审计用
        self._sess: dict | None = None   # 本次请求的登录会话（没登录 = None）
        AUTH.ensure_secret(self.state_root)     # 会话签名密钥（没有就生成，0600）
        self._multi: dict = {}  # 本次请求的表单多值字段
        self._token = ""        # 本次请求里出现的地址段（下载页里的链接要用）

    # ---- 小工具 ----
    async def _send(self, send, body: bytes, status: int = 200, ctype: str = "text/html; charset=utf-8",
                    cookie: str | None = None, location: str | None = None):
        hdrs = [(b"content-type", ctype.encode()),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
                (b"x-frame-options", b"DENY"),                 # 防被别人套在 iframe 里钓鱼
                (b"x-content-type-options", b"nosniff"),
                (b"referrer-policy", b"no-referrer")]          # 别把带口令的地址泄露给外站
        if cookie:
            hdrs.append((b"set-cookie", cookie.encode()))
        if location:
            hdrs.append((b"location", location.encode()))
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})

    def _redirect(self, base_path: str, q: str = "") -> bytes:
        return f'<meta http-equiv="refresh" content="0;url={base_path}{q}">'.encode()

    def _address(self, token: str) -> str:
        return f"https://{self.host}/kb-{token}"

    def _admin_net_block(self, scope, hdrs: dict) -> str:
        """管理页的网络层准入：不是部署机 / 公网但没开远程 → 返回原因（否则空串）。"""
        proxied = bool(hdrs.get("cf-connecting-ip") or hdrs.get("x-forwarded-for"))
        ip = (scope.get("client") or ("-", 0))[0]
        if not proxied and ip not in ("127.0.0.1", "::1"):
            return "管理页只允许在部署机上访问。"
        if proxied and not self.remote:
            return ("管理页默认不开放给公网访问。要能从手机上看，"
                    "请在窗口配置里把 kb.portal.admin_remote 设为 true（并保管好管理令）。")
        return ""

    def _admin_gate(self, scope, hdrs: dict, qs: dict) -> tuple[bool, str]:
        """管理页门禁：① 公网开关/本机限制 ② 管理员登录 **或** 管理令（两者之一）。

        管理员登录 = 账号密码那套（推荐）；管理令 = 老链接与脚本兼容用。
        """
        why = self._admin_net_block(scope, hdrs)
        if why:
            return False, why
        sess = self._sess if self._sess is not None else self._session(hdrs)
        if sess and sess.get("role") == "admin":
            return True, ""
        tok = (qs.get("k") or [""])[0] or hdrs.get("x-admin-token", "")
        want = ensure_admin_token(self.state_root)
        if want and tok and secrets.compare_digest(str(tok), str(want)):
            return True, ""
        if sess:
            return False, "这个账号不是管理员，进不了管理页。"
        if not AUTH.has_admin(self.state_root):
            return False, ("还没有管理员账号。在部署机上执行 "
                           f"`bash lighthouse.sh kb passwd {self.win.id} --admin` 生成管理员密码；"
                           f"或者用管理令地址进入。")
        return False, "请先用管理员账号登录（或带上管理令）。"

    def _has_credential(self, scope, hdrs: dict, qs: dict, sub: str, method: str,
                        body: bytes = b"") -> bool:
        """没登录时，这一次请求自己带没带凭据？

        - 管理令（?k= / x-admin-token）：等价于管理员身份，放行（老链接与脚本用）
        - 地址令牌 / 签名链接 / 短地址段：机器与「直接拿文件」那条线，放行
        - 申请页：只有显式打开 allow_public_request 才放行
        其余（裸着来的网页）一律要求登录 —— 这就是「不再谁都能看」。
        """
        bform: dict = {}
        if method == "POST" and body:
            try:
                bform = {k: v[0] for k, v in parse_qs(body.decode(errors="replace")).items()}
            except Exception:                                                # noqa: BLE001
                bform = {}
        adm = (qs.get("k") or [""])[0] or bform.get("k", "") or hdrs.get("x-admin-token", "")
        if sub in ("/zip",) or sub.startswith("/dl/"):
            return True          # 下载/打包端点自己按「谁」判权限，没身份照样拒
        want = ensure_admin_token(self.state_root, create=False)
        if adm and want and secrets.compare_digest(str(adm), str(want)):
            return True
        principal = scope.get("kb_principal")
        if isinstance(principal, dict) and principal.get("person"):
            return True                                   # 走 /kb-<地址段> 进来的
        if ((qs.get("t") or [""])[0] or (qs.get("s") or [""])[0] or (qs.get("p") or [""])[0]
                or bform.get("t", "")):
            return True                                   # 链接里带地址令牌或签名
        if sub == "/request" and self.public_request:
            return True
        if sub == "/register":
            return True                      # 注册靠邀请码把关，不需要先登录
        return False

    def _who_label(self) -> str:
        sess = self._sess if self._sess is not None else self._session({})
        if not sess:
            return ""
        who = sess.get("person") or sess.get("user") or ""
        return ("管理员 · " if sess.get("role") == "admin" else "") + str(who)

    def _is_admin_session(self) -> bool:
        return bool(self._sess and self._sess.get("role") == "admin")

    # ---- 登录 / 登出 / 未登录跳转 ----
    def _session(self, hdrs: dict) -> dict | None:
        """从 cookie 里读登录态（验签 + 查过期 + 查账号是否还在）。"""
        raw = hdrs.get("cookie", "") or ""
        for bit in raw.split(";"):
            k, _, v = bit.strip().partition("=")
            if k == AUTH.COOKIE and v:
                return AUTH.read_cookie(self.state_root, v)
        return None

    async def _need_login(self, send, sub: str, method: str):
        """没登录：页面请求就跳到登录页，接口请求回 401。"""
        if method == "GET" and sub in ("/request", "/files", "/admin", "/admin/usage", "/"):
            nxt = self.base + sub
            return await self._send(send, _page("请先登录", f'<div class="warn">这个页面需要登录。</div>'
                                                    f'<p><a class="btn" href="{esc(self.base)}/login?next='
                                                    f'{esc(nxt)}">去登录</a></p>', self.base), 401)
        return await self._send(send, _page("请先登录", '<div class="warn">需要登录后才能用这个地址。</div>',
                                            self.base), 401)

    def _fix_next(self, raw, default: str) -> str:
        """把 next 归一化成站内路径：/admin 与 <base>/admin 都认，别的丢掉（防跳走）。"""
        nxt = str(raw or "").strip()
        if not nxt:
            return default
        if nxt.startswith(self.base):
            return nxt
        if nxt.startswith("/"):
            return self.base + nxt
        return default

    async def _auth_route(self, send, sub: str, method: str, form: dict, hdrs: dict, qs: dict, ip: str):
        if sub == "/logout":
            self.audit("portal_logout", {}, True,
                       {"actor": (self._sess or {}).get("user") or "-", "ip": ip})
            raw = str((qs.get("next") or [""])[0] or "")
            nxt = self._fix_next(raw, "") if raw else ""
            link = self.base + "/login" + (f"?next={quote(nxt)}" if nxt else "")
            who = (self._sess or {}).get("user") or "当前账号"
            return await self._send(send, _page("已退出", f'<div class="ok">已退出登录（{esc(str(who))}）。</div>'
                                                            f'<p><a class="btn" href="{esc(link)}">'
                                                            '重新登录</a></p>', self.base),
                                    200, cookie=AUTH.clear_cookie())

        if method == "GET":
            if not AUTH.has_admin(self.state_root):
                boot = ('<div class="warn">还没有管理员账号。请在部署机上执行：<br><code>'
                        'bash lighthouse.sh kb passwd ' + self.win.id + ' --admin</code><br>'
                        '它会生成一个密码并只显示一次。</div>')
            else:
                boot = ""
            nxt = self._fix_next((qs.get("next") or [""])[0], self.base + "/request")
            return await self._send(send, page_login(self.base, nxt, boot))

        user = (form.get("user") or "").strip()
        pw = form.get("pw") or ""
        remember = bool(form.get("remember"))
        ok, why, rec = AUTH.verify_login(self.state_root, user, pw, ip)
        if not ok:
            self.audit("portal_login", {"user": user}, False,
                       {"actor": user or "-", "ip": ip, "reason": why,
                        "ua": hdrs.get("user-agent", "")[:60]})
            nxt = self._fix_next(form.get("next"), self.base + "/request")
            return await self._send(send, page_login(self.base, nxt, f'<div class="warn">{esc(why)}</div>',
                                                     user=user), 401)
        mins = AUTH.REMEMBER_MINUTES if remember else AUTH.SESSION_MINUTES
        self.audit("portal_login", {"user": user, "remember": remember}, True,
                   {"actor": user, "person": rec.get("person") or "", "role": rec.get("role"),
                    "ip": ip, "levels": list(self.levels) if rec.get("role") == "admin" else [],
                    "ua": hdrs.get("user-agent", "")[:60]})
        nxt = self._fix_next(form.get("next"),
                             self.base + ("/admin" if rec.get("role") == "admin" else "/files"))
        return await self._send(send, _page("登录成功", '<div class="ok">登录成功，正在进入…</div>'
                                          f'<p><a class="btn" href="{esc(nxt)}">继续</a></p>', self.base),
                                200, cookie=AUTH.cookie_header(self.state_root, rec, minutes=mins,
                                                               secure=bool(hdrs.get("cf-connecting-ip")
                                                                           or hdrs.get("x-forwarded-for"))))

    async def _register(self, send, method: str, qs: dict, form: dict, ip: str, hdrs: dict):
        """凭邀请码自助注册：一次填完 → 发地址 + 建账号 + 自动登录。"""
        import config as C
        if method == "GET":
            code = (qs.get("c") or [""])[0]
            bound = ""
            if code:
                rec, _why = INV.check(self.state_root, self.win.id, code)
                bound = (rec or {}).get("person") or ""
            return await self._send(send, page_register(self.base, self.levels, code=code,
                                                        bound=bound))
        code = (form.get("code") or "").strip()
        name = (form.get("name") or "").strip()
        dept = (form.get("dept") or "").strip()
        purpose = (form.get("purpose") or "").strip()
        user = (form.get("user") or "").strip()
        pw = form.get("pw") or ""
        pw2 = form.get("pw2") or ""

        def back(msg: str, status: int = 400):
            return self._send(send, page_register(self.base, self.levels, msg, code=code, bound=name), status)

        rec, why = INV.check(self.state_root, self.win.id, code)
        if not rec:
            self.audit("portal_register", {"code": INV.pretty(code), "name": name}, False,
                       {"reason": why, "ip": ip})
            return await back(f'<div class="warn">⛔ {esc(why)}</div>')
        if rec.get("person") and rec["person"] != name:
            self.audit("portal_register", {"code": rec["code"], "name": name}, False,
                       {"reason": "码不是给这个人的", "ip": ip})
            return await back(f'<div class="warn">⛔ 这张邀请码是给「{esc(rec["person"])}」的，'
                              '请用本人姓名注册。</div>')
        if not name or not purpose:
            return await back('<div class="warn">姓名和用途都要填。</div>')
        if not user or not user.isalnum() or len(user) < 3:
            return await back('<div class="warn">用户名至少 3 位，只能用字母和数字。</div>')
        if len(pw) < 8:
            return await back('<div class="warn">密码至少 8 位。</div>')
        if pw != pw2:
            return await back('<div class="warn">两次输入的密码不一样。</div>')
        if AUTH.get(self.state_root, user):
            return await back(f'<div class="warn">用户名「{esc(user)}」已经被用了，换一个。</div>')
        if ACC.get_user(self.state_root, self.win.id, name):
            return await back(f'<div class="warn">「{esc(name)}」这个同事已经领过地址了；'
                              '地址忘了或要换，找维护者。</div>')
        levels = [x for x in (rec.get("levels") or []) if x in self.levels] or [self.levels[0]]
        # ① 发地址（等级与有效期按邀请码上定的来）
        left = rec.get("expires")
        u = ACC.upsert_user(self.state_root, self.win.id, name, levels, dept=dept,
                            note=f"邀请码注册 {rec['code']}｜{purpose}"[:80],
                            minutes=int((float(left) - time.time()) / 60) if left else None)
        # ② 建网页账号（用他自己设的密码）
        AUTH.set_account(self.state_root, user, role="member", password=pw, person=name)
        # ③ 登记码的一次使用
        INV.use(self.state_root, self.win.id, rec["code"], person=name, username=user, ip=ip)
        KB.append_request(self.state_root, self.win.id, name=name, dept=dept,
                          purpose=f"[邀请码 {rec['code']}] {purpose}", level_requested=levels[0],
                          contact="", ip=ip, status="approved", auto=True,
                          decided_note=f"邀请码 {rec['code']} 自动放行（码由维护者生成）")
        self.audit("portal_register", {"code": rec["code"], "name": name, "user": user,
                                       "levels": levels}, True,
                   {"person": name, "levels": levels, "ip": ip, "user": user,
                    "ua": hdrs.get("user-agent", "")[:60]})
        self.audit("kb_invite_use", {"code": rec["code"], "person": name}, True,
                   {"person": name, "levels": levels, "ip": ip})
        addr = self._address(u["token"])
        who = AUTH.get(self.state_root, user)
        return await self._send(send, _page("注册成功", f'''<div class="ok">
<b>搞定，{esc(name)}。</b>下面两样都收好：</div>
<p><b>① 给 AI 助手用的地址</b>（填进 ChatGPT 连接器那种地方）：<br>
<code>{esc(addr)}</code></p>
<p><b>② 网页账号</b>：用户名 <code>{esc(user)}</code>，密码就是你刚设的那个 ——
<a href="{esc(self.base)}/login">点这里登录</a>，登录后能看到/下载你等级内的资料。</p>
<p class="hint">你的等级：{esc("、".join(levels))}　·　邀请码 {esc(rec["code"])} 已标记为使用
（维护者能看到是谁在什么时间用的）。忘密码找维护者重置。</p>''', self.base),
                                200, cookie=AUTH.cookie_header(self.state_root, who, minutes=AUTH.SESSION_MINUTES,
                                                               secure=bool(hdrs.get("cf-connecting-ip")
                                                                           or hdrs.get("x-forwarded-for"))))

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
                         "/login", "/logout", "/register",
                         "/admin/decide", "/admin/grant", "/admin/revoke", "/admin/rotate",
                         "/admin/scan", "/admin/doc", "/admin/upload", "/admin/bulk",
                         "/admin/user", "/admin/pass", "/admin/invite", "/healthz"}
        if sub not in portal_routes and not sub.startswith("/dl/"):
            accept = hdrs.get("accept", "")
            if sub in ("", "/") and method == "GET" and "text/html" in accept and "text/event-stream" not in accept:
                return await self._send(send, self._redirect(f"{self.base}/request"))
            return await self.app(scope, receive, send)

        # ---------- 登录闸门：网页门户一律要登录（MCP 那条线不受影响，见上面的分流）----------
        # 表单 POST 的凭据在 body 里（k= / t=），所以先把体读下来缓冲，再「重放」给后续处理
        replay = receive
        bodybuf = b""
        if method == "POST" and sub != "/admin/upload":
            more = True
            while more:
                _m = await receive()
                bodybuf += _m.get("body", b"")
                more = _m.get("more_body", False)

            async def replay():                                        # noqa: E306
                return {"type": "http.request", "body": bodybuf, "more_body": False}
            receive = replay
        self._sess = self._session(hdrs)
        if sub in ("/login", "/logout", "/register"):
            lform: dict = {}
            if method == "POST":
                lbody = b""
                more = True
                while more:
                    msg = await receive()
                    lbody += msg.get("body", b"")
                    more = msg.get("more_body", False)
                lform = {k: v[0] for k, v in parse_qs(lbody.decode(errors="replace")).items()}
            if sub == "/register":
                return await self._register(send, method, qs, lform, ip, hdrs)
            return await self._auth_route(send, sub, method, lform, hdrs, qs, ip)
        if not self._sess and not self._has_credential(scope, hdrs, qs, sub, method, bodybuf):
            if sub.startswith("/admin"):
                why = self._admin_net_block(scope, hdrs)      # 公网开关/非部署机 → 先说清楚
                if why:
                    self.audit("portal_admin", {"path": sub}, False, {"reason": why, "ip": ip})
                    return await self._send(send, _page("管理页无法访问",
                                                        f'<div class="warn">{esc(why)}</div>', self.base),
                                            403 if "公网" in why else 401)
            return await self._need_login(send, sub, method)

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
                    return await self._send(send, page_request(self.base, self.cfg, self.public_levels,
                                                               who=self._who_label()))
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
        sess = self._sess if self._sess is not None else self._session({})
        if sess:
            if sess.get("role") == "admin":
                self._token = ""
                return "维护者(登录)", list(self.levels), ""
            who = sess.get("person") or sess.get("user") or ""
            rec2 = ACC.get_user(self.state_root, self.win.id, who) if who else None
            if rec2:
                if not rec2.get("enabled", True):
                    return "", [], "你的账号已被停用，请联系维护者。"
                self._token = str(rec2.get("token") or "")
                return who, list(rec2.get("levels") or []), ""
            return "", [], f"这个账号（{who}）还没有分配资料等级，请联系维护者。"
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
        admin_preview = (bool(adm) and adm == ensure_admin_token(self.state_root, create=False)) \
            or self._is_admin_session()
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
            return await self._send(send, page_files(self.base, person, levels, docs, tok, dcfg, who=self._who_label()))

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
        if self._sess and self._sess.get("role") != "admin":
            who = self._sess.get("person") or self._sess.get("user") or ""
            if who:
                name = who                      # 同事只能以本人名义申请
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

        hdrs = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        ctype = hdrs.get("content-type", "")
        clen = hdrs.get("content-length", "?")
        trace = {"ct": ctype.split(";")[0], "len": clen, "ua": hdrs.get("user-agent", "")[:60]}

        ucfg = (self.cfg.get("kb") or {}).get("upload") or {}
        if ucfg.get("enabled") is False:
            self.audit("kb_upload", {}, False, {"reason": "上传未开启", "actor": "admin", "ip": ip})
            return await self._send(send, _page("上传未开启", '<div class="warn">本窗口关闭了网页上传'
                                                '（kb.upload.enabled=false）。可先把文件放进资料目录再点扫描。</div>',
                                                self.base), 403)
        max_file = int(ucfg.get("max_file_mb", 200)) * 1024 * 1024
        max_total = int(ucfg.get("max_total_mb", 1024)) * 1024 * 1024
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
                                     max_files=2000, max_fields=50, max_part_size=max_file)
            form = await parser.parse()
        except Exception as e:                                                   # noqa: BLE001
            self.audit("kb_upload", {}, False, {"reason": f"{e.__class__.__name__}: {e}",
                                                "actor": "admin", "ip": ip})
            trace["outcome"] = f"解析失败 {e.__class__.__name__}"
            self.audit("kb_upload", trace, False, {"actor": "admin", "ip": ip})
            print(f"[upload] ⛔ {trace}", flush=True)
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
            trace.update(files=len(files), names=[f.filename for f in files[:5]])
            if not files:
                trace["outcome"] = "没有文件"
                self.audit("kb_upload", trace, False, {"actor": "admin", "ip": ip})
                print(f"[upload] ⛔ {trace}", flush=True)
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
                rel_name = uf.filename or ""
                if cat:
                    # 文件夹上传常把顶层目录名也带上：跟分类同名就别再套一层
                    head, _, tail = rel_name.partition("/")
                    if tail and head.strip() == cat.strip():
                        rel_name = tail
                target, why = self._safe_rel(rel_name, base_dir)
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
        # 这次上传真正带来变化的篇目（新增 / 内容变了）；同名同内容的旧篇目一律不动
        fresh = {KB.doc_id(r2) for mark, r2, _n in res["rows"] if mark in ("新", "改")}

        def _tag(rel: str) -> str:
            for mark, r2, note in res["rows"]:
                if r2 != rel:
                    continue
                return {"新": "（新增）", "改": "（内容变了 → 已退回待批）",
                        "跳": "（类型不支持，" + note + "）",
                        "错": "（抽取失败：" + note + "）"}.get(mark, "")
            return "（已存在、内容没变 → 原样保留，等级和状态都没动）"

        lines = [x + _tag(rel) for x, rel in zip(lines, saved)] if len(lines) == len(saved) else lines
        published, bad = 0, []
        if after == "publish":
            for rel in saved:
                did = KB.doc_id(rel)
                if did not in fresh:
                    continue                     # 同名同内容的旧篇目：别悄悄改它的等级/状态
                try:
                    KB.set_status(self.state_root, self.win.id, did, "approved", level=level, by="维护者")
                    self.audit("kb_approve", {"doc_id": did, "level": level}, True,
                               {"actor": "admin", "ip": ip, "via": "upload"})
                    published += 1
                except (ValueError, RuntimeError) as e:                            # noqa: PERF203
                    bad.append(str(e))
        trace.update(saved=len(saved), bytes=total, after=after,
                     reasons=[x[:70] for x in lines if x.startswith("⛔")][:5])
        self.audit("kb_upload", trace, bool(saved), {"actor": "admin", "ip": ip})
        print(f"[upload] {'✅' if saved else '⛔'} {trace}", flush=True)
        same_new = len(saved) - len([1 for rel in saved if KB.doc_id(rel) in fresh])
        if published:
            tail = ("，其中 <b>" + str(published) + "</b> 篇已按 " + esc(level)
                    + " 公开（同事现在就能看/能下）")
        elif fresh:
            tail = "，已进「待批」，你在下面逐篇定等级即可"
        else:
            tail = "；但这次没有新资料或内容变化，所以什么都不会变（列表不会多出重复的一行）"
        if same_new:
            tail += (f"；另有 <b>{same_new}</b> 个文件资料库里本来就有、内容一模一样，"
                     "没新建条目也没动它的等级 —— 所以列表里不会多出重复的一行")
        msg = ('<div class="ok"><b>已收下 ' + str(len(saved)) + " 个文件</b>（"
               + str(round(total / 1048576, 2)) + " MB）" + tail + "</div>"
               + ('<ul class="hint">' + "".join("<li>" + x + "</li>" for x in lines[:40]) + "</ul>")
               + ("".join('<div class="warn">' + esc(x) + "</div>" for x in bad))
               + ('<p class="hint">资料目录同步结果：新增 ' + str(res["new"]) + " 篇 · 内容更新（退回待批）"
                  + str(res["changed"]) + " 篇 · 没变 " + str(res["same"]) + " 篇 · 类型不支持 "
                  + str(res["skipped"]) + " 篇</p>"))
        return await self._send(send, page_admin(self.base, self.state_root, self.win.id, admin,
                                                 self.levels, self.host, msg, remote=self.remote,
                                                 root=self.win.root, docs_rel=docs_rel,
                                                 who=self._who_label()))

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
            extra = ""
            if self._sess and not self._is_admin_session():
                why = (f"你现在是同事账号（{self._who_label()}）登录着，管理页面只有维护者能进。")
                extra = (f'<p><a class="btn" href="{esc(self.base)}/logout?next='
                         f'{esc(quote(sub or "/admin"))}">退出这个账号，用管理员登录</a>'
                         f'<span class="hint" style="margin-left:8px">'
                         f'（管理员用户名 admin，密码在部署机的 admin-password.txt）</span></p>'
                         f'<p class="hint">只想看资料 → <a href="{esc(self.base)}/files">去资料页</a>；'
                         f'要申请权限 → <a href="{esc(self.base)}/request">去申请</a></p>')
                code = 403
            else:
                code = 403 if ("公网" in why or "部署机" in why) else 401
            self.audit("portal_admin", {"path": sub}, False, {"reason": why, "ip": ip})
            return await self._send(send, _page("管理页无法访问",
                                                f'<div class="warn">{esc(why)}</div>{extra}', self.base), code)
        admin = ensure_admin_token(self.state_root)

        if sub == "/admin/usage" and method == "GET":
            by = (qs.get("by") or ["person"])[0]
            days = int((qs.get("days") or ["30"])[0] or 30)
            if by not in ("person", "day", "doc", "tool"):
                by = "person"
            return await self._send(send, page_usage(self.base, self.state_root, self.win.id, admin, by, days,
                                                     (qs.get("person") or [""])[0]))
        if sub == "/admin" and method == "GET":
            return await self._send(send, page_admin(
                self.base, self.state_root, self.win.id, admin, self.levels, self.host,
                remote=self.remote, root=self.win.root, q=qs, who=self._who_label(),
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
            picked = [x for x in (self._multi.get("levels") or []) if x in self.levels]
            if not picked and form.get("level") in self.levels:      # 兼容只传一个 level 的旧表单
                picked = [form["level"]]
            level = picked[0] if len(picked) == 1 else ""
            if not person or not picked:
                msg = '<div class="warn">姓名和等级都要填对。</div>'
            else:
                days = int(form.get("for_days", "30") or 0)
                minutes = days * 1440 if days else None
                u = ACC.upsert_user(self.state_root, self.win.id, person, picked,
                                    dept=form.get("dept", ""), note=form.get("note", ""), minutes=minutes)
                self.audit("kb_grant", {"person": person, "levels": picked}, True,
                           {"person": person, "levels": picked, "actor": "admin", "ip": ip,
                            "expires": u.get("expires")})
                msg = (f'<div class="ok">✅ {esc(person)} 的地址已发放（{"、".join(esc(x) for x in picked)}，'
                       f'{esc(ACC.describe_expiry(u))}）'
                       f'<br><code>{esc(self._address(u["token"]))}</code></div>')
        elif sub == "/admin/revoke":
            person = form.get("person", "")
            enabled = form.get("enabled", "0") == "1"
            if ACC.set_enabled(self.state_root, self.win.id, person, enabled):
                self.audit("kb_revoke", {"person": person, "enabled": enabled}, True, {"actor": "admin", "ip": ip})
                msg = f'<div class="ok">已{"恢复" if enabled else "停用"} {esc(person)} 的地址。</div>'
            else:
                msg = '<div class="warn">找不到这个人。</div>'
        elif sub == "/admin/invite":
            act = form.get("action") or "create"
            code = form.get("code") or ""
            if act == "create":
                picked = [x for x in (self._multi.get("levels") or []) if x in self.levels]
                if not picked:
                    msg = '<div class="warn">至少勾一个等级 —— 这张码发出去就是给人这一档的权限。</div>'
                else:
                    try:
                        days = int(form.get("days") or 30)
                        max_uses = int(form.get("max_uses") or 1)
                    except ValueError:
                        days, max_uses = 30, 1
                    rec = INV.create(self.state_root, self.win.id, levels=picked,
                                     person=form.get("person", ""), note=form.get("note", ""),
                                     days=days, max_uses=max_uses)
                    self.audit("kb_invite_create", {"code": rec["code"], "levels": picked,
                                                    "person": rec.get("person"),
                                                    "days": days, "max_uses": max_uses}, True,
                               {"actor": "admin", "ip": ip, "levels": picked})
                    msg = (f'<div class="ok">✅ 邀请码已生成：<code style="font-size:16px">{esc(rec["code"])}</code>'
                           f'<br>等级 {"、".join(esc(x) for x in picked)}　·　'
                           f'{"指定给 " + esc(rec["person"]) + "　·　" if rec.get("person") else ""}'
                           f'{esc(str(days))} 天内有效　·　可用 {max_uses} 次'
                           f'<br><span class="hint">把注册链接发给他：</span>'
                           f'<br><code>{esc(self.base)}/register?c={esc(rec["code"])}</code></div>')
            elif act in ("revoke", "enable"):
                done = INV.revoke(self.state_root, self.win.id, code, enabled=(act == "enable"))
                self.audit("kb_invite_revoke", {"code": INV.pretty(code), "action": act}, done,
                           {"actor": "admin", "ip": ip})
                msg = (f'<div class="ok">已{"恢复" if act == "enable" else "停用"} '
                       f'<code>{esc(INV.pretty(code))}</code>。</div>' if done
                       else '<div class="warn">没这张码。</div>')
            else:                                                     # delete
                done = INV.delete(self.state_root, self.win.id, code)
                self.audit("kb_invite_delete", {"code": INV.pretty(code)}, done,
                           {"actor": "admin", "ip": ip})
                msg = (f'<div class="ok">已删掉 <code>{esc(INV.pretty(code))}</code>。</div>' if done
                       else '<div class="warn">没这张码。</div>')
        elif sub == "/admin/pass":
            person = (form.get("person") or "").strip()
            act = form.get("action") or "save"
            user = (form.get("user") or "").strip() or person
            if not person:
                msg = '<div class="warn">没指定同事。</div>'
            elif act == "drop":
                done = AUTH.delete_account(self.state_root, user)
                self.audit("kb_account", {"user": user, "action": "delete"}, done,
                           {"person": person, "actor": "admin", "ip": ip})
                msg = (f'<div class="ok">已删掉账号 <b>{esc(user)}</b>（他的地址与资料权限不受影响）。</div>'
                       if done else '<div class="warn">没这个账号。</div>')
            else:
                rec = AUTH.get(self.state_root, user)
                try:
                    if rec:
                        _rec, pw = AUTH.reset_password(self.state_root, user)
                        act_word = "重置"
                    else:
                        _rec, pw = AUTH.new_account(self.state_root, user, role="member", person=person)
                        act_word = "开通"
                except ValueError as e:
                    pw, msg = None, f'<div class="warn">⛔ {esc(str(e))}</div>'
                if pw:
                    self.audit("kb_account", {"user": user, "action": act_word, "person": person}, True,
                               {"person": person, "actor": "admin", "ip": ip})
                    msg = (f'<div class="ok">✅ 已{act_word} <b>{esc(person)}</b> 的网页账号。'
                           f'<br>用户名 <code>{esc(user)}</code>　临时密码 '
                           f'<code style="font-size:15px">{esc(pw)}</code>'
                           f'<br><span class="hint">这串密码只显示这一次，请复制给他：'
                           f'登录 {esc(self.base)}/login，登录后能看到属于他等级的资料。</span></div>')
        elif sub == "/admin/user":
            person = (form.get("person") or "").strip()
            act = form.get("action") or "save"
            rec = ACC.get_user(self.state_root, self.win.id, person)
            if not rec:
                msg = '<div class="warn">找不到这个同事。</div>'
            elif act == "delete":
                ACC.delete_user(self.state_root, self.win.id, person)
                self.audit("kb_user_delete", {"person": person}, True, {"actor": "admin", "ip": ip})
                msg = f'<div class="ok">已删掉 <b>{esc(person)}</b> —— 他那条地址立刻失效。</div>'
            elif act == "rotate":
                u = ACC.rotate(self.state_root, self.win.id, person)
                self.audit("kb_rotate", {"person": person}, True, {"actor": "admin", "ip": ip})
                msg = (f'<div class="ok">✅ 已给 {esc(person)} 换新地址（旧地址立刻失效）'
                       f'<br><code>{esc(self._address(u["token"]))}</code></div>')
            else:                                                        # save
                lv = [x for x in (self._multi.get("levels") or []) if x in self.levels]
                bad = [x for x in (self._multi.get("levels") or []) if x not in self.levels]
                if bad:
                    msg = f'<div class="warn">等级不在本窗允许清单里：{esc("、".join(bad))}</div>'
                elif not lv:
                    msg = ('<div class="warn">至少要留一个等级 —— 都不勾就等于让他什么都看不到。'
                           '想完全断开就点「停用」或「删除」。</div>')
                else:
                    days = form.get("for_days", "")
                    until = form.get("until", "")
                    enabled = {"1": True, "0": False}.get(form.get("enabled", ""), None)
                    new_name = (form.get("new_name") or person).strip() or person
                    try:
                        exp = ACC.expiry_to_ts(days, until)
                        u = ACC.update_user(self.state_root, self.win.id, person,
                                            levels=lv, dept=form.get("dept", ""), note=form.get("note", ""),
                                            expires=(exp if (days or until) else ACC._UNSET),
                                            enabled=enabled, new_name=new_name)
                    except (ValueError, RuntimeError) as e:
                        u, exp = None, None
                        msg = f'<div class="warn">⛔ 没保存：{esc(str(e))}</div>'
                    if u:
                        self.audit("kb_user_update", {"person": person, "levels": lv,
                                                      "days": days, "until": until,
                                                      "enabled": enabled, "renamed": new_name != person},
                                   True, {"person": u["person"], "levels": lv, "actor": "admin", "ip": ip})
                        bits = ["等级 " + "、".join(lv)]
                        if days or until:
                            bits.append(ACC.describe_expiry(u))
                        if enabled is not None:
                            bits.append("已启用" if enabled else "已停用")
                        if new_name != person:
                            bits.append(f"改名：{person} → {new_name}")
                        msg = f'<div class="ok">✅ 已更新 <b>{esc(u["person"])}</b>：' + esc(" · ".join(bits)) + "</div>"

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
