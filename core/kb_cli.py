#!/usr/bin/env python3
"""灯塔·受控资料库维护 CLI —— 只由维护者在部署机上运行（也可从管理页点）。

  kb_cli.py init <窗口> [--lib ~/Documents/招投标文档库]     建资料库骨架
  kb_cli.py scan <窗口> [--extract auto|none|mineru] [--yes] 扫目录 → 抽文本 → 登记待批
  kb_cli.py pending <窗口> [--level L] [--limit N]
  kb_cli.py list <窗口> [--approved|--pending|--rejected] [--category X]
  kb_cli.py show <窗口> <doc_id>
  kb_cli.py approve <窗口> <doc_id|--all-pending|--category X> [--level L] [--yes]
  kb_cli.py reject <窗口> <doc_id> [--reason "…"]
  kb_cli.py reindex <窗口> [--yes]                          内容变了 → 重新抽取并退回待批
  kb_cli.py requests <窗口> [--pending] [--limit N]          看同事的申请
  kb_cli.py decide <窗口> <申请号> --approve --level L [--for 30d] | --deny [--reason "…"]
  kb_cli.py users <窗口> [--show-token]
  kb_cli.py grant <窗口> --name 张三 --level "L1,L2" [--for 30d] [--dept X] [--note "…"]
  kb_cli.py set-level <窗口> --name 张三 --level "L1,L2"
  kb_cli.py edit <窗口> --name 张三 --levels "L1-商务,L3-核心" --days 90 --dept 技术部 --note "XX 项目"
  kb_cli.py edit <窗口> --name 张三 --rename 张三丰        # 改名（地址不变）
  kb_cli.py rm <窗口> --name 张三 --yes                   # 彻底删除
  kb_cli.py passwd <窗口> --admin                          # 给自己设管理员网页密码（打印一次）
  kb_cli.py passwd <窗口> --user zhangsan --person 张三     # 给同事开通网页账号
  kb_cli.py accounts <窗口>                                # 列出网页账号
  kb_cli.py rotate <窗口> --name 张三                       换一条新地址（旧的立即失效）
  kb_cli.py revoke <窗口> --name 张三 [--enable]
  kb_cli.py invite <窗口> --name 张三 --out 文件.md [--level L] [--for 30d]
  kb_cli.py notify <窗口> [--ack] [--json]                  新申请（给 agent 用来汇报）
  kb_cli.py usage <窗口> [--days 7] [--by person|day|doc|tool] [--csv]
  kb_cli.py admin-url <窗口>

原则：**只有本 CLI（与管理页）能改变「公开 / 授权」**；MCP 侧没有等价能力。
每个子命令都先检查该窗口是不是资料库模式，不是就报错退出（fail-closed）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
import config as C          # noqa: E402
import kb as KB             # noqa: E402
import kb_access as ACC
import kb_auth as AUTH     # noqa: E402
import kb_ingest as ING     # noqa: E402
import kb_usage as USAGE    # noqa: E402
import kb_web as WEB        # noqa: E402

CST = timezone(timedelta(hours=8))
CLI = "bash lighthouse.sh"


# ---------------------------------------------------------------- 基础
def _win(wid: str) -> tuple[dict, Path, Path]:
    """返回 (窗口配置, 窗口根目录, 状态目录)；不是资料库模式就直接退出。"""
    wins = C.windows()
    if wid not in wins:
        print(f"❌ 没有这扇窗: {wid}（可用：{', '.join(wins) or '（无）'}）")
        raise SystemExit(1)
    cfg = wins[wid]
    if not C.window_kb_enabled(cfg):
        print(f'❌ 窗口 {wid} 不是资料库模式：请在 windows.json 里给它加 "kb": {{"enabled": true, …}} 后 restart')
        raise SystemExit(1)
    return cfg, C.window_root(cfg), C.state_dir()


def _audit(state: Path, wid: str, tool: str, args: dict, ok: bool, extra: dict | None = None) -> None:
    """维护者侧动作也要留痕（与管理页、MCP 写同一份审计）。"""
    rec = {"ts": datetime.now(CST).isoformat(timespec="seconds"), "window": wid,
           "principal": "-", "levels": None, "tool": tool, "args": args, "ok": bool(ok),
           "ip": "local", "ua": f"cli:{tool}", "actor": "cli"}
    if extra:
        rec.update(extra)
    p = Path(state) / "audit" / f"{wid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _parse_levels(cfg: dict, raw: str) -> list[str]:
    """把 "L1-商务,L2-技术" 解析成等级列表；任何一个不在本窗清单里就报错（fail-closed）。"""
    allowed = C.window_kb_levels(cfg)
    want = [x.strip() for x in (raw or "").replace("，", ",").split(",") if x.strip()]
    if not want:
        d = C.window_kb_default_level(cfg)
        if not d:
            print("❌ 本窗没有配置等级（kb.levels），请先在 windows.json 里定义")
            raise SystemExit(1)
        return [d]
    bad = [x for x in want if x not in allowed]
    if bad:
        print(f"❌ 不认识的等级 {bad}；本窗可用：{allowed}")
        raise SystemExit(1)
    return want


def _minutes(spec: str) -> int | None:
    """--for 支持 30m/2h/1d/7d/1w/30d/forever（复用 scope 的解析器，失败必须报错）。"""
    sys.path.insert(0, str(ROOT / "core"))
    import scope as SCOPE
    try:
        return SCOPE.parse_duration(spec)
    except ValueError as e:
        print(f"❌ {e}")
        raise SystemExit(1)


def _fmt_expire(minutes: int | None) -> str:
    if not minutes:
        return "无期限"
    return f"{minutes // 1440} 天" if minutes % 1440 == 0 else f"{minutes} 分钟"


def _public_url(cfg: dict, win_id: str) -> str:
    host = (C.load().get("hostname") or "").strip()
    return f"https://{host}{cfg['path']}" if host else f"http://127.0.0.1:{cfg['port']}{cfg['path']}"


def _address(cfg: dict, token: str) -> str:
    host = (C.load().get("hostname") or "").strip()
    return f"https://{host}/kb-{token}" if host else f"http://127.0.0.1:{cfg['port']}/kb-{token}"


# ---------------------------------------------------------------- 台账命令
def cmd_init(a) -> int:
    lib = Path(a.lib).expanduser()
    (lib / "原始文档").mkdir(parents=True, exist_ok=True)
    (lib / "同事说明").mkdir(parents=True, exist_ok=True)
    print(f"✅ 已建好资料库骨架：{lib}")
    print("   · 把资料放进「原始文档/」，按分类建子目录（分类名会自动成为资料分类）")
    print("   · 原始文档/ 里**不要**放报价、资质等商务文件（要放就单独建等级并不外发）")
    print(f"\n下一步：登记窗口\n  {CLI} new {a.window} {lib} --title \"招投标资料（受控·分级）\" "
          f"--include \"原始文档/**\" --exclude \"同事说明/**\" --public --yes")
    print(f"  然后在 windows.json 给 {a.window} 加：")
    print('    "kb": {"enabled": true, "docs_dir": "原始文档",')
    print('           "levels": ["L1-商务", "L2-技术", "L3-核心"], "default_level": "L1-商务",')
    print('           "portal": {"enabled": true, "public_levels": ["L1-商务", "L2-技术", "L3-核心"],')
    print('                      "auto_approve_levels": ["L1-商务"], "admin_remote": true}}')
    print(f"  再 {CLI} restart && {CLI} kb scan {a.window}")
    return 0


def _walk_docs(cfg: dict, root: Path) -> list[Path]:
    docs_dir = root / C.window_kb_docs_dir(cfg)
    if not docs_dir.is_dir():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(docs_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in sorted(filenames):
            if f.startswith("."):
                continue
            out.append(Path(dirpath) / f)
    return sorted(out)


def _unsupported_flag(state: Path, wid: str, rel: str) -> None:
    """给「类型不支持」的篇目打上 unsupported，approve 时会直接拒绝（fail-closed）。"""
    cat, err = KB.load_catalog(state, wid)
    if err:
        return
    ent = (cat.get("docs") or {}).get(KB.doc_id(rel))
    if not ent:
        return
    ent["unsupported"] = True
    KB.save_catalog(state, wid, cat)


def cmd_scan(a) -> int:
    cfg, root, state = _win(a.window)
    docs_dir = root / C.window_kb_docs_dir(cfg)
    if not docs_dir.is_dir():
        print(f"❌ 资料目录不存在: {docs_dir}")
        return 1
    res = ING.scan_library(state, a.window, root, C.window_kb_docs_dir(cfg), a.extract,
                            default_level=C.window_kb_default_level(cfg))
    if not res["total"]:
        print(f"（{docs_dir} 里没有文件）")
        return 0
    _audit(state, a.window, "kb_scan", {"extract": a.extract}, True,
           {"new": res["new"], "changed": res["changed"], "same": res["same"],
            "skipped": res["skipped"], "failed": res["failed"]})
    print(f"扫描 {docs_dir}")
    for mark, rel, note in res["rows"][:40]:
        print(f"  {mark} {rel}  — {note}")
    print(f"\n新增待批 {res['new']} 篇 · 内容变化退回待批 {res['changed']} 篇 · 未变 {res['same']} 篇 · "
          f"跳过 {res['skipped']} 篇 · 失败 {res['failed']} 篇")
    print(f"台账: {KB.catalog_path(state, a.window)}")
    print(f"\n下一步：{CLI} kb pending {a.window}   →   {CLI} kb approve {a.window} --all-pending --level <等级>")
    return 0


def cmd_pending(a) -> int:
    cfg, _root, state = _win(a.window)
    cat, err = KB.load_catalog(state, a.window)
    if err:
        print(f"❌ {err}")
        return 1
    docs = [(d, e) for d, e in (cat.get("docs") or {}).items() if e.get("status") == "pending"]
    if a.level:
        docs = [(d, e) for d, e in docs if e.get("level") == a.level]
    docs.sort(key=lambda t: (t[1].get("category") or "", t[1].get("title") or ""))
    docs = docs[: max(1, a.limit)]
    if not docs:
        print("没有待批资料。")
        return 0
    print(f"{'编号':<10} {'分类':<12} {'等级':<10} {'字数':>7}  标题")
    for d, e in docs:
        print(f"{d:<10} {(e.get('category') or '-'):<12} {(e.get('level') or '-'):<10} "
              f"{int(e.get('chars') or 0):>7}  {e.get('title')}")
    print(f"\n共 {len(docs)} 篇待批。批准：{CLI} kb approve {a.window} --all-pending --level <等级>")
    return 0


def cmd_list(a) -> int:
    cfg, _root, state = _win(a.window)
    cat, err = KB.load_catalog(state, a.window)
    if err:
        print(f"❌ {err}")
        return 1
    want = "approved" if a.approved else ("pending" if a.pending else ("rejected" if a.rejected else ""))
    print(f"{'编号':<10} {'状态':<9} {'等级':<10} {'分类':<12}  标题")
    n = 0
    for d, e in sorted((cat.get("docs") or {}).items(), key=lambda t: t[1].get("title") or ""):
        if want and e.get("status") != want:
            continue
        if a.category and e.get("category") != a.category:
            continue
        print(f"{d:<10} {(e.get('status') or '-'):<9} {(e.get('level') or '-'):<10} "
              f"{(e.get('category') or '-'):<12}  {e.get('title')}")
        n += 1
    print(f"\n共 {n} 篇")
    return 0


def cmd_show(a) -> int:
    _cfg, _root, state = _win(a.window)
    cat, err = KB.load_catalog(state, a.window)
    if err:
        print(f"❌ {err}")
        return 1
    e = (cat.get("docs") or {}).get(a.doc_id)
    if not e:
        print(f"❌ 没有这个编号: {a.doc_id}")
        return 1
    print(json.dumps(e, ensure_ascii=False, indent=2))
    return 0


def _approve_one(cfg: dict, state: Path, wid: str, did: str, level: str) -> str:
    try:
        rec = KB.set_status(state, wid, did, "approved", level=level, by="维护者")
    except ValueError as e:
        print(f"⛔ {did} 不能公开：{e}")
        return ""
    if not rec:
        return ""
    _audit(state, wid, "kb_approve", {"doc_id": did, "level": level}, True,
           {"title": rec.get("title"), "category": rec.get("category")})
    return rec.get("title") or did


def cmd_approve(a) -> int:
    cfg, _root, state = _win(a.window)
    levels = _parse_levels(cfg, a.level)
    cat, err = KB.load_catalog(state, a.window)
    if err:
        print(f"❌ {err}")
        return 1
    docs = cat.get("docs") or {}
    targets: list[str] = []
    if a.doc_id:
        targets = [a.doc_id]
    elif a.all_pending or a.category:
        targets = [d for d, e in docs.items()
                   if e.get("status") == "pending" and (not a.category or e.get("category") == a.category)]
    if not targets:
        print("没有要批准的（用 <doc_id> / --all-pending / --category X 指定）")
        return 1
    level = levels[0]
    if not a.yes and len(targets) > 1:
        ans = input(f"要把 {len(targets)} 篇标为「{level}」并公开吗？[y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消。")
            return 1
    ok = []
    for d in targets:
        t = _approve_one(cfg, state, a.window, d, level)
        if not t:
            print(f"⚠️  没有这个编号: {d}")
        else:
            ok.append(t)
    print(f"✅ 已公开 {len(ok)} 篇（等级：{level}）—— 有这条等级地址的同事立即可见，无需重启")
    for t in ok[:10]:
        print(f"   · {t}")
    return 0


def cmd_reject(a) -> int:
    _cfg, _root, state = _win(a.window)
    rec = KB.set_status(state, a.window, a.doc_id, "rejected", note=a.reason or "维护者驳回")
    if not rec:
        print(f"❌ 没有这个编号: {a.doc_id}")
        return 1
    _audit(state, a.window, "kb_reject", {"doc_id": a.doc_id}, True, {"reason": a.reason})
    print(f"🚫 已驳回「{rec.get('title')}」（外部看不到；随时可再 approve）")
    return 0


def cmd_reindex(a) -> int:
    a.extract = getattr(a, "extract", "auto")
    return cmd_scan(a)


# ---------------------------------------------------------------- 同事地址命令
def cmd_requests(a) -> int:
    _cfg, _root, state = _win(a.window)
    data, err = KB.load_requests(state, a.window)
    if err:
        print(f"❌ {err}")
        return 1
    rows = list((data.get("requests") or {}).values())
    if a.pending:
        rows = [r for r in rows if r.get("status") == "pending"]
    rows.sort(key=lambda r: r.get("created_at") or "")
    rows = rows[-a.limit:]
    if not rows:
        print("没有申请记录。")
        return 0
    print(f"{'申请号':<11} {'状态':<9} {'姓名':<8} {'部门':<10} {'申请等级':<10} 用途")
    for r in rows:
        print(f"{(r.get('id') or ''):<11} {(r.get('status') or ''):<9} {(r.get('name') or ''):<8} "
              f"{(r.get('dept') or '-'):<10} {(r.get('level_requested') or '-'):<10} "
              f"{(r.get('purpose') or '')[:40]}")
    pend = [r for r in rows if r.get("status") == "pending"]
    if pend:
        print(f"\n待批 {len(pend)} 条。批准：{CLI} kb decide {a.window} <申请号> --approve --level <等级>")
    return 0


def cmd_decide(a) -> int:
    cfg, _root, state = _win(a.window)
    data, err = KB.load_requests(state, a.window)
    if err:
        print(f"❌ {err}")
        return 1
    req = (data.get("requests") or {}).get(a.rid)
    if not req:
        print(f"❌ 没有这条申请: {a.rid}")
        return 1
    if a.deny:
        KB.set_request_status(state, a.window, a.rid, "denied", note=a.reason or "维护者驳回")
        _audit(state, a.window, "kb_decision", {"request": a.rid, "action": "deny"}, True, {"reason": a.reason})
        print(f"🚫 已驳回 {req.get('name')} 的申请（{a.rid}）")
        return 0
    if not a.approve:
        print("要 --approve 或 --deny 二选一")
        return 1
    level = _parse_levels(cfg, a.level)[0]
    minutes = ACC.DEFAULT_MINUTES if a.for_ is None else _minutes(a.for_)
    u = ACC.upsert_user(state, a.window, req.get("name") or "", [level], dept=req.get("dept", ""),
                        note=f"来自申请 {a.rid}", minutes=minutes)
    KB.set_request_status(state, a.window, a.rid, "approved", note=f"批准（{level}）")
    _audit(state, a.window, "kb_decision", {"request": a.rid, "action": "approve", "level": level}, True,
           {"person": u["person"]})
    print(f"✅ 已批准 {u['person']}（等级 {level}，{ACC.describe_expiry(u)}）")
    print(f"   地址: {_address(cfg, u['token'])}")
    return 0


def cmd_passwd(a) -> int:
    """给别人（或自己）设网页登录密码：不指定就用随机强密码，明文只打印这一次。"""
    cfg, _root, state = _win(a.window)
    if a.admin:
        user = (a.user or "admin").strip()
        role, person = "admin", (a.person or "主人")
    else:
        user = (a.user or "").strip()
        if not user:
            print("❌ 要指定 --user 用户名（给同事开通网页账号），或用 --admin 给自己设")
            return 1
        role, person = "member", (a.person or user)
    old = AUTH.get(state, user)
    try:
        if old:
            rec, pw = AUTH.reset_password(state, user, length=a.length) if not a.password else \
                AUTH.set_account(state, user, role=old.get("role") or role,
                                 password=a.password, person=old.get("person") or person)[:2]
            word = "重置"
        else:
            if a.password:
                rec = AUTH.set_account(state, user, role=role, password=a.password, person=person)[0]
                pw = a.password
            else:
                rec, pw = AUTH.new_account(state, user, role=role, person=person, length=a.length)
            word = "开通"
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    base = _public_url(cfg, a.window).rstrip("/")
    print(f"✅ 已{word} {'管理员' if rec.get('role') == 'admin' else '同事'}账号")
    print(f"   登录地址：{base}/login")
    print(f"   用户名：  {user}")
    print(f"   密码：    {pw}      ← 只显示这一次，请立刻保存/转发")
    if rec.get("role") != "admin":
        u = ACC.get_user(state, a.window, person)
        lv = "、".join((u or {}).get("levels") or []) or "（注意：这位同事还没有资料等级，登录后看不到资料）"
        print(f"   资料等级：{lv}")
    return 0


def cmd_accounts(a) -> int:
    """列出网页账号（谁有账号、什么角色、上次登录）。"""
    _cfg, _root, state = _win(a.window)
    rows = AUTH.list_accounts(state)
    if not rows:
        print("还没有任何网页账号。给自己开一个：bash lighthouse.sh kb passwd " + a.window + " --admin")
        return 0
    print(f"{'用户名':<14} {'角色':<8} {'绑定同事':<12} {'密码':<6} {'锁定':<6} 上次登录")
    for r in rows:
        print(f"{(r['user'] or ''):<14} {('管理员' if r['role'] == 'admin' else '同事'):<8} "
              f"{(r['person'] or '-'):<12} {('有' if r['has_pw'] else '无'):<6} "
              f"{('是' if r['locked'] else '否'):<6} "
              f"{(str(r['last_login'])[:16].replace('T', ' ') if r['last_login'] else '从未登录')}")
    print(f"\n共 {len(rows)} 个账号；管理页里每个同事行也能「开通账号 / 重置密码」。")
    return 0


def cmd_edit(a) -> int:
    """改同事的等级/部门/备注/有效期/启用状态/姓名（地址不变）—— 与网页同一个台账层。"""
    cfg, _root, state = _win(a.window)
    cfg_levels = list((cfg.get("kb") or {}).get("levels") or [])
    p = a.name
    if ACC.get_user(state, a.window, p) is None:
        print(f"❌ 没有这个同事：{p}")
        return 1
    if not any([a.levels, a.dept is not None, a.note is not None, a.days is not None, a.until,
                a.enable, a.disable, a.rename]):
        print("❌ 没说要改什么（--levels/--dept/--note/--days/--until/--enable/--disable/--rename）")
        return 1
    lv = None
    if a.levels is not None:
        lv = [x.strip() for x in str(a.levels).replace("，", ",").split(",") if x.strip()]
        bad = [x for x in lv if x not in cfg_levels]
        if bad:
            print(f"❌ 不认识的等级 {bad}；本窗可用：{cfg_levels}")
            return 1
        if not lv:
            print("❌ 至少要留一个等级（想断开就用 kb revoke / kb rm）")
            return 1
    enabled = True if a.enable else (False if a.disable else None)
    exp = None
    try:
        if a.days is not None or a.until:
            exp = ACC.expiry_to_ts(str(a.days if a.days is not None else ""), a.until)
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    try:
        u = ACC.update_user(state, a.window, p, levels=lv, dept=a.dept, note=a.note,
                            expires=(exp if (a.days is not None or a.until) else ACC._UNSET),
                            enabled=enabled, new_name=a.rename)
    except (ValueError, RuntimeError) as e:
        print(f"❌ {e}")
        return 1
    _audit_admin(state, a.window, "kb_user_update",
                 {"person": a.name, "levels": u.get("levels"), "renamed": bool(a.rename)}, u)
    bits = []
    if lv is not None:
        bits.append("等级 " + "、".join(lv))
    if a.dept is not None:
        bits.append(f"部门「{a.dept}」")
    if a.note is not None:
        bits.append("备注已更新")
    if a.days is not None or a.until:
        bits.append(ACC.describe_expiry(u))
    if enabled is not None:
        bits.append("已启用" if enabled else "已停用")
    if a.rename:
        bits.append(f"改名 {a.name} → {a.rename}")
    print(f"✅ 已更新 {u['person']}：{'；'.join(bits)}（地址不变）")
    return 0


def cmd_rm(a) -> int:
    """彻底删掉一个同事（那条地址立即失效）。"""
    _cfg, _root, state = _win(a.window)
    u = ACC.get_user(state, a.window, a.name)
    if not u:
        print(f"❌ 没有这个同事：{a.name}")
        return 1
    if not a.yes:
        print(f"⚠️  这会把 {a.name} 连地址一起删掉。确认就加 --yes。")
        return 1
    ACC.delete_user(state, a.window, a.name)
    _audit_admin(state, a.window, "kb_user_delete", {"person": a.name}, u)
    print(f"✅ 已删除 {a.name} —— 他那条地址立刻失效。")
    return 0


def _audit_admin(state_root, wid: str, tool: str, args: dict, u: dict) -> None:
    """CLI 侧的操作也写审计（和网页同一条流水）。"""
    import json as _json
    row = {"ts": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
           "window": wid, "tool": tool, "ok": True, "args": args, "actor": "admin", "via": "cli",
           "person": u.get("person"), "levels": u.get("levels")}
    d = Path(state_root) / "audit"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{wid}.jsonl", "a", encoding="utf-8") as f:
        f.write(_json.dumps(row, ensure_ascii=False) + "\n")


def cmd_users(a) -> int:
    cfg, _root, state = _win(a.window)
    rows = ACC.list_users(state, a.window)
    if not rows:
        print("还没有给任何人发过地址。")
        return 0
    print(f"{'姓名':<10} {'等级':<18} {'状态':<8} {'调用':>5} {'被拒':>5}  最后活跃            有效期")
    for u in rows:
        print(f"{(u.get('person') or ''):<10} {(','.join(u.get('levels') or [])):<18} "
              f"{('有效' if u.get('enabled', True) else '已停用'):<8} {u.get('calls', 0):>5} "
              f"{u.get('denied', 0):>5}  {(u.get('last_seen') or '从未使用')[:19]:<19} {ACC.describe_expiry(u)}")
        if a.show_token:
            print(f"           地址: {_address(cfg, u.get('token') or '')}")
    return 0


def cmd_grant(a) -> int:
    cfg, _root, state = _win(a.window)
    levels = _parse_levels(cfg, a.level)
    minutes = ACC.DEFAULT_MINUTES if a.for_ is None else _minutes(a.for_)
    existing = ACC.get_user(state, a.window, a.name)
    u = ACC.upsert_user(state, a.window, a.name, levels, dept=a.dept or "", note=a.note or "", minutes=minutes)
    _audit(state, a.window, "kb_grant", {"person": u["person"], "levels": levels}, True,
           {"expires": u.get("expires"), "updated": bool(existing)})
    verb = "已更新" if existing else "已发放"
    print(f"✅ {u['person']} 的地址{verb}（等级 {','.join(levels)}；{ACC.describe_expiry(u)}）")
    print(f"   地址: {_address(cfg, u['token'])}")
    return 0


def cmd_set_level(a) -> int:
    return cmd_grant(a)


def cmd_rotate(a) -> int:
    cfg, _root, state = _win(a.window)
    u = ACC.rotate(state, a.window, a.name, minutes=None)
    if not u:
        print(f"❌ 找不到 {a.name}")
        return 1
    _audit(state, a.window, "kb_rotate", {"person": u["person"]}, True, {})
    print(f"✅ 已给 {u['person']} 换新地址（等级 {','.join(u.get('levels') or [])}，旧地址立刻失效）")
    print(f"   新地址: {_address(cfg, u['token'])}")
    return 0


def cmd_revoke(a) -> int:
    _cfg, _root, state = _win(a.window)
    if not ACC.set_enabled(state, a.window, a.name, bool(a.enable)):
        print(f"❌ 找不到 {a.name}")
        return 1
    _audit(state, a.window, "kb_revoke", {"person": a.name, "enabled": bool(a.enable)}, True, {})
    print(f"{'✅ 已恢复' if a.enable else '🚫 已收回'} {a.name} 的访问权（立即生效，其他人不受影响）")
    return 0


INVITE_TMPL = """# 招投标资料库 —— 怎么用

## 一、这是什么
一个「只给你看授权范围」的只读资料库。你让 AI 助手连上它，就能问：
「库里有没有讲服务器双电源冗余的方案？把相关段落贴出来。」

## 二、拿到你的地址（两种方式）
1. 自己申请：打开 {req_url} 填表 → **记下「申请号 + 查询码」**；
   审批通过后回到 {req_url}/status 输入这两样，就能看到属于你的地址。
2. 由维护者直接发给你（就是本页末尾那条）。

## 三、把地址接进你的 AI（以 ChatGPT 为例）
1. ChatGPT → 设置 → 安全防护 → 打开「开发者模式」→ 插件页右上「创建应用」
2. 名称：招投标资料库    服务器 URL：粘贴你的地址    身份验证：无 → 勾确认 → 创建
3. 回到对话，输入框里打 `@招投标资料库` 选中它，然后提问。
（WorkBuddy / Cherry Studio / Claude 等：把同一条地址填进它的 MCP 配置即可。）

## 四、能问什么
- 「技术方案类有哪些？」（先给清单，再按编号读）
- 「等保三级的要求写在哪个文件里？」
- 「把《XX方案》第 3 节贴出来」

## 五、注意
- 只能读，不能改；看不到的内容就是没授权，不是坏了。
- 你的地址是**专属的**，可以随时被收回或更换；别转发给别人。
- 资料涉及项目信息，请勿外传。

## 你的地址（等级：{levels}，有效期：{expire}）
`{address}`
"""


def cmd_invite(a) -> int:
    cfg, _root, state = _win(a.window)
    levels = _parse_levels(cfg, a.level)
    minutes = ACC.DEFAULT_MINUTES if a.for_ is None else _minutes(a.for_)
    u = ACC.upsert_user(state, a.window, a.name, levels, note=a.note or "", minutes=minutes)
    _audit(state, a.window, "kb_grant", {"person": u["person"], "levels": levels}, True, {"invite": True})
    addr = _address(cfg, u["token"])
    text = INVITE_TMPL.format(req_url=_public_url(cfg, a.window) + "/request", levels=",".join(levels),
                              expire=ACC.describe_expiry(u), address=addr)
    out = Path(a.out).expanduser()
    if out.is_dir():
        out = out / f"{u['person']}-使用说明.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"✅ 已为「{u['person']}」发/更新地址（等级 {'、'.join(levels)}；{ACC.describe_expiry(u)}）")
    print(f"   地址: {addr}")
    print(f"   使用说明已写到: {out}")
    return 0


# ---------------------------------------------------------------- 汇报 / 用量
def cmd_notify(a) -> int:
    _cfg, _root, state = _win(a.window)
    data, err = KB.load_requests(state, a.window)
    if err:
        print(f"❌ {err}")
        return 1
    rows = [r for r in (data.get("requests") or {}).values() if not r.get("notified")]
    rows.sort(key=lambda r: r.get("created_at") or "")
    if not rows:
        print("没有新申请。")
        return 0
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            tag = "自动开通" if r.get("auto") else "待审批"
            print(f"[{tag}] {r.get('created_at', '')[:16]}  {r.get('name')}（{r.get('dept') or '-'}）"
                  f"  申请等级 {r.get('level_requested')}")
            print(f"          用途：{r.get('purpose')}")
            print(f"          申请号 {r.get('id')}　联系 {r.get('contact') or '-'}")
    if a.ack:
        d2, err2 = KB.load_requests(state, a.window)
        if not err2:
            for rid in [r["id"] for r in rows]:
                d2["requests"][rid]["notified"] = True
            KB.save_requests(state, a.window, d2)
            print(f"（已记：{len(rows)} 条不再重复提醒）")
    return 0


def cmd_usage(a) -> int:
    _cfg, _root, state = _win(a.window)
    argv = [a.window, "--days", str(a.days), "--by", a.by, "--state-root", str(state)]
    if a.csv:
        argv.append("--csv")
    return USAGE.main(argv)


def cmd_admin_url(a) -> int:
    cfg, _root, state = _win(a.window)
    if getattr(a, "rotate", False):
        tok = WEB.rotate_admin_token(state)
        _audit(state, a.window, "kb_admin_rotate", {}, True, {"note": "管理令已轮换，旧链接立即失效"})
        print("⚠️ 管理令已换成新的，之前那条管理页链接立刻失效。")
    else:
        tok = WEB.ensure_admin_token(state)
    base = _public_url(cfg, a.window)
    print("管理页（本机浏览器直接打开；可从手机访问时请保管好这条带管理令的地址）：")
    print(f"  {base}/admin?k={tok}")
    print(f"申请页（可以发给同事）：")
    print(f"  {base}/request")
    return 0


# ---------------------------------------------------------------- argparse
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lighthouse.sh kb", description="受控资料库：台账 / 同事地址 / 申请 / 用量")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("window"); p.add_argument("--lib", default="~/Documents/招投标文档库"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("scan"); p.add_argument("window"); p.add_argument("--extract", choices=["auto", "none", "mineru"], default="auto"); p.add_argument("--yes", action="store_true"); p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("reindex"); p.add_argument("window"); p.add_argument("--extract", choices=["auto", "none", "mineru"], default="auto"); p.add_argument("--yes", action="store_true"); p.set_defaults(fn=cmd_reindex)
    p = sub.add_parser("pending"); p.add_argument("window"); p.add_argument("--level", default=""); p.add_argument("--limit", type=int, default=200); p.set_defaults(fn=cmd_pending)
    p = sub.add_parser("list"); p.add_argument("window"); p.add_argument("--approved", action="store_true"); p.add_argument("--pending", action="store_true"); p.add_argument("--rejected", action="store_true"); p.add_argument("--category", default=""); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("show"); p.add_argument("window"); p.add_argument("doc_id"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("approve"); p.add_argument("window"); p.add_argument("doc_id", nargs="?"); p.add_argument("--all-pending", action="store_true"); p.add_argument("--category", default=""); p.add_argument("--level", default=""); p.add_argument("--yes", action="store_true"); p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("reject"); p.add_argument("window"); p.add_argument("doc_id"); p.add_argument("--reason", default=""); p.set_defaults(fn=cmd_reject)
    p = sub.add_parser("requests"); p.add_argument("window"); p.add_argument("--pending", action="store_true"); p.add_argument("--limit", type=int, default=50); p.set_defaults(fn=cmd_requests)
    p = sub.add_parser("decide"); p.add_argument("window"); p.add_argument("rid"); p.add_argument("--approve", action="store_true"); p.add_argument("--deny", action="store_true"); p.add_argument("--level", default=""); p.add_argument("--for", dest="for_", default=None); p.add_argument("--reason", default=""); p.set_defaults(fn=cmd_decide)
    p = sub.add_parser("users"); p.add_argument("window"); p.add_argument("--show-token", action="store_true"); p.set_defaults(fn=cmd_users)
    for name in ("grant", "set-level"):
        p = sub.add_parser(name); p.add_argument("window"); p.add_argument("--name", required=True); p.add_argument("--level", default=""); p.add_argument("--for", dest="for_", default=None); p.add_argument("--dept", default=""); p.add_argument("--note", default=""); p.set_defaults(fn=cmd_grant if name == "grant" else cmd_set_level)
    p = sub.add_parser("rotate"); p.add_argument("window"); p.add_argument("--name", required=True); p.set_defaults(fn=cmd_rotate)
    p = sub.add_parser("revoke"); p.add_argument("window"); p.add_argument("--name", required=True); p.add_argument("--enable", action="store_true"); p.set_defaults(fn=cmd_revoke)
    p = sub.add_parser("edit"); p.add_argument("window"); p.add_argument("--name", required=True)
    p.add_argument("--levels", default=None, help='等级，逗号分隔，如 "L1-商务,L2-技术"')
    p.add_argument("--dept", default=None); p.add_argument("--note", default=None)
    p.add_argument("--days", default=None, help="有效期：多少天（0=无期限）")
    p.add_argument("--until", default=None, help="或到某天，2026-12-31")
    p.add_argument("--enable", action="store_true"); p.add_argument("--disable", action="store_true")
    p.add_argument("--rename", default=None, help="改成新名字（地址不变）")
    p.set_defaults(fn=cmd_edit)
    p = sub.add_parser("rm"); p.add_argument("window"); p.add_argument("--name", required=True)
    p.add_argument("--yes", action="store_true"); p.set_defaults(fn=cmd_rm)
    p = sub.add_parser("passwd"); p.add_argument("window")
    p.add_argument("--user", default=None, help="用户名（给同事开通网页账号）")
    p.add_argument("--admin", action="store_true", help="给自己（管理员）设密码")
    p.add_argument("--password", default=None, help="自己指定密码（不给就随机生成）")
    p.add_argument("--person", default=None, help="绑定到哪位同事（默认同名）")
    p.add_argument("--length", type=int, default=14); p.set_defaults(fn=cmd_passwd)
    p = sub.add_parser("accounts"); p.add_argument("window"); p.set_defaults(fn=cmd_accounts)
    p = sub.add_parser("invite"); p.add_argument("window"); p.add_argument("--name", required=True); p.add_argument("--out", required=True); p.add_argument("--level", default=""); p.add_argument("--for", dest="for_", default=None); p.add_argument("--note", default=""); p.set_defaults(fn=cmd_invite)
    p = sub.add_parser("notify"); p.add_argument("window"); p.add_argument("--ack", action="store_true"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_notify)
    p = sub.add_parser("usage"); p.add_argument("window"); p.add_argument("--days", type=int, default=7); p.add_argument("--by", choices=["person", "day", "doc", "tool"], default="person"); p.add_argument("--csv", action="store_true"); p.set_defaults(fn=cmd_usage)
    p = sub.add_parser("admin-url"); p.add_argument("window"); p.add_argument("--rotate", action="store_true"); p.set_defaults(fn=cmd_admin_url)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    os.environ.setdefault("LIGHTHOUSE_CLI", "bash lighthouse.sh")
    raise SystemExit(main())
